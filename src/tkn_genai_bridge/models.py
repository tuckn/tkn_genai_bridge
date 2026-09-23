"""Validated public models; provider settings are deliberately explicit."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

Provider = Literal["codex", "claude-code", "github-copilot", "ollama", "azure-openai"]
PROVIDER_NAMES = {
    "codex": "Codex",
    "claude-code": "Claude Code",
    "github-copilot": "GitHub Copilot",
    "ollama": "Ollama",
    "azure-openai": "Azure OpenAI",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def endpoint_url(value: str, *, local: bool) -> str:
    normalized = value.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        raise ValueError("endpoint has an invalid host or port") from None
    if (
        not parsed.hostname
        or parsed.scheme not in ({"http", "https"} if local else {"https"})
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
        or any(char.isspace() for char in normalized)
        or "\\" in normalized
    ):
        raise ValueError("endpoint must be a credential-free URL without query or fragment")
    if local:
        if parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"} or parsed.path not in {
            "",
            "/",
        }:
            raise ValueError("Ollama endpoint must be a loopback origin without a path")
    elif not parsed.path.endswith("/openai/v1"):
        raise ValueError("Azure endpoint must end with /openai/v1")
    return normalized


class CliSettings(StrictModel):
    executable: str | None = None

    @field_validator("executable")
    @classmethod
    def executable_not_empty(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("executable must not be blank")
        return value


class OllamaSettings(StrictModel):
    base_url: str = "http://127.0.0.1:11434"
    think: bool | Literal["low", "medium", "high", "max"] | None = None
    context_tokens: int | None = Field(default=None, gt=0)
    temperature: float = Field(default=0.0, ge=0, le=2, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return endpoint_url(value, local=True)


class AzureSettings(StrictModel):
    endpoint: str | None = None
    auth: Literal["api_key", "default_credential", "interactive_browser", "token_provider"] = "api_key"
    api_key_env: str = "AZURE_OPENAI_API_KEY"
    tenant_id: str | None = None
    token_scope: str = "https://cognitiveservices.azure.com/.default"

    @field_validator("endpoint")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return endpoint_url(value, local=False) if value is not None else None

    @field_validator("api_key_env")
    @classmethod
    def validate_env_name(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("api_key_env must name an environment variable")
        return value

    @field_validator("token_scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        if not value.startswith("https://") or not value.endswith("/.default"):
            raise ValueError("token_scope must be an HTTPS .default scope")
        return value


class Profile(StrictModel):
    _profile_name: str | None = PrivateAttr(default=None)

    @property
    def profile_name(self) -> str | None:
        """Selected configuration name, independent of serialized profile settings."""
        return self._profile_name

    provider: Provider = "codex"
    model: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: float = Field(default=300.0, gt=0, le=86400, allow_inf_nan=False)
    max_output_tokens: int | None = Field(default=None, gt=0)
    local_only: bool = False
    cli: CliSettings | None = None
    ollama: OllamaSettings | None = None
    azure: AzureSettings | None = None

    @field_validator("model", "reasoning_effort")
    @classmethod
    def not_blank(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_capabilities(self) -> Profile:
        api = self.provider in {"ollama", "azure-openai"}
        if api and self.model is None:
            raise ValueError("API profiles require a model (Azure: deployment name)")
        if self.provider == "azure-openai" and (self.azure is None or self.azure.endpoint is None):
            raise ValueError("Azure profiles require azure.endpoint")
        if self.local_only and self.provider != "ollama":
            raise ValueError("local_only permits only a local Ollama provider")
        if self.azure is not None and self.provider != "azure-openai":
            raise ValueError("azure settings require the azure-openai provider")
        if self.ollama is not None and self.provider != "ollama":
            raise ValueError("ollama settings require the ollama provider")
        if self.cli is not None and api:
            raise ValueError("cli settings require a CLI provider")
        if self.max_output_tokens is not None and not api:
            raise ValueError("max_output_tokens is supported only by API providers")
        if self.provider == "ollama" and self.reasoning_effort is not None:
            raise ValueError("Ollama uses ollama.think; reasoning_effort is not translated")
        allowed = {
            "codex": {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"},
            "claude-code": {"low", "medium", "high", "xhigh", "max"},
            "github-copilot": {"low", "medium", "high", "xhigh", "max"},
            "azure-openai": {"none", "minimal", "low", "medium", "high", "xhigh"},
        }
        if self.reasoning_effort is not None and self.reasoning_effort not in allowed.get(
            self.provider, set()
        ):
            raise ValueError("reasoning_effort is unsupported by this provider")
        if self.provider == "ollama" and self.local_only and "cloud" in (self.model or "").casefold():
            raise ValueError("local_only rejects cloud model names")
        return self


class RuntimeConfig(StrictModel):
    schema_version: str = "1.0.0"
    default_profile: str = "codex-default"
    profiles: dict[str, Profile] = Field(default_factory=lambda: {"codex-default": Profile()})

    @model_validator(mode="after")
    def selected_profile_exists(self) -> RuntimeConfig:
        if self.default_profile not in self.profiles:
            raise ValueError("default_profile must identify a configured profile")
        if any(not name.strip() for name in self.profiles):
            raise ValueError("profile names must not be blank")
        return self


class GenerationRequest(StrictModel):
    prompt: str = Field(repr=False, min_length=1)
    output_schema: dict[str, Any] = Field(repr=False)
    schema_name: str = Field(default="generated_output", pattern=r"^[A-Za-z0-9_-]{1,64}$")


class Usage(StrictModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


class ResponseMetadata(StrictModel):
    """Server-reported information retained even when output cannot be accepted."""

    response_model: str | None = None
    usage: Usage = Field(default_factory=Usage)


class GenerationRecord(ResponseMetadata):
    provider: Provider
    requested_model: str | None
    bridge_version: str | None = None
    profile_name: str | None = None
    generation_settings_sha256: str | None = None
    started_at: str
    duration_seconds: float
    status: Literal["succeeded", "failed"]
    error_code: str | None = None
    prompt_sha256: str
    schema_sha256: str


class GenerationResult(StrictModel):
    data: dict[str, Any] = Field(repr=False)
    record: GenerationRecord


class GenerationPlan(StrictModel):
    bridge_version: str | None = None
    profile_name: str | None = None
    generation_settings_sha256: str | None = None
    provider: Provider
    model: str | None
    local_only: bool
    prompt_characters: int
    prompt_sha256: str
    schema_sha256: str
    timeout_seconds: float
    will_call_provider: bool = False
