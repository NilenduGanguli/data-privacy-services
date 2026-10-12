"""Errors that carry a stable machine code, rendered as ``{"detail", "code"}`` (contract §8.2 shape).

Framework-free: engine and layer modules raise :class:`CodedError`; ``dps.layers.api.common``
renders it. A message carries codes, counts, page numbers, limits and names — never a customer
value, key, token or response body.
"""

from __future__ import annotations


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
