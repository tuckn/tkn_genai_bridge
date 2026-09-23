"""Authentication, local preflight and validation of original API responses."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any

import httpx

from ..errors import ProviderError
from ..models import AzureSettings, ResponseMetadata, Usage
from ..validation import parse_object
from .base import ProviderResponse, model_name, number, preserve_metadata

TokenProvider = Callable[[], str]


def _close_credential(credential: Any) -> None:
    try:
        credential.close()
    except Exception:
        # Cleanup must not mask the result or leak SDK exception contents.
        logging.getLogger(__name__).warning("Azure credential cleanup failed")


def azure_headers(
    settings: AzureSettings,
    token_provider: TokenProvider | None,
    stack: ExitStack,
    *,
    credentials: dict[tuple[str, str | None], Any] | None = None,
) -> dict[str, str]:
    if settings.auth == "api_key":
        value = os.getenv(settings.api_key_env)
        if not value or not value.strip():
            raise ProviderError("Azure API key environment variable is unset", code="authentication")
        return {"api-key": value}
    if settings.auth == "token_provider":
        if token_provider is None:
            raise ProviderError(
                "azure.auth=token_provider requires a token_provider callback", code="authentication"
            )
        try:
            token = token_provider()
            if not isinstance(token, str) or not token.strip():
                raise ValueError("empty token")
        except Exception:
            raise ProviderError("Azure token provider failed", code="authentication") from None
        return {"Authorization": "Bearer " + token}
    try:
        from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential
    except ImportError:
        raise ProviderError(
            "Azure authentication dependency is missing; reinstall tkn-genai-bridge",
            code="missing_dependency",
        ) from None
    try:
        key = (settings.auth, settings.tenant_id)
        credential = credentials.get(key) if credentials is not None else None
        if credential is None:
            if settings.auth == "interactive_browser":
                kwargs = {"tenant_id": settings.tenant_id} if settings.tenant_id else {}
                credential = InteractiveBrowserCredential(**kwargs)
            else:
                credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            stack.callback(_close_credential, credential)
            if credentials is not None:
                credentials[key] = credential
        # Ask the credential each time so its cache/refresh policy handles token expiry.
        token = credential.get_token(settings.token_scope).token
        if not isinstance(token, str) or not token.strip():
            raise ValueError("empty token")
        return {"Authorization": "Bearer " + token}
    except Exception:
        raise ProviderError(
            "Azure authentication failed; verify login, tenant and access", code="authentication"
        ) from None


def post(
    client: httpx.Client, url: str, body: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    try:
        response = client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        raise ProviderError(
            "API timed out; submission and billing may be unknown",
            code="timeout",
            submission_unknown=True,
        ) from None
    except (httpx.TransportError, ValueError):
        raise ProviderError(
            "API transport failed; submission and billing may be unknown",
            code="transport",
            submission_unknown=True,
        ) from None
    if response.status_code != 200:
        raise ProviderError(
            f"API returned HTTP {response.status_code}",
            code=f"http_{response.status_code}",
            retryable=response.status_code in {429, 500, 502, 503, 504},
        )
    return parse_object(response.text)


def provider_response(payload: dict[str, Any], *, local: bool) -> ProviderResponse:
    """Preserve server-reported metadata and reject incomplete responses before SDK normalization."""
    if local:
        usage = Usage(
            input_tokens=number(payload.get("prompt_eval_count")),
            output_tokens=number(payload.get("eval_count")),
            cached_input_tokens=number(payload.get("prompt_eval_cached_count")),
        )
    else:
        counts = payload.get("usage") or {}
        if not isinstance(counts, dict):
            counts = {}
        details = counts.get("completion_tokens_details") or {}
        prompt_details = counts.get("prompt_tokens_details") or {}
        usage = Usage(
            input_tokens=number(counts.get("prompt_tokens")),
            output_tokens=number(counts.get("completion_tokens")),
            cached_input_tokens=number(prompt_details.get("cached_tokens"))
            if isinstance(prompt_details, dict)
            else None,
            reasoning_tokens=number(details.get("reasoning_tokens")) if isinstance(details, dict) else None,
            cache_write_tokens=number(prompt_details.get("cache_write_tokens"))
            if isinstance(prompt_details, dict)
            else None,
        )
    metadata = ResponseMetadata(response_model=model_name(payload.get("model")), usage=usage)
    with preserve_metadata(metadata):
        if local:
            if payload.get("done") is not True or payload.get("done_reason") != "stop":
                raise ProviderError("Ollama returned an incomplete response", code="incomplete_response")
            message = payload.get("message")
        else:
            choices = payload.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ProviderError("Azure returned an unexpected response shape", code="invalid_response")
            choice = choices[0]
            message = choice.get("message")
            if choice.get("finish_reason") != "stop":
                raise ProviderError("Azure returned an incomplete response", code="incomplete_response")
        if isinstance(message, dict) and (message.get("refusal") or message.get("tool_calls")):
            raise ProviderError("API returned a refusal or tool call", code="incomplete_response")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ProviderError("API response has no text content", code="invalid_response")
        return ProviderResponse(parse_object(message["content"]), metadata.response_model, usage)
