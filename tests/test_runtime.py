import json

import pytest

from tkn_genai_bridge import (
    GenerationRequest,
    OutputValidationError,
    Profile,
    ProviderError,
    RequestError,
    Runtime,
    Usage,
)
from tkn_genai_bridge.providers.base import ProviderResponse
from tkn_genai_bridge.validation import parse_object


class StubBackend:
    def __init__(self, value=None):
        self.calls = 0
        self.value = value if value is not None else {"summary": "Hello"}

    def generate(self, profile, request):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return ProviderResponse(self.value, "actual-model", Usage(input_tokens=4, output_tokens=2))


def test_plan_does_not_invoke_or_write(tmp_path, request_object):
    backend = StubBackend()
    runtime = Runtime(Profile(), backend=backend)
    before = list(tmp_path.rglob("*"))
    plan = runtime.plan(request_object)
    assert plan.will_call_provider is False
    assert len(plan.prompt_sha256) == 64
    assert request_object.prompt not in plan.model_dump_json()
    assert backend.calls == 0
    assert list(tmp_path.rglob("*")) == before


def test_result_metadata_and_observer(request_object):
    records = []
    backend = StubBackend()
    result = Runtime(Profile(model="requested"), backend=backend, observer=records.append).generate(
        request_object
    )
    assert result.data == {"summary": "Hello"}
    assert result.record.requested_model == "requested"
    assert result.record.response_model == "actual-model"
    assert result.record.usage.input_tokens == 4
    assert result.record.started_at.endswith("+00:00")
    assert records == [result.record]
    assert backend.calls == 1
    assert request_object.prompt not in result.record.model_dump_json()


@pytest.mark.parametrize("data", [{"summary": ""}, {"summary": 1}, {"summary": "ok", "extra": True}, {}])
def test_schema_validation_preserves_usage_and_emits_failed_record(request_object, data):
    records = []
    with pytest.raises(OutputValidationError) as exc:
        Runtime(Profile(), backend=StubBackend(data), observer=records.append).generate(request_object)
    assert exc.value.record.status == "failed"
    assert exc.value.record.usage.output_tokens == 2
    assert records == [exc.value.record]
    assert json.dumps(data) not in str(exc.value)


def test_no_retry_or_fallback_on_timeout(request_object):
    backend = StubBackend(ProviderError("timed out", code="timeout", submission_unknown=True))
    with pytest.raises(ProviderError) as exc:
        Runtime(Profile(), backend=backend).generate(request_object)
    assert backend.calls == 1
    assert exc.value.submission_unknown
    assert exc.value.record.error_code == "timeout"


def test_observer_failure_does_not_lose_success(request_object):
    def fail(record):
        raise RuntimeError("private observer payload")

    result = Runtime(Profile(), backend=StubBackend(), observer=fail).generate(request_object)
    assert result.record.status == "succeeded"


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        "{} trailing",
        chr(96) * 3 + "json\n{}\n" + chr(96) * 3,
        '{"a":NaN}',
        '{"a":1,"a":2}',
        '{"a":Infinity}',
    ],
)
def test_strict_json(text):
    with pytest.raises(OutputValidationError):
        parse_object(text)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array"},
        {"type": "object", "properties": {"a": {"type": "not-a-type"}}},
        {"type": "object", "properties": {"a": {"$ref": "https://example.com/schema"}}},
        {"type": "object", "properties": {"a": {"$ref": "#/$defs/missing"}}},
        {"type": "object", "$id": "https://example.com/id"},
        {"type": "object", "$schema": "http://json-schema.org/draft-07/schema#"},
    ],
)
def test_bad_schema_fails_before_generation(schema):
    backend = StubBackend()
    with pytest.raises(RequestError):
        Runtime(Profile(), backend=backend).generate(GenerationRequest(prompt="test", output_schema=schema))
    assert backend.calls == 0


def test_local_references_and_property_names_are_valid():
    schema = {
        "type": "object",
        "properties": {"$ref": {"$ref": "#/$defs/text"}},
        "$defs": {"text": {"type": "string"}},
        "required": ["$ref"],
    }
    result = Runtime(Profile(), backend=StubBackend({"$ref": "literal"})).generate(
        GenerationRequest(prompt="test", output_schema=schema)
    )
    assert result.data == {"$ref": "literal"}


def test_unknown_metadata_stays_null(request_object):
    class NoMetadata:
        def generate(self, profile, request):
            return ProviderResponse({"summary": "ok"})

    record = Runtime(Profile(model="requested"), backend=NoMetadata()).generate(request_object).record
    assert record.response_model is None
    assert record.usage.input_tokens is None


def test_temporary_io_failure_is_normalized(request_object):
    backend = StubBackend(PermissionError("private directory path"))
    with pytest.raises(ProviderError) as exc:
        Runtime(Profile(), backend=backend).generate(request_object)
    assert exc.value.code == "local_io"
    assert exc.value.record.status == "failed"
    assert "private directory" not in str(exc.value)
