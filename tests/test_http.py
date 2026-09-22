import json

import httpx
import pytest

from tkn_genai_runtime import AzureSettings, OllamaSettings, Profile, ProviderError, Runtime
from tkn_genai_runtime.providers.litellm import LiteLLMBackend


def azure(**kwargs):
    return Profile(
        provider="azure-openai",
        model="deployment",
        azure=AzureSettings(endpoint="https://example.openai.azure.com/openai/v1", **kwargs),
    )


def azure_reply(**kwargs):
    return {
        "model": "actual-model",
        "choices": [{"finish_reason": "stop", "message": {"content": '{"summary":"ok"}'}}],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 1},
        },
        **kwargs,
    }


def test_azure_payload_key_and_usage(request_object, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "private-test-key")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=azure_reply())

    profile = azure().model_copy(update={"max_output_tokens": 42, "reasoning_effort": "low"})
    result = Runtime(profile, backend=LiteLLMBackend(transport=httpx.MockTransport(handle))).generate(
        request_object
    )
    sent = json.loads(requests[0].content)
    assert sent["model"] == "deployment"
    assert sent["store"] is False and sent["stream"] is False
    assert sent["max_completion_tokens"] == 42
    assert sent["response_format"]["json_schema"]["schema"] == request_object.output_schema
    assert requests[0].headers["api-key"] == "private-test-key"
    assert result.record.response_model == "actual-model"
    assert result.record.usage.cached_input_tokens == 2
    assert "private-test-key" not in result.model_dump_json()


def test_token_callback_is_lazy_and_never_used_by_plan(request_object):
    tokens = []

    def token():
        tokens.append(1)
        return "test-token"

    def handle(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json=azure_reply())

    backend = LiteLLMBackend(transport=httpx.MockTransport(handle), token_provider=token)
    runtime = Runtime(azure(auth="token_provider"), backend=backend)
    runtime.plan(request_object)
    assert not tokens
    runtime.generate(request_object)
    assert len(tokens) == 1


@pytest.mark.parametrize("status", [301, 307, 401, 403, 429, 500])
def test_http_error_has_no_body_or_secret_and_no_retry(status, request_object, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key")
    calls = []

    def handle(request):
        calls.append(1)
        return httpx.Response(
            status, json={"error": "sensitive-body"}, headers={"location": "https://other.test"}
        )

    with pytest.raises(ProviderError) as exc:
        Runtime(azure(), backend=LiteLLMBackend(transport=httpx.MockTransport(handle))).generate(
            request_object
        )
    assert len(calls) == 1
    assert "sensitive-body" not in str(exc.value)
    assert exc.value.retryable == (status in {429, 500})


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"finish_reason": "length", "message": {"content": '{"summary":"ok"}'}}]},
        {"choices": [{"finish_reason": "stop", "message": {"refusal": "private", "content": None}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "{}", "tool_calls": [{}]}}]},
        {"choices": "unexpected"},
    ],
)
def test_incomplete_or_refused_azure_responses_fail(payload, request_object, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError):
        Runtime(azure(), backend=LiteLLMBackend(transport=transport)).generate(request_object)


def test_ollama_local_check_happens_before_sending_prompt(request_object):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"general.architecture": "test"}})
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "model": "local-model",
                "message": {"content": '{"summary":"local"}'},
                "prompt_eval_count": 12,
                "eval_count": 4,
            },
        )

    profile = Profile(
        provider="ollama",
        model="local-model",
        local_only=True,
        max_output_tokens=10,
        ollama=OllamaSettings(think=False, context_tokens=1000),
    )
    result = Runtime(profile, backend=LiteLLMBackend(transport=httpx.MockTransport(handle))).generate(
        request_object
    )
    assert [r.url.path for r in requests] == ["/api/show", "/api/chat"]
    assert request_object.prompt.encode() not in requests[0].content
    body = json.loads(requests[1].content)
    assert body["think"] is False
    assert body["options"]["num_predict"] == 10
    assert body["options"]["num_ctx"] == 1000
    assert result.record.usage.input_tokens == 12


@pytest.mark.parametrize(
    "info",
    [
        {"remote_host": "https://example.com", "model_info": {"x": 1}},
        {"remote_model": "cloud", "model_info": {"x": 1}},
        {},
        {"model_info": {}},
    ],
)
def test_local_only_fails_closed(request_object, info):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=info)

    profile = Profile(provider="ollama", model="alias", local_only=True)
    with pytest.raises(ProviderError, match="verified as local"):
        Runtime(profile, backend=LiteLLMBackend(transport=httpx.MockTransport(handle))).generate(
            request_object
        )
    assert len(calls) == 1 and calls[0].url.path == "/api/show"


def test_ollama_does_not_use_environment_proxy(request_object, monkeypatch):
    from tkn_genai_runtime.providers import litellm as http

    original = httpx.Client
    flags = []

    def client(**kwargs):
        flags.append(kwargs["trust_env"])
        return original(**kwargs)

    monkeypatch.setattr(http.httpx, "Client", client)
    monkeypatch.setenv("HTTP_PROXY", "http://must-not-connect.invalid")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": '{"summary":"ok"}'},
            },
        )
    )
    Runtime(Profile(provider="ollama", model="m"), backend=LiteLLMBackend(transport=transport)).generate(
        request_object
    )
    assert flags == [False]


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example",
        "http://localhost@remote.example",
        "http://localhost?key=private",
        "http://127.0.0.1/api/chat",
        "http://localhost:bad",
        "http://localhost:0",
    ],
)
def test_invalid_ollama_endpoints(url):
    with pytest.raises(ValueError):
        OllamaSettings(base_url=url)


def test_missing_key_does_not_submit(request_object, monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    def fail(request):
        pytest.fail("must not send unauthenticated request")

    with pytest.raises(ProviderError):
        Runtime(azure(), backend=LiteLLMBackend(transport=httpx.MockTransport(fail))).generate(request_object)
