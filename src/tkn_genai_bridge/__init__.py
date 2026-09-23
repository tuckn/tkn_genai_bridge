"""Shared structured-generation API for Python applications."""

from ._version import __version__
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
    ResponseMetadata,
    Usage,
)
from .runtime import Runtime

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
    "ResponseMetadata",
    "Runtime",
    "Usage",
    "__version__",
    "load_config",
    "load_profile",
]
