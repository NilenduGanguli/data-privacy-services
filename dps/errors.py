"""Errors that carry a stable machine code, rendered as ``{"detail", "code"}`` (contract §8.2 shape).

Framework-free: engine and layer modules raise :class:`CodedError`; ``dps.layers.api.common``
renders it. A message carries codes, counts, page numbers, limits and names — never a customer
value, key, token or response body.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


class CodedError(Exception):
    """An error with an HTTP-ish ``status`` and a stable snake_case ``code``."""

    status: int = 400

    def __init__(self, code: str, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        if status is not None:
            self.status = status


#: ``Idempotency-Key`` refusals shared by Layer 1 (M1, replay cache) and the job routes (M2,
#: durable): one code and one value-free detail each (contract §9). The key is never echoed.
IDEMPOTENCY_KEY_INVALID = "idempotency_key_invalid"
IDEMPOTENCY_KEY_INVALID_DETAIL = (
    "Idempotency-Key must be 1-128 characters of letters, digits and _ . : -"
)
IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
IDEMPOTENCY_KEY_REUSED_DETAIL = (
    "this Idempotency-Key was already used with a different request body"
)


# --- validation errors that never quote the request (security review S1) ------------------------
#: pydantic error types whose message quotes (part of) the input by construction.
_QUOTING_TYPES = frozenset(
    {
        "union_tag_invalid",
        "uuid_parsing",
        "uuid_type",
        "uuid_version",
        "get_attribute_error",
    }
)
#: Fixed messages used instead of a message that would quote the input.
_GENERIC = {
    "union_tag_invalid": "Input tag does not match any of the expected tags",
    "uuid_parsing": "Input should be a valid UUID",
    "uuid_type": "Input should be a valid UUID",
    "uuid_version": "Input should be a valid UUID of the expected version",
    "get_attribute_error": "Error extracting attribute",
    "value_error": "Value error",
    "assertion_error": "Assertion failed",
}
#: A location part kept as is: a field name, a JSON path or a list index. Anything else (a dict
#: key the caller chose, which could be a value) is shown as ``<key>``.
_LOC_PART = re.compile(r"[A-Za-z_$][A-Za-z0-9_.$\[\]*'\-]{0,127}")
#: Inputs shorter than this are not checked for being quoted (``en`` in a message is no echo).
_MIN_QUOTED = 4


def _scalar_texts(value: object) -> list[str]:
    """The scalar input as text (strings and numbers only; containers are not scanned)."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, str | int | float):
        text = str(value).strip()
        return [text] if len(text) >= _MIN_QUOTED else []
    return []


def safe_error_message(error: Mapping[str, Any]) -> str:
    """The error's message, or a fixed one when the message would quote the input."""
    kind = str(error.get("type", ""))
    msg = str(error.get("msg", ""))
    if kind in _QUOTING_TYPES or kind.startswith("url_"):
        return _GENERIC.get(kind, "Input should be a valid URL")
    if any(text in msg for text in _scalar_texts(error.get("input"))):
        return _GENERIC.get(kind, "Invalid value")
    return msg


def safe_error_loc(loc: Iterable[object]) -> list[str | int]:
    """Field names, paths and indexes only (see :data:`_LOC_PART`)."""
    out: list[str | int] = []
    for part in loc:
        if isinstance(part, int) and not isinstance(part, bool):
            out.append(part)
        elif isinstance(part, str) and _LOC_PART.fullmatch(part):
            out.append(part)
        else:
            out.append("<key>")
    return out


def safe_error_items(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``[{"loc", "msg", "type"}]`` — never the input, its context or a documentation URL."""
    return [
        {
            "loc": safe_error_loc(e.get("loc", ())),
            "msg": safe_error_message(e),
            "type": str(e.get("type", "")),
        }
        for e in errors
    ]


def safe_errors_text(errors: Iterable[Mapping[str, Any]], *, limit: int = 500) -> str:
    """``loc: msg; …`` from :func:`safe_error_items` (bounded to ``limit`` characters)."""
    parts = (
        f"{'.'.join(str(p) for p in item['loc']) or 'request'}: {item['msg']}"
        for item in safe_error_items(errors)
    )
    return "; ".join(parts)[:limit]
