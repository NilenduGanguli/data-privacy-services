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


@pytest.mark.db
def test_storage_state_refs_get_the_connection_rule_at_accept(api_client: Any) -> None:
    """SEC-5: ``auth.storage_state_ref`` is checked for the caller's tenant when the scan is
    accepted, with a connection ``secret_ref``'s rule and codes (contract §5.1): another tenant's
    folder (the fixture wrote ``t2/…``), an unlisted shared folder and a hidden segment are refused,
    audited, never echoed and never queued; the tenant's own reference is accepted."""
    c, sink = api_client
    for ref, status, code in (
        ("t2/dps/src/test-pg", 403, "secret_ref_foreign_tenant"),
        ("_shared/ui/demo-onboarding", 403, "secret_ref_not_shared"),
        ("dps/.hidden/demo", 422, "secret_ref_invalid"),
    ):
        r = _create(c, {"url": URL, "auth": {"storage_state_ref": ref}})
        assert (r.status_code, r.json().get("code")) == (status, code), r.text
        assert ref not in r.text and "t2" not in r.text and "ananya" not in r.text
    assert c.get("/api/v1/scans", headers=FULL).json()["items"] == []
    refused = [(e.outcome, e.status) for e in sink.events if e.action == "scan.create"]
    assert refused == [("rejected", 403), ("rejected", 403), ("rejected", 422)]
    ok = _create(c, {"url": URL, "auth": {"storage_state_ref": "dps/ui/demo-onboarding"}})
    assert ok.status_code == 202, ok.text
    assert ok.json()["scan"]["spec"]["auth"]["storage_state_ref"] == "dps/ui/demo-onboarding"


