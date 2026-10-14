"""Task 18: the scans API and its app wiring (contract §9; scope ``scan``)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from dps.config import Settings
from tests.api.test_api_connections import FULL, JOBS

URL = "http://127.0.0.1:8410/apply?email=ananya.k%40example-mail.in"
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def _client(api_client: Any) -> Any:
    client, _sink = api_client
    return client


def _create(c: Any, body: dict[str, Any] | None = None, **headers: str) -> Any:
    return c.post("/api/v1/scans", json=body or {"url": URL}, headers={**FULL, **headers})


async def _connect(settings: Settings, tenant: str) -> asyncpg.Connection:
    """A connection of the app's login with the json codecs and the tenant bound (the app's own
    pools belong to the client's event loop)."""
    conn = await asyncpg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        user=settings.pg_user,
        password=settings.pg_password,
        database=settings.pg_database,
    )
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)
    return conn


def _sql(settings: Settings, tenant: str, query: str, *args: Any) -> Any:
    async def run() -> Any:
        conn = await _connect(settings, tenant)
        try:
            return await conn.fetchrow(query.replace("{s}", settings.pg_schema), *args)
        finally:
            await conn.close()

    return asyncio.run(run())


@pytest.mark.db
def test_create_returns_key_and_seed_once_and_stores_no_secrets(api_client: Any) -> None:
    c = _client(api_client)
    r = _create(c)
    assert r.status_code == 202, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    scan, key, seed = body["scan"], body["key_material"], body["synthesis_seed"]
    assert scan["id"].startswith("s_") and scan["kind"] == "scan" and scan["state"] == "queued"
    assert key["dek"] and seed["seed"] and body["replay"] is False
    settings = c.app.state.settings
    row = _sql(
        settings,
        "t1",
        "SELECT spec::text AS spec, key_enc IS NOT NULL AS sealed FROM {s}.dps_job WHERE id = $1",
        scan["id"],
    )
    assert row["sealed"] is True
    spec = row["spec"]
    assert "ananya" not in spec and key["dek"] not in spec and seed["seed"] not in spec
    assert "email=<EMAIL>" in spec and '"url_origin": "http://127.0.0.1:8410"' in spec
    got = c.get(f"/api/v1/scans/{scan['id']}", headers=FULL)
    assert got.status_code == 200
    text = got.text
    assert "key_material" not in text and key["dek"] not in text and seed["seed"] not in text
    assert "ananya" not in text
    summary = got.json()["summary"]
    assert summary["pages_visited"] == 0 and summary["non_get_allowed"] == 0
    assert summary["personas"] == 1 and summary["mode"] == "synthesize"
    listed = c.get("/api/v1/scans", headers=FULL).json()
    assert [s["id"] for s in listed["items"]] == [scan["id"]]
    # another tenant cannot see it
    other = c.get(f"/api/v1/scans/{scan['id']}", headers={**FULL, "X-DPS-Tenant": "t2"})
    assert other.status_code == 404 and other.json()["code"] == "scan_not_found"


@pytest.mark.db
def test_create_validation_and_scope(api_client: Any) -> None:
    c = _client(api_client)
    r = _create(c, {"url": "http://evil.example/apply"})
    assert (r.status_code, r.json()["code"]) == (422, "host_not_registered")
    assert "evil" not in r.text
    r = _create(c, {"url": URL, "submit": True})
    assert r.status_code == 422 and "ananya" not in r.text
    r = _create(c, {"url": URL, "fill": {"submit": True}})
    assert r.status_code == 422
    r = _create(c, {"url": URL, "capture": {"har": True}})
    assert (r.status_code, r.json()["code"]) == (422, "har_not_supported")
    r = _create(c, {"url": "http://127.0.0.1:8410/admin", "scope": {"deny": ["/admin"]}})
    assert (r.status_code, r.json()["code"]) == (422, "start_url_denied")
    r = _create(c, {"url": URL, "policy": "no-such-policy"})
    assert (r.status_code, r.json()["code"]) == (422, "policy_invalid")
    r = _create(c, {"url": URL, "consistency": {"key_material": {"alg": "x", "kid": "k"}}})
    assert (r.status_code, r.json()["code"]) == (422, "key_material_invalid")
    r = c.post("/api/v1/scans", json={"url": URL}, headers=JOBS)
    assert r.status_code == 403
    assert c.get("/api/v1/scans", headers=JOBS).status_code == 403


