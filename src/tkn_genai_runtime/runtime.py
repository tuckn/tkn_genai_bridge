"""One synchronous structured-generation call, with a separate offline plan."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime

from .errors import GenAIError, ProviderError
from .models import GenerationPlan, GenerationRecord, GenerationRequest, GenerationResult, Profile
from .providers.base import Backend, ProviderResponse
from .providers.cli import CliBackend, resolve_executable
from .providers.http import HttpBackend, TokenProvider
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
    ) -> None:
        self.profile = Profile.model_validate(profile.model_dump())
        self.observer = observer
        self.backend = backend
        self.token_provider = token_provider

    def plan(self, request: GenerationRequest, *, check_executable: bool = False) -> GenerationPlan:
        prepare_validator(request)
        if check_executable and self.profile.provider in {"codex", "claude-code", "github-copilot"}:
            resolve_executable(self.profile)
        prompt_hash, schema_hash = fingerprints(request)
        return GenerationPlan(
            provider=self.profile.provider,
            model=self.profile.model,
            local_only=self.profile.local_only,
            prompt_characters=len(request.prompt),
            prompt_sha256=prompt_hash,
            schema_sha256=schema_hash,
            timeout_seconds=self.profile.timeout_seconds,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        request = GenerationRequest.model_validate(deepcopy(request.model_dump()))
        validator = prepare_validator(request)
        prompt_hash, schema_hash = fingerprints(request)
        started_at = datetime.now(UTC).isoformat()
        started = time.monotonic()
        response: ProviderResponse | None = None
        error: GenAIError | None = None
        try:
            backend = self.backend
            if backend is None:
                backend = (
                    HttpBackend(token_provider=self.token_provider)
                    if self.profile.provider in {"ollama", "azure-openai"}
                    else CliBackend()
                )
            response = backend.generate(self.profile, request)
            validate_output(response.data, validator)
        except GenAIError as exc:
            error = exc
        except OSError:
            error = ProviderError(
                "provider local I/O failed; check temporary directory permissions", code="local_io"
            )
        record = GenerationRecord(
            provider=self.profile.provider,
            requested_model=self.profile.model,
            response_model=response.response_model if response else None,
            started_at=started_at,
            duration_seconds=round(time.monotonic() - started, 6),
            status="failed" if error else "succeeded",
            error_code=error.code if error else None,
            prompt_sha256=prompt_hash,
            schema_sha256=schema_hash,
            **({"usage": response.usage} if response else {}),
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
