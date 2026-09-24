import json
import logging
import sys
from pathlib import Path

import pytest

from tkn_genai_bridge import CliSettings, Profile, ProviderError, Runtime, load_config
from tkn_genai_bridge.cli import main
from tkn_genai_bridge.config import config_template
from tkn_genai_bridge.providers import cli

COUNTS = {
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_tokens": 80,
    "thinking_tokens": 10,
    "total_tokens": 120,
}


def result_event(**updates):
    return (
        json.dumps(
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "structured_output": {"summary": "日本語"},
                    "usage": COUNTS,
                    **updates,
                },
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def profile(**kwargs):
    return Profile(provider="antigravity", cli=CliSettings(executable=sys.executable), **kwargs)


def test_single_stdin_turn_schema_and_cumulative_usage(request_object, monkeypatch):
    captures = []
    request_object = request_object.model_copy(
        update={"prompt": '日本語 & | $(echo no) "quoted"\nsecond line'}
    )

    def run(command, prompt, cwd, timeout, **kwargs):
        captures.append(cwd)
        assert cwd != Path.cwd()
        assert timeout == 17
        assert kwargs["provider"] == "antigravity"
        assert request_object.prompt not in command
        assert len(prompt.splitlines()) == 1
        assert json.loads(prompt) == {"event": "user", "message": {"content": request_object.prompt}}
        for flag in ("--input-format", "--output-format"):
            assert command[command.index(flag) + 1] == "stream-json"
        schema_path = Path(command[command.index("--json-schema") + 1])
        assert schema_path.parent == cwd
        assert json.loads(schema_path.read_text(encoding="utf-8")) == request_object.output_schema
        assert Path(command[command.index("--log-file") + 1]).parent == cwd
        assert "--disable-slash-commands" in command
        assert "--sandbox" in command
        assert command[command.index("--mode") + 1] == "plan"
        assert command[command.index("--print-timeout") + 1] == "0s"
        assert command[command.index("--model") + 1] == "selected"
        assert command[command.index("--effort") + 1] == "low"
        assert "--dangerously-skip-permissions" not in command
        assert "--continue" not in command and "--conversation" not in command
        return (
            json.dumps({"event": "init", "init": {"model": "selected"}})
            + "\n"
            + json.dumps({"event": "step_update", "step_update": {"usage": COUNTS}})
            + "\n"
            + result_event()
        )

    monkeypatch.setattr(cli, "run_process", run)
    result = Runtime(profile(model="selected", reasoning_effort="low", timeout_seconds=17)).generate(
        request_object
    )
    assert result.data == {"summary": "日本語"}
    assert result.record.provider == "antigravity"
    assert result.record.response_model is None
    assert result.record.requested_model == "selected"
    assert result.record.usage.completeness == "complete"
    assert result.record.usage.input_tokens == 100  # Do not add step counts to cumulative result.
    assert result.record.usage.output_tokens == 20
    assert result.record.usage.cached_input_tokens == 80
    assert result.record.usage.reasoning_tokens == 10
    assert result.record.usage.cache_write_tokens is None
    assert captures and not captures[0].exists()


def test_default_model_and_effort_are_omitted(request_object, monkeypatch):
    def run(command, *args, **kwargs):
        assert "--model" not in command and "--effort" not in command
        return result_event(usage=None)

    monkeypatch.setattr(cli, "run_process", run)
    result = Runtime(profile()).generate(request_object)
    assert result.record.usage.completeness == "unknown"
    assert result.record.response_model is None


@pytest.mark.parametrize(
    "output, code, completeness",
    [
        ("", "missing_output", "unknown"),
        ('{"event":"init"}\n', "missing_output", "unknown"),
        (result_event(status="ERROR", error="private-body"), "incomplete_response", "partial"),
        (result_event(status="WAITING"), "incomplete_response", "partial"),
        (result_event(status="UNRECOGNIZED"), "incomplete_response", "partial"),
        (result_event(status=None), "incomplete_response", "partial"),
        (result_event(structured_output=None), "missing_output", "complete"),
        (result_event(structured_output=[]), "missing_output", "complete"),
        (result_event(structured_output={"wrong": True}), "schema_mismatch", "complete"),
        ("private-body\n" + result_event(), "incomplete_response", "partial"),
        ("[]\n" + result_event(), "incomplete_response", "partial"),
        (result_event() + result_event(), "incomplete_response", "partial"),
        (result_event() + '{"event":"step_update"}\n', "incomplete_response", "partial"),
        ('{"event":"result","result":[]}\n', "missing_output", "unknown"),
    ],
)
def test_invalid_results_preserve_metadata(request_object, monkeypatch, output, code, completeness):
    from tkn_genai_bridge import GenAIError

    monkeypatch.setattr(cli, "run_process", lambda *args, **kwargs: output)
    with pytest.raises(GenAIError) as exc:
        Runtime(profile()).generate(request_object)
    assert exc.value.code == code
    assert exc.value.record.status == "failed"
    assert exc.value.record.usage.completeness == completeness
    assert "private-body" not in str(exc.value)
    if completeness == "partial":
        assert exc.value.record.usage.input_tokens is None
        assert exc.value.record.usage.known_subtotal.input_tokens == 100


@pytest.mark.parametrize("timed_out", [False, True])
def test_process_failure_retains_partial_cumulative_usage(tmp_path, monkeypatch, timed_out):
    class Process:
        returncode = 3

        def communicate(self, prompt, timeout):
            if timed_out:
                raise cli.subprocess.TimeoutExpired("agy", timeout, output=result_event().encode("utf-8"))
            return result_event(), "private-stderr"

    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(cli, "_stop_process", lambda process: result_event())
    with pytest.raises(ProviderError) as exc:
        cli.run_process(["agy"], "private-input", tmp_path, 1, provider="antigravity")
    assert exc.value.code == ("timeout" if timed_out else "process_exit")
    assert exc.value.metadata.usage.completeness == "partial"
    assert exc.value.metadata.usage.known_subtotal.input_tokens == 100
    assert exc.value.metadata.usage.input_tokens is None
    assert "private" not in str(exc.value)


def test_plan_checks_executable_without_generation_or_files(tmp_path, request_object, monkeypatch):
    monkeypatch.setattr(cli, "run_process", lambda *args, **kwargs: pytest.fail("must not launch CLI"))
    monkeypatch.setattr(
        cli.tempfile, "TemporaryDirectory", lambda *args, **kwargs: pytest.fail("must not write")
    )
    before = set(tmp_path.rglob("*"))
    runtime = Runtime(profile())
    plan = runtime.plan(request_object, check_executable=True)
    assert plan.provider == "antigravity" and not plan.will_call_provider
    assert plan.generation_settings_sha256
    assert before == set(tmp_path.rglob("*"))
    missing = Profile(provider="antigravity", cli=CliSettings(executable="absent-agy-12345"))
    with pytest.raises(ProviderError) as exc:
        Runtime(missing).plan(request_object, check_executable=True)
    assert exc.value.code == "executable_missing"


def test_default_executable_fingerprint(request_object):
    implicit = Runtime(Profile(provider="antigravity")).plan(request_object)
    explicit = Runtime(Profile(provider="antigravity", cli=CliSettings(executable="agy"))).plan(
        request_object
    )
    assert implicit.generation_settings_sha256 == explicit.generation_settings_sha256


@pytest.mark.skipif(sys.platform != "win32", reason="Windows executable discovery")
def test_windows_winget_discovery(tmp_path, monkeypatch):
    executable = tmp_path / "local/Microsoft/WinGet/Links/agy.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.resolve_executable(Profile(provider="antigravity")) == str(executable)


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_supported_efforts(effort):
    assert Profile(provider="antigravity", reasoning_effort=effort).reasoning_effort == effort


@pytest.mark.parametrize(
    "settings", [{"reasoning_effort": "xhigh"}, {"max_output_tokens": 5}, {"local_only": True}]
)
def test_unsupported_capabilities(settings):
    with pytest.raises(ValueError):
        Profile(provider="antigravity", **settings)


def test_example_profile_and_cli_dry_run(tmp_path, capsys, monkeypatch):
    # Keep this CLI test from changing the log handlers used by later library tests.
    monkeypatch.setattr(
        "tkn_genai_bridge.cli.configure_logging", lambda **kwargs: logging.getLogger(__name__)
    )
    config = tmp_path / "config.yaml"
    config.write_text(config_template(), encoding="utf-8")
    resolved = load_config(config_file=config)
    assert resolved.config.default_profile == "codex-default"
    assert resolved.profile("antigravity-default").cli.executable == "agy"
    monkeypatch.setattr(cli.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(cli, "run_process", lambda *args, **kwargs: pytest.fail("must not launch CLI"))
    prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
    prompt.write_text("synthetic", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert (
        main(
            [
                "generate",
                "--config",
                str(config),
                "--profile",
                "antigravity-default",
                "--prompt-file",
                str(prompt),
                "--schema-file",
                str(schema),
                "--dry-run",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["provider"] == "antigravity"
    assert output["profile_name"] == "antigravity-default"
    assert output["will_call_provider"] is False
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
