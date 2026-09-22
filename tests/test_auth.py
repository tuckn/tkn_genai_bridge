from contextlib import ExitStack

import pytest

from tkn_genai_runtime import AzureSettings, ProviderError
from tkn_genai_runtime.providers.http import azure_headers


@pytest.mark.parametrize("mode", ["default_credential", "interactive_browser"])
def test_entra_auth_is_explicit_and_closes_credentials(mode, monkeypatch):
    identity = pytest.importorskip("azure.identity")
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
