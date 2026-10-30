import json
import sys
from pathlib import Path

import pytest

from tkn_genai_bridge import CliSettings, ImageInput, Profile, ProviderError, Runtime
from tkn_genai_bridge.providers import cli


@pytest.mark.parametrize("provider", ["codex", "claude-code", "github-copilot"])
@pytest.mark.parametrize("effort", [None, "low", "high"])
def test_commands_stdin_and_private_directory(provider, effort, request_object, monkeypatch):
    captures = []

    def run(command, prompt, cwd, timeout, **kwargs):
        captures.append((command, prompt, cwd, timeout))
        assert cwd != Path.cwd()
        assert request_object.prompt not in command
        if provider == "codex":
            schema_path = Path(command[command.index("--output-schema") + 1])
            assert json.loads(schema_path.read_text(encoding="utf-8")) == request_object.output_schema
            Path(command[command.index("--output-last-message") + 1]).write_text(
                '{"summary":"日本語"}', encoding="utf-8"
            )
            return '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}\n'
        if provider == "claude-code":
            assert command[command.index("--tools") + 1] == ""
            assert "--no-session-persistence" in command
            return json.dumps(
                {
                    "structured_output": {"summary": "日本語"},
                    "is_error": False,
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                    "modelUsage": {"actual": {}},
                }
            )
        assert "--deny-tool=shell" in command and "--no-auto-update" in command
        assert "Return only one JSON object" in prompt
        return '{"summary":"日本語"}'

    monkeypatch.setattr(cli, "run_process", run)
    profile = Profile(
        provider=provider,
        model="selected",
        reasoning_effort=effort,
        cli=CliSettings(executable=sys.executable),
        timeout_seconds=17,
    )
    result = Runtime(profile).generate(request_object)
    assert result.data == {"summary": "日本語"}
    assert result.record.reasoning_effort == effort
    assert len(captures) == 1
    command, prompt, cwd, timeout = captures[0]
    assert command[command.index("--model") + 1] == "selected"
    assert request_object.prompt in prompt
    assert not cwd.exists()
    assert timeout == 17
    if provider == "codex":
        settings = [command[i + 1] for i, item in enumerate(command) if item == "-c"]
        assert settings == ([f"model_reasoning_effort={json.dumps(effort)}"] if effort else [])
    elif effort:
        assert command[command.index("--effort") + 1] == effort
    else:
        assert "--effort" not in command
    if provider == "codex":
        assert "--ephemeral" in command and "--ignore-user-config" in command
        assert result.record.usage.input_tokens == 10
    elif provider == "claude-code":
        assert result.record.response_model == "actual"
    else:
        assert result.record.usage.input_tokens is None


def test_codex_missing_output_is_failure(request_object, monkeypatch):
    monkeypatch.setattr(cli, "run_process", lambda *args, **kwargs: "")
    with pytest.raises(ProviderError, match="output file"):
        Runtime(Profile(cli=CliSettings(executable=sys.executable))).generate(request_object)


@pytest.mark.parametrize("with_images", [False, True])
@pytest.mark.parametrize("selected_model", [False, True])
def test_copilot_current_cli_command(with_images, selected_model, request_object, monkeypatch):
    images = [ImageInput(data=b"\xff\xd8\xffsynthetic", media_type="image/jpeg")] if with_images else []
    request = request_object.model_copy(update={"images": images})
    calls = []

    def run(command, prompt, cwd, timeout, **kwargs):
        calls.append(cwd)
        expected = [
            cli.resolve_executable(profile),
            "-s",
            "--no-ask-user",
            "--no-color",
            "--no-auto-update",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--sync-session=off",
            "--deny-tool=shell",
            "--deny-tool=write",
            "--deny-tool=read",
            "--deny-tool=url",
            "--deny-tool=memory",
        ]
        if with_images:
            path = cwd / "image-0001.jpg"
            assert path.read_bytes() == images[0].data
            expected += ["--output-format", "json", "--attachment", str(path)]
        if selected_model:
            expected += ["--model", "selected", "--effort", "low"]
        assert command == expected
        assert prompt == cli.schema_prompt(request)
        assert kwargs == {"provider": "github-copilot"}
        assert cwd != Path.cwd()
        assert timeout == 17
        if with_images:
            return (
                '{"type":"assistant.message","data":{"content":"{\\"summary\\":\\"ok\\"}"}}\n'
                '{"type":"result","exitCode":0}\n'
            )
        return '{"summary":"ok"}'

    monkeypatch.setattr(cli, "run_process", run)
    profile = Profile(
        provider="github-copilot",
        model="selected" if selected_model else None,
        reasoning_effort="low" if selected_model else None,
        cli=CliSettings(executable=sys.executable),
        timeout_seconds=17,
    )
    assert Runtime(profile).generate(request).data == {"summary": "ok"}
    assert len(calls) == 1
    assert not calls[0].exists()


