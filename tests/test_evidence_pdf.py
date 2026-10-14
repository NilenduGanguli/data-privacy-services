"""Evidence-pack and API roll-up PDFs (M5 Task 14): every section, escaped and Latin-1-safe text,
rendered once and then streamed from the stored artefact, scoped and tenant-isolated."""

from __future__ import annotations

import asyncio
import functools
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

from dps.layers.reports.pdf import pdf_text, render_evidence_pdf, render_rollup_pdf  # noqa: E402

SHA = "6ec5e6ddf67837f2b91df0fea306b9db0fcd146c58a7d233c79d6bae6ab7d73b"
BASE: dict[str, Any] = {
    "pack_version": "dps.evidence.v1",
    "id": "ev_1",
    "tenant_id": "retail",
    "job_id": "j_01K7",
    "job_kind": "run",
    "outcome": "succeeded",
    "mode": "synthesize",
    "created_at": "2026-10-11T09:49:12Z",
    "engine_version": "1.0.0",
    "label": "synthetic — measured, not claimed anonymous",
    "purpose": {"ticket": "CHG-48213", "legal_basis": "legitimate interest — UAT provisioning"},
    "source": {"engine": "oracle", "object": "CRM.CUSTOMERS"},
    "target": {"object": "CRM_SYNTH.CUSTOMERS", "write_mode": "table"},
    "policy": {"name": "synthetic-realistic", "hash": "7c15", "version": "1"},
    "snapshot": {
        "id": "snap_1",
        "content_hash": "abcd",
        "accepted_by": "key_owner",
        "accepted_at": None,
    },
    "columns": [
        {
            "path": "$.FULL_NAME",
            "entity_type": "PERSON_NAME",
            "method": "surrogate",
            "values": 500,
            "data_type": "VARCHAR2(120)",
            "confidence": 0.96,
            "override": False,
        }
    ],
    "counts": {"rows_read": 500, "rows_written": 500, "partitions": 2, "values_transformed": 500},
    "verification": {"mode": "strict", "hits": 0, "passed": True, "values_checked": 500},
    "partitions": [{"no": 0, "rows": 250, "attempts": 1, "resumed": 0, "state": "done"}],
    "timings": {"duration_s": 12.5},
    "config_hash": "abc",
    "consistency": {"kid": None, "scope": "key"},
}
FIDELITY = {
    "engine": "copula",
    "score": 0.96,
    "corr_delta": 0.019,
    "limits": {"column": 0.05, "corr": 0.1},
    "measured_on": {"synthetic_rows": 300},
    "columns": [{"path": "$.BALANCE", "metric": "ks", "distance": 0.021, "null_rate_delta": 0.0}],
    "corr_matrix": {"columns": ["$.BALANCE", "$.LIMIT"], "delta": [[0.0, 0.024], [0.024, 0.0]]},
}
PRIVACY = {
    "exact_row_matches": 0,
    "exact_row_resamples": 1,
    "nndr": {
        "ratio": 1.04,
        "synth_to_real_median": 0.412,
        "real_to_real_median": 0.398,
        "target": 0.95,
        "passed": True,
    },
    "qi_uniqueness": {"source": 0.029, "synthetic": 0.008, "unique_overlap": 0},
    "membership_inference": {"run": False, "reason": "not available for the copula engine"},
}


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(pdf)).pages)


def test_synthetic_pack_renders_every_section() -> None:
    pdf = render_evidence_pdf({**BASE, "fidelity": FIDELITY, "privacy": PRIVACY}, pack_sha256=SHA)
    assert pdf.startswith(b"%PDF-")
    text = _text(pdf)
    for heading in (
        "Summary",
        "Configuration",
        "Classification snapshot",
        "Execution",
        "Verification",
        "fidelity and privacy",
        "Regulatory note",
        "Signatures",
    ):
        assert heading in text
    assert "1.04" in text and "Exact-row matches" in text and SHA in text and "CHG-48213" in text
    assert "key_owner" in text and "not available for the copula engine" in text


