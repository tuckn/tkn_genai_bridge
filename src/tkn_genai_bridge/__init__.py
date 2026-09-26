"""Shared structured-generation API for Python applications."""

from ._version import __version__
from .config import load_config, load_profile
from .costs import estimate_cost, estimate_tokens
from .errors import ConfigError, GenAIError, OutputValidationError, ProviderError, RequestError
from .images import ImageInput, ImageMetadata
from .models import (
    AzureSettings,
    CliSettings,
    CostEstimate,
    GenerationPlan,
    GenerationRecord,
    GenerationRequest,
    GenerationResult,
    OllamaSettings,
    Profile,
    ResponseMetadata,
    TokenCounts,
    TokenEstimate,
    TokenPricing,
    Usage,
)
from .runtime import Runtime

__all__ = [
    "AzureSettings",
    "CliSettings",
    "ConfigError",
    "CostEstimate",
    "GenAIError",
    "GenerationPlan",
    "GenerationRecord",
    "GenerationRequest",
    "GenerationResult",
    "ImageInput",
    "ImageMetadata",
    "OllamaSettings",
    "OutputValidationError",
    "Profile",
    "ProviderError",
    "RequestError",
    "ResponseMetadata",
    "Runtime",
    "TokenCounts",
    "TokenEstimate",
    "TokenPricing",
    "Usage",
    "__version__",
    "estimate_cost",
    "estimate_tokens",
    "load_config",
    "load_profile",
]
