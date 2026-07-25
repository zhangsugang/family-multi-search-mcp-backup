from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "conversation_url",
    "cdp_url",
    "profile_path",
    "storage_state",
    "verifier_digest",
    "salt",
}


def sanitize_public(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_public(item)
            for key, item in value.items()
            if str(key).lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_public(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if "127.0.0.1:93" in lowered or "127.0.0.1:955" in lowered:
            return "[redacted]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def validate_query(value: Any, *, maximum: int = 4000) -> str:
    if not isinstance(value, str):
        raise ValueError("query must be a string")
    query = value.strip()
    if not query:
        raise ValueError("query is required")
    if len(query) > maximum:
        raise ValueError("query is too long")
    return query


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("invalid integer value")
    if not minimum <= value <= maximum:
        raise ValueError("integer value out of range")
    return value
