import json
from pathlib import Path

import pytest

from tkn_genai_runtime import ConfigError, load_config, load_profile
from tkn_genai_runtime.config import config_template, initialize_config
from tkn_genai_runtime.models import Profile


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def layer(model, version="1.0.0"):
    return f'schema_version: "{version}"\nprofiles:\n  codex-default:\n    model: {model}\n'


def test_five_layers_and_provenance(tmp_path):
    user = write(tmp_path / "user.yaml", layer("user", "1.0.9"))
    project = write(tmp_path / ".tkn/config.yaml", layer("project"))
    explicit = write(tmp_path / "explicit.yaml", layer("explicit"))
    before = {p: p.read_bytes() for p in (user, project, explicit)}
    resolved = load_config(
        cwd=tmp_path,
        user_file=user,
        include_project=True,
        config_file=explicit,
        overrides={"profiles": {"codex-default": {"model": "options"}}},
    )
    assert resolved.profile().model == "options"
    assert [s.name for s in resolved.sources] == ["built-in", "user", "project", "explicit", "options"]
    assert resolved.sources[1].schema_version == "1.0.9"
    assert resolved.config.schema_version == "1.0.0"
    assert resolved.field_sources["profiles.codex-default.model"] == "options"
    assert before == {p: p.read_bytes() for p in before}


def test_library_does_not_read_application_config(tmp_path):
    write(tmp_path / ".tkn/config.yaml", "application_specific: true")
    assert load_profile().provider == "codex"
    with pytest.raises(ConfigError):
        load_config(include_project=True)


@pytest.mark.parametrize("version", [None, 1, "1", "01.0.0", "1.0.0-beta", "0.9.0", "1.1.0", "2.0.0"])
def test_rejects_missing_invalid_and_unsupported_schema(tmp_path, version):
    path = write(tmp_path / "config.yaml", json.dumps({"schema_version": version}))
    with pytest.raises(ConfigError):
        load_config(config_file=path)


def test_invalid_overridden_values_fail_before_merge(tmp_path):
    user = write(
        tmp_path / "user.yaml", 'schema_version: "1.0.0"\nprofiles:\n  p:\n    timeout_seconds: false'
    )
    explicit = write(
        tmp_path / "extra.yaml", 'schema_version: "1.0.0"\nprofiles:\n  p:\n    timeout_seconds: 30'
    )
    with pytest.raises(ConfigError):
        load_config(user_file=user, config_file=explicit)


@pytest.mark.parametrize(
    "body",
    [
        'schema_version: "1.0.0"\nunknown: true',
        'schema_version: "1.0.0"\nprofiles:\n  p:\n    cli:\n      unknown: x',
        'schema_version: "1.0.0"\nprofiles:\n  p:\n    timeout_seconds: -1',
        'schema_version: "1.0.0"\nprofiles:\n  p:\n    timeout_seconds: .nan',
        'schema_version: "1.0.0"\nschema_version: "1.0.9"',
        'schema_version: "1.0.0"\nprofiles:\n  p:\n    azure:\n      api_key: do-not-display',
    ],
)
def test_invalid_config_is_rejected_without_leaking_values(tmp_path, body):
    path = write(tmp_path / "config.yaml", body)
    with pytest.raises(ConfigError) as exc:
        load_config(config_file=path)
    assert "do-not-display" not in str(exc.value)


def test_relative_explicit_config_and_windows_executable(tmp_path):
    path = write(
        tmp_path / "config.yaml",
        'schema_version: "1.0.0"\nprofiles:\n  codex-default:\n'
        "    cli:\n      executable: 'C:\\path with spaces\\codex.exe'\n",
    )
    assert load_config(cwd=tmp_path, config_file=Path("config.yaml")).profile().cli.executable == (
        r"C:\path with spaces\codex.exe"
    )
    assert load_config(config_file=path).sources[-1].path == str(path)


def test_missing_profile_or_file_fails(tmp_path):
    with pytest.raises(ConfigError):
        load_profile("missing")
    with pytest.raises(ConfigError):
        load_config(config_file=tmp_path / "missing.yaml")


def test_nested_overrides_and_input_immutability(tmp_path):
    path = write(
        tmp_path / "config.yaml",
        'schema_version: "1.0.0"\nprofiles:\n  local:\n'
        "    provider: ollama\n    model: example\n    ollama:\n      think: false\n",
    )
    options = {"ollama": {"context_tokens": 4096}}
    profile = load_profile("local", config_file=path, overrides=options)
    assert profile.ollama.think is False
    assert profile.ollama.context_tokens == 4096
    assert options == {"ollama": {"context_tokens": 4096}}


def test_init_dry_run_idempotency_and_protection(tmp_path):
    path = tmp_path / "new/config.yaml"
    assert initialize_config(path, dry_run=True)["status"] == "would_create"
    assert not path.parent.exists()
    assert initialize_config(path)["status"] == "created"
    assert path.read_text(encoding="utf-8").splitlines()[0] == 'schema_version: "1.0.0"'
    assert initialize_config(path)["status"] == "unchanged"
    assert list(path.parent.iterdir()) == [path]
    path.write_text(config_template() + "# user edit\n", encoding="utf-8")
    before = path.read_bytes()
    for dry_run in (True, False):
        with pytest.raises(ConfigError):
            initialize_config(path, dry_run=dry_run)
    assert path.read_bytes() == before


def test_init_atomic_publish_failure_cleans_pending_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"

    def fail(*args):
        raise OSError("failure")

    monkeypatch.setattr("tkn_genai_runtime.config.os.link", fail)
    with pytest.raises(ConfigError):
        initialize_config(path)
    assert not path.exists()
    assert not list(tmp_path.glob(".genai-*"))


@pytest.mark.parametrize(
    "settings",
    [
        {"provider": "ollama"},
        {"provider": "azure-openai", "model": "deploy"},
        {"provider": "codex", "local_only": True},
        {"provider": "codex", "max_output_tokens": 2},
        {"provider": "ollama", "model": "m", "reasoning_effort": "low"},
        {"provider": "ollama", "model": "m-cloud", "local_only": True},
        {"provider": "codex", "ollama": {}},
    ],
)
def test_invalid_capability_combinations(settings):
    with pytest.raises(ValueError):
        Profile.model_validate(settings)