@pytest.mark.db
def test_policy_references_resolve_at_accept_and_pin_the_version(api_client: Any) -> None:
    """M5 (contract §5.5): ``policy: "@<name>[:v<N>]"`` on ``POST /scans`` is the tenant's
    published body at accept time, so the job spec — which the browser-worker runs and the
    evidence names — holds exactly that version; the policy store's refusal codes come back as
    they are, audited, and another tenant cannot name the policy."""
    from dps.jobs_models import SealedSelection
    from dps.secrets import open_json
    from tests.fixtures.db import TEST_KEK
    from tests.test_policy_store import NAME, _adopt_and_tune

    c, sink = api_client
    hashes = _adopt_and_tune(c)
    active = _create(c, {"url": URL, "policy": f"@{NAME}"})
    assert active.status_code == 202, active.text
    stored = active.json()["scan"]["spec"]["policy"]
    # S3 security review F-18: the spec keeps the body's value-free label; the body is sealed
    assert (stored["name"], stored["version"], stored["sealed"]) == (NAME, 2, True)
    scan_id = active.json()["scan"]["id"]
    row = _sql(
        c.app.state.settings, "t1", "SELECT spec_enc FROM {s}.dps_job WHERE id = $1", scan_id
    )
    body = open_json(TEST_KEK, row["spec_enc"], SealedSelection, aad=f"dps_job.spec:{scan_id}")
    assert body.policy is not None
    assert body.policy["detect"]["min_score"] == 0.55  # the tuned v2 body, not the preset's
    pinned = _create(c, {"url": URL, "policy": f"@{NAME}:v1"})
    assert pinned.status_code == 202, pinned.text
    assert pinned.json()["scan"]["spec"]["policy"]["version"] == 1
    for ref, status, code in (
        ("@nope", 404, "policy_not_found"),
        (f"@{NAME}:v9", 404, "policy_not_found"),
        ("@Not-A-Ref", 400, "invalid_policy_ref"),
    ):
        r = _create(c, {"url": URL, "policy": ref})
        assert (r.status_code, r.json()["code"]) == (status, code), r.text
    other = _create(c, {"url": URL, "policy": f"@{NAME}"}, **{"X-DPS-Tenant": "t2"})
    assert (other.status_code, other.json()["code"]) == (404, "policy_not_found")
    created = [(e.outcome, e.status) for e in sink.events if e.action == "scan.create"]
    assert created == [
        ("ok", 202),
        ("ok", 202),
        ("rejected", 404),
        ("rejected", 404),
        ("rejected", 400),
        ("rejected", 404),
    ]
    assert hashes["v1"] != hashes["v2"]


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
    uncoded one keeps the field list of the app's 422 — ``{"detail": [{loc, msg, type}], "code":
    "invalid_request"}`` since the S1 handler of the security review (no scan-specific code, no
    input echoed)."""
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
        assert out["code"] == "invalid_request" and isinstance(out["detail"], list)
        assert all(set(item) == {"loc", "msg", "type"} for item in out["detail"])
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


@pytest.mark.db
def test_the_overview_counts_the_personal_fields_scans_found(api_client: Any) -> None:
    """M5's Overview card "Layer 3 · UI scanner" says "N personal data fields": the summary's
    ``layers.ui.personal_fields`` sums the scans' pages' personal fields in the window, beside
    ``fields`` (every field inventoried) and ``risk_flags``."""
    from dps.layers.scanner.models import PageRecord
    from dps.layers.scanner.store import ScanStore

    c = _client(api_client)
    scan_id = _create(c).json()["scan"]["id"]
    settings = c.app.state.settings

    async def seed() -> None:
        conn = await _connect(settings, "t1")
        try:
            store = ScanStore(settings.pg_schema)
            for n, fields, personal in ((1, 5, 3), (2, 4, 1)):
                page = PageRecord(
                    page_no=n,
                    url_redacted=f"http://127.0.0.1:8410/p{n}",
                    depth=n - 1,
                    parent_page_no=None if n == 1 else 1,
                    status="done",
                    discovered_via="start" if n == 1 else "link",
                    template=f"127.0.0.1:8410/p{n}",
                    form_count=1,
                    field_count=fields,
                    personal_field_count=personal,
                )
                await store.upsert_page(conn, "t1", scan_id, page)
        finally:
            await conn.close()

    asyncio.run(seed())
    summary = c.get("/api/v1/reports/summary", params={"days": 7}, headers=FULL)
    assert summary.status_code == 200, summary.text
    ui = summary.json()["layers"]["ui"]
    assert ui["personal_fields"] == 4 and ui["risk_flags"] == 0


@pytest.mark.db
def test_overview_and_reports_rows_name_the_scanned_host_its_forms_and_personas(
    api_client: Any,
) -> None:
    """S2 console r1 B2 (mockups 01 and 09: "UI scan of <host> · N pages · N forms · N personas ·
    N submissions"): a scan's ``recent_jobs`` brief names the host of its redacted origin (a scan
    has no ``object_ref``), and its evidence pack's summary names that host and carries the forms
    and personas of the pack's ``scan`` block (a scan pack has ``source.origin``, no
    ``source.object``). Both read "—" before. The pack comes from the real scan-pack builder."""
    from dps.jobs import Job
    from dps.layers.scanner.inventory import build_scan_evidence

    c = _client(api_client)
    created = _create(c, {"url": URL, "fill": {"personas": 3, "locale": "en_IN"}})
    assert created.status_code == 202, created.text
    job = Job.model_validate(created.json()["scan"])
    settings = c.app.state.settings
    pack = build_scan_evidence(
        evidence_id="ev_s2b2scan",
        job=job,
        runtime=None,
        pages=[],
        fields=[],
        counts={"pages_visited": 21, "forms": 7, "fields": 63, "personal": 51},
        outcome=None,
        result="succeeded",
        errors=[],
        settings=settings,
        exports={},
    ).model_dump(mode="json")
    assert pack["source"]["origin"] == "http://127.0.0.1:8410" and "object" not in pack["source"]

    async def store() -> None:
        conn = await _connect(settings, "t1")
        try:
            await conn.execute(
                f"INSERT INTO {settings.pg_schema}.dps_evidence "
                "(id, tenant_id, job_id, pack, pack_sha256) VALUES ($1, 't1', $2, $3::jsonb, 'x')",
                pack["id"],
                job.id,
                pack,
            )
        finally:
            await conn.close()

    asyncio.run(store())
    summary = c.get("/api/v1/reports/summary", params={"days": 7}, headers=FULL)
    assert summary.status_code == 200, summary.text
    [brief] = [j for j in summary.json()["recent_jobs"] if j["id"] == job.id]
    assert (brief["kind"], brief["source"], brief["target"]) == ("scan", "127.0.0.1:8410", None)
    listed = c.get("/api/v1/reports/evidence", params={"layer": "ui"}, headers=FULL)
    assert listed.status_code == 200, listed.text
    [item] = [i for i in listed.json()["items"] if i["job_id"] == job.id]
    s = item["summary"]
    got = (s["source"], s["pages"], s["forms"], s["personas"], s["submissions"])
    assert got == ("127.0.0.1:8410", 21, 7, 3, 0), s
    assert "ananya" not in summary.text + listed.text  # the start URL's query stays redacted


@pytest.mark.db
def test_hosts_registered_on_the_admin_page_decide_what_a_tenant_may_scan(
    api_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M5 Admin › UI-scanner hosts writes ``dps_tenant.registered_hosts`` (normalised); from then on
    ``POST /scans`` accepts exactly those hosts for the tenant, and the local fallback list
    (``DPS_SCAN_REGISTERED_HOSTS``) no longer applies to it; another tenant keeps the fallback.
    The SSRF guard (S5) resolves a registered name: the made-up UAT host resolves, in this test, to
    an internal (RFC 1918) address."""
    uat = {"uat.bank.example": ["10.20.30.40"]}

    def resolve(host: str) -> list[str]:
        if host not in uat:
            raise OSError("no such host")
        return uat[host]

    monkeypatch.setattr("dps.netguard.default_resolver", resolve)
    c = _client(api_client)
    assert _create(c).status_code == 202  # no hosts registered: the local fallback list applies
    made = c.post(
        "/api/v1/admin/tenants",
        json={"tenant_id": "t2", "registered_hosts": ["HTTPS://UAT.Bank.Example:8443/apply"]},
        headers=FULL,
    )
    assert made.status_code == 201, made.text
    assert made.json()["effective"]["registered_hosts"] == ["uat.bank.example"]
    t2 = {"X-DPS-Tenant": "t2"}
    fallback = _create(c, {"url": URL}, **t2)
    assert (fallback.status_code, fallback.json()["code"]) == (422, "host_not_registered")
    registered = _create(c, {"url": "https://uat.bank.example/apply"}, **t2)
    assert registered.status_code == 202, registered.text
    assert registered.json()["scan"]["spec"]["url_origin"] == "https://uat.bank.example"
    assert _create(c).status_code == 202  # t1 is unchanged
    patched = c.patch("/api/v1/admin/tenants/t2", json={"registered_hosts": []}, headers=FULL)
    assert patched.status_code == 200, patched.text
    assert _create(c, {"url": URL}, **t2).status_code == 202  # no hosts: the fallback again


