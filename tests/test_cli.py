import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tkn_genai_bridge.cli import config_lines, main
from tkn_genai_bridge.config import config_template


def test_config_init_and_list_json_are_read_only(tmp_path, capsys):
    target = tmp_path / "shared/config.yaml"
    assert main(["config", "init", "--path", str(target), "--dry-run"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "would_create"
    assert "[SUCCESS]" in output.err
    assert not target.parent.exists()
    assert main(["config", "init", "--path", str(target)]) == 0
    capsys.readouterr()
    original = target.read_bytes()
    assert main(["config", "list", "--json", "--config", str(target), "--model", "overridden"]) == 0
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
    assert json.loads(output.out)["profile_name"] == "local"
    assert len(json.loads(output.out)["generation_settings_sha256"]) == 64
    assert "private-prompt" not in output.out + output.err
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_expected_error_is_json_without_traceback(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(config_template() + "\nsecret_key: PRIVATE\n", encoding="utf-8")
    assert main(["config", "list", "--config", str(config)]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "invalid_config"
    assert "PRIVATE" not in output.out + output.err
    assert "Traceback" not in output.err


def test_unknown_profile_override_cannot_create_profile(capsys):
    assert main(["config", "list", "--profile", "typo", "--model", "value"]) == 2
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
        main(["-q", "-v", "config", "list"])
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
        assert data["record"]["profile_name"] == "local"
        assert "[SUCCESS]" in completed.stderr
        assert [path for path, _ in requests] == ["/api/show", "/api/chat"]
        assert not (tmp_path / "isolated-home").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_config_lines_preserve_paths_types_and_single_line_strings():
    assert config_lines(
        {
            "path": r"C:\Users\ExampleUser\path with spaces\codex.exe",
            "items": [{"active": True}, False, None, 3, 1.5],
            "empty_list": [],
            "empty_map": {},
            "text": "日本語=値\r\n\t\x00\x1b\x7f",
            "blank": "",
        }
    ) == [
        r"path=C:\Users\ExampleUser\path with spaces\codex.exe",
        "items[0].active=true",
        "items[1]=false",
        "items[2]=null",
        "items[3]=3",
        "items[4]=1.5",
        "empty_list=[]",
        "empty_map={}",
        r"text=日本語=値\r\n\t\u0000\u001b\u007f",
        "blank=",
    ]


@pytest.mark.parametrize("json_output", [False, True])
def test_config_list_metadata_precedence_and_no_side_effects(tmp_path, capsys, monkeypatch, json_output):
    from tkn_genai_bridge.config import user_config_path

    user = user_config_path()
    user.parent.mkdir(parents=True)
    user.write_text(
        'schema_version: "1.0.9"\nprofiles:\n  codex-default:\n'
        "    model: user\n    timeout_seconds: 60.0\n",
        encoding="utf-8",
    )
    project = tmp_path / ".tkn" / "config.yaml"
    project.parent.mkdir()
    project.write_text(
        'schema_version: "1.1.0"\nprofiles:\n  codex-default:\n    model: project\n',
        encoding="utf-8",
    )
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        'schema_version: "1.1.8"\nprofiles:\n  selected:\n    provider: codex\n'
        "    model: explicit\n    cli:\n"
        "      executable: 'C:\\Users\\ExampleUser\\path with spaces\\codex.exe'\n"
        "  azure:\n    provider: azure-openai\n    model: synthetic\n    azure:\n"
        "      endpoint: https://example.openai.azure.com/openai/v1\n"
        "      api_key_env: TEST_BRIDGE_API_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_BRIDGE_API_KEY", "PRIVATE credential")
    monkeypatch.setattr(
        "tkn_genai_bridge.cli.Runtime", lambda *a, **k: pytest.fail("listing must not open a runtime")
    )
    before_files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    before_dirs = {p for p in tmp_path.rglob("*") if p.is_dir()}
    args = [
        "config", "list", "--config", str(explicit), "--profile", "selected",
        "--model", "overridden",
    ]
    assert main([*args, *(["--json"] if json_output else [])]) == 0
    output = capsys.readouterr()
    assert output.err == "[INFO] Showing resolved configuration\n"
    assert "PRIVATE credential" not in output.out + output.err
    if json_output:
        data = json.loads(output.out)
        assert data["settings"]["default_profile"] == "selected"
        assert data["settings"]["schema_version"] == "1.1.0"
        assert data["settings"]["profiles"]["selected"]["model"] == "overridden"
        assert data["settings"]["profiles"]["selected"]["cli"]["executable"] == (
            r"C:\Users\ExampleUser\path with spaces\codex.exe"
        )
        assert [source["name"] for source in data["sources"]] == [
            "built-in", "user", "project", "explicit", "options"
        ]
        assert [source["schema_version"] for source in data["sources"]] == [
            "1.1.0", "1.0.9", "1.1.0", "1.1.8", "1.1.0"
        ]
        assert all(source["migrated"] is False for source in data["sources"])
        assert data["field_sources"]["profiles.codex-default.model"] == "project"
        assert data["field_sources"]["profiles.codex-default.timeout_seconds"] == "user"
        assert data["field_sources"]["profiles.selected.model"] == "options"
        assert data["field_sources"]["profiles.selected.cli.executable"] == "explicit"
        assert data["field_sources"]["profiles.local-vision.local_only"] == "built-in"
        assert data["user_config_path"] == str(user.resolve())
    else:
        lines = output.out.splitlines()
        for expected in (
            "settings.default_profile=selected",
            "settings.schema_version=1.1.0",
            "settings.profiles.selected.model=overridden",
            r"settings.profiles.selected.cli.executable=C:\Users\ExampleUser\path with spaces\codex.exe",
            "settings.profiles.codex-default.model=project",
            "settings.profiles.local-vision.local_only=true",
            "settings.profiles.selected.pricing={}",
            "settings.profiles.selected.azure=null",
            "sources[1].schema_version=1.0.9",
            "sources[3].schema_version=1.1.8",
            "sources[3].migrated=false",
            "field_sources.profiles.selected.model=options",
            "field_sources.profiles.codex-default.timeout_seconds=user",
            f"user_config_path={user.resolve()}",
        ):
            assert expected in lines
    assert before_files == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before_dirs == {p for p in tmp_path.rglob("*") if p.is_dir()}


@pytest.mark.parametrize("json_output", [False, True])
def test_config_list_quiet_and_no_project_config(tmp_path, capsys, json_output):
    project = tmp_path / ".tkn" / "config.yaml"
    project.parent.mkdir()
    project.write_text("application_specific: true", encoding="utf-8")
    args = ["-q", "config", "list", "--no-project-config"]
    assert main([*args, *(["--json"] if json_output else [])]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    if json_output:
        assert json.loads(output.out)["sources"] == [
            {"name": "built-in", "path": None, "schema_version": "1.1.0", "migrated": False}
        ]
    else:
        assert "settings.default_profile=codex-default" in output.out.splitlines()
        assert "sources[0].name=built-in" in output.out.splitlines()
        assert "sources[1]" not in output.out
    assert not (tmp_path / "home").exists()


def test_config_list_help_and_removed_show(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["config", "list", "--help"])
    assert exc.value.code == 0
    help_output = capsys.readouterr().out
    assert "--json" in help_output and "--profile" in help_output
    with pytest.raises(SystemExit) as exc:
        main(["config", "show"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
