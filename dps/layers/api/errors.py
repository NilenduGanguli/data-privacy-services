"""Value-free API errors for the M2 routes (contract §8.2): ``{"detail", "code", **extra}``.

Routers raise :class:`DpsHttpError` or let a domain exception reach :func:`to_http`, which maps
every error class of the job platform to a status and a stable code. Every branch keeps the
exception's own value-free message (domain errors never quote a value, key or credential);
anything unexpected becomes ``500 internal_error`` with only its class name logged.
:func:`install_error_handlers` renders :class:`DpsHttpError` and any ``HTTPException`` that carries
a ``code`` attribute (``dps.auth.CodedAuthHTTPException``; M1's coded exceptions use the same wire
shape), and leaves M0's plain ``{"detail"}`` responses unchanged. :func:`to_http` keeps the status and
code of M1's ``CodedError`` and of any coded ``HTTPException`` that reaches an M2 route, so an M1
error raised there is not turned into a 500 (contract §8.2, integration 2026-10-12).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from dps.audit import AuditUnavailable
from dps.auth import coded_http_exception_handler
from dps.connections import ConnectionStoreError
from dps.errors import CodedError
from dps.jobs import JobStateError, KeyMaterialRequired, QueueFull
from dps.jobs_models import SpecError
from dps.layers.connectors.base import ConnectorError
from dps.layers.connectors.selection import SelectionError
from dps.observability import get_logger
from dps.secrets import SealError, SecretUnavailable
from dps.snapshots import SnapshotStateError
from dps.storage import StorageError

logger = get_logger(__name__)
#: The fixed message of :func:`dps.jobs.decode_cursor` (every M2 list pages with it).
MALFORMED_CURSOR = "malformed cursor"


class DpsHttpError(Exception):
    """An HTTP error with a stable code; ``detail`` and ``extra`` are value-free."""

    def __init__(
        self, status: int, code: str, detail: str, extra: dict[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail
        self.extra: dict[str, Any] = dict(extra or {})
        self.headers: dict[str, str] = {}

    def body(self) -> dict[str, Any]:
        """The wire shape ``{"detail", "code", **extra}``."""
        return {"detail": self.detail, "code": self.code, **self.extra}


def _connector_status(code: str) -> int:
    if code.startswith("engine_") or code in (
        "object_not_found",
        "no_key",
        "connection_invalid",
        "tls_required",  # M5: TLS-only egress refuses a clear-text connection
        "host_forbidden",  # F-08: an SSRF target (link-local/metadata, loopback outside local …)
    ):
        return 422
    if code.startswith("selection_"):
        return 422
    return 502


def to_http(exc: Exception) -> DpsHttpError:
    """Map a domain exception to its HTTP status and code (value-free)."""
    if isinstance(exc, DpsHttpError):
        return exc
    if isinstance(exc, CodedError):  # M1's framework-free errors keep their status and code
        return DpsHttpError(exc.status, exc.code, exc.detail)
    code = getattr(exc, "code", None)
    if isinstance(exc, StarletteHTTPException) and isinstance(code, str):
        # M1's CodedHTTPException, dps.auth's CodedAuthHTTPException: the one wire shape (§8.2)
        err = DpsHttpError(exc.status_code, code, str(exc.detail))
        err.headers = dict(exc.headers or {})
        return err
    if isinstance(exc, SpecError):
        return DpsHttpError(exc.status, exc.code, exc.detail)
    if isinstance(exc, SelectionError):
        return DpsHttpError(422, exc.code, str(exc))
    if isinstance(exc, ConnectionStoreError | SnapshotStateError):
        return DpsHttpError(exc.status, exc.code, exc.detail)
    if isinstance(exc, JobStateError):
        return DpsHttpError(exc.status, exc.code, exc.detail)
    if isinstance(exc, QueueFull):
        return DpsHttpError(429, "queue_full", "the tenant's job queue is full")
    if isinstance(exc, KeyMaterialRequired):
        return DpsHttpError(
            409, "key_material_required", "resubmit consistency.key_material (or synthesis_seed)"
        )
    if isinstance(exc, SecretUnavailable | SealError):
        return DpsHttpError(
            409, "secret_unavailable", "the connection's credentials are unavailable or expired"
        )
    if isinstance(exc, ConnectorError):
        return DpsHttpError(_connector_status(exc.code), exc.code, exc.detail or exc.code)
    if isinstance(exc, StorageError):
        if exc.code == "storage_not_found":
            return DpsHttpError(404, exc.code, "the stored object does not exist")
        return DpsHttpError(502, exc.code, "object storage failed")
    if isinstance(exc, ValueError) and str(exc) == MALFORMED_CURSOR:  # dps.jobs.decode_cursor
        return DpsHttpError(400, "cursor_invalid", "the cursor is not one this API issued")
    if isinstance(exc, AuditUnavailable):
        return DpsHttpError(503, "audit_unavailable", "audit unavailable")
    logger.error("api_error", error=type(exc).__name__)
    return DpsHttpError(500, "internal_error", "internal error")


async def _dps_http_error(_: Request, exc: Exception) -> Response:
    err = exc if isinstance(exc, DpsHttpError) else to_http(exc)
    return JSONResponse(err.body(), status_code=err.status, headers=err.headers or None)


def install_error_handlers(app: FastAPI) -> None:
    """Render :class:`DpsHttpError` and coded ``HTTPException``s as ``{"detail", "code", …}``."""
    app.add_exception_handler(DpsHttpError, _dps_http_error)
    app.add_exception_handler(StarletteHTTPException, coded_http_exception_handler)
