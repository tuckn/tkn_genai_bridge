"""Small transport interface, also usable by custom caller-owned adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import GenerationRequest, Profile, Usage


@dataclass
class ProviderResponse:
    data: dict[str, Any]
    response_model: str | None = None
    usage: Usage = field(default_factory=Usage)


class Backend(Protocol):
    def generate(self, profile: Profile, request: GenerationRequest) -> ProviderResponse: ...


def number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def model_name(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
