"""Strict JSON and schema validation without external reference retrieval."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from .errors import OutputValidationError, RequestError
from .models import GenerationRequest


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text.lstrip("\ufeff"), parse_constant=_reject_constant, object_pairs_hook=_unique_object
        )
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (ValueError, TypeError, RecursionError):
        raise OutputValidationError(
            "provider output is not one strict JSON object", code="invalid_json"
        ) from None


def _schema_nodes(schema: Any) -> list[dict[str, Any]]:
    """Walk schema positions only; fields named '$ref' and enum values are data."""
    if not isinstance(schema, dict):
        return []
    result = [schema]
    for key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
        for child in schema.get(key, {}).values():
            result.extend(_schema_nodes(child))
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(key, []):
            result.extend(_schema_nodes(child))
    for key in (
        "items",
        "contains",
        "additionalProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "not",
        "if",
        "then",
        "else",
        "propertyNames",
        "contentSchema",
    ):
        if key in schema:
            result.extend(_schema_nodes(schema[key]))
    return result


def prepare_validator(request: GenerationRequest) -> Draft202012Validator:
    schema = request.output_schema
    if not request.prompt.strip():
        raise RequestError("prompt must not be blank", code="invalid_request")
    if schema.get("type") != "object":
        raise RequestError("output_schema must have a root type of object", code="invalid_schema")
    try:
        json.dumps(schema, allow_nan=False)
        Draft202012Validator.check_schema(schema)
        nodes = _schema_nodes(schema)
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        registry: Registry[Any] = Registry()  # The default registry refuses remote retrieval.
        resolver = registry.resolver_with_root(resource)
        for node in nodes:
            if "$id" in node or "$dynamicRef" in node or "$dynamicAnchor" in node:
                raise ValueError("schema identifiers and dynamic references are unsupported")
            if "$schema" in node and node["$schema"] != "https://json-schema.org/draft/2020-12/schema":
                raise ValueError("only JSON Schema Draft 2020-12 is supported")
            if "$ref" in node:
                ref = node["$ref"]
                if not ref.startswith("#"):
                    raise ValueError("external references are not allowed")
                resolver.lookup(ref)
        return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    except (SchemaError, ValueError, TypeError, RecursionError, Unresolvable):
        raise RequestError(
            "invalid Draft 2020-12 schema or unsupported/unresolved reference", code="invalid_schema"
        ) from None


def validate_output(data: dict[str, Any], validator: Draft202012Validator) -> None:
    try:
        json.dumps(data, allow_nan=False)
        validator.validate(data)
    except (ValidationError, ValueError, TypeError, RecursionError, Unresolvable):
        raise OutputValidationError(
            "provider output does not satisfy the requested JSON Schema", code="schema_mismatch"
        ) from None


def fingerprints(request: GenerationRequest) -> tuple[str, str]:
    schema = json.dumps(
        request.output_schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return (hashlib.sha256(request.prompt.encode()).hexdigest(), hashlib.sha256(schema.encode()).hexdigest())
