"""App wiring for M2 (offline): routes without a database, reserved tenants, CORS, nested SPA routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dps.app import FRONTEND_DIST, create_app
from dps.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(_env_file=None, auth_enabled=False, llm_provider="stub")
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_m2_routes_answer_503_without_a_database() -> None:
    with TestClient(create_app(_settings())) as c:
        for path in (
            "/api/v1/connections",
            "/api/v1/connections/c_1",
            "/api/v1/jobs",
            "/api/v1/jobs/stats",
            "/api/v1/snapshots",
            "/api/v1/job-groups",
            "/api/v1/evidence",
            "/api/v1/artefacts/a_1",
        ):
            r = c.get(path)
            assert r.status_code == 503 and r.json() == {
                "detail": "the metadata database is not configured",
                "code": "db_unavailable",
            }
        ready = c.get("/readyz").json()
        assert ready["ready"] is True  # api-core never depends on the DB
        assert ready["components"]["db"] == {"ok": False, "detail": "unconfigured"}
        assert ready["components"]["jobs"]["ok"] is False


def test_reserved_tenant_ids_are_refused_even_for_wildcard_keys() -> None:
    settings = _settings(
        auth_enabled=True,
        rate_limit_enabled=False,
        api_keys='[{"name": "full", "key": "dps_full", "tenant_ids": ["*"], "scopes": ["*"]}]',
    )
    with TestClient(create_app(settings)) as c:
        for tenant in ("_system", "_x", "-"):
            r = c.post(
                "/api/v1/detect",
                json={"text": "x"},
                headers={"X-API-KEY": "dps_full", "X-DPS-Tenant": tenant},
            )
            if tenant == "-":  # not reserved, just not an identifier: M0's 400 without a code
                assert r.status_code == 400 and "code" not in r.json()
                continue
            assert r.status_code == 403
            assert r.json() == {"detail": "tenant id is reserved", "code": "tenant_reserved"}
        ok = c.post(
            "/api/v1/detect",
            json={"text": "x"},
            headers={"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"},
        )
        assert ok.status_code == 200
        anon = c.post("/api/v1/detect", json={"text": "x"})
        assert anon.status_code == 401 and anon.json() == {"detail": "missing API key"}
        assert "ApiKey" in anon.headers["www-authenticate"]


def test_cors_allows_patch_delete_and_idempotency() -> None:
    with TestClient(create_app(_settings())) as c:
        r = c.options(
            "/api/v1/connections/c_1",
            headers={
                "Origin": "http://x",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "Idempotency-Key",
            },
        )
        assert r.status_code == 200 and "PATCH" in r.headers["access-control-allow-methods"]
        assert "DELETE" in r.headers["access-control-allow-methods"]
        assert "idempotency-key" in r.headers["access-control-allow-headers"].lower()


def test_nested_spa_routes_serve_relative_assets() -> None:
    assets = sorted((FRONTEND_DIST / "assets").glob("*.js"))
    if not assets:
        pytest.skip("frontend not built")
    with TestClient(create_app(_settings())) as c:
        r = c.get(f"/data-sources/jobs/assets/{assets[0].name}")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"]
        page = c.get("/data-sources/jobs/j_1")
        assert page.headers["content-type"].startswith("text/html")
        assert "content-security-policy" in page.headers
        nested_index = c.get("/data-sources/jobs/index.html")
        assert "content-security-policy" in nested_index.headers  # the page always has its CSP
        assert (
            c.get("/data-sources/../../pyproject.toml")
            .headers["content-type"]
            .startswith("text/html")
        )
        assert c.get("/api/v1/nothing-here").status_code == 404


def test_a_database_that_is_down_at_boot_leaves_api_core_ready() -> None:
    import socket

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = _settings(
        pg_host="127.0.0.1",
        pg_port=port,
        pg_password="unused-local",
        job_kek="A" * 43 + "=",
        migrations_mode="verify",
    )
    with TestClient(create_app(settings)) as c:
        ready = c.get("/readyz").json()
        assert ready["ready"] is True
        assert ready["components"]["db"]["ok"] is False
        assert ready["components"]["db"]["detail"] == "down"
        assert ready["components"]["jobs"] == {"ok": False, "detail": "db down"}
        r = c.get("/api/v1/connections")
        assert r.status_code == 503 and r.json() == {
            "detail": "the metadata database is unavailable",
            "code": "db_unavailable",
        }
        assert "unused-local" not in str(ready)