@pytest.mark.parametrize("with_images", [False, True])
def test_copilot_argument_failure_preserves_private_diagnostics(
    with_images, request_object, monkeypatch, caplog
):
    stderr = (
        "error: --no-remote was removed. Use /remote off in a session; "
        "manage incoming connections in /environments.\n"
    )
    images = [ImageInput(data=b"\xff\xd8\xffsynthetic", media_type="image/jpeg")] if with_images else []
    request = request_object.model_copy(update={"images": images})
    calls = []
    records = []

    class Process:
        returncode = 1

        def communicate(self, prompt, timeout):
            assert prompt == cli.schema_prompt(request)
            return "private generated body", stderr

    def popen(command, **kwargs):
        calls.append(command)
        assert kwargs["shell"] is False
        assert kwargs["stdin"] == cli.subprocess.PIPE
        assert kwargs["stdout"] == cli.subprocess.PIPE
        assert kwargs["stderr"] == cli.subprocess.PIPE
        return Process()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    runtime = Runtime(
        Profile(provider="github-copilot", cli=CliSettings(executable=sys.executable)),
        observer=records.append,
    )
    with pytest.raises(ProviderError) as exc:
        runtime.generate(request)
    error = exc.value
    assert error.code == "process_exit"
    assert error.process_diagnostics.exit_code == 1
    assert error.process_diagnostics.stderr == stderr
    assert not error.process_diagnostics.stderr_truncated
    assert error.process_diagnostics.error_events == ""
    assert "CLI arguments" in str(error) and "process_diagnostics" in str(error)
    assert len(calls) == 1
    assert records == [error.record]
    assert error.record.status == "failed"
    assert len(error.record.images) == len(images)
    public = str(error) + repr(error) + repr(error.process_diagnostics) + caplog.text
    public += error.record.model_dump_json()
    assert stderr.strip() not in public
    assert "private generated body" not in public
    assert request.prompt not in public


@pytest.mark.parametrize(
    "envelope",
    [
        {"is_error": True, "structured_output": {"summary": "fake success"}},
        {"subtype": "error_max_turns", "structured_output": {"summary": "fake success"}},
        {"result": "no structured output"},
    ],
)
def test_claude_unsuccessful_envelope(request_object, envelope, monkeypatch):
    monkeypatch.setattr(cli, "run_process", lambda *args, **kwargs: json.dumps(envelope))
    profile = Profile(provider="claude-code", cli=CliSettings(executable=sys.executable))
    with pytest.raises(ProviderError):
        Runtime(profile).generate(request_object)


def test_shell_shim_is_rejected(tmp_path):
    shim = tmp_path / "codex.cmd"
    shim.write_text("@echo off", encoding="utf-8")
    with pytest.raises(ProviderError, match="native executable"):
        cli.resolve_executable(Profile(cli=CliSettings(executable=str(shim))))


def test_missing_executable_is_rejected():
    with pytest.raises(ProviderError):
        cli.resolve_executable(Profile(cli=CliSettings(executable="no-such-executable-12345")))


