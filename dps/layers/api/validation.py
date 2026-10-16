"""422 responses that never echo request input (security review S1).

``{"detail": [{"loc", "msg", "type"}], "code": "invalid_request"}``: pydantic's ``input``,
``ctx`` and documentation ``url`` are dropped, a message that would quote the input
(``union_tag_invalid`` names the tag it got; a validator may quote its value) is replaced by a fixed
one, and a location part that is not a field name, path or index is shown as ``<key>``
(:mod:`dps.errors`). ``loc`` holds field names and list indexes; for dict-valued payloads
(``data``) pydantic does not descend into ``Any``, so customer keys never appear either.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from dps.errors import safe_error_items

INVALID_REQUEST = "invalid_request"


async def scrubbed(_: Request, exc: Exception) -> JSONResponse:
    """The value-free 422 body (module docstring)."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    return JSONResponse(
        {"detail": safe_error_items(errors), "code": INVALID_REQUEST}, status_code=422
    )


def install(app: FastAPI) -> None:
    """Register :func:`scrubbed` for every request-validation error of ``app``."""
    app.add_exception_handler(RequestValidationError, scrubbed)
