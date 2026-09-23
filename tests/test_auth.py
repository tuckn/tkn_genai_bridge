from contextlib import ExitStack

import httpx
import pytest

from tkn_genai_bridge import AzureSettings, Profile, ProviderError, Runtime
from tkn_genai_bridge.providers.http import azure_headers


@pytest.mark.parametrize("mode", ["default_credential", "interactive_browser"])
def test_entra_auth_is_explicit_and_closes_credentials(mode, monkeypatch):
    import azure.identity as identity

    calls = []

    class Credential:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def get_token(self, scope):
            calls.append(scope)
            return type("Token", (), {"token": "synthetic-token"})()

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(identity, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(identity, "InteractiveBrowserCredential", Credential)
    settings = AzureSettings(auth=mode, tenant_id="synthetic-tenant")
    with ExitStack() as stack:
        headers = azure_headers(settings, None, stack)
        assert headers == {"Authorization": "Bearer synthetic-token"}
    assert calls[-1] == "closed"
    if mode == "default_credential":
        assert calls[0] == {"exclude_interactive_browser_credential": True}
    else:
        assert calls[0] == {"tenant_id": "synthetic-tenant"}


def test_callback_failure_does_not_expose_secret():
    def fail():
        raise ValueError("PRIVATE TOKEN")

    with ExitStack() as stack, pytest.raises(ProviderError) as exc:
        azure_headers(AzureSettings(auth="token_provider"), fail, stack)
    assert "PRIVATE TOKEN" not in str(exc.value)


@pytest.mark.parametrize("mode", ["default_credential", "interactive_browser"])
@pytest.mark.parametrize("failure", [None, "authentication", "generation"])
def test_runtime_reuses_credential_and_closes_on_success_or_failure(
    mode, failure, request_object, monkeypatch
):
    import azure.identity as identity

    from tkn_genai_bridge import runtime as module
    from tkn_genai_bridge.providers.litellm import LiteLLMBackend

    created, tokens, closed, submitted = [], [], [], []

    class Credential:
        def __init__(self, **kwargs):
            created.append(self)

        def get_token(self, scope):
            tokens.append(scope)
            if failure == "authentication" and len(tokens) == 2:
                raise ValueError("PRIVATE AUTH DETAILS")
            return type("Token", (), {"token": f"synthetic-{len(tokens)}"})()

        def close(self):
            closed.append(self)

    def handle(request):
        submitted.append(request)
        assert request.headers["Authorization"] == f"Bearer synthetic-{len(tokens)}"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length"
                        if failure == "generation" and len(submitted) == 2
                        else "stop",
                        "message": {"content": '{"summary":"ok"}'},
                    }
                ],
            },
        )

    monkeypatch.setattr(identity, "DefaultAzureCredential", Credential)
    monkeypatch.setattr(identity, "InteractiveBrowserCredential", Credential)
    monkeypatch.setattr(
        module,
        "LiteLLMBackend",
        lambda **kwargs: LiteLLMBackend(transport=httpx.MockTransport(handle), **kwargs),
    )
    profile = Profile(
        provider="azure-openai",
        model="deployment",
        azure=AzureSettings(
            endpoint="https://example.openai.azure.com/openai/v1",
            auth=mode,
        ),
    )

    def generate():
        with Runtime(profile) as runtime:
            runtime.plan(request_object)
            assert not created and not tokens and not submitted
            runtime.generate(request_object)
            assert len(created) == 1 and not closed
            runtime.generate(request_object)
            assert len(created) == 1 and not closed
        runtime.close()  # Idempotent; cannot close the credential twice.
        with pytest.raises(ProviderError, match="runtime is closed"):
            runtime.generate(request_object)

    if failure:
        with pytest.raises(ProviderError) as exc:
            generate()
        assert "PRIVATE" not in str(exc.value)
    else:
        generate()
    assert len(tokens) == 2
    assert len(created) == 1 and closed == created
    assert len(submitted) == (1 if failure == "authentication" else 2)


def test_callback_tokens_are_refreshed_and_caller_keeps_ownership(request_object):
    from tkn_genai_bridge.providers.litellm import LiteLLMBackend

    class Tokens:
        calls = 0

        def __call__(self):
            self.calls += 1
            return f"token-{self.calls}"

        def close(self):
            pytest.fail("injected callback belongs to the caller")

    tokens = Tokens()

    def handle(request):
        assert request.headers["Authorization"] == f"Bearer token-{tokens.calls}"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"summary":"ok"}'}}],
            },
        )

    backend = LiteLLMBackend(transport=httpx.MockTransport(handle), token_provider=tokens)
    profile = Profile(
        provider="azure-openai",
        model="m",
        azure=AzureSettings(
            endpoint="https://example.openai.azure.com/openai/v1",
            auth="token_provider",
        ),
    )
    with Runtime(profile, backend=backend) as runtime:
        runtime.generate(request_object)
    # Runtime must not close an injected backend, which may be shared by callers.
    with Runtime(profile, backend=backend) as runtime:
        runtime.generate(request_object)
    backend.close()
    backend.close()
    assert tokens.calls == 2
    with pytest.raises(ProviderError, match="backend is closed"):
        backend.generate(profile, request_object)


def test_credential_cleanup_failure_does_not_mask_result_or_leak(monkeypatch, caplog):
    import azure.identity as identity

    class Credential:
        def __init__(self, **kwargs):
            pass

        def get_token(self, scope):
            return type("Token", (), {"token": "synthetic"})()

        def close(self):
            raise ValueError("PRIVATE CLEANUP DETAILS")

    monkeypatch.setattr(identity, "InteractiveBrowserCredential", Credential)
    with ExitStack() as stack:
        azure_headers(AzureSettings(auth="interactive_browser"), None, stack)
    assert "cleanup failed" in caplog.text
    assert "PRIVATE" not in caplog.text
