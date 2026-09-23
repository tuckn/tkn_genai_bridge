"""One synchronous structured-generation call, with a separate offline plan."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from types import TracebackType

from ._version import __version__
from .errors import GenAIError, ProviderError
from .models import GenerationPlan, GenerationRecord, GenerationRequest, GenerationResult, Profile, Usage
from .provenance import generation_settings_hash
from .providers.base import Backend, ProviderResponse
from .providers.cli import CliBackend, resolve_executable
from .providers.http import TokenProvider
from .providers.litellm import LiteLLMBackend
from .validation import fingerprints, prepare_validator, validate_output

Observer = Callable[[GenerationRecord], None]


class Runtime:
    def __init__(
        self,
        profile: Profile,
        *,
        observer: Observer | None = None,
        backend: Backend | None = None,
        token_provider: TokenProvider | None = None,
        profile_name: str | None = None,
    ) -> None:
        self.profile = Profile.model_validate(profile.model_dump())
        self.observer = observer
        self.backend = backend
        self.token_provider = token_provider
        self.profile_name = profile_name if profile_name is not None else profile.profile_name
        self._owned_backend: LiteLLMBackend | None = None
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderError("runtime is closed; create a new Runtime", code="runtime_closed")

    def close(self) -> None:
        """Release owned authentication resources. Injected backends remain caller-owned."""
        if not self._closed:
            self._closed = True
            if self._owned_backend is not None:
                self._owned_backend.close()

    def __enter__(self) -> Runtime:
        self._ensure_open()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def plan(self, request: GenerationRequest, *, check_executable: bool = False) -> GenerationPlan:
        self._ensure_open()
        prepare_validator(request)
        if check_executable and self.profile.provider in {"codex", "claude-code", "github-copilot"}:
            resolve_executable(self.profile)
        prompt_hash, schema_hash = fingerprints(request)
        return GenerationPlan(
            bridge_version=__version__,
            profile_name=self.profile_name,
            generation_settings_sha256=generation_settings_hash(self.profile, request),
            provider=self.profile.provider,
            model=self.profile.model,
            local_only=self.profile.local_only,
            prompt_characters=len(request.prompt),
            prompt_sha256=prompt_hash,
            schema_sha256=schema_hash,
            timeout_seconds=self.profile.timeout_seconds,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self._ensure_open()
        request = GenerationRequest.model_validate(deepcopy(request.model_dump()))
        validator = prepare_validator(request)
        prompt_hash, schema_hash = fingerprints(request)
        settings_hash = generation_settings_hash(self.profile, request)
        started_at = datetime.now(UTC).isoformat()
        started = time.monotonic()
        response: ProviderResponse | None = None
        error: GenAIError | None = None
        try:
            backend = self.backend
            if backend is None:
                if self.profile.provider in {"ollama", "azure-openai"}:
                    if self._owned_backend is None:
                        self._owned_backend = LiteLLMBackend(token_provider=self.token_provider)
                    backend = self._owned_backend
                else:
                    backend = CliBackend()
            response = backend.generate(self.profile, request)
            validate_output(response.data, validator)
        except GenAIError as exc:
            error = exc
        except OSError:
            error = ProviderError(
                "provider local I/O failed; check temporary directory permissions", code="local_io"
            )
        metadata = response if response is not None else error.metadata if error else None
        record = GenerationRecord(
            bridge_version=__version__,
            profile_name=self.profile_name,
            generation_settings_sha256=settings_hash,
            provider=self.profile.provider,
            requested_model=self.profile.model,
            response_model=metadata.response_model if metadata else None,
            started_at=started_at,
            duration_seconds=round(time.monotonic() - started, 6),
            status="failed" if error else "succeeded",
            error_code=error.code if error else None,
            prompt_sha256=prompt_hash,
            schema_sha256=schema_hash,
            usage=metadata.usage if metadata else Usage(),
        )
        if self.observer:
            try:
                self.observer(record)
            except Exception:
                logging.getLogger(__name__).warning("generation observer failed; result is preserved")
        if error:
            error.record = record
            raise error
        assert response is not None
        return GenerationResult(data=response.data, record=record)
