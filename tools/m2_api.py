"""A small client for the M2 job API, shared by ``bench_connector.py`` and ``demo_m2.py``.

Endpoints come from ``DPS_BASE_URL`` / ``DPS_TEST_APP_URL`` / ``DPS_APP_PORT`` (contract §12.2) and
the key from ``DPS_API_KEY`` (default: the compose stack's local demo key, which holds every scope).
Nothing here prints a body, a value or a key; failures raise :class:`ApiError` with the HTTP status
and the API's error *code*.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

#: In-network endpoints of the compose stack (the app and the worker resolve these names).
SOURCE_PG = {"host": "source-pg", "port": 5432, "database": "bank"}
#: The demo connections (contract §11; their secrets are written by the demo-secrets one-shot).
CONNECTIONS: dict[str, dict[str, Any]] = {
    "bank-pg-ro": {
        "engine": "postgres",
        "params": SOURCE_PG,
        "secret_ref": "dps/src/bank-pg-ro",
        "roles": ["source"],
        "tags": ["production"],
    },
    "bank-uat": {
        "engine": "postgres",
        "params": SOURCE_PG,
        "secret_ref": "dps/tgt/bank-uat",
        "roles": ["target"],
    },
    "bank-restore-rw": {
        "engine": "postgres",
        "params": SOURCE_PG,
        "secret_ref": "dps/src/bank-restore-rw",
        "roles": ["source", "target"],
    },
    "exports-s3": {
        "engine": "s3",
        "params": {"bucket": "dps-exports", "endpoint_url": "http://minio:9000"},
        "secret_ref": "dps/s3/exports",
        "roles": ["target"],
    },
}
TERMINAL = ("succeeded", "failed", "dead", "canceled")
#: In place the key is never rewritten and outputs must fit the existing columns.
IN_PLACE_REVIEW = [
    {"path": "$.customer_id", "method": {"method": "keep"}},
    {"path": "$.national_id", "method": {"method": "fpe_digits"}},
    {"path": "$.tax_id", "method": {"method": "mask_partial"}},
]


class ApiError(RuntimeError):
    """An API call that did not answer as expected (status and code only)."""

    def __init__(self, what: str, status: int, code: str | None) -> None:
        super().__init__(f"{what}: HTTP {status} {code or ''}".strip())
        self.status, self.code = status, code


def base_url() -> str:
    """The app's URL (``DPS_BASE_URL``, ``DPS_TEST_APP_URL``, else ``DPS_APP_PORT`` / 8400)."""
    return (
        os.environ.get("DPS_BASE_URL")
        or os.environ.get("DPS_TEST_APP_URL")
        or f"http://127.0.0.1:{os.environ.get('DPS_APP_PORT') or '8400'}"
    ).rstrip("/")


def api_key() -> str:
    """``DPS_API_KEY`` (or ``DPS_BOOTSTRAP_API_KEY``), else the compose stack's demo key."""
    return (
        os.environ.get("DPS_API_KEY")
        or os.environ.get("DPS_BOOTSTRAP_API_KEY")
        or "dps_local_dev_key_change_me"  # the compose stack's local demo literal
    )


def source_dsn() -> str:
    """The demo source's superuser DSN on the host (``DPS_TEST_SOURCE_PG_DSN`` / port variable)."""
    explicit = os.environ.get("DPS_TEST_SOURCE_PG_DSN")
    if explicit:
        return explicit
    port = os.environ.get("DPS_SOURCE_PG_PORT") or "5442"
    return f"postgresql://postgres:postgres@127.0.0.1:{port}/bank"  # local demo literal


class Client:
    """Thin JSON client over ``httpx`` (one key, one tenant: the key's default)."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self.http = httpx.Client(base_url=url or base_url(), timeout=60.0)
        self.headers = {"X-API-KEY": key or api_key()}

    def call(
        self, method: str, path: str, what: str, *, ok: tuple[int, ...] = (200,), **kw: Any
    ) -> Any:
        """One request; :class:`ApiError` unless the status is in ``ok``."""
        r = self.http.request(method, path, headers=self.headers, **kw)
        if r.status_code not in ok:
            code = None
            try:
                code = r.json().get("code")
            except ValueError:
                pass
            raise ApiError(what, r.status_code, code)
        return r.json() if r.content else None

    def ensure_connections(self, names: tuple[str, ...] = tuple(CONNECTIONS)) -> dict[str, str]:
        """``{name: id}``, creating the demo connections that are missing."""
        found: dict[str, str] = {}
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            page = self.call("GET", "/api/v1/connections", "list connections", params=params)
            found.update({item["name"]: item["id"] for item in page["items"]})
            cursor = page["next_cursor"]
            if not cursor:
                break
        for name in names:
            if name not in found:
                made = self.call(
                    "POST",
                    "/api/v1/connections",
                    f"create {name}",
                    ok=(201,),
                    json={"name": name, **CONNECTIONS[name]},
                )
                found[name] = made["id"]
        return {name: found[name] for name in names}

    def wait(self, job_id: str, timeout: float, *, every: float = 1.0) -> dict[str, Any]:
        """Poll ``GET /jobs/{id}`` until the job is terminal (``TimeoutError`` after ``timeout``)."""
        deadline = time.monotonic() + timeout
        while True:
            job: dict[str, Any] = self.call("GET", f"/api/v1/jobs/{job_id}", "job")
            if job["state"] in TERMINAL:
                return job
            if time.monotonic() >= deadline:
                raise TimeoutError(f"job {job_id} is still {job['state']}/{job['phase']}")
            time.sleep(every)

    def discover_and_accept(
        self,
        connection_id: str,
        owner: str,
        name: str,
        *,
        overrides: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
    ) -> str:
        """Discover one table, apply review overrides, accept; returns the snapshot id."""
        out = self.call(
            "POST",
            "/api/v1/jobs/discover",
            f"discover {owner}.{name}",
            ok=(202,),
            json={"connection_id": connection_id, "objects": [{"owner": owner, "name": name}]},
        )
        job = self.wait(out["job"]["id"], timeout)
        if job["state"] != "succeeded":
            raise ApiError(f"discover {owner}.{name} {job['state']}", 200, job.get("error_code"))
        snap_id = str(job["progress"]["snapshot_ids"][0])
        if overrides:
            self.call(
                "PATCH", f"/api/v1/snapshots/{snap_id}", "review", json={"overrides": overrides}
            )
        self.call("POST", f"/api/v1/snapshots/{snap_id}/accept", "accept")
        return snap_id

    def submit_run(self, spec: dict[str, Any]) -> dict[str, Any]:
        """``POST /jobs/run``; the answer holds the key material once (the caller decides)."""
        out: dict[str, Any] = self.call("POST", "/api/v1/jobs/run", "run", ok=(202,), json=spec)
        return out

    def count(self, connection_id: str, obj: str, selection: dict[str, Any] | None = None) -> int:
        """Rows a selection matches (``POST /connections/{id}/count``)."""
        out = self.call(
            "POST",
            f"/api/v1/connections/{connection_id}/count",
            "count",
            json={"object": obj, "selection": selection or {}},
        )
        return int(out["rows"])
