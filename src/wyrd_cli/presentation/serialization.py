"""Deterministic JSON conversion for presentation DTOs and errors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


def to_json_value(value: Any) -> Any:
    """Convert presentation-neutral values without changing domain ordering."""

    if isinstance(value, BaseModel):
        return to_json_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json_value(item) for item in value]
    return value


def dumps(value: Any) -> str:
    """Serialize one compact UTF-8 JSON value with lexicographic object keys."""

    return json.dumps(
        to_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def error_envelope(code: str, message: str, details: Mapping[str, Any] | None = None) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "details": dict(details or {}),
            "message": message,
        }
    }
