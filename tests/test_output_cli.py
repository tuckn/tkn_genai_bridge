"""Public CLI display contract, using synthetic data and no provider calls."""

import json
import os
import subprocess
import sys

import pytest

from tkn_genai_bridge import ConfigError, GenerationRequest, ProviderError, RequestError
from tkn_genai_bridge.cli import main
from tkn_genai_bridge.output import emit, result_lines


@pytest.mark.parametrize(
    "args",
    [
        ["--json", "config", "list"],
        ["config", "--json", "list"],
        ["config", "list", "--json"],
        ["--json", "config", "--json", "list", "--json"],
    ],
)
def test_json_survives_every_subcommand_level(args, capsys):
    assert main(args) == 0
    capture = capsys.readouterr()
    assert json.loads(capture.out)["settings"]["schema_version"] == "1.1.0"
    assert "\x1b" not in capture.out


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("logs", [[], ["--quiet"], ["--verbose"]])
def test_config_init_format_and_dry_run(tmp_path, capsys, json_output, logs):
    target = tmp_path / "new folder" / "config.yaml"
    args = [*logs, "config", "init", "--path", str(target), "--dry-run"]
    assert main([*args, *(["--json"] if json_output else [])]) == 0
    capture = capsys.readouterr()
    expected = {"status": "would_create", "path": str(target)}
    if json_output:
        assert json.loads(capture.out) == expected
    else:
        assert capture.out.splitlines() == list(result_lines(expected))
    assert not target.parent.exists()
    assert bool(capture.err) == ("--quiet" not in logs)


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("logs", [[], ["-q"], ["-v"]])
@pytest.mark.parametrize("tty", [False, True])
def test_generation_keeps_complete_data_in_both_formats(monkeypatch, capsys, json_output, logs, tty):
    payload = {
        "data": {
            "path": "C:\\path with spaces\\" + "nested\\" * 50 + "日本語.txt",
            "items": [{"index": n, "ok": True} for n in range(100)],
            "text": "line 1\nline 2\t\x1b[31m",
        },
        "record": {"status": "succeeded", "usage": {"input_tokens": 4}},
    }
    monkeypatch.setattr("tkn_genai_bridge.cli._execute", lambda *args: payload)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
    args = [*logs, "generate", "--prompt-file", "prompt.txt", "--schema-file", "schema.json"]
    assert main([*args, *(["--json"] if json_output else [])]) == 0
    capture = capsys.readouterr()
    assert "\x1b" not in capture.out
    if json_output:
        assert json.loads(capture.out) == payload
    else:
        assert capture.out.splitlines() == list(result_lines(payload))
        assert "data.items[99].index=99" in capture.out
        assert f"data.path={payload['data']['path']}" in capture.out
    assert bool(capture.err) == ("-q" not in logs)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["unknown"],
        ["generate"],
        ["config", "unknown"],
        ["config", "list", "--unknown"],
        ["config", "list", "--timeout-seconds", "bad"],
        ["-q", "config", "list", "-v"],
        ["config", "-q", "list", "-v"],
    ],
)
@pytest.mark.parametrize("json_position", [None, "before", "after"])
def test_argument_errors_obey_requested_format(args, json_position, capsys):
    args = list(args)
    if json_position == "before":
        args.insert(0, "--json")
    elif json_position == "after":
        args.append("--json")
    assert main(args) == 2
    capture = capsys.readouterr()
    if json_position is not None:
        assert json.loads(capture.out)["error"]["code"] == "argument_error"
    else:
        assert "error.code=argument_error" in capture.out
    assert "[ERROR]" in capture.err and "Traceback" not in capture.err


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    "failure,code,exit_code",
    [
        (ConfigError("invalid settings"), "runtime_error", 2),
        (RequestError("invalid request", code="invalid_request"), "invalid_request", 1),
        (ProviderError("provider failed", http_status=429, retryable=True), "provider_error", 1),
        (OSError("PRIVATE unexpected OS message"), "runtime_error", 1),
        (RuntimeError("PRIVATE unexpected response"), "runtime_error", 1),
        (KeyboardInterrupt(), "interrupted", 130),
    ],
)
def test_failures_and_interrupts_keep_format(monkeypatch, capsys, failure, code, exit_code, json_output):
    def fail(*args):
        raise failure

    monkeypatch.setattr("tkn_genai_bridge.cli._execute", fail)
    assert main(["config", "list", *(["--json"] if json_output else [])]) == exit_code
    capture = capsys.readouterr()
    if json_output:
        error = json.loads(capture.out)["error"]
        assert error["code"] == code
        if isinstance(failure, ProviderError):
            assert error["retryable"] and error["http_status"] == 429
    else:
        assert f"error.code={code}" in capture.out.splitlines()
    assert "PRIVATE" not in capture.out + capture.err
    assert "Traceback" not in capture.err


