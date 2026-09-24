"""Stable, content-free exceptions for callers and CLI diagnostics."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import GenerationRecord, ResponseMetadata


class GenAIError(RuntimeError):
    """Base exception; messages exclude prompts, responses and credentials."""

    def __init__(self, message: str, *, code: str = "runtime_error") -> None:
        super().__init__(message)
        self.code = code
        self.record: GenerationRecord | None = None
        self.metadata: ResponseMetadata | None = None


class ConfigError(GenAIError):
    """Invalid configuration or a protected configuration file."""


class RequestError(GenAIError):
    """An invalid request rejected before generation."""


class ProviderError(GenAIError):
    """Transport, authentication or incomplete response failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        submission_unknown: bool = False,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.retryable = retryable
        self.submission_unknown = submission_unknown
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError("http_status must be an HTTP status code")
        if retry_after_seconds is not None and (
            isinstance(retry_after_seconds, bool)
            or not isinstance(retry_after_seconds, int | float)
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be finite and non-negative")
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


class OutputValidationError(GenAIError):
    """Output is invalid JSON or does not match the requested schema."""
