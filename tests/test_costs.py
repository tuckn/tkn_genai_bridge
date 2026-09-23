"""Accounting contracts: unknowns, offline plans, repricing and failure records."""

import json
import socket
import subprocess
import sys

import pytest
from pydantic import ValidationError

from tkn_genai_bridge import (
    AzureSettings,
    ConfigError,
    GenerationRecord,
    Profile,
    ProviderError,
    ResponseMetadata,
    Runtime,
    TokenPricing,
    Usage,
    estimate_cost,
    estimate_tokens,
    load_config,
    load_profile,
)
from tkn_genai_bridge.cli import main
from tkn_genai_bridge.providers.base import ProviderResponse
from tkn_genai_bridge.providers.cli import _claude_metadata, _codex_metadata
from tkn_genai_bridge.providers.http import provider_response


def price(**updates):
    return TokenPricing(
        **{
            "currency": "JPY",
            "pricing_date": "2026-01-01",
            "input_per_million": 30.0,
            "output_per_million": 180.0,
            **updates,
        }
    )


def profile(**updates):
    return Profile(
        **{
            "provider": "azure-openai",
            "model": "deployment",
            "max_output_tokens": 16000,
            "azure": AzureSettings(endpoint="https://example.openai.azure.com/openai/v1"),
            "pricing": {"deployment": price()},
            **updates,
        }
    )


def test_reprice_preserves_tokens_rates_currency_and_date():
    usage = Usage(input_tokens=60000, output_tokens=16000, reasoning_tokens=12000)
    original = estimate_cost(usage, price())
    updated = estimate_cost(usage, price(input_per_million=40, pricing_date="2026-02-01"))
    assert original.amount == pytest.approx(4.68)
    assert updated.amount == pytest.approx(5.28)
    assert original.usage == updated.usage == usage
    assert original.pricing.pricing_date == "2026-01-01"
    assert original.currency == "JPY"
    assert original.basis == "scenario"
    assert json.loads(original.model_dump_json())["amount"] == original.amount


def test_cache_and_reasoning_are_not_double_counted():
    usage = Usage(
        input_tokens=1000,
        output_tokens=200,
        cached_input_tokens=600,
        cache_write_tokens=100,
        reasoning_tokens=150,
    )
    rates = price(
        input_per_million=2,
        output_per_million=10,
        cached_input_per_million=0.2,
        cache_write_per_million=2.5,
        cache_policy="observed",
    )
    assert estimate_cost(usage, rates).amount == pytest.approx(0.00297)
    assert estimate_cost(usage, price(input_per_million=2, output_per_million=10)).amount == 0.004
    uncached = usage.model_copy(update={"input_tokens": 300, "input_tokens_scope": "uncached"})
    assert estimate_cost(uncached, rates).amount == pytest.approx(0.00297)
    assert estimate_cost(uncached, price(input_per_million=2, output_per_million=10)).amount == 0.004


@pytest.mark.parametrize(
    "usage,rates,reason",
    [
        (Usage(input_tokens=0, output_tokens=0), None, "pricing_not_configured"),
        (Usage(input_tokens=10), price(), "usage_missing"),
        (Usage(output_tokens=10), price(), "usage_missing"),
        (
            Usage(input_tokens=10, output_tokens=0),
            price(cache_policy="observed", cached_input_per_million=0),
            "cache_usage_missing",
        ),
        (
            Usage(input_tokens=10, output_tokens=0, cached_input_tokens=0),
            price(cache_policy="observed", cached_input_per_million=0, cache_write_per_million=0),
            "cache_usage_missing",
        ),
        (
            Usage(input_tokens=10, output_tokens=0, input_tokens_scope="uncached"),
            price(),
            "cache_usage_missing",
        ),
        (Usage(input_tokens=10, output_tokens=0, cached_input_tokens=11), price(), "inconsistent_usage"),
        (
            Usage(input_tokens=10, output_tokens=0, cached_input_tokens=6, cache_write_tokens=5),
            price(),
            "inconsistent_usage",
        ),
        (Usage(input_tokens=10, output_tokens=1, reasoning_tokens=2), price(), "inconsistent_usage"),
        (Usage(input_tokens=10**20, output_tokens=0), price(input_per_million=1e308), "non_finite_cost"),
    ],
)
def test_unknown_or_inconsistent_is_not_free(usage, rates, reason):
    result = estimate_cost(usage, rates)
    assert result.status == "unavailable"
    assert result.amount is None
    assert result.unavailable_reason == reason
    assert result.usage == usage


def test_zero_is_valid_only_with_known_tokens_and_explicit_prices():
    assert estimate_cost(Usage(input_tokens=0, output_tokens=0), price()).amount == 0
    assert (
        estimate_cost(
            Usage(input_tokens=100, output_tokens=200), price(input_per_million=0, output_per_million=0)
        ).amount
        == 0
    )
    assert estimate_cost(Usage(), price(input_per_million=0, output_per_million=0)).amount is None


