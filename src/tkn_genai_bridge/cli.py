"""Configuration management and one-shot structured generation."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn

from pydantic import ValidationError

from . import __version__
from .config import initialize_config, load_config, user_config_path
from .errors import ConfigError, GenAIError, OutputValidationError, ProviderError, RequestError
from .images import ImageInput
from .logging_utils import SUCCESS, configure_logging
from .models import GenerationRequest
from .output import emit, text_value
from .runtime import Runtime
from .validation import parse_object


class Parser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        raise ConfigError(message, code="argument_error")


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="既定の key=value 一覧の代わりに機械処理用 JSON を stdout に表示",
    )
    logging = parser.add_mutually_exclusive_group()
    logging.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="stderr のログをエラーだけにする。最終結果は表示",
    )
    logging.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="stderr の診断情報を増やす。最終結果の形式は変更しない",
    )


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
    root = Parser(
        prog="tkn-genai-bridge",
        description="Python CLI 共通の生成AI接続と設定管理。結果は key=value 一覧、--json で JSON",
        epilog="結果は stdout、ログは stderr。終了コード: 成功 0、生成・実行失敗 1、"
        "引数・設定・入力検証エラー 2、中断 130。--json はコマンドの前後に指定可能。",
    )
    root.add_argument("--version", action="version", version=__version__)
    _common(root)
    commands = root.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config", help="共有設定の作成と確認")
    _common(config)
    actions = config.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="設定を作成。同一内容は unchanged、編集済みは保護")
    _common(init)
    init.add_argument("--path", type=Path, help="作成先。既定: ~/.tkn/genai_bridge/config.yaml")
    init.add_argument("--dry-run", action="store_true", help="書き込み・認証・通信なしで作成予定を確認")
    listing = actions.add_parser("list", help="解決済み設定と設定元を key=value で表示。通信・書き込みなし")
    _common(listing)
    _settings(listing)
    generate = commands.add_parser("generate", help="生成AIを呼び出し、検証済み結果と実行情報を表示")
    _common(generate)
    _settings(generate)
    generate.add_argument("--prompt-file", required=True, type=Path, help="UTF-8 の入力プロンプト")
    generate.add_argument("--schema-file", required=True, type=Path, help="UTF-8 の JSON Schema")
    generate.add_argument(
        "--image",
        action="append",
        type=Path,
        default=[],
        help="添付するローカル画像（PNG/JPEG/WebP）。複数回指定でき、指定順に渡す",
    )
    generate.add_argument(
        "--estimate-input-tokens",
        type=int,
        help="dry-run の入力token概算値。余裕分を含めた最終値を指定",
    )
    generate.add_argument(
        "--estimate-input-method",
        help="入力推定方法の識別名。--estimate-input-tokens と併用",
    )
    generate.add_argument(
        "--estimate-output-tokens",
        type=int,
        help="dry-run の出力token仮定値。生成条件や予算上限は変更しない",
    )
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


def _path(path: Path) -> str:
    return str(path.expanduser().absolute())


def _execute(args: argparse.Namespace, paths: dict[str, Any]) -> dict[str, Any]:
    if args.command == "config" and args.action == "init":
        paths["config_file"] = _path(args.path or user_config_path())
        return initialize_config(args.path, dry_run=args.dry_run)

    # On configuration errors these are candidate sources, not claims that every file is invalid.
    paths["user_config"] = _path(user_config_path())
    if not args.no_project_config:
        paths["project_config"] = _path(Path.cwd() / ".tkn" / "config.yaml")
    if args.config is not None:
        paths["config_file"] = _path(args.config)
    resolved = _resolved(args)
    if args.command == "config":
        return {
            "settings": resolved.config.model_dump(),
            "user_config_path": str(user_config_path().resolve()),
            "sources": [asdict(source) for source in resolved.sources],
            "field_sources": resolved.field_sources,
        }

    texts: dict[str, str] = {}
    for role in ("prompt_file", "schema_file"):
        paths.clear()
        paths[role] = _path(getattr(args, role))
        try:
            texts[role] = getattr(args, role).expanduser().read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            raise ConfigError("cannot read input as a UTF-8 file", code="input_io") from None
    try:
        schema = parse_object(texts["schema_file"])
    except OutputValidationError:
        raise RequestError("schema file must contain one strict JSON object", code="invalid_schema") from None
    images = []
    for path in args.image:
        paths.clear()
        paths["image"] = _path(path)
        images.append(ImageInput.from_file(path))
    paths.clear()
    paths.update(
        prompt_file=_path(args.prompt_file),
        schema_file=_path(args.schema_file),
        images=[_path(path) for path in args.image],
    )
    request = GenerationRequest(prompt=texts["prompt_file"], output_schema=schema, images=images)
    with Runtime(resolved.profile()) as runtime:
        if args.dry_run:
            return runtime.plan(
                request,
                check_executable=True,
                output_tokens=args.estimate_output_tokens,
                input_tokens=args.estimate_input_tokens,
                input_tokens_method=args.estimate_input_method,
            ).model_dump()
        logging.getLogger("tkn_genai_bridge").info("Generating with %s", runtime.profile.provider)
        return runtime.generate(request).model_dump()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    option_tokens = argv[: argv.index("--")] if "--" in argv else argv
    # Retain the requested format even when a required argument or subcommand is invalid.
    json_output = "--json" in option_tokens
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logger = configure_logging(quiet=False, verbose=False)
    paths: dict[str, Any] = {}
    result: dict[str, Any]
    try:
        argument_parser = parser()
        args = argument_parser.parse_args(argv)
        if getattr(args, "quiet", False) and getattr(args, "verbose", False):
            argument_parser.error("--quiet and --verbose cannot be combined")
        logger = configure_logging(
            quiet=getattr(args, "quiet", False), verbose=getattr(args, "verbose", False)
        )
        if args.command == "generate":
            estimates = (args.estimate_input_tokens, args.estimate_output_tokens)
            if any(value is not None for value in estimates) or args.estimate_input_method is not None:
                if not args.dry_run or any(value is not None and value < 0 for value in estimates):
                    argument_parser.error(
                        "token estimate options require --dry-run and non-negative integers"
                    )
                if args.estimate_input_method is not None and args.estimate_input_tokens is None:
                    argument_parser.error("--estimate-input-method requires --estimate-input-tokens")
        result = _execute(args, paths)
        emit(result, json_output=json_output)
        if args.command == "config" and args.action == "list":
            logger.info("Showing resolved configuration")
        else:
            logger.log(SUCCESS, "Completed")
        return 0
    except GenAIError as exc:
        logger.error("%s: %s", exc.code, text_value(str(exc)))
        result = {"error": {"code": exc.code, "message": str(exc)}}
        if isinstance(exc, ProviderError):
            result["error"].update(
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                retryable=exc.retryable,
                submission_unknown=exc.submission_unknown,
            )
        if exc.record:
            result["record"] = exc.record.model_dump()
        exit_code = 2 if isinstance(exc, ConfigError) else 1
    except ValidationError:
        logger.error("invalid input; check types, required fields and provider options")
        result = {"error": {"code": "invalid_input", "message": "invalid input"}}
        exit_code = 2
    except KeyboardInterrupt:
        logger.error("interrupted; a submitted provider request may still have run")
        result = {"error": {"code": "interrupted", "message": "interrupted"}}
        exit_code = 130
    except Exception as exc:
        # Arbitrary exception text/tracebacks can contain prompts, responses or credentials.
        logger.error("unexpected runtime failure (%s)", type(exc).__name__)
        result = {"error": {"code": "runtime_error", "message": "unexpected runtime failure"}}
        exit_code = 1
    if paths:
        result["error"]["paths"] = paths
    emit(result, json_output=json_output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
