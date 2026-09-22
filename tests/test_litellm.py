"""Exercise the real SDK with synthetic replies; never send a generation to a service."""

import json
import os
import subprocess
import sys
import textwrap

import httpx
import pytest

from tkn_genai_runtime import (
    AzureSettings,
    OllamaSettings,
    OutputValidationError,
    Profile,
    ProviderError,
    Runtime,
)
from tkn_genai_runtime.providers.litellm import LiteLLMBackend, load_sdk


def reply(local, content='{"summary":"ok"}', **updates):
    if local:
        return {"done": True, "done_reason": "stop", "message": {"content": content}, **updates}
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}], **updates}


def profile(local):
    if local:
        return Profile(provider="ollama", model="local/model:latest")
    return Profile(
        provider="azure-openai",
        model="deployment",
        azure=AzureSettings(endpoint="https://example.openai.azure.com/openai/v1"),
    )


@pytest.mark.parametrize("local", [True, False])
def test_real_sdk_called_and_missing_server_metadata_stays_unknown(local, request_object, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-key")
    sdk = load_sdk()
    original = sdk.completion
    calls, requests = [], []

    def complete(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == profile(local).model
        schema = body["format"] if local else body["response_format"]["json_schema"]["schema"]
        assert schema == request_object.output_schema
        return httpx.Response(200, json=reply(local))

    monkeypatch.setattr(sdk, "completion", complete)
    result = Runtime(profile(local), backend=LiteLLMBackend(transport=httpx.MockTransport(handle))).generate(
        request_object
    )
    assert len(calls) == len(requests) == 1
    assert calls[0]["num_retries"] == calls[0]["max_retries"] == 0
    assert calls[0]["caching"] is False and calls[0]["drop_params"] is False
    assert result.record.response_model is None
    assert all(value is None for value in result.record.usage.model_dump().values())


@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "transport")])
def test_transport_failure_is_safe_and_not_retried(local, error, code, request_object, monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-key")
    calls = []

    def handle(request):
        calls.append(request)
        raise error("PRIVATE transport details", request=request)

    with pytest.raises(ProviderError) as exc:
        Runtime(profile(local), backend=LiteLLMBackend(transport=httpx.MockTransport(handle))).generate(
            request_object
        )
    assert len(calls) == 1
    assert exc.value.code == code
    assert exc.value.submission_unknown is True
    output = capsys.readouterr()
    assert "PRIVATE" not in str(exc.value) + output.out + output.err


@pytest.mark.parametrize("update", [{"done": False}, {"done_reason": None}, {"done_reason": "length"}])
def test_sdk_must_not_normalize_incomplete_local_response_to_success(update, request_object):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=reply(True, **update)))
    with pytest.raises(ProviderError) as exc:
        Runtime(profile(True), backend=LiteLLMBackend(transport=transport)).generate(request_object)
    assert exc.value.code == "incomplete_response"


@pytest.mark.parametrize("think", [False, True, "low", "medium", "high", "max"])
def test_think_is_passed_without_reinterpretation(think, request_object):
    def handle(request):
        assert json.loads(request.content)["think"] == think
        return httpx.Response(200, json=reply(True))

    configured = profile(True).model_copy(update={"ollama": OllamaSettings(think=think)})
    Runtime(configured, backend=LiteLLMBackend(transport=httpx.MockTransport(handle))).generate(
        request_object
    )


@pytest.mark.parametrize("setting", ["model_fallbacks", "model_alias_map", "input_callback", "callbacks"])
def test_ambient_sdk_routing_and_callbacks_are_rejected(setting, request_object, monkeypatch):
    sdk = load_sdk()
    monkeypatch.setattr(sdk, setting, ["external"])

    def fail(_):
        pytest.fail("must not send a generation with unsafe global SDK settings")

    with pytest.raises(ProviderError) as exc:
        Runtime(profile(True), backend=LiteLLMBackend(transport=httpx.MockTransport(fail))).generate(
            request_object
        )
    assert exc.value.code == "sdk_configuration"


def test_sdk_schema_mutation_cannot_weaken_application_validation(request_object, monkeypatch):
    sdk = load_sdk()
    original = sdk.completion

    def complete(**kwargs):
        kwargs["response_format"]["json_schema"]["schema"]["properties"]["summary"].pop("minLength")
        return original(**kwargs)

    monkeypatch.setattr(sdk, "completion", complete)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=reply(True, '{"summary":""}')))
    with pytest.raises(OutputValidationError):
        Runtime(profile(True), backend=LiteLLMBackend(transport=transport)).generate(request_object)
    assert request_object.output_schema["properties"]["summary"]["minLength"] == 1


def test_fresh_process_plan_is_lazy_and_sdk_generation_is_offline(tmp_path):
    # A socket-level guard also catches SDK metadata/tokenizer/background traffic
    # that bypasses our injected transport. An empty home/cache prevents warm-cache success.
    program = textwrap.dedent("""
        import json, os, socket, sys, time, threading
        attempts = []
        state = threading.local()
        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
        original_pair = socket.socketpair
        def pair(*args, **kwargs):
            # Windows asyncio implements its internal wake-up pipe using sockets.
            state.creating_pair = True
            try:
                return original_pair(*args, **kwargs)
            finally:
                state.creating_pair = False
        def guarded_connect(original, *args, **kwargs):
            if getattr(state, "creating_pair", False):
                return original(*args, **kwargs)
            attempts.append(str(args[1:]))
            raise RuntimeError("network disabled")
        socket.socketpair = pair
        socket.socket.connect = lambda *a, **kw: guarded_connect(original_connect, *a, **kw)
        socket.socket.connect_ex = lambda *a, **kw: guarded_connect(original_connect_ex, *a, **kw)
        from tkn_genai_runtime import GenerationRequest, Runtime, Profile
        from tkn_genai_runtime.providers.litellm import LiteLLMBackend
        import httpx
        calls = []
        def handle(request):
            calls.append(request.url.path)
            if request.url.path == "/api/show":
                return httpx.Response(200, json={"model_info": {"general.architecture": "fixture"}})
            return httpx.Response(200, json={"done": True, "done_reason": "stop",
                "message": {"content": "{}"}})
        runtime = Runtime(Profile(provider="ollama", model="offline-fixture", local_only=True),
                          backend=LiteLLMBackend(transport=httpx.MockTransport(handle)))
        request = GenerationRequest(prompt="synthetic", output_schema={"type": "object"})
        assert not runtime.plan(request).will_call_provider
        assert "litellm" not in sys.modules and not calls
        assert runtime.generate(request).data == {}
        # no-log handlers may finish on an SDK worker thread.
        time.sleep(0.2)
        assert calls == ["/api/show", "/api/chat"], calls
        assert not attempts, attempts
        assert "SHOULD_NOT_LOAD_DOTENV" not in os.environ
        print(json.dumps({"ok": True}))
    """)
    (tmp_path / ".env").write_text("SHOULD_NOT_LOAD_DOTENV=true\n", encoding="utf-8")
    env = dict(
        os.environ,
        USERPROFILE=str(tmp_path / "home"),
        HOME=str(tmp_path / "home"),
        PYTHONUTF8="1",
        CUSTOM_TIKTOKEN_CACHE_DIR=str(tmp_path / "token-cache"),
        LITELLM_LOG="DEBUG",
        LITELLM_LOCAL_MODEL_COST_MAP="False",
        PYTHONDONTWRITEBYTECODE="1",
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True}
    assert not result.stderr
    assert not (tmp_path / "home").exists()
    assert not (tmp_path / "token-cache").exists()
