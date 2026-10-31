"""Application-owned configuration, using temporary files and offline operations."""

import json
from pathlib import Path

import pytest

from tkn_genai_bridge import ConfigError, load_config, load_profile
from tkn_genai_bridge.cli import main

APP_CONFIG = '''schema_version: "1.1.0"
default_profile: app
profiles:
  app:
    provider: ollama
    model: synthetic
'''
INVALID_CONFIGS = [
    ("profiles: [", "invalid_config"),
    ('schema_version: "2.0.0"', "unsupported_schema"),
    ('schema_version: "1.1.0"\nunknown: true', "invalid_config"),
    ('schema_version: "1.1.0"\nprofiles:\n  app:\n    timeout_seconds: -1', "invalid_config"),
]


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def temporary_shared_config_and_offline_guard(tmp_path, monkeypatch):
    # Explicitly redirect the shared path into pytest's temporary directory.
    monkeypatch.setattr("tkn_genai_bridge.config.user_config_path", lambda: tmp_path / "shared.yaml")
    monkeypatch.setattr("tkn_genai_bridge.cli.user_config_path", lambda: tmp_path / "shared.yaml")

    def fail(*args, **kwargs):
        pytest.fail("configuration tests must not connect, spawn processes, or generate")

    monkeypatch.setattr("socket.socket.connect", fail)
    monkeypatch.setattr("socket.create_connection", fail)
    monkeypatch.setattr("subprocess.Popen", fail)
    monkeypatch.setattr("tkn_genai_bridge.runtime.Runtime.generate", fail)


@pytest.mark.parametrize("body,code", INVALID_CONFIGS)
def test_disabled_files_are_never_read(tmp_path, monkeypatch, body, code):
    import tkn_genai_bridge.config as config_module

    user = write(tmp_path / "shared.yaml", body)
    project = write(tmp_path / ".tkn/config.yaml", body)
    explicit = write(tmp_path / "app.yaml", APP_CONFIG)
    before = {p: p.read_bytes() for p in (user, project, explicit)}
    read = config_module._read
    read_paths = []

    def record_read(path):
        read_paths.append(path)
        return read(path)

    monkeypatch.setattr(config_module, "_read", record_read)
    resolved = load_config(
        config_file=Path("app.yaml"),
        include_user=False,
        cwd=tmp_path,
        overrides={"profiles": {"app": {"timeout_seconds": 42.0}}},
    )
    assert resolved.profile().model == "synthetic"
    assert resolved.profile().timeout_seconds == 42.0
    assert [source.name for source in resolved.sources] == ["built-in", "explicit", "options"]
    assert resolved.sources[1].path == str(explicit.resolve())
    assert resolved.sources[1].schema_version == "1.1.0"
    assert resolved.field_sources["default_profile"] == "explicit"
    assert resolved.field_sources["profiles.app.model"] == "explicit"
    assert resolved.field_sources["profiles.app.timeout_seconds"] == "options"
    assert resolved.field_sources["profiles.app.local_only"] == "built-in"
    assert not {"user", "project"}.intersection(resolved.field_sources.values())
    profile = load_profile(
        config_file=explicit, include_user=False, overrides={"model": "overridden"}
    )
    assert profile.profile_name == "app"
    assert profile.model == "overridden"
    assert read_paths == [explicit.resolve(), explicit.resolve()]
    assert before == {p: p.read_bytes() for p in before}

    # Merely supplying config_file retains legacy validation of the shared file.
    for loader in (load_config, load_profile):
        with pytest.raises(ConfigError) as exc:
            loader(config_file=explicit)
        assert exc.value.code == code


def test_shared_profiles_remain_available_only_when_enabled(tmp_path):
    write(tmp_path / "shared.yaml", APP_CONFIG.replace("app", "shared").replace("synthetic", "user-model"))
    explicit = write(tmp_path / "app.yaml", APP_CONFIG)
    assert load_profile().profile_name == "shared"
    assert load_profile("shared", config_file=explicit).model == "user-model"
    assert load_config(config_file=explicit).profile("shared").model == "user-model"
    isolated = load_config(config_file=explicit, include_user=False)
    assert "shared" not in isolated.config.profiles
    assert not any(key.startswith("profiles.shared.") for key in isolated.field_sources)
    with pytest.raises(ConfigError) as exc:
        isolated.profile("shared")
    assert exc.value.code == "unknown_profile"
    with pytest.raises(ConfigError) as exc:
        load_profile("shared", config_file=explicit, include_user=False)
    assert exc.value.code == "unknown_profile"


