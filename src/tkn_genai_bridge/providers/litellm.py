"""Lazy LiteLLM SDK adapter; the runtime owns routing and execution policy."""

from __future__ import annotations

import importlib
import logging
import os
from contextlib import ExitStack
from copy import deepcopy
from threading import Lock
from types import ModuleType
from typing import Any

import httpx

from ..errors import GenAIError, ProviderError
from ..images import message_content, validate_image_provider
from ..models import GenerationRequest, OllamaSettings, Profile, ResponseMetadata
from ..validation import parse_object
from .base import ProviderResponse
from .cli import schema_prompt
from .http import TokenProvider, azure_headers, http_error, post, provider_response

_lock = Lock()
_sdk: ModuleType | None = None


def load_sdk() -> ModuleType:
    """Initialize only for generation, using bundled metadata and quiet SDK logging.

    These settings are process-wide. Applications should let this package own
    LiteLLM initialization; external callbacks/fallbacks are not supported.
    """
    global _sdk
    with _lock:
        if _sdk is None:
            os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
            os.environ["LITELLM_MODE"] = "PRODUCTION"
            os.environ["LITELLM_LOG"] = "ERROR"
            # An empty custom cache could make the tokenizer download its data.
            os.environ.pop("CUSTOM_TIKTOKEN_CACHE_DIR", None)
            try:
                sdk = importlib.import_module("litellm")
            except ImportError:
                raise ProviderError("install LiteLLM dependencies", code="missing_dependency") from None
            for name, value in {
                "telemetry": False,
                "suppress_debug_info": True,
                "set_verbose": False,
                "turn_off_message_logging": True,
            }.items():
                setattr(sdk, name, value)
            for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
                logging.getLogger(name).disabled = True
            _sdk = sdk
        # Empty request fallbacks can inherit global fallbacks in LiteLLM.
        # Fail closed rather than silently changing another user's SDK settings.
        if any(
            getattr(_sdk, name, None)
            for name in (
                "model_fallbacks",
                "model_alias_map",
                "callbacks",
                "input_callback",
                "success_callback",
                "failure_callback",
                "_async_success_callback",
                "_async_failure_callback",
            )
        ):
            raise ProviderError(
                "LiteLLM global callbacks and fallbacks are incompatible with this runtime",
                code="sdk_configuration",
            )
        return _sdk


class _ResponseGuard:
    def __init__(self, *, local: bool) -> None:
        self.local = local
        self.response: ProviderResponse | None = None
        self.error: GenAIError | None = None

    def __call__(self, response: httpx.Response) -> None:
        try:
            if response.status_code != 200:
                raise http_error(response)
            response.read()
            self.response = provider_response(parse_object(response.text), local=self.local)
        except GenAIError as exc:
            # SDKs wrap hook exceptions. Retain only our content-free error.
            self.error = exc
            raise