#: Bodies the ScanSpec model refuses before validate_scan runs: POST /scans still answers the
#: contract §9 code (value-free), mapped by the refused field's location.
MODEL_REFUSALS: list[tuple[dict[str, Any], str]] = [
    ({"url": "http:/"}, "url_invalid"),
    ({"url": URL, "scope": {"max_depth": 6}}, "scope_invalid"),
    ({"url": URL, "scope": {"max_pages": 0}}, "scope_invalid"),
    ({"url": URL, "scope": {"deny": ["no-slash"]}}, "scope_invalid"),
    ({"url": URL, "scope": {"allow": ["/a?b"]}}, "scope_invalid"),
    ({"url": URL, "auth": {"storage_state_ref": "dps/../other"}}, "auth_invalid"),
    (
        {"url": URL, "auth": {"storage_state_ref": "dps/ui/x", "storage_state": {"cookies": []}}},
        "auth_invalid",
    ),
    ({"url": URL, "fill": {"personas": 51}}, "fill_invalid"),
    ({"url": URL, "fill": {"personas": 0}}, "fill_invalid"),
    ({"url": URL, "fill": {"personas": 2, "display_persona": 3}}, "fill_invalid"),
    ({"url": URL, "fill": {"locale": "fr_FR"}}, "fill_invalid"),
    # two refused sections: the code of the first in validate_scan's order
    ({"url": URL, "fill": {"personas": 51}, "scope": {"max_depth": 9}}, "scope_invalid"),
]


@pytest.mark.db
@pytest.mark.parametrize(("body", "code"), MODEL_REFUSALS)
def test_model_refusals_carry_the_contract_code(
    api_client: Any, body: dict[str, Any], code: str
) -> None:
    c = _client(api_client)
    r = _create(c, body)
    assert r.status_code == 422, r.text
    assert r.json() == {"detail": f"the scan was refused: {code}", "code": code}
    assert "ananya" not in r.text and "example-mail" not in r.text
    _client_, sink = api_client
    refused = [e for e in sink.events if e.action == "scan.create"]
    assert [(e.outcome, e.status) for e in refused] == [("rejected", 422)]
    assert "ananya" not in json.dumps([e.model_dump(mode="json") for e in refused])


@pytest.mark.db
def test_uncoded_model_refusals_stay_field_lists(api_client: Any) -> None:
    """An unknown key (decision D4), a wrong ``mode`` or a body mixing a coded refusal with an
    uncoded one keeps FastAPI's ``[{type, loc, msg}]`` 422 (no code, no input echoed)."""
    c = _client(api_client)
    for body in (
        {"url": URL, "scope": {"submit": True}},
        {"url": URL, "fill": {"submit": True}},
        {"url": URL, "mode": "publish"},
        {"url": URL, "fill": {"personas": 51}, "mode": "publish"},
    ):
        r = _create(c, body)
        assert r.status_code == 422, r.text
        out = r.json()
        assert "code" not in out and isinstance(out["detail"], list)
        assert "ananya" not in r.text
    assert c.post("/api/v1/scans", json={"url": URL}, headers=JOBS).status_code == 403
    bad = c.post("/api/v1/scans", json={"url": URL, "fill": {"personas": 51}}, headers=JOBS)
    assert bad.status_code == 403  # the scope check still comes first


@pytest.mark.db
def test_wrap_key_returns_only_wrapped_material(api_client: Any) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from dps.engine.transform.crypto import unwrap_dek

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    c = _client(api_client)
    r = _create(c, {"url": URL, "consistency": {"wrap_key": {"public_key_pem": public_pem}}})
    assert r.status_code == 202, r.text
    key = r.json()["key_material"]
    assert key["dek"] is None and key["wrapped_dek"]
    assert len(unwrap_dek(key["wrapped_dek"], private_pem)) == 32
    spec = c.get(f"/api/v1/scans/{r.json()['scan']['id']}", headers=FULL).json()["scan"]["spec"]
    assert spec["consistency"]["key_wrapped"] is True
    bad = _create(c, {"url": URL, "consistency": {"wrap_key": {"public_key_pem": "nope"}}})
    assert (bad.status_code, bad.json()["code"]) == (422, "wrap_key_invalid")