def test_obfuscate_pack_has_no_synthetic_section() -> None:
    pack = {**BASE, "mode": "obfuscate", "label": "pseudonymised — still personal data"}
    text = _text(render_evidence_pdf(pack, pack_sha256=SHA))
    assert "fidelity and privacy" not in text and "pseudonymised" in text


def test_tabular_none_says_privacy_was_not_measured() -> None:
    text = _text(render_evidence_pdf({**BASE, "privacy": {"engine": "none"}}, pack_sha256=SHA))
    assert "Privacy not measured" in text


def test_linked_and_dp_packs_render_both_tables() -> None:
    linked = {
        "engine": "mostly",
        "score": 0.9,
        "tables": {
            "parent": {
                **FIDELITY,
                "columns": [{"path": "$.SEGMENT", "metric": "tvd", "distance": 0.01}],
            },
            "child": {
                **FIDELITY,
                "columns": [{"path": "$.AMOUNT", "metric": "ks", "distance": 0.03}],
            },
        },
        "measured_on": {"synthetic_parents": 10, "synthetic_children": 40},
    }
    privacy = {**PRIVACY, "dp": {"enabled": True, "epsilon": 2.5}}
    tabular = {"engine": "mostly", "linked": {"parent_key": "$.ID", "child_fk": "$.CUSTOMER_ID"}}
    text = _text(
        render_evidence_pdf(
            {**BASE, "fidelity": linked, "privacy": privacy, "tabular": tabular}, pack_sha256=SHA
        )
    )
    assert "$.SEGMENT" in text and "$.AMOUNT" in text and "parent" in text and "child" in text
    assert "Differential privacy" in text and "2.5" in text and "child_fk" in text


def test_malformed_numbers_never_break_the_render() -> None:
    odd = {
        **BASE,
        "fidelity": {**FIDELITY, "score": None, "columns": [{"path": "$.X", "distance": "n/a"}]},
        "privacy": {"nndr": {"ratio": None}, "qi_uniqueness": "x"},
        "partitions": [{"no": "a"}],
        "timings": None,
    }
    assert render_evidence_pdf(odd, pack_sha256=SHA).startswith(b"%PDF-")


def _wide_fidelity(n: int) -> dict[str, Any]:
    """A fidelity report shaped like ``metrics.fidelity_report`` for ``n`` numeric columns."""
    names = [f"$.BALANCE_M{i:02d}" for i in range(n)]
    delta = [[0.0 if i == j else round(0.001 * ((i * 7 + j * 3) % 50), 4) for j in range(n)]
             for i in range(n)]  # fmt: skip
    if n > 3:
        delta[n - 1][2] = delta[2][n - 1] = 0.731  # the largest pair delta
    return {
        **FIDELITY,
        "corr_delta": 0.024,
        "corr_max": 0.731,
        "columns": [{"path": p, "metric": "ks", "distance": 0.02} for p in names],
        "corr_matrix": {"columns": names, "delta": delta},
    }


def test_wide_correlation_matrix_renders_the_largest_pairs() -> None:
    """80 numeric columns (review B1: 58+ raised "negative availWidth" and the route answered 500)."""
    wide = _wide_fidelity(80)
    text = _text(
        render_evidence_pdf({**BASE, "fidelity": wide, "privacy": PRIVACY}, pack_sha256=SHA)
    )
    assert "matrix of 80 columns" in text and "0.731" in text
    assert "$.BALANCE_M79" in text and "$.BALANCE_M02" in text  # the largest pair is listed


def test_wide_linked_tables_render() -> None:
    wide = _wide_fidelity(80)
    linked = {"engine": "mostly", "score": 0.9, "tables": {"parent": wide, "child": wide}}
    pack = {**BASE, "fidelity": linked, "privacy": PRIVACY}
    text = _text(render_evidence_pdf(pack, pack_sha256=SHA))
    assert text.count("matrix of 80 columns") == 2


