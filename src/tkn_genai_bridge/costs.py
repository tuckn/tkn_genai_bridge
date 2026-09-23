"""Offline token planning and reference costs; no pricing lookup or budget enforcement."""

from __future__ import annotations

import json
import math
from decimal import Decimal, localcontext

from .models import (
    CostBasis,
    CostEstimate,
    CostUnavailableReason,
    GenerationRequest,
    Profile,
    TokenEstimate,
    TokenPricing,
    Usage,
)
from .providers.cli import schema_prompt


def estimate_tokens(
    request: GenerationRequest, profile: Profile, *, output_tokens: int | None = None
) -> TokenEstimate:
    """Estimate the visible request envelope without SDKs, tokenizers, I/O writes or network.

    UTF-8 byte length plus a margin is deliberately coarse. Hidden provider prompts,
    tool turns and retries mean this is not a guaranteed bound on actual usage.
    """
    envelope = {
        "messages": [
            {
                "role": "user",
                "content": schema_prompt(request)
                if profile.provider in {"ollama", "github-copilot"}
                else request.prompt,
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "strict": True, "schema": request.output_schema},
        },
    }
    text = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return TokenEstimate(
        input_tokens=len(text.encode("utf-8")) + 512,
        output_tokens=output_tokens if output_tokens is not None else profile.max_output_tokens,
        output_tokens_source="caller"
        if output_tokens is not None
        else ("profile_limit" if profile.max_output_tokens is not None else "unknown"),
    )


def estimate_cost(
    usage: Usage,
    pricing: TokenPricing | None,
    *,
    basis: CostBasis = "scenario",
    pricing_model: str | None = None,
) -> CostEstimate:
    """Reprice reported or caller-supplied tokens. Missing data never becomes zero.

    Reasoning tokens are already included in output_tokens, so are not added again.
    no-cache prices all input at the ordinary rate; observed applies configured
    cache rates only when the necessary counts are available.
    """
    usage = Usage.model_validate(usage.model_dump())
    if pricing is not None:
        pricing = TokenPricing.model_validate(pricing.model_dump())

    def result(amount: float | None = None, reason: CostUnavailableReason | None = None) -> CostEstimate:
        return CostEstimate(
            status="unavailable" if reason else "estimated",
            amount=amount,
            currency=pricing.currency if pricing else None,
            basis=basis,
            unavailable_reason=reason,
            usage=usage,
            pricing=pricing,
            pricing_model=pricing_model,
        )

    if pricing is None:
        return result(reason="pricing_not_configured")
    if usage.input_tokens is None or usage.output_tokens is None:
        return result(reason="usage_missing")
    total_input = usage.input_tokens
    cached, writes = usage.cached_input_tokens, usage.cache_write_tokens
    if usage.input_tokens_scope == "uncached":
        if cached is None or writes is None:
            return result(reason="cache_usage_missing")
        total_input += cached + writes
    if (cached or 0) + (writes or 0) > total_input or (
        usage.reasoning_tokens is not None and usage.reasoning_tokens > usage.output_tokens
    ):
        return result(reason="inconsistent_usage")
    cached_count = write_count = 0
    if pricing.cache_policy == "observed":
        if cached is None or (pricing.cache_write_per_million is not None and writes is None):
            return result(reason="cache_usage_missing")
        cached_count = cached
        write_count = writes if pricing.cache_write_per_million is not None and writes is not None else 0
    # Decimal arithmetic avoids rounding each category or accumulating binary errors.
    with localcontext() as context:
        context.prec = 50
        amount = float(
            (
                Decimal(total_input - cached_count - write_count) * Decimal(str(pricing.input_per_million))
                + Decimal(cached_count) * Decimal(str(pricing.cached_input_per_million or 0))
                + Decimal(write_count) * Decimal(str(pricing.cache_write_per_million or 0))
                + Decimal(usage.output_tokens) * Decimal(str(pricing.output_per_million))
            )
            / Decimal(1_000_000)
        )
    if not math.isfinite(amount):
        return result(reason="non_finite_cost")
    return result(amount=amount)


def profile_cost(
    profile: Profile, usage: Usage, *, basis: CostBasis, response_model: str | None = None
) -> CostEstimate:
    # Azure deployment names are keys. Never substitute a different price on model override.
    model = profile.model if profile.model is not None else response_model
    return estimate_cost(
        usage,
        profile.pricing.get(model) if model is not None else None,
        basis=basis,
        pricing_model=model,
    )
