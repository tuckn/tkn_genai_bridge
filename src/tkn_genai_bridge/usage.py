"""Accounting for independently reported usage fragments, without filling missing data."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from .models import TokenCounts, Usage

TOKEN_FIELDS = tuple(name for name in TokenCounts.model_fields if name != "input_tokens_scope")


def summarize_usage(
    fragments: Sequence[TokenCounts], *, complete: bool, scope: Literal["total", "uncached"] = "total"
) -> Usage:
    """Sum disjoint fragments. A subtotal is per field, not necessarily a priceable whole."""
    known: dict[str, int | None] = {}
    totals: dict[str, int | None] = {}
    for field in TOKEN_FIELDS:
        values = [getattr(fragment, field) for fragment in fragments]
        reported = [value for value in values if value is not None]
        known[field] = sum(reported) if reported else None
        totals[field] = known[field] if complete and len(reported) == len(values) else None
    has_known = any(value is not None for value in known.values())
    return Usage(
        **totals,
        input_tokens_scope=scope,
        completeness="complete" if complete and fragments else "partial" if has_known else "unknown",
        known_subtotal=TokenCounts(**known, input_tokens_scope=scope) if has_known else None,
    )
