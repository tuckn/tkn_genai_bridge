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
from ..images import IMAGE_SUFFIXES, validate_image_provider
from ..models import CLI_EXECUTABLES, GenerationRequest, Profile, ResponseMetadata, TokenCounts, Usage
from ..usage import summarize_usage
from ..validation import parse_object
from .base import ProviderResponse, model_name, number, preserve_metadata


def resolve_executable(profile: Profile) -> str:
    name = (
        profile.cli.executable
        if profile.cli and profile.cli.executable
        else CLI_EXECUTABLES[profile.provider]
    )
    candidate = Path(name).expanduser()
    found = str(candidate.resolve()) if candidate.is_file() else shutil.which(name)
    if not found and os.name == "nt":
        command = CLI_EXECUTABLES[profile.provider]
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


def _stop_process(process: subprocess.Popen[str]) -> str | None:
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
        stdout, _stderr = process.communicate(timeout=5)
        return stdout
    except subprocess.TimeoutExpired as exc:
        # Do not wait indefinitely for inherited pipes held by an escaped child.
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        return _stdout_text(exc.output)


def _stdout_text(value: str | bytes | None) -> str | None:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _process_metadata(stdout: str, provider: str | None) -> ResponseMetadata | None:
    if provider == "codex":
        model, usage = _codex_metadata(stdout, interrupted=True)
        return ResponseMetadata(response_model=model, usage=usage)
    if provider == "antigravity":
        return _antigravity_result(stdout, interrupted=True)[1]
    if provider == "claude-code":
        try:
            return _claude_metadata(parse_object(stdout), interrupted=True)
        except GenAIError:
            pass
    return None


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
        except subprocess.TimeoutExpired as exc:
            # communicate() after timeout returns the complete buffered stream; never concatenate it.
            stopped_stdout = _stop_process(process)
            captured = stopped_stdout if stopped_stdout is not None else _stdout_text(exc.output)
            error = ProviderError(
                "provider timed out; submission and billing may be unknown",
                code="timeout",
                submission_unknown=True,
            )
            error.metadata = _process_metadata(captured or "", provider)
            raise error from None
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
        error.metadata = _process_metadata(stdout, provider)
        raise error
    return stdout


def _codex_metadata(stdout: str, *, interrupted: bool = False) -> tuple[str | None, Usage]:
    """Account for completed turns; never mistake a truncated stream for a full total."""
    response_model = None
    fragments: list[TokenCounts] = []
    stream_complete = not interrupted
    turn_open = False
    saw_start = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            stream_complete = False
            continue
        if not isinstance(event, dict):
            stream_complete = False
            continue
        response_model = model_name(event.get("model")) or response_model
        kind = event.get("type")
        if kind == "turn.started":
            if turn_open:
                stream_complete = False
            turn_open = saw_start = True
        elif kind == "turn.completed":
            if not turn_open and (saw_start or fragments):
                # An unmatched/repeated completion is not another independent usage fragment.
                stream_complete = False
                continue
            turn_open = False
            counts = event.get("usage")
            if not isinstance(counts, dict):
                counts = {}
            fragments.append(
                TokenCounts(
                    input_tokens=number(counts.get("input_tokens")),
                    output_tokens=number(counts.get("output_tokens")),
                    cached_input_tokens=number(counts.get("cached_input_tokens")),
                    reasoning_tokens=number(counts.get("reasoning_output_tokens")),
                    cache_write_tokens=number(counts.get("cache_write_input_tokens"))
                    if "cache_write_input_tokens" in counts
                    else number(counts.get("cache_write_tokens")),
                )
            )
        elif kind in {"turn.failed", "error"}:
            stream_complete = False
            turn_open = False
    return response_model, summarize_usage(fragments, complete=stream_complete and not turn_open)