@pytest.mark.db
def test_idempotent_replay_returns_no_key(api_client: Any) -> None:
    c = _client(api_client)
    first = _create(c, **{"Idempotency-Key": "scan-001"})
    second = _create(c, **{"Idempotency-Key": "scan-001"})
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["scan"]["id"] == first.json()["scan"]["id"]
    assert second.json()["replay"] is True and second.json()["key_material"] is None
    assert second.json()["synthesis_seed"] is None
    other = _create(c, {"url": URL + "&x=1"}, **{"Idempotency-Key": "scan-001"})
    assert (other.status_code, other.json()["code"]) == (422, "idempotency_key_reused")


def _seed_rows(settings: Settings, job_id: str) -> None:
    """Two pages, three fields, a blocked POST counted twice, five events — through ScanStore."""
    from dps.layers.scanner.models import (
        FieldRecord,
        PageRecord,
        RequestCount,
        RiskFlag,
        ScanEvent,
    )
    from dps.layers.scanner.store import ScanStore

    async def run() -> None:
        conn = await _connect(settings, "t1")
        store = ScanStore(settings.pg_schema)
        try:
            flag = RiskFlag(
                flag="autocomplete_enabled_sensitive",
                severity="high",
                page_no=1,
                field_nos=[1],
                entity_types=["EMAIL"],
            )
            pages = [
                PageRecord(
                    page_no=n,
                    url_redacted=f"http://127.0.0.1:8410/p{n}",
                    depth=n - 1,
                    parent_page_no=None if n == 1 else 1,
                    status="done",
                    discovered_via="start" if n == 1 else "link",
                    template=f"127.0.0.1:8410/p{n}",
                    form_count=1,
                    field_count=2 if n == 1 else 1,
                    risk_flags=[flag] if n == 1 else [],
                )
                for n in (1, 2)
            ]
            for p in pages:
                await store.upsert_page(conn, "t1", job_id, p)

            def field(page_no: int, no: int) -> FieldRecord:
                return FieldRecord(
                    page_no=page_no,
                    form_index=1,
                    field_no=no,
                    selector=f"input#f{no}",
                    tag="input",
                    input_type="email",
                    name_attr=f"f{no}",
                    id_attr=None,
                    label="Email",
                    label_source="for",
                    autocomplete=None,
                    pattern=None,
                    maxlength=None,
                    required=False,
                    options_count=None,
                    entity_type="EMAIL",
                    sensitivity="HIGH",
                    confidence=0.9,
                    fill_synthesize="persona@example.org",
                    risk_codes=["autocomplete_enabled_sensitive"] if page_no == 1 else [],
                )

            await store.replace_fields(conn, "t1", job_id, 1, [field(1, 1), field(1, 2)])
            await store.replace_fields(conn, "t1", job_id, 2, [field(2, 1)])
            await store.add_request_counts(
                conn,
                "t1",
                job_id,
                [
                    RequestCount(
                        page_no=1,
                        method="POST",
                        resource_type="fetch",
                        decision="blocked",
                        reason="method",
                        host="127.0.0.1",
                        count=2,
                    )
                ],
            )
            await store.append_events(
                conn,
                "t1",
                job_id,
                [
                    ScanEvent(seq=n, ts=NOW, kind="navigate", result="200", note_code="link")
                    for n in range(1, 6)
                ],
            )
        finally:
            await conn.close()

    asyncio.run(run())


def _save_export(settings: Settings, job_id: str, data: bytes) -> str:
    from dps.storage import ArtefactService, LocalObjectStore

    @asynccontextmanager
    async def acquire(tenant: str | None) -> AsyncIterator[asyncpg.Connection]:
        conn = await _connect(settings, tenant or "t1")
        try:
            yield conn
        finally:
            await conn.close()

    async def run() -> str:
        service = ArtefactService(LocalObjectStore(Path(settings.artefact_dir)), acquire=acquire)
        return await service.save(
            tenant_id="t1",
            job_id=job_id,
            kind="export_json",
            data=data,
            content_type="application/json",
            meta={},
        )

    return asyncio.run(run())


