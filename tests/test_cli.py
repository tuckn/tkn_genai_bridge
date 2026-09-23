import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tkn_genai_bridge.cli import main
from tkn_genai_bridge.config import config_template


def test_config_commands_are_json_and_read_only(tmp_path, capsys):
    target = tmp_path / "shared/config.yaml"
    assert main(["config", "init", "--path", str(target), "--dry-run"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "would_create"
    assert "[SUCCESS]" in output.err
    assert not target.parent.exists()
    assert main(["config", "init", "--path", str(target)]) == 0
    capsys.readouterr()
    original = target.read_bytes()
    assert main(["config", "show", "--config", str(target), "--model", "overridden"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["settings"]["profiles"]["codex-default"]["model"] == "overridden"
    assert data["field_sources"]["profiles.codex-default.model"] == "options"
    assert target.read_bytes() == original


def test_dry_run_does_not_call_api_or_create_files(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(
        'schema_version: "1.0.0"\ndefault_profile: local\nprofiles:\n  local:\n'
        "    provider: ollama\n    model: synthetic\n",
        encoding="utf-8",
    )
    prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
    prompt.write_text("private-prompt", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")
    from tkn_genai_bridge.providers.litellm import LiteLLMBackend

    monkeypatch.setattr(LiteLLMBackend, "generate", lambda *args: pytest.fail("must not generate"))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert (
        main(
            [
                "generate",
                "--config",
                str(config),
                "--prompt-file",
                str(prompt),
                "--schema-file",
                str(schema),
                "--dry-run",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["will_call_provider"] is False
    assert "private-prompt" not in output.out + output.err
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_expected_error_is_json_without_traceback(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(config_template() + "\nsecret_key: PRIVATE\n", encoding="utf-8")
    assert main(["config", "show", "--config", str(config)]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "invalid_config"
    assert "PRIVATE" not in output.out + output.err
    assert "Traceback" not in output.err


def test_unknown_profile_override_cannot_create_profile(capsys):
    assert main(["config", "show", "--profile", "typo", "--model", "value"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unknown_profile"


def test_invalid_schema_file_is_an_input_error(tmp_path, capsys):
    prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
    prompt.write_text("synthetic", encoding="utf-8")
    schema.write_text("PRIVATE invalid JSON", encoding="utf-8")
    assert main(["generate", "--prompt-file", str(prompt), "--schema-file", str(schema), "--dry-run"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "invalid_schema"
    assert "PRIVATE" not in output.out + output.err


def test_quiet_verbose_are_exclusive(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["-q", "-v", "config", "show"])
    assert exc.value.code == 2


def test_real_entrypoint_with_loopback_fixture(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            if self.path == "/api/show":
                payload = {"model_info": {"general.architecture": "fixture"}}
            else:
                payload = {
                    "model": "fixture-model",
                    "done": True,
                    "done_reason": "stop",
                    "message": {"content": '{"summary":"日本語の結果"}'},
                    "prompt_eval_count": 8,
                    "eval_count": 4,
                }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = tmp_path / "runtime.yaml"
        config.write_text(
            'schema_version: "1.0.0"\ndefault_profile: local\nprofiles:\n  local:\n'
            "    provider: ollama\n    model: fixture-model\n    local_only: true\n"
            f"    ollama:\n      base_url: http://127.0.0.1:{server.server_port}\n",
            encoding="utf-8",
        )
        prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
        prompt.write_text("匿名の入力", encoding="utf-8")
        schema.write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                    "additionalProperties": False,
                }
            ),
            encoding="utf-8",
        )
        env = dict(
            os.environ,
            USERPROFILE=str(tmp_path / "isolated-home"),
            HOME=str(tmp_path / "isolated-home"),
            PYTHONUTF8="1",
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tkn_genai_bridge",
                "generate",
                "--config",
                str(config),
                "--prompt-file",
                str(prompt),
                "--schema-file",
                str(schema),
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        data = json.loads(completed.stdout)
        assert data["data"] == {"summary": "日本語の結果"}
        assert data["record"]["response_model"] == "fixture-model"
        assert data["record"]["usage"]["input_tokens"] == 8
        assert "[SUCCESS]" in completed.stderr
        assert [path for path, _ in requests] == ["/api/show", "/api/chat"]
        assert not (tmp_path / "isolated-home").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
