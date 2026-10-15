"""The HTTP client the operator and demo tools share (M5 Task 24).

    from demo_client import DpsClient, load_keys        # tools/ on sys.path (python tools/<tool>.py)
    from tools.demo_client import DpsClient, load_keys  # from the repository root (tests)

    c = DpsClient("http://127.0.0.1:8400", load_keys(Path("var/demo/demo-keys.json"))["demo-console"],
                  tenant="retail-banking-apac")
    c.get("/reports/summary", days=14)                  # paths are under /api/v1 unless they start
    job = c.post("/jobs/run", spec)["job"]              # with /api/ or name an ops route (/health,
    c.wait_job(job["id"])                               # /readyz, /metrics)

Every call sends ``X-API-KEY`` (and ``X-DPS-Tenant`` when a tenant is set). An answer with an
unexpected status raises :class:`DemoError`, whose message is the method, the **route template**
(ids, numbers and days replaced by ``{id}``, ``{n}``, ``{day}``), the status and the response's
error code — never a request or response body, a query string, a value or the key (contract §0
rule 1). A transport failure raises :class:`DemoError` with status 0 and code ``unreachable``.

:func:`load_keys` reads the demo key file ``tools/demo_seed.py`` writes (``var/demo/demo-keys.json``,
mode 0600, git-ignored): a JSON object mapping key names to raw keys (``{"demo-console": "dps_…",
…}``; a ``{"keys": {…}}`` wrapper is accepted). It refuses a file other users can read or write.
"""

from __future__ import annotations

import json
import re
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

API_PREFIX = "/api/v1"
#: Paths served outside the API prefix: the ops routes, the API itself and the console's demo files.
OUTSIDE_API = ("/health", "/readyz", "/metrics", "/api/", "/demo/", "/docs")
TERMINAL_STATES = frozenset({"succeeded", "failed", "dead", "canceled"})
POLL_SECONDS = 2.0
WAIT_KINDS = ("jobs", "scans")
#: Id-like segments: ``<prefix>_<26-char Crockford ULID>`` (contract §1 ``new_id``; matched
#: leniently, any long alphanumeric tail) and UUIDs; numbers and ISO days get their own placeholders.
_ID = re.compile(r"^[a-z][a-z0-9]{0,15}_[0-9A-Za-z]{20,}$|^[0-9a-fA-F-]{32,36}$")
_NUMBER = re.compile(r"^\d+$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: An error code is a value-free identifier (contract §8.2); anything else is dropped.
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class DemoError(RuntimeError):
    """A call that did not answer as expected. ``status`` is 0 when the stack was not reached;
    ``code`` is the response's error code when it sent one."""

    status: int
    code: str | None

    def __init__(self, method: str, path: str, status: int, code: str | None = None) -> None:
        self.method = method.upper()
        self.template = route_template(path)
        self.status = status
        self.code = code
        where = f"HTTP {status}" if status else "not reached"
        super().__init__(
            f"{self.method} {self.template} -> {where}" + (f" ({code})" if code else "")
        )


def route_template(path: str) -> str:
    """``path`` without scheme, host and query, with id-like segments replaced by placeholders."""
    parts = urlsplit(path)
    segments = []
    for seg in parts.path.split("/"):
        if _ID.match(seg):
            segments.append("{id}")
        elif _DAY.match(seg):
            segments.append("{day}")
        elif _NUMBER.match(seg):
            segments.append("{n}")
        else:
            segments.append(seg)
    return "/".join(segments) or "/"


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) and _CODE.match(code) else None


