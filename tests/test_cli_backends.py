import json
import sys
from pathlib import Path

import pytest

from tkn_genai_bridge import CliSettings, Profile, ProviderError, Runtime
from tkn_genai_bridge.providers import cli


@pytest.mark.parametrize("provider", ["codex", "claude-code", "github-copilot"])
def test_commands_stdin_and_private_directory(provider, request_object, monkeypatch):
    captures = []

    def run(command, prompt, cwd, timeout):
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
        reasoning_effort="low",
        cli=CliSettings(executable=sys.executable),
        timeout_seconds=17,
    )
    result = Runtime(profile).generate(request_object)
    assert result.data == {"summary": "日本語"}
    assert len(captures) == 1
    command, prompt, cwd, timeout = captures[0]
    assert command[command.index("--model") + 1] == "selected"
    assert request_object.prompt in prompt
    assert not cwd.exists()
    assert timeout == 17
    if provider == "codex":
        assert "--ephemeral" in command and "--ignore-user-config" in command
        assert result.record.usage.input_tokens == 10
    elif provider == "claude-code":
        assert result.record.response_model == "actual"
    else:
        assert result.record.usage.input_tokens is None


def test_codex_missing_output_is_failure(request_object, monkeypatch):
    monkeypatch.setattr(cli, "run_process", lambda *args: "")
    with pytest.raises(ProviderError, match="output file"):
        Runtime(Profile(cli=CliSettings(executable=sys.executable))).generate(request_object)


@pytest.mark.parametrize(
    "envelope",
    [
        {"is_error": True, "structured_output": {"summary": "fake success"}},
        {"subtype": "error_max_turns", "structured_output": {"summary": "fake success"}},
        {"result": "no structured output"},
    ],
)
def test_claude_unsuccessful_envelope(request_object, envelope, monkeypatch):
    monkeypatch.setattr(cli, "run_process", lambda *args: json.dumps(envelope))
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
