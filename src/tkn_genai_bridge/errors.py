"""Stable, content-free exceptions for callers and CLI diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import GenerationRecord


class GenAIError(RuntimeError):
    """Base exception; messages exclude prompts, responses and credentials."""

    def __init__(self, message: str, *, code: str = "runtime_error") -> None:
        super().__init__(message)
        self.code = code
        self.record: GenerationRecord | None = None


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
    ) -> None:
        super().__init__(message, code=code)
        self.retryable = retryable
        self.submission_unknown = submission_unknown


class OutputValidationError(GenAIError):
    """Output is invalid JSON or does not match the requested schema."""