@pytest.mark.parametrize("n", [2, 8, 16, 17, 20, 57, 58, 120])
def test_every_matrix_width_renders(n: int) -> None:
    pack = {**BASE, "fidelity": _wide_fidelity(n), "privacy": PRIVACY}
    assert render_evidence_pdf(pack, pack_sha256=SHA).startswith(b"%PDF-")


def test_wide_matrix_with_malformed_cells_still_renders() -> None:
    wide = _wide_fidelity(60)
    wide["corr_matrix"]["delta"][5] = "x"
    wide["corr_matrix"]["delta"][6][1] = None
    wide["corr_matrix"]["delta"][7][1] = float("nan")
    pack = {**BASE, "fidelity": wide, "privacy": PRIVACY}
    assert render_evidence_pdf(pack, pack_sha256=SHA).startswith(b"%PDF-")


def test_scan_pack_renders_the_ui_scan_section() -> None:
    scan = {"pages": 12, "forms": 3, "fields": 40, "personas": 5, "blocked_requests": {"POST": 7}}
    text = _text(render_evidence_pdf({**BASE, "job_kind": "scan", "scan": scan}, pack_sha256=SHA))
    assert "UI scan" in text and "POST" in text and "Submissions" in text


def test_markup_is_escaped_and_unicode_folded() -> None:
    evil = {
        **BASE,
        "source": {
            "engine": "postgres",
            "object": '<link href="https://evil.example">x</link> café ≥ 1',
        },
    }
    pdf = render_evidence_pdf(evil, pack_sha256=SHA)
    text = _text(pdf)
    assert b"/URI" not in pdf and b"/Annots" not in pdf  # rendered as text, never as a live link
    assert "evil.example" in text and ">=" in text and pdf_text("<b>") == "&lt;b&gt;"


def test_rollup_pdf_renders() -> None:
    doc = {
        "day": "2026-10-10",
        "action": "obfuscate",
        "calls": 12,
        "findings": 40,
        "values": 33,
        "verify_passed": 12,
        "verify_failed": 0,
        "by_outcome": {"ok": 12},
        "entities": {"SSN": 10},
        "sha256": SHA,
    }
    text = _text(render_rollup_pdf(doc))
    assert "obfuscate" in text and "2026-10-10" in text and SHA in text and "SSN" in text


# --- routes (metadata DB) --------------------------------------------------------------------------
@pytest.fixture
def pdf_app(db_settings, tmp_path: Path) -> Iterator[tuple[Any, Any]]:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from dps.app import create_app
    from tests.fixtures.db import _truncate, api_settings

    def make(**overrides: Any) -> Any:
        return create_app(api_settings(db_settings, tmp_path, **overrides))

    try:
        yield TestClient, make
    finally:
        asyncio.run(_truncate(db_settings))


async def _seed_pack(tenant: str, extra: dict[str, Any] | None = None) -> str:
    from dps import db, jobs
    from dps.jobs import JobKind, JobPhase

    job = await jobs.create_job(tenant_id=tenant, kind=JobKind.run, phase=JobPhase.prepare, spec={})
    async with db.acquire(tenant) as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident()}.dps_evidence (id, tenant_id, job_id, pack, pack_sha256) "
            "VALUES ('ev_pdf', $1, $2, $3::jsonb, $4)",
            tenant,
            job.id,
            {
                **BASE,
                "id": "ev_pdf",
                "tenant_id": tenant,
                "job_id": job.id,
                "reveal_map": [],
                "errors": [],
                **(extra or {}),
            },
            SHA,
        )
    return job.id


async def _pdf_rows(tenant: str) -> tuple[int, str | None]:
    from dps import db

    async with db.acquire(tenant) as conn:
        n = await conn.fetchval(
            f"SELECT count(*) FROM {db.schema_ident()}.dps_artefact WHERE kind = 'evidence_pdf'"
        )
        linked = await conn.fetchval(
            f"SELECT pdf_artefact_id FROM {db.schema_ident()}.dps_evidence WHERE id = 'ev_pdf'"
        )
    return int(n), linked


