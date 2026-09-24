"""HTTP retry hints are parsed, retained and returned, never automatically acted upon."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from tkn_genai_bridge import AzureSettings, Profile, ProviderError, Runtime
from tkn_genai_bridge.cli import main
from tkn_genai_bridge.providers.http import post, retry_after_seconds
from tkn_genai_bridge.providers.litellm import LiteLLMBackend


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("0", 0.0),
        (" 120 ", 120.0),
        ("1.5", None),
        ("-1", None),
        ("+1", None),
        ("NaN", None),
        ("inf", None),
        ("PRIVATE malformed", None),
        ("1, 2", None),
        ("9" * 200, None),
        ("Wed, 23 Sep 2026 10:00:30 GMT", 30.0),
        ("Wed, 23 Sep 2026 09:59:59 GMT", 0.0),
    ],
)
def test_retry_after_seconds_and_dates(value, expected):
    assert retry_after_seconds(value, now=datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)) == expected


@pytest.mark.parametrize("status", [301, 401, 429, 503])
@pytest.mark.parametrize("local", [True, False])
def test_generation_http_metadata_survives_sdk_wrapping(status, local, request_object, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "PRIVATE key")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Retry-After": "17", "X-Private": "PRIVATE secret"},
            json={"error": "PRIVATE body"},
        )

    p = (
        Profile(provider="ollama", model="synthetic")
        if local
        else Profile(
            provider="azure-openai",
            model="deployment",
            azure=AzureSettings(endpoint="https://example.test/openai/v1"),
        )
    )
    with Runtime(p, backend=LiteLLMBackend(transport=httpx.MockTransport(handle))) as runtime:
        with pytest.raises(ProviderError) as exc:
            runtime.generate(request_object)
    error = exc.value
    assert error.http_status == status and error.retry_after_seconds == 17
    assert error.retryable == (status in {429, 503})
    assert len(calls) == 1 and error.record.status == "failed"
    assert "PRIVATE" not in str(error) + error.record.model_dump_json()


def test_preflight_http_uses_same_retry_contract():
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(503, headers={"Retry-After": "7"}))
    ) as client:
        with pytest.raises(ProviderError) as exc:
            post(client, "http://localhost/api/show", {"model": "synthetic"})
    assert exc.value.http_status == 503 and exc.value.retry_after_seconds == 7


def test_missing_or_invalid_retry_hint_does_not_hide_status():
    for headers in ({}, {"Retry-After": "PRIVATE unknown"}):
        with httpx.Client(
            transport=httpx.MockTransport(lambda _, headers=headers: httpx.Response(429, headers=headers))
        ) as client:
            with pytest.raises(ProviderError) as exc:
                post(client, "http://localhost/api/show", {})
        assert exc.value.http_status == 429 and exc.value.retry_after_seconds is None


def test_non_http_errors_remain_unknown_and_backwards_compatible():
    error = ProviderError("timeout", submission_unknown=True)
    assert error.http_status is None and error.retry_after_seconds is None
    assert error.submission_unknown and not error.retryable


@pytest.mark.parametrize(
    "updates",
    [
        {"http_status": True},
        {"http_status": 99},
        {"http_status": 600},
        {"retry_after_seconds": -1},
        {"retry_after_seconds": float("inf")},
        {"retry_after_seconds": float("nan")},
        {"retry_after_seconds": True},
    ],
)
def test_invalid_error_metadata_is_rejected(updates):
    with pytest.raises(ValueError):
        ProviderError("synthetic", **updates)


def test_cli_json_exposes_safe_retry_metadata(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        'schema_version: "1.1.0"\ndefault_profile: p\nprofiles:\n  p:\n    provider: ollama\n    model: m\n',
        encoding="utf-8",
    )
    prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
    prompt.write_text("synthetic", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")

    def fail(*args):
        raise ProviderError(
            "synthetic", code="http_429", http_status=429, retry_after_seconds=12, retryable=True
        )

    monkeypatch.setattr(Runtime, "generate", fail)
    assert (
        main(
            ["generate", "--config", str(config), "--prompt-file", str(prompt), "--schema-file", str(schema)]
        )
        == 1
    )
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["http_status"] == 429 and error["retry_after_seconds"] == 12
    assert error["retryable"] and not error["submission_unknown"]