class DpsClient:
    """A small synchronous client for one DPS stack, one key and (optionally) one tenant."""

    #: Tests replace the transport (``class Fake(DpsClient): _transport = httpx.MockTransport(…)``).
    _transport: ClassVar[httpx.BaseTransport | None] = None

    def __init__(
        self, base_url: str, api_key: str, *, tenant: str | None = None, timeout: float = 60.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._tenant = tenant
        self._timeout = timeout
        headers = {"X-API-KEY": api_key}
        if tenant:
            headers["X-DPS-Tenant"] = tenant
        self._http = httpx.Client(
            base_url=self._base_url, headers=headers, timeout=timeout, transport=self._transport
        )

    def __repr__(self) -> str:
        tenant = f", tenant={self._tenant!r}" if self._tenant else ""
        return f"DpsClient({self._base_url!r}{tenant})"

    @property
    def base_url(self) -> str:
        """The stack this client talks to."""
        return self._base_url

    @property
    def tenant(self) -> str | None:
        """The ``X-DPS-Tenant`` this client sends (``None``: the key's default tenant)."""
        return self._tenant

    @staticmethod
    def url_path(path: str) -> str:
        """``path`` under ``/api/v1`` unless it already names the API, an ops route or a full URL."""
        if "://" in path or path == "/" or path.startswith(OUTSIDE_API):
            return path
        return API_PREFIX + (path if path.startswith("/") else "/" + path)

    def raw(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """Send one request and return the response whatever its status (``kw`` go to httpx)."""
        target = self.url_path(path)
        try:
            return self._http.request(method.upper(), target, **kw)
        except httpx.HTTPError:
            raise DemoError(method, target, 0, "unreachable") from None

    def _call(
        self, method: str, path: str, expect: int | tuple[int, ...], **kw: Any
    ) -> httpx.Response:
        response = self.raw(method, path, **kw)
        allowed = (expect,) if isinstance(expect, int) else expect
        if response.status_code not in allowed:
            raise DemoError(
                method, self.url_path(path), response.status_code, _error_code(response)
            )
        return response

    @staticmethod
    def _body(response: httpx.Response) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get(self, path: str, **params: Any) -> Any:
        """``GET`` → the JSON body (200 expected); ``params`` become the query string."""
        return self._body(self._call("GET", path, 200, params=params or None))

    def post(
        self, path: str, body: Any | None = None, *, expect: int | tuple[int, ...] = (200, 201, 202)
    ) -> Any:
        """``POST`` a JSON body (or none) → the JSON body."""
        kw: dict[str, Any] = {} if body is None else {"json": body}
        return self._body(self._call("POST", path, expect, **kw))

    def patch(self, path: str, body: Any) -> Any:
        """``PATCH`` a JSON body → the JSON body (200 expected)."""
        return self._body(self._call("PATCH", path, 200, json=body))

    def delete(self, path: str) -> Any:
        """``DELETE`` → the JSON body, ``None`` for 204."""
        return self._body(self._call("DELETE", path, (200, 204)))

    def download(self, path: str, **params: Any) -> bytes:
        """``GET`` a file (PDF, CSV, HTML, an artefact) → its bytes (200 expected)."""
        return self._call("GET", path, 200, params=params or None).content

    def wait_job(self, job_id: str, *, timeout: float = 900, kind: str = "jobs") -> dict[str, Any]:
        """Poll ``GET /{kind}/{id}`` every 2 s until the job is terminal and return it.

        ``kind="scans"`` reads a scan (``{scan, summary}``) and returns the scan with its
        ``summary`` key added, so ``["state"]`` works for both kinds.

        Raises:
            DemoError: an unexpected status, or code ``wait_timeout`` after ``timeout`` seconds.
            ValueError: ``kind`` is not ``jobs`` or ``scans``.
        """
        if kind not in WAIT_KINDS:
            raise ValueError(f"kind must be one of {WAIT_KINDS}")
        path = f"/{kind}/{job_id}"
        deadline = time.monotonic() + timeout
        while True:
            body = self.get(path)
            job: dict[str, Any] = body
            if kind == "scans" and isinstance(body, dict) and isinstance(body.get("scan"), dict):
                job = {**body["scan"], "summary": body.get("summary")}
            if job.get("state") in TERMINAL_STATES:
                return job
            if time.monotonic() >= deadline:
                raise DemoError("GET", self.url_path(path), 0, "wait_timeout")
            time.sleep(POLL_SECONDS)

    def as_tenant(self, tenant: str) -> DpsClient:
        """The same stack and key, another ``X-DPS-Tenant``."""
        return type(self)(self._base_url, self._api_key, tenant=tenant, timeout=self._timeout)

    def close(self) -> None:
        """Close the connection pool."""
        self._http.close()

    def __enter__(self) -> DpsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def load_keys(path: Path) -> dict[str, str]:
    """The demo keys ``{name: raw key}`` from ``path`` (``var/demo/demo-keys.json``).

    Raises:
        PermissionError: other users may read or write the file (it holds raw keys).
        ValueError: the file is not a JSON object of key names to keys. Messages name the file
            and the rule, never a key.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IROTH | stat.S_IWOTH):
        raise PermissionError(
            f"{path} is readable or writable by other users (mode {mode:04o}): chmod 600 it"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError(f"{path} is not a JSON document") from None
    keys = raw.get("keys", raw) if isinstance(raw, dict) else raw
    if not isinstance(keys, Mapping):
        raise ValueError(f"{path}: expected a JSON object of key names to keys")
    out: dict[str, str] = {}
    for name, key in keys.items():
        if not isinstance(name, str) or not name or not isinstance(key, str) or not key:
            raise ValueError(f"{path}: every entry must map a non-empty name to a key string")
        out[name] = key
    return out