@pytest.mark.parametrize("json_output", [False, True])
def test_input_validation_error_is_safe(monkeypatch, capsys, json_output):
    def invalid(*args):
        GenerationRequest(prompt="", output_schema={"type": "object"})

    monkeypatch.setattr("tkn_genai_bridge.cli._execute", invalid)
    assert main(["config", "list", *(["--json"] if json_output else [])]) == 2
    capture = capsys.readouterr()
    if json_output:
        assert json.loads(capture.out)["error"]["code"] == "invalid_input"
    else:
        assert "error.code=invalid_input" in capture.out


@pytest.mark.parametrize("json_output", [False, True])
def test_failure_paths_are_separate_from_reason(tmp_path, capsys, json_output):
    path = tmp_path / "missing input 日本語.txt"
    args = ["generate", "--prompt-file", str(path), "--schema-file", "schema.json"]
    assert main([*args, *(["--json"] if json_output else [])]) == 2
    capture = capsys.readouterr()
    if json_output:
        error = json.loads(capture.out)["error"]
        assert error["paths"] == {"prompt_file": str(path)}
        assert str(path) not in error["message"]
    else:
        assert f"error.paths.prompt_file={path}" in capture.out.splitlines()
        assert "error.code=input_io" in capture.out.splitlines()


@pytest.mark.parametrize("json_output", [False, True])
def test_config_failure_exposes_candidate_paths(tmp_path, capsys, json_output):
    path = tmp_path / "broken config.yaml"
    path.write_text("PRIVATE invalid config", encoding="utf-8")
    assert main(["config", "list", "--config", str(path), *(["--json"] if json_output else [])]) == 2
    capture = capsys.readouterr()
    if json_output:
        assert json.loads(capture.out)["error"]["paths"]["config_file"] == str(path)
    else:
        assert f"error.paths.config_file={path}" in capture.out.splitlines()
    assert "PRIVATE" not in capture.out + capture.err


