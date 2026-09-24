"""Stable fingerprints of explicit generation settings, excluding authentication."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import CLI_EXECUTABLES, GenerationRequest, OllamaSettings, Profile


def generation_settings_hash(profile: Profile, request: GenerationRequest) -> str:
    # Use an allowlist: future credential fields must not silently enter the hash.
    settings: dict[str, Any] = {
        "fingerprint_version": 1,
        "provider": profile.provider,
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
        "timeout_seconds": profile.timeout_seconds,
        "max_output_tokens": profile.max_output_tokens,
        "local_only": profile.local_only,
        "schema_name": request.schema_name,
    }
    if profile.provider == "azure-openai":
        assert profile.azure is not None
        settings["endpoint"] = profile.azure.endpoint
    elif profile.provider == "ollama":
        settings["ollama"] = (profile.ollama or OllamaSettings()).model_dump()
    else:
        settings["executable"] = (
            profile.cli.executable
            if profile.cli and profile.cli.executable
            else CLI_EXECUTABLES[profile.provider]
        )
    canonical = json.dumps(
        settings, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
