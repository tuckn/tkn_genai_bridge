"""Non-interactive CLI adapters, using private temporary working directories."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..errors import GenAIError, ProviderError
from ..models import GenerationRequest, Profile, ResponseMetadata, Usage
from ..validation import parse_object
from .base import ProviderResponse, model_name, number, preserve_metadata

_EXECUTABLES = {"codex": "codex", "claude-code": "claude", "github-copilot": "copilot"}


def resolve_executable(profile: Profile) -> str:
    name = (
        profile.cli.executable if profile.cli and profile.cli.executable else _EXECUTABLES[profile.provider]
    )
    candidate = Path(name).expanduser()
    found = str(candidate.resolve()) if candidate.is_file() else shutil.which(name)
    if not found and os.name == "nt":
        command = _EXECUTABLES[profile.provider]
        if name.casefold() in {command, f"{command}.exe"}:
            candidates = [Path.home() / ".local" / "bin" / f"{command}.exe"]
            if os.getenv("LOCALAPPDATA"):
                local = Path(os.environ["LOCALAPPDATA"])
                candidates.extend(
                    [
                        local / "Microsoft" / "WinGet" / "Links" / f"{command}.exe",
                        local / "Programs" / "OpenAI" / "Codex" / "bin" / f"{command}.exe",
                    ]
                )
            found = next((str(p) for p in candidates if p.is_file()), None)
    if not found:
        raise ProviderError(
            "provider executable was not found; set cli.executable or update PATH", code="executable_missing"
        )
    if profile.provider == "codex" and "\\windowsapps\\" in found.replace("/", "\\").casefold():
        raise ProviderError(
            "use the standalone Codex CLI instead of the WindowsApps launcher", code="invalid_executable"
        )
    if Path(found).suffix.casefold() in {".cmd", ".bat", ".ps1"}:
        raise ProviderError(
            "configure a native executable; shell shims are not supported", code="invalid_executable"
        )
    return found


def schema_prompt(request: GenerationRequest) -> str:
    instruction = (
        files("tkn_genai_bridge.resources").joinpath("json_instruction.txt").read_text(encoding="utf-8")
    )
    return (
        request.prompt.rstrip()
        + "\n\n"
        + instruction
        + json.dumps(request.output_schema, ensure_ascii=False, separators=(",", ":"))
    )


def _stop_process(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        # Limit cleanup to this invocation's process tree, never to executable names.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        # Do not wait indefinitely for inherited pipes held by an escaped child.
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


def run_process(
    command: list[str], prompt: str, cwd: Path, timeout: float, *, provider: str | None = None
) -> str:
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            start_new_session=os.name != "nt",
        )
        try:
            stdout, _stderr = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_process(process)
            raise ProviderError(
                "provider timed out; submission and billing may be unknown",
                code="timeout",
                submission_unknown=True,
            ) from None
        except KeyboardInterrupt:
            _stop_process(process)
            raise
    except OSError:
        raise ProviderError(
            "cannot start provider; check executable and permissions", code="process_start"
        ) from None
    if process.returncode:
        error = ProviderError(
            f"provider exited with status {process.returncode}; check its login and model",
            code="process_exit",
            submission_unknown=True,
        )
        if provider == "codex":
            model, usage = _codex_metadata(stdout)
            error.metadata = ResponseMetadata(response_model=model, usage=usage)
        elif provider == "claude-code":
            try:
                error.metadata = _claude_metadata(parse_object(stdout))
            except GenAIError:
                pass  # A malformed envelope has no reliably extractable metadata.
        raise error
    return stdout


def _codex_metadata(stdout: str) -> tuple[str | None, Usage]:
    response_model = None
    counts: dict[str, Any] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        response_model = model_name(event.get("model")) or response_model
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            counts = event["usage"]
    return response_model, Usage(
        input_tokens=number(counts.get("input_tokens")),
        output_tokens=number(counts.get("output_tokens")),
        cached_input_tokens=number(counts.get("cached_input_tokens")),
        reasoning_tokens=number(counts.get("reasoning_output_tokens")),
        cache_write_tokens=number(counts.get("cache_write_tokens")),
    )


def _claude_metadata(envelope: dict[str, Any]) -> ResponseMetadata:
    counts = envelope.get("usage") or {}
    if not isinstance(counts, dict):
        counts = {}
    model_usage = envelope.get("modelUsage")
    model = model_name(envelope.get("model"))
    if model is None and isinstance(model_usage, dict) and len(model_usage) == 1:
        model = model_name(next(iter(model_usage)))
    return ResponseMetadata(
        response_model=model,
        usage=Usage(
            input_tokens=number(counts.get("input_tokens")),
            output_tokens=number(counts.get("output_tokens")),
            cached_input_tokens=number(counts.get("cache_read_input_tokens")),
            cache_write_tokens=number(counts.get("cache_creation_input_tokens")),
            input_tokens_scope="uncached",
        ),
    )


class CliBackend:
    def generate(self, profile: Profile, request: GenerationRequest) -> ProviderResponse:
        executable = resolve_executable(profile)
        with tempfile.TemporaryDirectory(prefix="tkn-genai-bridge-") as folder:
            cwd = Path(folder)
            command = [executable]
            prompt = request.prompt
            if profile.provider == "codex":
                schema_path, output_path = cwd / "schema.json", cwd / "output.json"
                schema_path.write_text(
                    json.dumps(request.output_schema, ensure_ascii=False), encoding="utf-8"
                )
                command += [
                    "exec",
                    "--json",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                    "--color",
                    "never",
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                ]
                if profile.reasoning_effort:
                    command += ["-c", f"model_reasoning_effort={json.dumps(profile.reasoning_effort)}"]
            elif profile.provider == "claude-code":
                command += [
                    "-p",
                    "--output-format",
                    "json",
                    "--json-schema",
                    json.dumps(request.output_schema, ensure_ascii=False, separators=(",", ":")),
                    "--tools",
                    "",
                    "--permission-mode",
                    "dontAsk",
                    "--setting-sources",
                    "project",
                    "--strict-mcp-config",
                    "--disable-slash-commands",
                    "--no-chrome",
                    "--no-session-persistence",
                ]
            else:
                command += [
                    "-s",
                    "--no-ask-user",
                    "--no-color",
                    "--no-auto-update",
                    "--no-custom-instructions",
                    "--disable-builtin-mcps",
                    "--no-remote",
                    "--no-remote-export",
                    "--deny-tool=shell",
                    "--deny-tool=write",
                    "--deny-tool=read",
                    "--deny-tool=url",
                    "--deny-tool=memory",
                ]
                prompt = schema_prompt(request)
            if profile.model:
                command += ["--model", profile.model]
            if profile.reasoning_effort and profile.provider != "codex":
                command += ["--effort", profile.reasoning_effort]
            if profile.provider == "codex":
                command.append("-")
            stdout = run_process(command, prompt, cwd, profile.timeout_seconds, provider=profile.provider)
            if profile.provider == "codex":
                model, usage = _codex_metadata(stdout)
                with preserve_metadata(ResponseMetadata(response_model=model, usage=usage)):
                    try:
                        output = output_path.read_text(encoding="utf-8-sig")
                    except (OSError, UnicodeError):
                        raise ProviderError(
                            "Codex completed without a readable output file", code="missing_output"
                        ) from None
                    return ProviderResponse(parse_object(output), model, usage)
            envelope = parse_object(stdout)
            if profile.provider == "github-copilot":
                return ProviderResponse(envelope)  # Silent text mode has no reliable usage/model metadata.
            metadata = _claude_metadata(envelope)
            with preserve_metadata(metadata):
                if envelope.get("is_error") or envelope.get("subtype") not in {None, "success"}:
                    raise ProviderError(
                        "Claude Code returned an unsuccessful result", code="incomplete_response"
                    )
                data = envelope.get("structured_output")
                if not isinstance(data, dict):
                    raise ProviderError("Claude Code returned no structured_output", code="missing_output")
                return ProviderResponse(data, metadata.response_model, metadata.usage)
