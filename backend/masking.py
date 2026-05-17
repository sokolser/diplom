

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "passwd",
    "pwd",
    "client_id",
    "authorization",
    "api_key",
    "apikey",
    "key",
)


def is_sensitive_key(key: str) -> bool:

    normalized = str(key or "").lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def mask_secret(value: Any, visible: int = 4) -> str:

    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if len(text) <= visible:
        return "*" * len(text)
    return "*" * max(len(text) - visible, 4) + text[-visible:]


def mask_mapping(data: Mapping[str, Any], visible: int = 4) -> dict[str, Any]:

    result: dict[str, Any] = {}
    for key, value in data.items():
        if is_sensitive_key(key):
            result[str(key)] = mask_secret(value, visible=visible)
        elif isinstance(value, Mapping):
            result[str(key)] = mask_mapping(value, visible=visible)
        else:
            result[str(key)] = value
    return result
