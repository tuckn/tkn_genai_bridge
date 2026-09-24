"""Partial CLI streams must preserve evidence without inventing totals."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from tkn_genai_bridge import (
    CliSettings,
    Profile,
    ProviderError,
    Runtime,
    TokenCounts,
    TokenPricing,
    Usage,
    estimate_cost,
)
from tkn_genai_bridge.providers import cli


def stream(*events):
    return "\n".join(json.dumps(event) for event in events)


def completed(**counts):
    return {"type": "turn.completed", "usage": counts}


def prices():
    return TokenPricing(
        currency="JPY", pricing_date="2026-01-01", input_per_million=30, output_per_million=180
    )


def test_multiple_completed_turns_sum_disjoint_usage():
    _, usage = cli._codex_metadata(
        stream(
            {"type": "turn.started"},
            completed(input_tokens=100, output_tokens=10, cached_input_tokens=50),
            {"type": "turn.started"},
            completed(input_tokens=200, output_tokens=20, cached_input_tokens=100),
        )
    )
    assert usage.completeness == "complete"
    assert (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens) == (300, 30, 150)
    assert usage.known_subtotal.input_tokens == 300
    assert usage.reasoning_tokens is None
    assert estimate_cost(usage, prices()).amount == pytest.approx(0.0144)


@pytest.mark.parametrize(
    "tail",
    [
        {"type": "turn.failed"},
        {"type": "error"},
        {"type": "turn.started"},
    ],
)
def test_partial_stream_retains_known_zero_and_subtotal(tail):
    _, usage = cli._codex_metadata(stream(completed(input_tokens=100, output_tokens=0), tail))
    assert usage.completeness == "partial"
    assert usage.input_tokens is None and usage.output_tokens is None
    assert usage.known_subtotal.input_tokens == 100
    assert usage.known_subtotal.output_tokens == 0
    assert usage.known_subtotal.cached_input_tokens is None
    cost = estimate_cost(usage, prices())
    assert cost.amount is None and cost.unavailable_reason == "usage_incomplete"
    assert cost.usage.known_subtotal.input_tokens == 100


def test_missing_field_does_not_become_zero_or_a_total():
    _, usage = cli._codex_metadata(
        stream(
            {"type": "turn.started"},
            completed(input_tokens=100, output_tokens=10),
            {"type": "turn.started"},
            completed(input_tokens=200),
        )
    )
    assert usage.completeness == "complete"  # Stream coverage, not availability of every field.
    assert usage.input_tokens == 300
    assert usage.output_tokens is None
    assert usage.known_subtotal.output_tokens == 10
    assert estimate_cost(usage, prices()).unavailable_reason == "usage_missing"


def test_missing_usage_fragment_prevents_false_total():
    _, usage = cli._codex_metadata(
        stream(
            {"type": "turn.started"},
            completed(input_tokens=100, output_tokens=10),
            {"type": "turn.started"},
            {"type": "turn.completed"},
        )
    )
    assert usage.input_tokens is None and usage.output_tokens is None
    assert usage.known_subtotal.input_tokens == 100


@pytest.mark.parametrize("suffix", ['\n{"type":', "\n[1,2]", "\nPRIVATE malformed text"])
def test_truncated_or_malformed_stream_is_incomplete(suffix):
    _, usage = cli._codex_metadata(stream(completed(input_tokens=100, output_tokens=10)) + suffix)
    assert usage.completeness == "partial"
    assert usage.input_tokens is None and usage.known_subtotal.input_tokens == 100
    assert "PRIVATE" not in usage.model_dump_json()


def test_no_reported_counts_are_unknown_not_zero():
    for value in ("", stream({"type": "turn.failed"}), "PRIVATE invalid"):
        _, usage = cli._codex_metadata(value)
        assert usage.completeness == "unknown"
        assert usage.known_subtotal is None
        assert usage.input_tokens is None


def test_cache_write_alias_prefers_current_field_and_keeps_zero():
    _, usage = cli._codex_metadata(stream(completed(cache_write_input_tokens=0, cache_write_tokens=40)))
    assert usage.cache_write_tokens == 0


def test_usage_contract_validates_scope_and_disallows_partial_totals():
    with pytest.raises(ValidationError):
        Usage(completeness="partial", input_tokens=100)
    with pytest.raises(ValidationError):
        Usage(completeness="unknown", known_subtotal=TokenCounts(input_tokens=100))
    with pytest.raises(ValidationError):
        Usage(
            completeness="partial", known_subtotal=TokenCounts(input_tokens_scope="uncached", input_tokens=10)
        )
    with pytest.raises(ValidationError):
        Usage(input_tokens=10, known_subtotal=TokenCounts(input_tokens=11))
    legacy = Usage.model_validate({"input_tokens": 100, "output_tokens": 20})
    assert legacy.completeness == "complete" and legacy.known_subtotal is None
    assert Usage().completeness == "unknown"
    assert Usage.model_validate_json(legacy.model_dump_json()) == legacy


def test_runtime_failure_cost_and_observer_use_known_subtotal(request_object, monkeypatch):
    records = []

    def run(*args, **kwargs):
        error = ProviderError("synthetic failure", code="process_exit", submission_unknown=True)
        error.metadata = cli._process_metadata(stream(completed(input_tokens=100, output_tokens=10)), "codex")
        raise error

    monkeypatch.setattr(cli, "run_process", run)
    p = Profile(model="m", cli=CliSettings(executable=sys.executable), pricing={"m": prices()})
    with pytest.raises(ProviderError) as exc:
        Runtime(p, observer=records.append).generate(request_object)
    record = exc.value.record
    assert records == [record]
    assert record.status == "failed" and record.usage.completeness == "partial"
    assert record.cost_estimate.unavailable_reason == "usage_incomplete"
    assert record.cost_estimate.usage.known_subtotal.output_tokens == 10


def test_schema_failure_after_complete_stream_still_has_full_usage(request_object, monkeypatch):
    def run(command, *args, **kwargs):
        Path(command[command.index("--output-last-message") + 1]).write_text(
            '{"summary":0}', encoding="utf-8"
        )
        return stream(completed(input_tokens=100, output_tokens=10))

    monkeypatch.setattr(cli, "run_process", run)
    from tkn_genai_bridge import OutputValidationError

    p = Profile(model="m", cli=CliSettings(executable=sys.executable), pricing={"m": prices()})
    with pytest.raises(OutputValidationError) as exc:
        Runtime(p).generate(request_object)
    assert exc.value.record.usage.completeness == "complete"
    assert exc.value.record.cost_estimate.amount == pytest.approx(0.0048)


def test_timeout_retains_flushed_usage_without_double_counting(tmp_path):
    payload = stream(completed(input_tokens=123, output_tokens=4), {"type": "turn.started"})
    script = "import time; print(" + repr(payload) + ", flush=True); time.sleep(10)"
    with pytest.raises(ProviderError) as exc:
        cli.run_process([sys.executable, "-u", "-c", script], "", tmp_path, 1.0, provider="codex")
    assert exc.value.code == "timeout" and exc.value.submission_unknown
    assert exc.value.metadata.usage.completeness == "partial"
    assert exc.value.metadata.usage.known_subtotal.input_tokens == 123
    assert exc.value.metadata.usage.input_tokens is None


def test_timeout_falls_back_to_exception_bytes_when_drain_has_no_output(tmp_path, monkeypatch):
    payload = stream(completed(input_tokens=123, output_tokens=4)).encode()

    class Process:
        def communicate(self, *args, **kwargs):
            raise subprocess.TimeoutExpired("synthetic", 1, output=payload)

    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(cli, "_stop_process", lambda *args: None)
    with pytest.raises(ProviderError) as exc:
        cli.run_process(["synthetic"], "", tmp_path, 1, provider="codex")
    assert exc.value.metadata.usage.known_subtotal.input_tokens == 123


def test_claude_error_envelope_is_distinct_from_interrupted_process():
    payload = {
        "is_error": True,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 30,
        },
    }
    whole = cli._claude_metadata(payload).usage
    partial = cli._claude_metadata(payload, interrupted=True).usage
    assert whole.completeness == "complete" and whole.input_tokens == 10
    assert partial.completeness == "partial" and partial.input_tokens is None
    assert partial.known_subtotal.input_tokens_scope == "uncached"
    assert partial.known_subtotal.cached_input_tokens == 100


@pytest.mark.parametrize("with_start", [True, False])
def test_repeated_completion_is_not_counted_twice(with_start):
    events = [{"type": "turn.started"}] if with_start else []
    events += [completed(input_tokens=100, output_tokens=10)] * 2
    _, usage = cli._codex_metadata(stream(*events))
    assert usage.completeness == "partial"
    assert usage.input_tokens is None
    assert usage.known_subtotal.input_tokens == 100