@pytest.mark.parametrize(
    "updates",
    [
        {"currency": "jpy"},
        {"currency": ""},
        {"pricing_date": "2026-02-30"},
        {"pricing_date": "20260101"},
        {"pricing_date": "2026-1-1"},
        {"input_per_million": -1},
        {"input_per_million": True},
        {"input_per_million": "2"},
        {"output_per_million": float("inf")},
        {"cached_input_per_million": float("nan")},
        {"cache_write_per_million": -1},
        {"cache_policy": "observed"},
        {"max_cost_jpy": 100},
    ],
)
def test_invalid_prices_are_rejected(updates):
    with pytest.raises(ValidationError):
        price(**updates)


@pytest.mark.parametrize("value", [-1, True, 1.5, "4"])
def test_invalid_token_counts_are_rejected(value):
    with pytest.raises(ValidationError):
        Usage(input_tokens=value)


@pytest.mark.parametrize("provider", ["codex", "claude-code", "github-copilot", "ollama", "azure-openai"])
def test_plan_is_offline_and_distinguishes_estimates(provider, request_object, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("offline plan must not call network, auth, process or SDK")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("tkn_genai_bridge.providers.litellm.load_sdk", forbidden)
    p = profile() if provider == "azure-openai" else Profile(provider=provider, model="m")
    before = {f: f.read_bytes() for f in tmp_path.rglob("*") if f.is_file()}
    with Runtime(p, token_provider=forbidden) as runtime:
        plan = runtime.plan(request_object, output_tokens=1000)
    assert plan.token_estimate.input_tokens > len(request_object.prompt.encode("utf-8"))
    assert plan.token_estimate.output_tokens == 1000
    assert plan.token_estimate.output_tokens_source == "caller"
    assert plan.cost_estimate.basis == "planned"
    assert plan.will_call_provider is False
    assert request_object.prompt not in plan.model_dump_json()
    assert before == {f: f.read_bytes() for f in tmp_path.rglob("*") if f.is_file()}


def test_plan_includes_schema_unicode_and_changes_neither_request_nor_profile(request_object):
    p = profile()
    before = p.model_dump(), request_object.model_dump()
    short = estimate_tokens(request_object, p)
    extended = request_object.model_copy(update={"prompt": request_object.prompt + "日本語"})
    assert estimate_tokens(extended, p).input_tokens == short.input_tokens + 9
    extended = request_object.model_copy(
        update={"output_schema": {**request_object.output_schema, "description": "long schema description"}}
    )
    assert estimate_tokens(extended, p).input_tokens > short.input_tokens
    assert short.output_tokens == 16000
    assert short.output_tokens_source == "profile_limit"
    assert before == (p.model_dump(), request_object.model_dump())
    with pytest.raises(ValidationError):
        estimate_tokens(request_object, p, output_tokens=-1)


def test_unknown_output_and_unknown_model_price_are_not_guessed(request_object):
    with Runtime(profile(max_output_tokens=None)) as runtime:
        plan = runtime.plan(request_object)
    assert plan.token_estimate.output_tokens is None
    assert plan.cost_estimate.unavailable_reason == "usage_missing"
    p = profile(model="other-deployment")
    assert Runtime(p).plan(request_object).cost_estimate.unavailable_reason == "pricing_not_configured"
    assert Runtime(Profile()).plan(request_object).cost_estimate.amount is None


def test_plan_reserves_higher_cache_write_rate_without_reuse(request_object):
    rates = price(cache_policy="observed", cached_input_per_million=3, cache_write_per_million=60)
    plan = Runtime(profile(pricing={"deployment": rates})).plan(request_object)
    assert plan.cost_estimate.usage.cached_input_tokens == 0
    assert plan.cost_estimate.usage.cache_write_tokens == plan.token_estimate.input_tokens
    assert plan.cost_estimate.amount == pytest.approx(
        (plan.token_estimate.input_tokens * 60 + 16000 * 180) / 1_000_000
    )


@pytest.mark.parametrize("outcome", ["success", "schema", "provider", "transport"])
def test_record_and_observer_keep_cost_after_failure(outcome, request_object):
    usage = Usage(input_tokens=1000, output_tokens=200)

    class Backend:
        def generate(self, p, r):
            if outcome in {"provider", "transport"}:
                error = ProviderError("synthetic failure")
                if outcome == "provider":
                    error.metadata = ResponseMetadata(response_model="returned-model", usage=usage)
                raise error
            return ProviderResponse(
                {"summary": "ok" if outcome == "success" else 4}, response_model="returned-model", usage=usage
            )

    records = []
    with Runtime(profile(), backend=Backend(), observer=records.append) as runtime:
        if outcome == "success":
            record = runtime.generate(request_object).record
        else:
            from tkn_genai_bridge import GenAIError

            with pytest.raises(GenAIError) as exc:
                runtime.generate(request_object)
            record = exc.value.record
    assert records == [record]
    assert record.cost_estimate.basis == "reported"
    assert record.cost_estimate.pricing_model == "deployment"
    assert record.cost_estimate.amount == (None if outcome == "transport" else pytest.approx(0.066))
    assert record.cost_estimate.usage == record.usage
    old = record.model_dump(exclude={"cost_estimate"})
    assert GenerationRecord.model_validate(old).cost_estimate is None


def test_default_cli_model_uses_only_exact_reported_model(request_object):
    class Backend:
        def generate(self, p, r):
            return ProviderResponse(
                {"summary": "ok"}, response_model="actual", usage=Usage(input_tokens=1000, output_tokens=200)
            )

    p = Profile(pricing={"actual": price()})
    with Runtime(p, backend=Backend()) as runtime:
        assert runtime.plan(request_object, output_tokens=200).cost_estimate.amount is None
        record = runtime.generate(request_object).record
    assert record.cost_estimate.pricing_model == "actual"
    assert record.cost_estimate.amount == pytest.approx(0.066)


def test_claude_counts_keep_reported_scope_and_cache_writes():
    metadata = _claude_metadata(
        {
            "usage": {
                "input_tokens": 300,
                "cache_read_input_tokens": 600,
                "cache_creation_input_tokens": 100,
                "output_tokens": 200,
            }
        }
    )
    assert metadata.usage.input_tokens == 300
    assert metadata.usage.input_tokens_scope == "uncached"
    assert metadata.usage.cache_write_tokens == 100
    assert estimate_cost(metadata.usage, price()).amount == pytest.approx(0.066)
    missing = _claude_metadata({"usage": {"input_tokens": 300, "output_tokens": 200}})
    assert estimate_cost(missing.usage, price()).amount is None


def test_api_and_codex_cache_writes_are_retained():
    response = provider_response(
        {
            "model": "reported",
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 40},
            },
        },
        local=False,
    )
    assert response.usage.cache_write_tokens == 40
    assert response.usage.input_tokens_scope == "total"
    _, usage = _codex_metadata(json.dumps({"type": "turn.completed", "usage": {"cache_write_tokens": 40}}))
    assert usage.cache_write_tokens == 40


