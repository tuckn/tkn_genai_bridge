"""Caller token estimates must affect planning only, with explicit provenance."""

import json

import pytest
from pydantic import ValidationError

from tkn_genai_bridge import Profile, RequestError, Runtime, TokenPricing, estimate_tokens
from tkn_genai_bridge.cli import main


def test_caller_estimate_keeps_method_and_does_not_add_margin(request_object, monkeypatch):
    def forbidden(*args):
        pytest.fail("caller estimate should not rebuild the estimation envelope")

    monkeypatch.setattr("tkn_genai_bridge.costs.schema_prompt", forbidden)
    p = Profile(
        provider="ollama",
        model="m",
        max_output_tokens=100,
        pricing={
            "m": TokenPricing(
                currency="JPY", pricing_date="2026-01-01", input_per_million=30, output_per_million=180
            )
        },
    )
    plan = Runtime(p).plan(request_object, input_tokens=1000, input_tokens_method="local-o200k-with-margin")
    assert plan.token_estimate.input_tokens == 1000
    assert plan.token_estimate.input_tokens_source == "caller" and plan.token_estimate.margin_tokens == 0
    assert plan.token_estimate.method == "local-o200k-with-margin"
    assert plan.cost_estimate.usage.input_tokens == 1000
    assert plan.cost_estimate.amount == pytest.approx(0.048)


def test_supplied_estimates_do_not_change_generation_identity(request_object):
    runtime = Runtime(Profile())
    before = runtime.profile.model_dump(), request_object.model_dump()
    a = runtime.plan(request_object)
    b = runtime.plan(request_object, input_tokens=0, output_tokens=0)
    assert b.token_estimate.input_tokens == 0 and b.token_estimate.method == "caller-supplied"
    assert b.token_estimate.output_tokens == 0
    assert a.generation_settings_sha256 == b.generation_settings_sha256
    assert a.prompt_sha256 == b.prompt_sha256 and a.schema_sha256 == b.schema_sha256
    assert before == (runtime.profile.model_dump(), request_object.model_dump())


@pytest.mark.parametrize("value", [-1, True, 1.5, "4"])
def test_invalid_input_estimate_is_rejected(value, request_object):
    with pytest.raises(ValidationError):
        Runtime(Profile()).plan(request_object, input_tokens=value)


@pytest.mark.parametrize("method", ["", "has spaces", "line\nsecret", "x" * 121])
def test_invalid_method_is_rejected(method, request_object):
    with pytest.raises(ValidationError):
        estimate_tokens(request_object, Profile(), input_tokens=1, input_tokens_method=method)


def test_method_requires_estimate_and_request_still_validates(request_object):
    with pytest.raises(RequestError):
        Runtime(Profile()).plan(request_object, input_tokens_method="local")
    with pytest.raises(RequestError):
        Runtime(Profile()).plan(request_object.model_copy(update={"prompt": " "}), input_tokens=1)


def test_cli_input_estimate_and_method_are_dry_run_only(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        'schema_version: "1.1.0"\ndefault_profile: p\nprofiles:\n  p:\n    provider: ollama\n    model: m\n',
        encoding="utf-8",
    )
    prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
    prompt.write_text("synthetic", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")
    args = ["generate", "--config", str(config), "--prompt-file", str(prompt), "--schema-file", str(schema)]
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    assert (
        main([*args, "--dry-run", "--estimate-input-tokens", "42", "--estimate-input-method", "custom-v1"])
        == 0
    )
    estimate = json.loads(capsys.readouterr().out)["token_estimate"]
    assert estimate["input_tokens"] == 42 and estimate["method"] == "custom-v1"
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}
    for options in (
        ["--estimate-input-tokens", "42"],
        ["--dry-run", "--estimate-input-method", "custom"],
        ["--dry-run", "--estimate-input-tokens", "-1"],
    ):
        with pytest.raises(SystemExit) as exc:
            main([*args, *options])
        assert exc.value.code == 2