@pytest.mark.db
def test_reads_pages_fields_events_inventory_export(api_client: Any) -> None:
    c = _client(api_client)
    scan_id = _create(c).json()["scan"]["id"]
    settings = c.app.state.settings
    _seed_rows(settings, scan_id)
    base = f"/api/v1/scans/{scan_id}"
    summary = c.get(base, headers=FULL).json()["summary"]
    assert summary["pages_visited"] == 2 and summary["pages_discovered"] == 2
    assert summary["blocked_requests"] == {"POST": 2} and summary["non_get_allowed"] == 0
    assert [f["code"] for f in summary["risk_flags"]] == ["A1"]
    assert summary["navigations"] == 5 and summary["fields"] == 3
    first = c.get(f"{base}/pages?limit=1", headers=FULL).json()
    assert [p["page_no"] for p in first["items"]] == [1] and first["next_cursor"] == "1"
    rest = c.get(f"{base}/pages?limit=1&after=1", headers=FULL).json()
    assert [p["page_no"] for p in rest["items"]] == [2]
    assert c.get(f"{base}/pages?after=x", headers=FULL).json()["code"] == "cursor_invalid"
    page = c.get(f"{base}/pages/1", headers=FULL).json()
    assert [f["field_no"] for f in page["fields"]] == [1, 2]
    assert page["requests"][0]["method"] == "POST" and page["requests"][0]["count"] == 2
    missing = c.get(f"{base}/pages/9", headers=FULL)
    assert (missing.status_code, missing.json()["code"]) == (404, "page_not_found")
    events = c.get(f"{base}/events?after=2&limit=2", headers=FULL).json()
    assert [e["seq"] for e in events["items"]] == [3, 4] and events["next_cursor"] == "4"
    inv = c.get(f"{base}/inventory", headers=FULL).json()
    assert len(inv["items"]) == 3 and inv["risk_flags"][0]["code"] == "A1"
    assert "fill_synthesize" not in json.dumps(inv)
    csv_r = c.get(f"{base}/inventory?format=csv", headers=FULL)
    assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv")
    assert csv_r.headers["content-disposition"] == f'attachment; filename="{scan_id}-inventory.csv"'
    lines = csv_r.text.strip().splitlines()
    assert len(lines) == 4 and lines[0].startswith("page_no,path,")
    assert "persona@example.org" not in csv_r.text
    not_ready = c.get(f"{base}/export", headers=FULL)
    assert (not_ready.status_code, not_ready.json()["code"]) == (409, "export_not_ready")
    payload = b'{"format": "dps.scan-export.v1"}'
    _save_export(settings, scan_id, payload)
    ready = c.get(f"{base}/export?format=json", headers=FULL)
    assert ready.status_code == 200 and ready.content == payload
    assert ready.headers["content-disposition"] == f'attachment; filename="{scan_id}-export.json"'
    assert c.get(f"{base}/export?format=csv", headers=FULL).status_code == 409


@pytest.mark.db
def test_cancel_queued_scan(api_client: Any) -> None:
    c = _client(api_client)
    scan_id = _create(c).json()["scan"]["id"]
    r = c.post(f"/api/v1/scans/{scan_id}/cancel", headers=FULL)
    assert r.status_code == 200 and r.json()["state"] == "canceled"
    again = c.post(f"/api/v1/scans/{scan_id}/cancel", headers=FULL)
    assert (again.status_code, again.json()["code"]) == (409, "not_cancelable")
    unknown = c.post("/api/v1/scans/s_01UNKNOWN/cancel", headers=FULL)
    assert (unknown.status_code, unknown.json()["code"]) == (404, "scan_not_found")
    _, sink = api_client
    assert any(e.action == "scan.cancel" for e in sink.events)
    assert any(e.action == "scan.create" for e in sink.events)