def _claude_metadata(envelope: dict[str, Any], *, interrupted: bool = False) -> ResponseMetadata:
    counts = envelope.get("usage") or {}
    if not isinstance(counts, dict):
        counts = {}
    model_usage = envelope.get("modelUsage")
    model = model_name(envelope.get("model"))
    if model is None and isinstance(model_usage, dict) and len(model_usage) == 1:
        model = model_name(next(iter(model_usage)))
    fragment = TokenCounts(
        input_tokens=number(counts.get("input_tokens")),
        output_tokens=number(counts.get("output_tokens")),
        cached_input_tokens=number(counts.get("cache_read_input_tokens")),
        cache_write_tokens=number(counts.get("cache_creation_input_tokens")),
        input_tokens_scope="uncached",
    )
    # A terminal error envelope may still report the whole invocation's usage.
    complete = not interrupted
    return ResponseMetadata(
        response_model=model,
        usage=summarize_usage([fragment] if counts else [], complete=complete, scope="uncached"),
    )


def _antigravity_result(
    stdout: str, *, interrupted: bool = False
) -> tuple[dict[str, Any] | None, ResponseMetadata, bool]:
    """Read a single-turn NDJSON result; usage on the result is cumulative."""
    envelope = None
    valid = True
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if envelope is not None:
            valid = False  # Exactly one terminal result, with no following events.
        try:
            event = json.loads(line)
        except ValueError:
            valid = False
            continue
        if not isinstance(event, dict):
            valid = False
            continue
        if event.get("event") == "result":
            result = event.get("result")
            if not isinstance(result, dict):
                valid = False
                continue
            envelope = result
    if envelope is None:
        return None, ResponseMetadata(), False
    counts = envelope.get("usage")
    if not isinstance(counts, dict):
        counts = {}
    fragment = TokenCounts(
        input_tokens=number(counts.get("input_tokens")),
        output_tokens=number(counts.get("output_tokens")),
        cached_input_tokens=number(counts.get("cache_read_tokens")),
        reasoning_tokens=number(counts.get("thinking_tokens")),
    )
    # init.model echoes a requested override; it is not an observed response model.
    metadata = ResponseMetadata(
        usage=summarize_usage(
            [fragment] if counts else [],
            complete=valid and not interrupted and envelope.get("status") == "SUCCESS",
        )
    )
    return envelope, metadata, valid


class CliBackend:
    def generate(self, profile: Profile, request: GenerationRequest) -> ProviderResponse:
        validate_image_provider(profile.provider, request.images)
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
                for index, image in enumerate(request.images, start=1):
                    image_path = cwd / f"image-{index:04d}{IMAGE_SUFFIXES[image.media_type]}"
                    image_path.write_bytes(image.data)
                    command += ["--image", str(image_path)]
            elif profile.provider == "claude-code":
                # Claude's CLI validator rejects the Draft 2020-12 declaration.
                # Omit only the root dialect marker from the transport copy;
                # Runtime still validates output against the untouched original.
                cli_schema = dict(request.output_schema)
                cli_schema.pop("$schema", None)
                command += [
                    "-p",
                    "--output-format",
                    "json",
                    "--json-schema",
                    json.dumps(cli_schema, ensure_ascii=False, separators=(",", ":")),
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
            elif profile.provider == "antigravity":
                schema_path = cwd / "schema.json"
                schema_path.write_text(
                    json.dumps(request.output_schema, ensure_ascii=False), encoding="utf-8"
                )
                command += [
                    "--input-format",
                    "stream-json",
                    "--output-format",
                    "stream-json",
                    "--json-schema",
                    str(schema_path),
                    "--disable-slash-commands",
                    "--mode",
                    "plan",
                    "--sandbox",
                    "--print-timeout",
                    "0s",
                    "--log-file",
                    str(cwd / "agy.log"),
                ]
                # One user event, then communicate() closes stdin after writing it.
                prompt = (
                    json.dumps({"event": "user", "message": {"content": request.prompt}}, ensure_ascii=False)
                    + "\n"
                )
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
            if profile.provider == "antigravity":
                envelope, metadata, valid = _antigravity_result(stdout)
                with preserve_metadata(metadata):
                    if envelope is None:
                        raise ProviderError("Antigravity returned no result event", code="missing_output")
                    if not valid or envelope.get("status") != "SUCCESS":
                        raise ProviderError(
                            "Antigravity returned an unsuccessful or invalid result",
                            code="incomplete_response",
                        )
                    data = envelope.get("structured_output")
                    if not isinstance(data, dict):
                        raise ProviderError(
                            "Antigravity returned no structured_output", code="missing_output"
                        )
                    return ProviderResponse(data, metadata.response_model, metadata.usage)
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
