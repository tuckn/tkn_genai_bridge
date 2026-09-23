"""Shared profiles with source tracking and non-destructive initialization."""

from __future__ import annotations

import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, get_args

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from .errors import ConfigError
from .models import Profile, RuntimeConfig

SCHEMA_VERSION = "1.0.0"


def config_template() -> str:
    return files("tkn_genai_bridge.resources").joinpath("config.example.yaml").read_text(encoding="utf-8")


def user_config_path() -> Path:
    return Path.home() / ".tkn" / "genai_bridge" / "config.yaml"


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ConfigError("YAML keys must be unique strings", code="invalid_config")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _version(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ConfigError('schema_version must be a quoted "MAJOR.MINOR.PATCH" string', code="invalid_config")
    major, minor, _patch = map(int, value.split("."))
    if major != 1 or minor > 0:
        raise ConfigError(
            "supported config schema: 1.0.x; migrate explicitly or update the package",
            code="unsupported_schema",
        )
    return value


def _fragment(value: dict[str, Any], model: type[BaseModel]) -> None:
    """Validate each layer before merge, including overwritten values and nested keys."""
    for key, item in value.items():
        if key not in model.model_fields:
            raise ConfigError("configuration contains an unknown key", code="invalid_config")
        if model is RuntimeConfig and key == "schema_version":
            _version(item)
            continue
        if model is RuntimeConfig and key == "profiles":
            if not isinstance(item, dict) or any(not isinstance(k, str) or not k.strip() for k in item):
                raise ConfigError("profiles must be a mapping of non-blank names", code="invalid_config")
            for profile in item.values():
                if not isinstance(profile, dict):
                    raise ConfigError("each profile must be a mapping", code="invalid_config")
                _fragment(profile, Profile)
            continue
        field = model.model_fields[key]
        annotation = field.rebuild_annotation()
        nested = next(
            (
                t
                for t in (field.annotation, *get_args(field.annotation))
                if isinstance(t, type) and issubclass(t, BaseModel)
            ),
            None,
        )
        if nested is not None and isinstance(item, dict):
            _fragment(item, nested)
        else:
            try:
                TypeAdapter(annotation).validate_python(item, strict=True)
            except (ValidationError, ValueError, TypeError):
                raise ConfigError(f"invalid configuration field: {key}", code="invalid_config") from None


def _read(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_UniqueLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError):
        raise ConfigError("cannot read configuration as UTF-8 YAML", code="invalid_config") from None
    if not isinstance(value, dict):
        raise ConfigError("configuration must be a mapping", code="invalid_config")
    _version(value.get("schema_version"))
    _fragment(value, RuntimeConfig)
    return value


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _leaves(value: dict[str, Any], prefix: str = "") -> list[str]:
    result: list[str] = []
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        result.extend(_leaves(item, path) if isinstance(item, dict) and item else [path])
    return result


@dataclass(frozen=True)
class ConfigSource:
    name: str
    path: str | None
    schema_version: str
    migrated: bool = False


@dataclass(frozen=True)
class ResolvedConfig:
    config: RuntimeConfig
    sources: tuple[ConfigSource, ...]
    field_sources: dict[str, str]

    def profile(self, name: str | None = None) -> Profile:
        selected = name if name is not None else self.config.default_profile
        try:
            profile = self.config.profiles[selected].model_copy(deep=True)
        except KeyError:
            raise ConfigError("profile was not found; inspect config show", code="unknown_profile") from None
        profile._profile_name = selected
        return profile


def load_config(
    *,
    config_file: Path | None = None,
    include_project: bool = False,
    cwd: Path | None = None,
    user_file: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> ResolvedConfig:
    """Library default: shared config + explicit file, excluding an application's CWD config."""
    current = (cwd or Path.cwd()).resolve()
    merged = RuntimeConfig().model_dump()
    sources = [ConfigSource("built-in", None, SCHEMA_VERSION)]
    winners = dict.fromkeys(_leaves(merged), "built-in")
    paths = [("user", user_file if user_file is not None else user_config_path(), False)]
    if include_project:
        paths.append(("project", current / ".tkn" / "config.yaml", False))
    if config_file is not None:
        paths.append(("explicit", config_file, True))
    for name, path, required in paths:
        path = path.expanduser()
        path = (current / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.exists():
            if required:
                raise ConfigError("explicit configuration file does not exist", code="missing_config")
            continue
        layer = _read(path)
        sources.append(ConfigSource(name, str(path), layer.pop("schema_version")))
        merged = _merge(merged, layer)
        for leaf in _leaves(layer):
            winners[leaf] = name
    if overrides:
        if "schema_version" in overrides:
            raise ConfigError("overrides cannot change schema_version", code="invalid_config")
        _fragment(overrides, RuntimeConfig)
        merged = _merge(merged, overrides)
        sources.append(ConfigSource("options", None, SCHEMA_VERSION))
        for leaf in _leaves(overrides):
            winners[leaf] = "options"
    merged["schema_version"] = SCHEMA_VERSION
    try:
        config = RuntimeConfig.model_validate(merged)
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors(include_input=False, include_url=False))
        raise ConfigError(f"invalid resolved configuration: {messages}", code="invalid_config") from None
    effective_winners: dict[str, str] = {}
    for leaf in _leaves(config.model_dump()):
        parent = leaf
        while parent not in winners and "." in parent:
            parent = parent.rsplit(".", 1)[0]
        effective_winners[leaf] = winners.get(parent, "built-in")
    return ResolvedConfig(config, tuple(sources), effective_winners)


def load_profile(
    name: str | None = None,
    *,
    config_file: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Profile:
    profile = load_config(config_file=config_file).profile(name)
    if not overrides:
        return profile
    _fragment(overrides, Profile)
    try:
        updated = Profile.model_validate(_merge(profile.model_dump(), overrides))
    except ValidationError:
        raise ConfigError("invalid profile overrides", code="invalid_config") from None
    updated._profile_name = profile.profile_name
    return updated


def initialize_config(path: Path | None = None, *, dry_run: bool = False) -> dict[str, str]:
    target = (path or user_config_path()).expanduser().resolve()
    content = config_template()
    if target.exists():
        try:
            if target.read_text(encoding="utf-8-sig") == content:
                return {"status": "unchanged", "path": str(target)}
        except (OSError, UnicodeError):
            pass
        raise ConfigError("existing configuration differs; it has been preserved", code="config_conflict")
    if dry_run:
        return {"status": "would_create", "path": str(target)}
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent, prefix=".genai-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)  # Atomic publish; never replace a concurrently created config.
    except FileExistsError:
        raise ConfigError(
            "configuration appeared during initialization; preserved", code="config_conflict"
        ) from None
    except OSError:
        raise ConfigError(
            "cannot create config; check permissions and hard-link support", code="config_io"
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"status": "created", "path": str(target)}