@pytest.mark.db
def test_persona_preview_is_reproducible_and_matches_the_scan_persona(api_client: Any) -> None:
    from dps.engine.synth.surrogates import seed_from_caller
    from dps.layers.scanner.personas_fill import persona_values

    c = _client(api_client)
    r = c.post("/api/v1/scans/personas/preview", json={"locale": "en_IN", "count": 2}, headers=FULL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["personas"]) == 2 and body["notes"]
    first = body["personas"][0]["values"]
    assert list(first) == ["Name", "Date of birth", "Aadhaar", "PAN", "Mobile"]
    again = c.post(
        "/api/v1/scans/personas/preview",
        json={"locale": "en_IN", "count": 2, "synthesis_seed": body["synthesis_seed"]},
        headers=FULL,
    ).json()
    assert again["personas"] == body["personas"]
    engine = c.app.state.engine
    seed = seed_from_caller(body["synthesis_seed"])
    values = persona_values(
        engine, seed=seed, persona_no=1, locale="en_IN", policy=engine.policy(None)
    )
    assert first["Aadhaar"] == values["aadhaar"] and first["Name"] == values["name"]
    for bad in ({"locale": "fr_FR"}, {"count": 6}, {"submit": True}):
        assert c.post("/api/v1/scans/personas/preview", json=bad, headers=FULL).status_code == 422


def test_openapi_has_no_submit_route() -> None:
    from fastapi.testclient import TestClient

    from dps.app import create_app

    app = create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    spec = TestClient(app).get("/api/v1/openapi.json").json()
    assert not [p for p in spec["paths"] if "submit" in p.lower()]
    scans = {p: set(ops) for p, ops in spec["paths"].items() if p.startswith("/api/v1/scans")}
    assert scans == {
        "/api/v1/scans": {"get", "post"},
        "/api/v1/scans/personas/preview": {"post"},
        "/api/v1/scans/{scan_id}": {"get"},
        "/api/v1/scans/{scan_id}/pages": {"get"},
        "/api/v1/scans/{scan_id}/pages/{page_no}": {"get"},
        "/api/v1/scans/{scan_id}/events": {"get"},
        "/api/v1/scans/{scan_id}/inventory": {"get"},
        "/api/v1/scans/{scan_id}/export": {"get"},
        "/api/v1/scans/{scan_id}/cancel": {"post"},
    }


def test_csp_allows_blob_images() -> None:
    from dps.app import CSP

    assert "img-src 'self' data: blob:" in CSP


@pytest.mark.db
async def test_browser_readiness_from_heartbeats(migrated_db: Settings) -> None:
    from dps import jobs
    from dps.app import refresh_browser_readiness
    from dps.observability import READINESS

    assert await refresh_browser_readiness(migrated_db) == 0
    assert READINESS.snapshot()["components"]["browser"]["ok"] is False
    await jobs.register_worker("bw-test:1:abcdef", role="browser-worker", version="test")
    await jobs.register_worker("w-test:1:abcdef", role="worker", version="test")
    assert await refresh_browser_readiness(migrated_db) == 1
    snap = READINESS.snapshot()
    assert snap["components"]["browser"] == {"ok": True, "detail": "1 online"}
    assert "browser" not in READINESS.required


def test_api_imports_without_playwright_or_pillow() -> None:
    """The app image has no Playwright: the app and every scanner module it imports load without
    it. Pillow is in the app image (M1's ``[docs]``), but none of the scanner modules the API
    imports may need it either (only ``capture.py`` does, in the browser-worker)."""
    scanner = (
        "dps.layers.api.routers.scans, dps.layers.scanner, dps.layers.scanner.inventory, "
        "dps.layers.scanner.runtime, dps.layers.scanner.store, dps.layers.scanner.models, "
        "dps.layers.scanner.classify, dps.layers.scanner.personas_fill, dps.layers.scanner.urls"
    )
    both = "import sys; sys.modules['playwright'] = None; sys.modules['PIL'] = None; "
    subprocess.run([sys.executable, "-c", both + "import " + scanner], check=True, timeout=120)
    no_pw = "import sys; sys.modules['playwright'] = None; import dps.app; "
    no_pw += "app = dps.app.create_app(); "
    no_pw += "assert '/api/v1/scans' in app.openapi()['paths']"
    env = {
        **os.environ,
        "DPS_ENV": "local",
        "DPS_LLM_PROVIDER": "stub",
        "DPS_AUTH_ENABLED": "false",
    }
    subprocess.run([sys.executable, "-c", no_pw], check=True, timeout=120, env=env, cwd="/")
