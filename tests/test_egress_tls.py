"""TLS-only connections (M5 Task 16, spec §10): with ``DPS_EGRESS_TLS_REQUIRED`` a connection
that would travel in clear is refused (``tls_required``, 422) — at create, at update and at use."""

from __future__ import annotations

from typing import Any

import pytest

from dps.config import Settings
from dps.jobs_models import ConnectionParams, ConnectionSpec, SpecError
from dps.netguard import require_tls_connection

STRICT = Settings(_env_file=None, egress_tls_required=True)
LAX = Settings(_env_file=None, egress_tls_required=False)


def _spec(engine: str, **params: Any) -> ConnectionSpec:
    base: dict[str, Any] = {"host": "db.example", "database": "bank"}
    roles = {"source"}
    if engine == "s3":
        base, roles = {"bucket": "uat-exports"}, {"target"}
    elif engine == "pvc":
        base, roles = {"base_path": "exports"}, {"target"}
    elif engine == "oracle":
        base = {"host": "ora.example", "service_name": "FREEPDB1"}
    base.update(params)
    return ConnectionSpec(
        name="c1",
        engine=engine,  # type: ignore[arg-type]
        params=ConnectionParams(**base),
        secret_ref=None if engine == "pvc" else "dps/src/x",
        roles=roles,  # type: ignore[arg-type]
    )


CASES: list[tuple[ConnectionSpec, dict[str, str] | None, bool]] = [
    (_spec("postgres"), None, False),
    (_spec("postgres", options={"sslmode": "verify-full"}), None, True),
    (_spec("postgres", options={"sslmode": "require"}), None, False),
    (_spec("mongo", options={"tls": "true"}), None, True),
    (_spec("mongo"), None, False),
    (_spec("oracle", options={"protocol": "tcps"}), None, True),
    (_spec("oracle", options={"protocol": "tcp"}), None, False),
    (_spec("oracle"), {"dsn": "tcps://h:2484/s"}, True),
    (_spec("s3", endpoint_url="http://minio:9000"), None, False),
    (_spec("s3", endpoint_url="https://s3.example"), None, True),
    (_spec("s3"), None, True),
    (_spec("pvc"), None, True),
]


@pytest.mark.parametrize("spec,secret,ok", CASES)
def test_tls_required(spec: ConnectionSpec, secret: dict[str, str] | None, ok: bool) -> None:
    if ok:
        require_tls_connection(spec, STRICT, secret=secret)
    else:
        with pytest.raises(SpecError) as err:
            require_tls_connection(spec, STRICT, secret=secret)
        assert err.value.code == "tls_required" and err.value.status == 422
        assert "minio" not in err.value.detail and "db.example" not in err.value.detail
    require_tls_connection(spec, LAX, secret=secret)  # every case passes without the rule


def test_protocol_is_an_allowed_option() -> None:
    assert _spec("oracle", options={"protocol": "tcps"}).params.options == {"protocol": "tcps"}
    with pytest.raises(ValueError):
        _spec("oracle", options={"proto": "tcps"})


@pytest.mark.db
def test_connections_are_refused_at_create_update_and_use(db_settings, tmp_path) -> None:  # noqa: ANN001
    import asyncio

    from fastapi.testclient import TestClient

    from dps.app import create_app
    from dps.audit import MemoryAuditSink
    from tests.api.test_api_connections import FULL, JOBS, PG
    from tests.fixtures.db import _truncate, api_settings

    body = {
        "name": "clear-src",
        "engine": "postgres",
        "params": PG,
        "secret_ref": "dps/src/test-pg",
    }
    tls_params = {**PG, "options": {"sslmode": "verify-full"}}
    try:
        with TestClient(
            create_app(api_settings(db_settings, tmp_path), audit_sink=MemoryAuditSink())
        ) as lax:
            created = lax.post("/api/v1/connections", json=body, headers=JOBS)
            assert created.status_code == 201, created.text
            conn_id = created.json()["id"]
        strict = api_settings(db_settings, tmp_path, egress_tls_required=True)
        with TestClient(create_app(strict, audit_sink=MemoryAuditSink())) as c:
            refused = c.post("/api/v1/connections", json={**body, "name": "clear-2"}, headers=JOBS)
            assert (refused.status_code, refused.json()["code"]) == (422, "tls_required")
            patched = c.patch(f"/api/v1/connections/{conn_id}", json={"params": PG}, headers=JOBS)
            assert (patched.status_code, patched.json()["code"]) == (422, "tls_required")
            # WP-I (main SEC-3): on a production connection the admin rule answers first
            prod = c.post(
                "/api/v1/connections",
                json={**body, "name": "prod-src", "params": tls_params, "tags": ["production"]},
                headers=FULL,
            )
            assert prod.status_code == 201, prod.text
            by_jobs = c.patch(
                f"/api/v1/connections/{prod.json()['id']}", json={"params": PG}, headers=JOBS
            )
            assert (by_jobs.status_code, by_jobs.json()["code"]) == (403, "admin_scope_required")
            by_admin = c.patch(
                f"/api/v1/connections/{prod.json()['id']}", json={"params": PG}, headers=FULL
            )
            assert (by_admin.status_code, by_admin.json()["code"]) == (422, "tls_required")
            used = c.get(f"/api/v1/connections/{conn_id}/objects", headers=JOBS)
            assert (used.status_code, used.json()["code"]) == (422, "tls_required")
            tls = {**PG, "options": {"sslmode": "verify-full"}}
            ok = c.post(
                "/api/v1/connections", json={**body, "name": "tls-src", "params": tls}, headers=JOBS
            )
            assert ok.status_code == 201, ok.text
    finally:
        asyncio.run(_truncate(db_settings))
