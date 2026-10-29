"""CLI-only rendering; library results and stored/protocol JSON remain unchanged."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def text_value(value: Any) -> str:
    """Preserve full strings and path separators while escaping terminal controls."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
    escapes = {"\r": r"\r", "\n": r"\n", "\t": r"\t"}
    return "".join(
        escapes.get(char, f"\\u{ord(char):04x}")
        if ord(char) < 32 or 127 <= ord(char) <= 159 or char in "\u2028\u2029"
        else char
        for char in value
    )


def result_lines(value: Any, prefix: str = "") -> Iterator[str]:
    """Flatten the complete result without truncation, including mapping keys."""
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from result_lines(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from result_lines(item, f"{prefix}[{index}]")
    else:
        yield f"{text_value(prefix)}={text_value(value)}"


def emit(result: dict[str, Any], *, json_output: bool) -> None:
    """Serialize fully before writing so serialization errors cannot leave partial JSON."""
    display = (
        json.dumps(result, ensure_ascii=False, allow_nan=False)
        if json_output
        else "\n".join(result_lines(result))
    )
    print(display)