def test_control_characters_in_keys_and_values_and_json(capsys):
    result = {"a\nkey": "line\r\n\t\x00\x1b\x85\u2028end", "empty": [], "mapping": {}, "null": None}
    emit(result, json_output=False)
    assert capsys.readouterr().out.splitlines() == [
        r"a\nkey=line\r\n\t\u0000\u001b\u0085\u2028end",
        "empty=[]",
        "mapping={}",
        "null=null",
    ]
    emit(result, json_output=True)
    assert json.loads(capsys.readouterr().out) == result
    with pytest.raises(ValueError):
        emit({"amount": float("nan")}, json_output=True)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "args", [["--json", "--help"], ["--json", "--version"], ["config", "list", "--json", "--help"]]
)
def test_help_and_version_remain_normal_guidance(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 0
    capture = capsys.readouterr()
    assert capture.out and capture.err == ""
    assert not capture.out.startswith("{")


@pytest.mark.parametrize("json_output", [False, True])
def test_real_cli_pipe_and_redirect_do_not_switch_format(tmp_path, json_output):
    env = dict(
        os.environ,
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    args = [
        sys.executable,
        "-m",
        "tkn_genai_bridge",
        "config",
        "init",
        "--path",
        str(tmp_path / "target config.yaml"),
        "--dry-run",
    ]
    if json_output:
        args.append("--json")
    piped = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=20)
    output_file = tmp_path / "redirect.txt"
    with output_file.open("w", encoding="utf-8") as stdout:
        redirected = subprocess.run(
            args, cwd=tmp_path, env=env, stdout=stdout, stderr=subprocess.PIPE, encoding="utf-8", timeout=20
        )
    assert piped.returncode == redirected.returncode == 0
    text = output_file.read_text(encoding="utf-8")
    assert text == piped.stdout
    if json_output:
        assert json.loads(text)["status"] == "would_create"
    else:
        assert text.splitlines()[0] == "status=would_create"
    assert not (tmp_path / "target config.yaml").exists()
    assert not (tmp_path / "home").exists()


@pytest.fixture
def synthetic_inputs(tmp_path):
    config = tmp_path / "runtime.yaml"
    config.write_text(
        'schema_version: "1.1.0"\ndefault_profile: local\nprofiles:\n  local:\n'
        "    provider: ollama\n    model: synthetic\n",
        encoding="utf-8",
    )
    prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
    prompt.write_text("PRIVATE prompt", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")
    return [
        "generate",
        "--config",
        str(config),
        "--prompt-file",
        str(prompt),
        "--schema-file",
        str(schema),
    ]


@pytest.mark.parametrize("json_output", [False, True])
def test_real_plan_keeps_dry_run_readonly(synthetic_inputs, tmp_path, capsys, monkeypatch, json_output):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run must not use network, subprocesses, credentials or scratch")

    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("tempfile.TemporaryDirectory", forbidden)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert main([*synthetic_inputs, "--dry-run", *(["--json"] if json_output else [])]) == 0
    capture = capsys.readouterr()
    if json_output:
        result = json.loads(capture.out)
        assert result["will_call_provider"] is False and result["profile_name"] == "local"
    else:
        assert "will_call_provider=false" in capture.out.splitlines()
        assert "profile_name=local" in capture.out.splitlines()
    assert "PRIVATE prompt" not in capture.out + capture.err
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("json_output", [False, True])
def test_partial_failure_retains_safe_record(synthetic_inputs, capsys, monkeypatch, json_output):
    from tkn_genai_bridge import ResponseMetadata, TokenCounts, Usage
    from tkn_genai_bridge.providers.litellm import LiteLLMBackend

    failure = ProviderError("request failed", code="http_429", http_status=429, retryable=True)
    failure.metadata = ResponseMetadata(
        usage=Usage(completeness="partial", known_subtotal=TokenCounts(input_tokens=7))
    )

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(LiteLLMBackend, "generate", fail)
    assert main([*synthetic_inputs, *(["--json"] if json_output else [])]) == 1
    capture = capsys.readouterr()
    if json_output:
        result = json.loads(capture.out)
        assert result["error"]["code"] == "http_429"
        assert result["record"]["status"] == "failed"
        assert result["record"]["usage"]["completeness"] == "partial"
        assert result["record"]["usage"]["known_subtotal"]["input_tokens"] == 7
        assert result["record"]["usage"]["input_tokens"] is None
    else:
        for line in (
            "error.code=http_429",
            "record.status=failed",
            "record.usage.completeness=partial",
            "record.usage.known_subtotal.input_tokens=7",
        ):
            assert line in capture.out.splitlines()
    assert "PRIVATE prompt" not in capture.out + capture.err


@pytest.mark.parametrize("json_output", [False, True])
def test_actual_config_creation_idempotency_and_protection(tmp_path, capsys, json_output):
    path = tmp_path / "created config.yaml"
    args = ["config", "init", "--path", str(path), *(["--json"] if json_output else [])]
    for status in ("created", "unchanged"):
        assert main(args) == 0
        capture = capsys.readouterr()
        if json_output:
            assert json.loads(capture.out)["status"] == status
        else:
            assert f"status={status}" in capture.out.splitlines()
            assert f"path={path}" in capture.out.splitlines()
    path.write_text("# PRIVATE edited configuration\n", encoding="utf-8")
    before = path.read_bytes()
    assert main(args) == 2
    capture = capsys.readouterr()
    if json_output:
        assert json.loads(capture.out)["error"]["paths"]["config_file"] == str(path)
    else:
        assert "error.code=config_conflict" in capture.out
        assert f"error.paths.config_file={path}" in capture.out.splitlines()
    assert "PRIVATE" not in capture.out + capture.err
    assert path.read_bytes() == before


def test_json_after_option_terminator_is_not_a_format_flag(capsys):
    assert main(["config", "list", "--", "--json"]) == 2
    assert "error.code=argument_error" in capsys.readouterr().out


@pytest.mark.parametrize("json_output", [False, True])
def test_interrupt_is_a_process_result(tmp_path, json_output):
    env = dict(os.environ, HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"), PYTHONUTF8="1")
    script = (
        "import sys; import tkn_genai_bridge.cli as cli\n"
        "def interrupt(*args): raise KeyboardInterrupt()\n"
        "cli._execute = interrupt\n"
        "sys.exit(cli.main(sys.argv[1:]))\n"
    )
    args = [sys.executable, "-c", script, "config", "list", *(["--json"] if json_output else [])]
    result = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=20)
    assert result.returncode == 130
    if json_output:
        assert json.loads(result.stdout)["error"]["code"] == "interrupted"
    else:
        assert "error.code=interrupted" in result.stdout.splitlines()
    assert "Traceback" not in result.stderr
