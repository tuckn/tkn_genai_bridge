"""Rejected output must still account for work reported by the provider."""

import json
import sys
from pathlib import Path

import httpx
import pytest

from tkn_genai_bridge import (
    AzureSettings,
    CliSettings,
    GenAIError,
    Profile,
    Runtime,
    __version__,
)
from tkn_genai_bridge.providers import cli
from tkn_genai_bridge.providers.litellm import LiteLLMBackend, load_sdk


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("failure", ["json", "length", "message", "refusal", "tools", "schema", "shape"])
def test_api_failure_retains_metadata(local, failure, request_object, monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "PRIVATE-KEY")
    message = {"content": '{"summary":"ok"}'}
    if failure == "json":
        message["content"] = "PRIVATE-BODY invalid json"
    elif failure == "schema":
        message["content"] = '{"summary":0}'
    elif failure == "refusal":
        message.update(content=None, refusal="PRIVATE-BODY")
    elif failure == "tools":
        message["tool_calls"] = [{"name": "PRIVATE-BODY"}]
    elif failure == "message":
        message = None
    if local:
        profile = Profile(provider="ollama", model="requested")
        payload = {
            "done": True,
            "done_reason": "length" if failure == "length" else "stop",
            "message": message,
            "model": "actual",
            "prompt_eval_count": 100,
            "eval_count": 20,
            "prompt_eval_cached_count": 4,
        }
        if failure == "shape":
            payload.pop("message")
    else:
        profile = Profile(
            provider="azure-openai",
            model="deployment",
            azure=AzureSettings(endpoint="https://example.openai.azure.com/openai/v1"),
        )
        payload = {
            "choices": [{"finish_reason": "length" if failure == "length" else "stop", "message": message}],
            "model": "actual",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 4},
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        }
        if failure == "shape":
            payload["choices"] = []
    calls, records = [], []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    backend = LiteLLMBackend(transport=httpx.MockTransport(handle))
    with Runtime(profile, backend=backend, observer=records.append) as runtime:
        with pytest.raises(GenAIError) as exc:
            runtime.generate(request_object)
    record = exc.value.record
    assert record.status == "failed"
    assert record.response_model == "actual"
    assert record.usage.input_tokens == 100
    assert record.usage.output_tokens == 20
    assert record.usage.cached_input_tokens == 4
    assert record.usage.reasoning_tokens == (None if local else 3)
    assert record.bridge_version == __version__
    assert len(record.generation_settings_sha256) == 64
    assert records == [record] and len(calls) == 1
    output = capsys.readouterr()
    assert "PRIVATE" not in record.model_dump_json() + str(exc.value) + output.out + output.err


@pytest.mark.parametrize("provider", ["codex", "claude-code"])
@pytest.mark.parametrize("failure", ["missing", "invalid"])
def test_cli_failure_retains_metadata(provider, failure, request_object, monkeypatch):
    def run(command, prompt, cwd, timeout, **kwargs):
        if provider == "codex":
            if failure == "invalid":
                Path(command[command.index("--output-last-message") + 1]).write_text(
                    "PRIVATE-BODY", encoding="utf-8"
                )
            return json.dumps(
                {
                    "type": "turn.completed",
                    "model": "actual",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                    },
                }
            )
        return json.dumps(
            {
                "is_error": failure == "invalid",
                "modelUsage": {"actual": {}},
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "result": "PRIVATE-BODY",
            }
        )

    monkeypatch.setattr(cli, "run_process", run)
    profile = Profile(provider=provider, cli=CliSettings(executable=sys.executable))
    with pytest.raises(GenAIError) as exc:
        Runtime(profile).generate(request_object)
    assert exc.value.record.response_model == "actual"
    assert exc.value.record.usage.input_tokens == 100
    assert exc.value.record.usage.output_tokens == 20
    assert "PRIVATE" not in str(exc.value) + exc.value.record.model_dump_json()


def test_sdk_failure_after_response_preserves_reported_usage(request_object, monkeypatch):
    sdk = load_sdk()
    original = sdk.completion

    def complete(**kwargs):
        original(**kwargs)
        raise ValueError("PRIVATE SDK DETAILS")

    monkeypatch.setattr(sdk, "completion", complete)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": '{"summary":"ok"}'},
                "model": "actual",
                "prompt_eval_count": 100,
                "eval_count": 20,
            },
        )
    )
    with pytest.raises(GenAIError) as exc:
        Runtime(Profile(provider="ollama", model="m"), backend=LiteLLMBackend(transport=transport)).generate(
            request_object
        )
    assert exc.value.code == "sdk_error"
    assert exc.value.record.response_model == "actual"
    assert exc.value.record.usage.output_tokens == 20
    assert "PRIVATE" not in str(exc.value)


def test_malformed_envelope_cannot_supply_metadata(request_object):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="PRIVATE invalid envelope"))
    with pytest.raises(GenAIError) as exc:
        Runtime(Profile(provider="ollama", model="m"), backend=LiteLLMBackend(transport=transport)).generate(
            request_object
        )
    assert exc.value.record.response_model is None
    assert all(
        value is None for value in exc.value.record.usage.model_dump(exclude={"input_tokens_scope"}).values()
    )


@pytest.mark.parametrize("provider", ["codex", "claude-code"])
def test_nonzero_process_exit_still_retains_reported_usage(provider, tmp_path):
    payload = {
        "type": "turn.completed",
        "model": "actual",
        "is_error": True,
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "result": "PRIVATE-BODY",
    }
    script = "import sys; print(" + repr(json.dumps(payload)) + "); sys.exit(1)"
    with pytest.raises(GenAIError) as exc:
        cli.run_process([sys.executable, "-c", script], "", tmp_path, 5, provider=provider)
    assert exc.value.code == "process_exit"
    assert exc.value.metadata.response_model == "actual"
    assert exc.value.metadata.usage.output_tokens == 20
    assert "PRIVATE" not in str(exc.value)