@pytest.mark.parametrize("loader", [load_config, load_profile])
@pytest.mark.parametrize("body,code", [(None, "missing_config"), *INVALID_CONFIGS])
def test_explicit_file_failure_never_falls_back(tmp_path, loader, body, code):
    write(tmp_path / "shared.yaml", APP_CONFIG)
    explicit = tmp_path / "app.yaml"
    if body is not None:
        write(explicit, body)
    with pytest.raises(ConfigError) as exc:
        loader(config_file=explicit, include_user=False)
    assert exc.value.code == code


def test_user_file_conflict_fails_before_reading_any_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tkn_genai_bridge.config._read", lambda *args: pytest.fail("must validate arguments first")
    )
    explicit = write(tmp_path / "app.yaml", APP_CONFIG)
    with pytest.raises(ConfigError, match="user_file cannot be combined") as exc:
        load_config(include_user=False, user_file=tmp_path / "missing.yaml", config_file=explicit)
    assert exc.value.code == "invalid_config"


def test_user_switch_is_independent_of_project_and_explicit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tkn_genai_bridge.config.user_config_path",
        lambda: pytest.fail("disabled user path must not be resolved"),
    )
    built_in = load_config(include_user=False)
    assert [source.name for source in built_in.sources] == ["built-in"]
    assert set(built_in.field_sources.values()) == {"built-in"}
    assert load_profile(include_user=False).provider == "codex"
    project = write(tmp_path / ".tkn/config.yaml", APP_CONFIG)
    resolved = load_config(include_user=False, include_project=True)
    assert resolved.profile().model == "synthetic"
    assert [source.name for source in resolved.sources] == ["built-in", "project"]
    assert resolved.sources[-1].path == str(project.resolve())
    assert resolved.field_sources["profiles.app.model"] == "project"


@pytest.mark.parametrize("body,code", INVALID_CONFIGS)
def test_cli_app_only_list_and_dry_run(tmp_path, capsys, body, code):
    write(tmp_path / "shared.yaml", body)
    write(tmp_path / ".tkn/config.yaml", body)
    explicit = write(tmp_path / "app.yaml", APP_CONFIG)
    prompt = write(tmp_path / "prompt.txt", "Synthetic input")
    schema = write(tmp_path / "schema.json", '{"type": "object"}')
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    options = ["--json", "--config", str(explicit), "--no-user-config", "--no-project-config"]
    assert main(["config", "list", *options, "--model", "override-model"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["settings"]["profiles"]["app"]["model"] == "override-model"
    assert [source["name"] for source in result["sources"]] == ["built-in", "explicit", "options"]
    assert result["field_sources"]["profiles.app.model"] == "options"
    assert not {"user", "project"}.intersection(result["field_sources"].values())
    assert main([
        "generate", *options, "--model", "override-model", "--prompt-file", str(prompt),
        "--schema-file", str(schema), "--dry-run",
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["will_call_provider"] is False
    assert plan["profile_name"] == "app"
    assert plan["model"] == "override-model"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("body,code", [(None, "missing_config"), *INVALID_CONFIGS])
def test_cli_explicit_errors_only_report_enabled_paths(tmp_path, capsys, body, code):
    write(tmp_path / "shared.yaml", APP_CONFIG)
    write(tmp_path / ".tkn/config.yaml", APP_CONFIG)
    explicit = tmp_path / "app.yaml"
    if body is not None:
        write(explicit, body)
    assert main([
        "config", "list", "--json", "--config", str(explicit),
        "--no-user-config", "--no-project-config",
    ]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == code
    assert error["paths"] == {"config_file": str(explicit.resolve())}


def test_cli_user_switch_keeps_project_layer_and_rejects_shared_profile(tmp_path, capsys):
    write(tmp_path / "shared.yaml", APP_CONFIG.replace("app", "shared"))
    write(tmp_path / ".tkn/config.yaml", APP_CONFIG)
    assert main(["config", "list", "--json", "--no-user-config"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [source["name"] for source in result["sources"]] == ["built-in", "project"]
    assert main([
        "config", "list", "--json", "--no-user-config", "--no-project-config", "--profile", "shared",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unknown_profile"