def test_process_exit_does_not_leak_stdout_stderr(tmp_path):
    with pytest.raises(ProviderError) as exc:
        cli.run_process(
            [
                sys.executable,
                "-c",
                "import sys; print('private-body'); print('private-key', file=sys.stderr); sys.exit(3)",
            ],
            "private-prompt",
            tmp_path,
            5,
        )
    assert "private" not in str(exc.value)
    assert "status 3" in str(exc.value)
    assert exc.value.process_diagnostics.exit_code == 3
    assert exc.value.process_diagnostics.stderr.strip() == "private-key"
    assert "private" not in repr(exc.value.process_diagnostics)
    assert exc.value.process_diagnostics.error_events == ""


def test_process_diagnostics_are_bounded_and_exclude_success_output():
    stdout = "\n".join(
        [
            json.dumps({"type": "item.completed", "item": {"text": "private generated body"}}),
            json.dumps({"type": "turn.failed", "error": {"message": "request rejected"}}),
        ]
    )
    result = cli.process_diagnostics(7, "x" * 20000 + "error tail", stdout, "codex")
    assert len(result.stderr) == 16384 and result.stderr.endswith("error tail")
    assert result.stderr_truncated
    assert "request rejected" in result.error_events
    assert "private generated body" not in result.error_events
    assert not result.error_events_truncated


def test_timeout_diagnostics_retain_stderr(tmp_path):
    script = (
        "import sys,time; print('diagnostic before timeout', file=sys.stderr, flush=True); time.sleep(10)"
    )
    with pytest.raises(ProviderError) as exc:
        cli.run_process([sys.executable, "-u", "-c", script], "", tmp_path, 1)
    assert exc.value.code == "timeout"
    assert "diagnostic before timeout" in exc.value.process_diagnostics.stderr
    assert "diagnostic before timeout" not in str(exc.value)


def test_process_timeout(tmp_path):
    with pytest.raises(ProviderError) as exc:
        cli.run_process([sys.executable, "-c", "import time; time.sleep(5)"], "", tmp_path, 0.05)
    assert exc.value.code == "timeout"
    assert exc.value.submission_unknown


def test_process_stdin_handles_metacharacters_without_shell(tmp_path):
    prompt = '日本語 & | $(echo no) "quoted" \nsecond line'
    output = cli.run_process(
        [sys.executable, "-X", "utf8", "-c", "import sys; print(sys.stdin.read(), end='')"],
        prompt,
        tmp_path,
        5,
    )
    assert output == prompt


@pytest.mark.parametrize("valid", [True, False])
def test_claude_example_schema_omits_only_root_dialect_and_keeps_validation(monkeypatch, valid):
    from copy import deepcopy

    from tkn_genai_bridge import GenerationRequest, OutputValidationError
    from tkn_genai_bridge.validation import fingerprints

    example = Path(__file__).resolve().parents[1] / "examples/output.schema.json"
    schema = json.loads(example.read_text(encoding="utf-8"))
    # A user property named "$schema" is data, not a dialect declaration.
    schema["properties"]["$schema"] = {"type": "string", "const": "literal-value"}
    original = deepcopy(schema)
    request = GenerationRequest(prompt="Synthetic summary.", output_schema=schema)
    expected_hash = fingerprints(request)[1]
    data = {"summary": "valid" if valid else "", "$schema": "literal-value"}

    def run(command, *args, **kwargs):
        forwarded = json.loads(command[command.index("--json-schema") + 1])
        assert "$schema" not in forwarded
        assert forwarded == {k: v for k, v in original.items() if k != "$schema"}
        assert forwarded["properties"]["$schema"] == original["properties"]["$schema"]
        assert "--tools" in command and "--strict-mcp-config" in command
        return json.dumps(
            {"is_error": False, "structured_output": data, "usage": {"input_tokens": 3, "output_tokens": 2}}
        )

    monkeypatch.setattr(cli, "run_process", run)
    profile = Profile(provider="claude-code", cli=CliSettings(executable=sys.executable))
    if valid:
        result = Runtime(profile).generate(request)
        assert result.data == data
        record = result.record
    else:
        with pytest.raises(OutputValidationError) as exc:
            Runtime(profile).generate(request)
        assert exc.value.code == "schema_mismatch"
        record = exc.value.record
    assert record.schema_sha256 == expected_hash
    assert record.usage.input_tokens == 3
    assert request.output_schema == original
    assert schema == original