class LiteLLMBackend:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self.transport = transport
        self.token_provider = token_provider
        self._auth_stack = ExitStack()
        self._credentials: dict[tuple[str, str | None], Any] = {}
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._auth_stack.close()
            self._credentials.clear()

    def generate(self, profile: Profile, request: GenerationRequest) -> ProviderResponse:
        if self._closed:
            raise ProviderError("backend is closed", code="runtime_closed")
        validate_image_provider(profile.provider, request.images)
        local = profile.provider == "ollama"
        timeout = httpx.Timeout(profile.timeout_seconds, connect=min(10, profile.timeout_seconds))
        guard = _ResponseGuard(local=local)
        with ExitStack() as stack:
            headers = (
                azure_headers(
                    profile.azure, self.token_provider, self._auth_stack, credentials=self._credentials
                )
                if profile.azure
                else {}
            )
            client = stack.enter_context(
                httpx.Client(
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=not local,
                    transport=self.transport,
                )
            )
            options = profile.ollama or OllamaSettings()
            if local and profile.local_only:
                info = post(client, options.base_url + "/api/show", {"model": profile.model})
                if (
                    info.get("remote_model")
                    or info.get("remote_host")
                    or not isinstance(info.get("model_info"), dict)
                    or not info["model_info"]
                ):
                    raise ProviderError("Ollama model could not be verified as local", code="local_only")
            sdk = load_sdk()
            client.event_hooks["response"].append(guard)
            parameters: dict[str, Any] = {
                "model": profile.model,
                "messages": [
                    {
                        "role": "user",
                        "content": message_content(
                            schema_prompt(request) if local else request.prompt, request.images
                        ),
                    }
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name,
                        "strict": True,
                        "schema": deepcopy(request.output_schema),
                    },
                },
                "stream": False,
                "timeout": timeout,
                "num_retries": 0,
                "max_retries": 0,
                "fallbacks": [],
                "drop_params": False,
                "caching": False,
                "no-log": True,
            }
            if profile.max_output_tokens is not None:
                parameters["max_completion_tokens"] = profile.max_output_tokens
            if local:
                # Unknown Ollama models otherwise trigger a second /api/show via
                # LiteLLM's global client during cost logging, outside our client.
                # Register local inference as zero-priced; public usage still
                # comes exclusively from the server, never SDK estimates.
                with _lock:
                    # register_model() itself probes unknown Ollama models, so
                    # seed the public metadata map directly without a lookup.
                    sdk.model_cost.setdefault(
                        f"ollama_chat/{profile.model}",
                        {
                            "litellm_provider": "ollama_chat",
                            "mode": "chat",
                            "input_cost_per_token": 0.0,
                            "output_cost_per_token": 0.0,
                        },
                    )
                handlers = importlib.import_module("litellm.llms.custom_httpx.http_handler")

                parameters.update(
                    custom_llm_provider="ollama_chat",
                    api_base=options.base_url,
                    client=handlers.HTTPHandler(client=client),
                    temperature=options.temperature,
                )
                if options.think is not None:
                    parameters["think"] = options.think
                if options.context_tokens is not None:
                    parameters["num_ctx"] = options.context_tokens
            else:
                from openai import OpenAI

                assert profile.azure and profile.azure.endpoint
                key = headers.get("api-key") or headers["Authorization"].removeprefix("Bearer ")
                # Azure's v1 surface uses the OpenAI client, not dated deployment URLs.
                azure_client = stack.enter_context(
                    OpenAI(
                        api_key=key,
                        base_url=profile.azure.endpoint,
                        default_headers=headers,
                        http_client=client,
                        max_retries=0,
                        timeout=timeout,
                    )
                )
                parameters.update(
                    custom_llm_provider="azure",
                    api_version="v1",
                    api_base=profile.azure.endpoint.removesuffix("/openai/v1"),
                    api_key=key,
                    client=azure_client,
                    store=False,
                    allowed_openai_params=["reasoning_effort"],
                )
                if profile.reasoning_effort is not None:
                    parameters["reasoning_effort"] = profile.reasoning_effort
            try:
                sdk.completion(**parameters)
            except Exception as exc:
                if guard.error is not None:
                    raise guard.error from None
                error: ProviderError
                if isinstance(exc, sdk.Timeout):
                    error = ProviderError(
                        "API timed out; submission and billing may be unknown",
                        code="timeout",
                        submission_unknown=True,
                    )
                elif isinstance(exc, sdk.APIConnectionError):
                    error = ProviderError(
                        "API transport failed; submission and billing may be unknown",
                        code="transport",
                        submission_unknown=True,
                    )
                else:
                    error = ProviderError("LiteLLM generation failed", code="sdk_error")
                if guard.response is not None:
                    error.metadata = ResponseMetadata(
                        response_model=guard.response.response_model, usage=guard.response.usage
                    )
                raise error from None
            if guard.error is not None:
                raise guard.error
            if guard.response is None:
                raise ProviderError("API returned no response", code="invalid_response")
            return guard.response
