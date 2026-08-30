from __future__ import annotations

import json
from typing import Any


class StructuredOutputError(ValueError):
    pass


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"Model output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise StructuredOutputError("Model output must be a JSON object")
    return parsed


def require_fields(payload: dict[str, Any], fields: set[str]) -> None:
    missing = fields - set(payload)
    if missing:
        raise StructuredOutputError(f"Model output missing fields: {sorted(missing)}")
