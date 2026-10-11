"""Document refusals and failures with stable codes (rendered as {"detail", "code"}).

Codes used across M1 (status): ``unsupported_format`` 415 · ``empty_document`` 422 ·
``corrupt_document`` 422 · ``encrypted_pdf`` 422 · ``unsupported_encoding`` 422 ·
``csv_ragged_rows`` 422 · ``document_too_large`` 413 · ``too_many_pages`` 413 ·
``image_too_large`` 413 · ``text_too_long`` 413 · ``too_many_values`` 413 · ``invalid_request`` 422 ·
``redacted_pdf_not_allowed`` 422 · ``redaction_unsupported_format`` 422 · ``ocr_required`` 422 ·
``redaction_needs_ocr`` 422 · ``redaction_unlocated`` 422 · ``leakage`` 422 ·
``redaction_verify_failed`` 500 · ``ocr_unconfigured`` 503 · ``ocr_unavailable`` 503.

A message carries codes, counts, page numbers, limits and names — never a document's content.
"""

from __future__ import annotations

from dps.errors import CodedError


class DocumentError(CodedError):
    """A document the pipeline refuses (4xx) or cannot process right now (5xx)."""

    status = 422