def _db_key(c: Any, name: str, tenants: list[str], scopes: list[str]) -> dict[str, Any]:
    """Mint an M5 database key through the admin API (the raw key is in this answer only)."""
    r = c.post(
        "/api/v1/admin/keys",
        json={"name": name, "tenant_ids": tenants, "scopes": scopes, "expires_in_days": 30},
        headers=FULL,
    )
    assert r.status_code == 201, r.text
    created: dict[str, Any] = r.json()
    return created


def _scan_routes(c: Any, scan_id: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every ``/api/v1/scans`` operation the app serves, with concrete paths (from OpenAPI, so a
    route added later is covered too)."""
    spec = c.get("/api/v1/openapi.json").json()
    bodies: dict[str, dict[str, Any]] = {
        "/api/v1/scans": {"url": URL},
        "/api/v1/scans/personas/preview": {"locale": "en_IN", "count": 1},
    }
    out = []
    for path, ops in sorted(spec["paths"].items()):
        if not path.startswith("/api/v1/scans"):
            continue
        concrete = path.replace("{scan_id}", scan_id).replace("{page_no}", "1")
        for method in sorted(ops):
            out.append((method, concrete, bodies.get(path) if method == "post" else None))
    return out


def _job_routes(c: Any, job_id: str) -> list[tuple[str, str]]:
    """Every ``/api/v1/jobs/{job_id}…`` operation the app serves, with ``job_id`` filled in (from
    OpenAPI, so a route added later is covered too)."""
    spec = c.get("/api/v1/openapi.json").json()
    return [
        (method, path.replace("{job_id}", job_id))
        for path, ops in sorted(spec["paths"].items())
        if path.startswith("/api/v1/jobs/{job_id}")
        for method in sorted(ops)
    ]


@pytest.mark.db
def test_database_keys_with_the_scan_scope_drive_every_scans_route(api_client: Any) -> None:
    """M5 database keys (``/admin/keys``) on the scanner: a key with ``scan`` passes the scope and
    tenant checks of every scans route (and reads the scan's evidence); a database key without
    ``scan`` is refused on every one — and on every ``/api/v1/jobs/{job_id}…`` route that names
    the scan (S2 adaptations r1 B1); a key of another tenant sees no scan of this one; a
    disabled key stops at once; the scan and its audit rows name the database key."""
    c, sink = api_client
    scanner = _db_key(c, "qa-scanner", ["t1"], ["scan"])
    jobs_only = _db_key(c, "crm-provisioner", ["t1"], ["jobs", "detect"])
    other = _db_key(c, "qa-scanner-t2", ["t2"], ["scan"])
    scan_h = {"X-API-KEY": scanner["key"]}
    created = c.post("/api/v1/scans", json={"url": URL}, headers=scan_h)
    assert created.status_code == 202, created.text
    scan = created.json()["scan"]
    assert scan["tenant_id"] == "t1" and scan["created_by"] == scanner["record"]["id"]
    assert created.json()["key_material"]["dek"] and created.json()["synthesis_seed"]["seed"]
    routes = _scan_routes(c, scan["id"])
    assert len(routes) == 10
    for method, path, body in routes:
        if (method, path) == ("post", f"/api/v1/scans/{scan['id']}/cancel"):
            continue  # last: it ends the scan
        r = c.request(method, path, json=body, headers=scan_h)
        assert r.status_code not in (401, 403), (method, path, r.status_code, r.text)
        denied = c.request(method, path, json=body, headers={"X-API-KEY": jobs_only["key"]})
        assert denied.status_code == 403, (method, path, denied.text)
        anonymous = c.request(method, path, json=body)
        assert anonymous.status_code == 401, (method, path)
    job_routes = _job_routes(c, scan["id"])
    assert {p.rsplit("/", 1)[-1] for _, p in job_routes} >= {"cancel", "pause", "resume", "retry"}
    for method, path in job_routes:
        denied = c.request(method, path, headers={"X-API-KEY": jobs_only["key"]})
        assert (denied.status_code, denied.json()["code"]) == (403, "scope_required"), (
            method,
            path,
            denied.text,
        )
        assert c.request(method, path, headers=scan_h).status_code == 403, (method, path)
    still = c.get(f"/api/v1/scans/{scan['id']}", headers=scan_h).json()["scan"]
    assert still["state"] == "queued"  # no refused control changed it
    listed = c.get("/api/v1/scans", headers=scan_h).json()["items"]
    assert len(listed) == 2 and listed[-1]["id"] == scan["id"]  # + the route walk's POST /scans
    foreign = c.get(f"/api/v1/scans/{scan['id']}", headers={"X-API-KEY": other["key"]})
    assert (foreign.status_code, foreign.json()["code"]) == (404, "scan_not_found")
    ungranted = c.get(
        f"/api/v1/scans/{scan['id']}", headers={"X-API-KEY": other["key"], "X-DPS-Tenant": "t1"}
    )
    assert ungranted.status_code == 403
    canceled = c.post(f"/api/v1/scans/{scan['id']}/cancel", headers=scan_h)
    assert canceled.status_code == 200 and canceled.json()["state"] == "canceled"
    evidence = c.get("/api/v1/evidence", params={"job_id": scan["id"]}, headers=scan_h)
    assert evidence.status_code == 200, evidence.text
    assert c.get("/api/v1/evidence", headers={"X-API-KEY": jobs_only["key"]}).status_code == 200
    rows = [e for e in sink.events if e.action in ("scan.create", "scan.cancel")]
    assert {e.key_id for e in rows} == {scanner["record"]["id"]}
    disabled = c.post(f"/api/v1/admin/keys/{scanner['record']['id']}/disable", headers=FULL)
    assert disabled.status_code == 200, disabled.text
    assert c.get("/api/v1/scans", headers=scan_h).status_code == 401
    assert all(scanner["key"] not in json.dumps(e.model_dump(mode="json")) for e in sink.events)


@pytest.mark.db
def test_a_key_without_the_scan_scope_neither_controls_nor_lists_scans_through_jobs(
    api_client: Any,
) -> None:
    """S2 adaptations r1 B1 (contract §8.1: ``scan`` covers scans). ``/api/v1/jobs`` serves every
    job kind, so a key holding ``jobs`` but not ``scan`` got 200 on ``POST /jobs/{scan}/cancel``
    and the scan was canceled. Now each ``/jobs/{id}`` route checks the job's kind: 403
    ``scope_required`` (audited ``job.cancel`` rejected), the scan does not change, and
    ``GET /jobs`` lists no scan for such a key. A key holding both scopes still controls it."""
    c, sink = api_client
    jobs_db = _db_key(c, "crm-provisioner", ["t1"], ["jobs", "detect"])
    both = _db_key(c, "ops-desk", ["t1"], ["jobs", "scan"])
    scan = _create(c).json()["scan"]
    for headers in (JOBS, {"X-API-KEY": jobs_db["key"]}):
        for action in ("cancel", "pause", "resume"):
            r = c.post(f"/api/v1/jobs/{scan['id']}/{action}", headers=headers)
            assert (r.status_code, r.json()["code"]) == (403, "scope_required"), (action, r.text)
        for path in (f"/api/v1/jobs/{scan['id']}", f"/api/v1/jobs/{scan['id']}/partitions"):
            r = c.get(path, headers=headers)
            assert (r.status_code, r.json()["code"]) == (403, "scope_required"), (path, r.text)
        listed = c.get("/api/v1/jobs", headers=headers)
        assert listed.status_code == 200, listed.text
        assert all(j["kind"] != "scan" for j in listed.json()["items"])
        only_scans = c.get("/api/v1/jobs", params={"kind": "scan"}, headers=headers)
        assert (only_scans.status_code, only_scans.json()["code"]) == (403, "scope_required")
    state = c.get(f"/api/v1/scans/{scan['id']}", headers=FULL).json()["scan"]["state"]
    assert state == "queued"
    refused = [e for e in sink.events if e.action == "job.cancel" and e.job_id == scan["id"]]
    assert len(refused) == 2 and {(e.outcome, e.status) for e in refused} == {("rejected", 403)}
    both_h = {"X-API-KEY": both["key"]}
    assert scan["id"] in [j["id"] for j in c.get("/api/v1/jobs", headers=both_h).json()["items"]]
    assert c.get(f"/api/v1/jobs/{scan['id']}", headers=both_h).status_code == 200
    canceled = c.post(f"/api/v1/jobs/{scan['id']}/cancel", headers=both_h)
    assert canceled.status_code == 200 and canceled.json()["state"] == "canceled", canceled.text


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
