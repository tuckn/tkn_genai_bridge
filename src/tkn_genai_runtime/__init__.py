"""Shared structured-generation API for Python applications."""

from .config import load_config, load_profile
from .errors import ConfigError, GenAIError, OutputValidationError, ProviderError, RequestError
from .models import (
    AzureSettings,
    CliSettings,
    GenerationPlan,
    GenerationRecord,
    GenerationRequest,
    GenerationResult,
    OllamaSettings,
    Profile,
    Usage,
)
from .runtime import Runtime

__version__ = "0.2.0"

__all__ = [
    "AzureSettings",
    "CliSettings",
    "ConfigError",
    "GenAIError",
    "GenerationPlan",
    "GenerationRecord",
    "GenerationRequest",
    "GenerationResult",
    "OllamaSettings",
    "OutputValidationError",
    "Profile",
    "ProviderError",
    "RequestError",
    "Runtime",
    "Usage",
    "load_config",
    "load_profile",
]