def config_file(tmp_path, rates=None):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.1.0",
                "default_profile": "p",
                "profiles": {
                    "p": {
                        "provider": "ollama",
                        "model": "m",
                        "max_output_tokens": 100,
                        "pricing": {"m": rates or price().model_dump()},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_pricing_layer_override_provenance_and_generation_hash(tmp_path, request_object):
    path = config_file(tmp_path)
    before = path.read_bytes()
    base = load_profile(config_file=path)
    changed = load_profile(config_file=path, overrides={"pricing": {"m": {"input_per_million": 99}}})
    assert changed.pricing["m"].output_per_million == 180
    assert changed.pricing["m"].input_per_million == 99
    assert Runtime(base).plan(request_object).generation_settings_sha256 == (
        Runtime(changed).plan(request_object).generation_settings_sha256
    )
    resolved = load_config(
        config_file=path, overrides={"profiles": {"p": {"pricing": {"m": {"input_per_million": 99}}}}}
    )
    assert resolved.field_sources["profiles.p.pricing.m.input_per_million"] == "options"
    assert resolved.field_sources["profiles.p.pricing.m.output_per_million"] == "explicit"
    assert load_profile(config_file=path, overrides={"model": "other"}).pricing.get("other") is None
    assert before == path.read_bytes()


@pytest.mark.parametrize(
    "updates",
    [
        {"input_per_million": -1},
        {"pricing_date": "2026-02-30"},
        {"currency": "xxx"},
        {"unknown": "PRIVATE"},
        {"output_per_million": "2"},
    ],
)
def test_invalid_pricing_layer_cannot_be_hidden_by_valid_override(tmp_path, updates):
    path = config_file(tmp_path, {**price().model_dump(), **updates})
    with pytest.raises(ConfigError) as exc:
        load_config(config_file=path, overrides={"profiles": {"p": {"pricing": {"m": price().model_dump()}}}})
    assert "PRIVATE" not in str(exc.value)


def test_cli_estimate_and_show_are_read_only_json(tmp_path, capsys):
    path = config_file(tmp_path)
    prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
    prompt.write_text("PRIVATE prompt", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")
    args = ["generate", "--config", str(path), "--prompt-file", str(prompt), "--schema-file", str(schema)]
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    assert main([*args, "--dry-run", "--estimate-output-tokens", "20"]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["token_estimate"]["output_tokens"] == 20
    assert result["cost_estimate"]["amount"] > 0
    assert "PRIVATE" not in output.out + output.err
    assert main(["config", "show", "--config", str(path)]) == 0
    assert (
        json.loads(capsys.readouterr().out)["settings"]["profiles"]["p"]["pricing"]["m"]["currency"] == "JPY"
    )
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}
    for tail in (["--estimate-output-tokens", "20"], ["--dry-run", "--estimate-output-tokens", "-1"]):
        with pytest.raises(SystemExit) as exc:
            main([*args, *tail])
        assert exc.value.code == 2


def test_fresh_plan_does_not_import_sdk_or_tokenizer(tmp_path):
    script = """
import socket, sys
from tkn_genai_bridge import Runtime, Profile, GenerationRequest
socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
Runtime(Profile()).plan(GenerationRequest(prompt='synthetic', output_schema={'type': 'object'}))
assert not any(m in sys.modules for m in ('litellm', 'tiktoken', 'openai', 'azure.identity'))
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=20
    )
    assert completed.returncode == 0, completed.stderr
