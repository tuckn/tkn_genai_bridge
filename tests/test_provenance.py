import pytest

from tkn_genai_bridge import (
    AzureSettings,
    CliSettings,
    GenerationRecord,
    OllamaSettings,
    Profile,
    ProviderError,
    Runtime,
    __version__,
    load_config,
    load_profile,
)
from tkn_genai_bridge.providers.base import ProviderResponse


class Backend:
    def generate(self, profile, request):
        return ProviderResponse({"summary": "ok"})


def test_profile_selection_and_overrides_flow_into_plan_and_result(tmp_path, request_object):
    config = tmp_path / "config.yaml"
    config.write_text(
        'schema_version: "1.0.0"\ndefault_profile: selected\nprofiles:\n'
        "  selected:\n    model: base\n  alias:\n    model: base\n",
        encoding="utf-8",
    )
    resolved = load_config(config_file=config)
    assert resolved.profile().profile_name == "selected"
    assert resolved.profile("alias").profile_name == "alias"
    assert resolved.config.profiles["selected"].profile_name is None
    profile = load_profile(config_file=config, overrides={"reasoning_effort": "high"})
    assert profile.profile_name == "selected"
    assert "profile_name" not in profile.model_dump()
    with Runtime(profile, backend=Backend()) as runtime:
        plan = runtime.plan(request_object)
        record = runtime.generate(request_object).record
    assert plan.bridge_version == record.bridge_version == __version__
    assert plan.profile_name == record.profile_name == "selected"
    assert plan.generation_settings_sha256 == record.generation_settings_sha256
    assert len(record.generation_settings_sha256) == 64


@pytest.mark.parametrize(
    "updates",
    [
        {"model": "another"},
        {"reasoning_effort": "high"},
        {"timeout_seconds": 42.0},
        {"cli": CliSettings(executable="another-executable")},
    ],
)
def test_generation_options_change_fingerprint(updates, request_object):
    original = Runtime(Profile()).plan(request_object).generation_settings_sha256
    changed = Runtime(Profile(**updates)).plan(request_object).generation_settings_sha256
    assert original != changed


@pytest.mark.parametrize(
    "updates",
    [
        {"think": False},
        {"think": True},
        {"temperature": 0.5},
        {"context_tokens": 4096},
        {"base_url": "http://127.0.0.1:11435"},
    ],
)
def test_ollama_options_change_fingerprint(updates, request_object):
    original = Runtime(Profile(provider="ollama", model="m")).plan(request_object)
    changed = Runtime(Profile(provider="ollama", model="m", ollama=OllamaSettings(**updates))).plan(
        request_object
    )
    assert original.generation_settings_sha256 != changed.generation_settings_sha256


def test_canonical_defaults_names_and_prompt_schema_are_independent(request_object):
    implicit = Profile(provider="ollama", model="m")
    explicit = Profile(provider="ollama", model="m", ollama=OllamaSettings())
    base = Runtime(implicit).plan(request_object).generation_settings_sha256
    assert Runtime(explicit, profile_name="alias").plan(request_object).generation_settings_sha256 == base
    changed = request_object.model_copy(update={"prompt": "another", "output_schema": {"type": "object"}})
    assert Runtime(implicit).plan(changed).generation_settings_sha256 == base
    renamed = request_object.model_copy(update={"schema_name": "different"})
    assert Runtime(implicit).plan(renamed).generation_settings_sha256 != base
    assert (
        Runtime(Profile()).plan(request_object).generation_settings_sha256
        == Runtime(Profile(cli=CliSettings(executable="codex")))
        .plan(request_object)
        .generation_settings_sha256
    )


def test_authentication_excluded_but_endpoint_and_token_limit_included(request_object, monkeypatch):
    endpoint = "https://example.openai.azure.com/openai/v1"

    def fingerprint(**kwargs):
        return Runtime(Profile(provider="azure-openai", model="deployment", **kwargs)).plan(request_object)

    original = fingerprint(azure=AzureSettings(endpoint=endpoint))
    monkeypatch.setenv("CUSTOM_KEY", "PRIVATE VALUE")
    changed = fingerprint(
        azure=AzureSettings(
            endpoint=endpoint,
            auth="interactive_browser",
            api_key_env="CUSTOM_KEY",
            tenant_id="private-tenant",
            token_scope="https://example.com/.default",
        )
    )
    assert changed.generation_settings_sha256 == original.generation_settings_sha256
    assert "private" not in changed.model_dump_json().lower()
    assert fingerprint(
        azure=AzureSettings(endpoint=endpoint), max_output_tokens=100
    ).generation_settings_sha256 != (original.generation_settings_sha256)
    assert (
        fingerprint(
            azure=AzureSettings(endpoint="https://another.openai.azure.com/openai/v1")
        ).generation_settings_sha256
        != original.generation_settings_sha256
    )


def test_failure_and_legacy_record_provenance(request_object):
    class Failure:
        def generate(self, profile, request):
            raise ProviderError("synthetic")

    with Runtime(Profile(), backend=Failure(), profile_name="manual") as runtime:
        plan = runtime.plan(request_object)
        with pytest.raises(ProviderError) as exc:
            runtime.generate(request_object)
    record = exc.value.record
    assert record.profile_name == "manual"
    assert record.generation_settings_sha256 == plan.generation_settings_sha256
    legacy = record.model_dump(exclude={"bridge_version", "profile_name", "generation_settings_sha256"})
    old = GenerationRecord.model_validate(legacy)
    assert old.bridge_version is None and old.generation_settings_sha256 is None
