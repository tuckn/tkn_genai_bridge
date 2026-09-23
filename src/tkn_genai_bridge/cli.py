"""Configuration management and one-shot structured generation."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import __version__
from .config import initialize_config, load_config, user_config_path
from .errors import ConfigError, GenAIError, OutputValidationError, RequestError
from .logging_utils import SUCCESS, configure_logging
from .models import GenerationRequest
from .runtime import Runtime
from .validation import parse_object


def _settings(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="追加で読み込む共有設定ファイル")
    parser.add_argument(
        "--no-project-config", action="store_true", help="CWD の .tkn/config.yaml を読み込まない"
    )
    parser.add_argument("--profile", help="今回使用する接続プロファイル")
    parser.add_argument("--model", help="今回使用するモデル名。Azure ではデプロイ名")
    parser.add_argument(
        "--reasoning-effort", help="今回使用する推論設定。Ollama は設定ファイルの think を使用"
    )
    parser.add_argument("--timeout-seconds", type=float, help="プロセス待機／HTTP I/O のタイムアウト秒数")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="tkn-genai-bridge", description="Python CLI 共通の生成AI接続と設定管理"
    )
    root.add_argument("--version", action="version", version=__version__)
    logging = root.add_mutually_exclusive_group()
    logging.add_argument("-q", "--quiet", action="store_true", help="エラーだけを stderr に表示")
    logging.add_argument("-v", "--verbose", action="store_true", help="診断情報を stderr に表示")
    commands = root.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config", help="共有設定の作成と確認")
    actions = config.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="設定を作成。同一内容は unchanged、編集済みは保護")
    init.add_argument("--path", type=Path, help="作成先。既定: ~/.tkn/genai_bridge/config.yaml")
    init.add_argument("--dry-run", action="store_true", help="書き込み・認証・通信なしで作成予定を確認")
    show = actions.add_parser("show", help="解決済み設定と設定元を JSON で表示。通信・書き込みなし")
    _settings(show)
    generate = commands.add_parser("generate", help="生成AIを呼び出し、検証済み JSON を stdout に表示")
    _settings(generate)
    generate.add_argument("--prompt-file", required=True, type=Path, help="UTF-8 の入力プロンプト")
    generate.add_argument("--schema-file", required=True, type=Path, help="UTF-8 の JSON Schema")
    generate.add_argument(
        "--dry-run",
        action="store_true",
        help="設定・入力・実行ファイルを検証。通信・認証・AI呼び出し・ファイル作成なし",
    )
    return root


def _resolved(args: argparse.Namespace) -> Any:
    arguments = {"config_file": args.config, "include_project": not args.no_project_config}
    first = load_config(**arguments)
    selected = args.profile if args.profile is not None else first.config.default_profile
    # Typos must not implicitly create a profile from built-in defaults.
    first.profile(selected)
    values = {
        key: getattr(args, key)
        for key in ("model", "reasoning_effort", "timeout_seconds")
        if getattr(args, key) is not None
    }
    overrides: dict[str, Any] = {}
    if args.profile is not None:
        overrides["default_profile"] = selected
    if values:
        overrides["profiles"] = {selected: values}
    return load_config(**arguments, overrides=overrides)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    logger = configure_logging(quiet=args.quiet, verbose=args.verbose)
    result: dict[str, Any]
    try:
        if args.command == "config" and args.action == "init":
            result = initialize_config(args.path, dry_run=args.dry_run)
        else:
            resolved = _resolved(args)
            if args.command == "config":
                result = {
                    "settings": resolved.config.model_dump(),
                    "user_config_path": str(user_config_path().resolve()),
                    "sources": [asdict(source) for source in resolved.sources],
                    "field_sources": resolved.field_sources,
                }
            else:
                try:
                    prompt = args.prompt_file.expanduser().read_text(encoding="utf-8-sig")
                    schema = parse_object(args.schema_file.expanduser().read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError):
                    raise ConfigError("cannot read prompt/schema as UTF-8 files", code="input_io") from None
                except OutputValidationError:
                    raise RequestError(
                        "schema file must contain one strict JSON object", code="invalid_schema"
                    ) from None
                request = GenerationRequest(prompt=prompt, output_schema=schema)
                with Runtime(resolved.profile()) as runtime:
                    if args.dry_run:
                        result = runtime.plan(request, check_executable=True).model_dump()
                    else:
                        logger.info("Generating with %s", runtime.profile.provider)
                        result = runtime.generate(request).model_dump()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        logger.log(SUCCESS, "Completed")
        return 0
    except GenAIError as exc:
        logger.error("%s: %s", exc.code, exc)
        result = {"error": {"code": exc.code, "message": str(exc)}}
        if exc.record:
            result["record"] = exc.record.model_dump()
        print(json.dumps(result, ensure_ascii=False))
        return 2 if isinstance(exc, ConfigError) else 1
    except ValidationError:
        logger.error("invalid input; check types, required fields and provider options")
        print(json.dumps({"error": {"code": "invalid_input", "message": "invalid input"}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