@pytest.mark.db
def test_pdf_route_renders_once_and_then_streams_the_artefact(pdf_app) -> None:  # noqa: ANN001
    client_cls, make = pdf_app
    with client_cls(make()) as client:
        client.portal.call(functools.partial(_seed_pack, "pdf1"))
        h = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "pdf1"}
        first = client.get("/api/v1/evidence/ev_pdf/pdf", headers=h)
        second = client.get("/api/v1/evidence/ev_pdf/pdf", headers=h)
        assert first.status_code == 200 and second.status_code == 200
        assert first.headers["content-type"] == "application/pdf"
        assert 'filename="evidence-ev_pdf.pdf"' in first.headers["content-disposition"]
        assert first.content == second.content and first.content.startswith(b"%PDF-")
        n, linked = client.portal.call(functools.partial(_pdf_rows, "pdf1"))
        assert n == 1 and linked is not None
        listed = client.get("/api/v1/evidence/ev_pdf", headers=h).json()
        assert "evidence_pdf" in [a["kind"] for a in listed["artefacts"]]


@pytest.mark.db
def test_pdf_route_serves_a_wide_synthetic_pack(pdf_app) -> None:  # noqa: ANN001
    """Review B1: an 80-numeric-column synthetic pack answered 500 internal_error."""
    client_cls, make = pdf_app
    with client_cls(make()) as client:
        wide = {"fidelity": _wide_fidelity(80), "privacy": PRIVACY}
        client.portal.call(functools.partial(_seed_pack, "pdfw", wide))
        h = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "pdfw"}
        r = client.get("/api/v1/evidence/ev_pdf/pdf", headers=h)
        assert r.status_code == 200 and r.content.startswith(b"%PDF-")
        assert "matrix of 80 columns" in _text(r.content)


@pytest.mark.db
def test_pdf_route_scope_tenancy_and_switch(pdf_app) -> None:  # noqa: ANN001
    client_cls, make = pdf_app
    with client_cls(make()) as client:
        client.portal.call(functools.partial(_seed_pack, "t1"))
        detect = {"X-API-KEY": "dps_detect", "X-DPS-Tenant": "t1"}
        assert client.get("/api/v1/evidence/ev_pdf/pdf", headers=detect).status_code == 403
        other = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t2"}
        r = client.get("/api/v1/evidence/ev_pdf/pdf", headers=other)
        assert r.status_code == 404 and r.json()["code"] == "evidence_not_found"
    with client_cls(make(report_pdf_enabled=False)) as client:
        jobs_key = {"X-API-KEY": "dps_jobs", "X-DPS-Tenant": "t1"}
        r = client.get("/api/v1/evidence/ev_pdf/pdf", headers=jobs_key)
        assert r.status_code == 404 and r.json()["code"] == "pdf_disabled"


async def _seed_rollup(tenant: str) -> None:
    from dps import db

    async with db.acquire(tenant) as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident()}.dps_audit_log (tenant_id, route, action, outcome, counts) "
            "VALUES ($1, '/api/v1/obfuscate', 'obfuscate', 'ok', '{\"findings_total\": 2}')",
            tenant,
        )


@pytest.mark.db
def test_rollup_pdf_route(pdf_app) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    client_cls, make = pdf_app
    with client_cls(make()) as client:
        client.portal.call(functools.partial(_seed_rollup, "t1"))
        day = datetime.now(UTC).date().isoformat()
        h = {"X-API-KEY": "dps_detect", "X-DPS-Tenant": "t1"}
        r = client.get(
            f"/api/v1/reports/api-rollups/{day}",
            params={"action": "obfuscate", "format": "pdf"},
            headers=h,
        )
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert f"api-rollup-{day}-obfuscate.pdf" in r.headers["content-disposition"]
        assert "obfuscate" in _text(r.content)
