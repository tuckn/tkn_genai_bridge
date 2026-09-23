"""Small transport interface, also usable by custom caller-owned adapters."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import GenAIError
from ..models import GenerationRequest, Profile, ResponseMetadata, Usage


@dataclass
class ProviderResponse:
    data: dict[str, Any]
    response_model: str | None = None
    usage: Usage = field(default_factory=Usage)


class Backend(Protocol):
    def generate(self, profile: Profile, request: GenerationRequest) -> ProviderResponse: ...


@contextmanager
def preserve_metadata(metadata: ResponseMetadata) -> Iterator[None]:
    """Carry only typed metadata across output parsing and validation failures."""
    try:
        yield
    except GenAIError as exc:
        exc.metadata = metadata
        raise


def number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def model_name(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
