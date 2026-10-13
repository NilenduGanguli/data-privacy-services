"""No source PII value survives a masked copy of the demo tables (integrated verify r1, B1).

The 10-table demo's UAT copy and its in-place and append outputs kept ``contacts.value``,
``cards.cardholder_name``, ``kyc_documents.doc_number`` and ``addresses.line1`` raw while every
evidence pack said "verified · 0 leaks". These tests load the demo's own rows
(``tools/seed_sources.generate_rows``: surrogate values, no real data) into a scratch schema, run
discovery and a job end to end, and compare the target with the source — counts only, no value is
printed. ``addresses.city`` is classified ``CITY`` and kept by the policy (``CITY: keep`` in
``gdpr-pseudonymise``): a declared decision in the snapshot and the evidence, asserted as such.

A run whose accepted snapshot keeps a personal-data column as not personal must end with its
verification ``review_required``, never "passed".
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from functools import cache
from typing import Any

import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection
from dps.jobs import JobState
from dps.jobs_models import ObjectRef, RunSpec, SnapshotOverride
from dps.layers.connectors.evidence import get_evidence, list_evidence
from dps.layers.connectors.submit import submit_run
from dps.snapshots import MIXED_PII
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive
from tests.fixtures.sources import SourcePg
from tools.seed_sources import TABLE_DDL, columns_of, generate_rows

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
RegisterSource = Callable[..., Awaitable[Connection]]
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest — UAT provisioning"}
#: The personal-data columns the demo's UAT copy kept raw, per table.
PII = {
    "contacts": ("value",),
    "cards": ("cardholder_name",),
    "kyc_documents": ("doc_number",),
    "addresses": ("line1",),
}


@cache
def demo_rows() -> dict[str, list[dict[str, Any]]]:
    return generate_rows(rows=300, seed=5)


async def load(source_pg: SourcePg, table: str) -> tuple[ObjectRef, list[dict[str, Any]]]:
    """The demo table (its DDL without foreign keys) with the demo's rows."""
    ddl = re.sub(r"\s+REFERENCES \{s\}\.\w+", "", TABLE_DDL[table])
    rows = demo_rows()[table]
    names = columns_of(table)
    obj = await source_pg.table(table, ddl, [tuple(r[c] for c in names) for r in rows])
    return obj, rows


def run_spec(conn_id: str, obj: ObjectRef, snap_id: str, target: dict[str, Any]) -> RunSpec:
    return RunSpec.model_validate(
        {
            "mode": "obfuscate",
            "snapshot_id": snap_id,
            "purpose": PURPOSE,
            "target": target,
            "source": {
                "connection_id": conn_id,
                "object": obj.model_dump(),
                "parallelism": 2,
                "partition_rows": 1000,
                "batch_rows": 200,
            },
        }
    )


def survivors(out: list[Any], source_values: set[str]) -> int:
    """Output cells (any column) equal to a source PII value — a count, never a value."""
    return sum(1 for r in out for v in r.values() if v is not None and str(v) in source_values)


@pytest.mark.parametrize("table", sorted(PII))
async def test_no_source_pii_value_survives_a_uat_copy(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    table: str,
) -> None:
    obj, rows = await load(source_pg, table)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    cols = {c.name: c for c in snap.columns}
    for name in PII[table]:
        assert cols[name].entity_type is not None, f"{table}.{name} kept as not personal"
        assert cols[name].method.method != "keep", f"{table}.{name}"
    if table == "contacts":
        assert cols["value"].entity_type == MIXED_PII
    if table == "addresses":
        assert cols["city"].entity_type == "CITY" and cols["city"].method.method == "keep"
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": f"{table}_uat"},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    out = [
        dict(r)
        for r in await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".{table}_uat')
    ]
    assert len(out) == len(rows)
    source_values = {str(r[n]) for r in rows for n in PII[table] if r[n] is not None}
    assert source_values and survivors(out, source_values) == 0, f"{table}: raw PII in the copy"
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None
    v = ev.pack.verification
    assert v["passed"] and v["status"] == "passed" and not v["review_required"]
    assert v["post_sample"]["kept_column_hits"] == 0 and v["post_sample"]["kept_columns"] == []


async def test_in_place_replaces_every_contact_value(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj, rows = await load(source_pg, "contacts")
    rw = await register_source("t1", ref="dps/src/test-pg-rw", roles={"source", "target"})
    snap = await discover_and_accept(
        handler_ctx,
        "t1",
        rw.id,
        obj,
        overrides=[SnapshotOverride(path="$.contact_id", method={"method": "keep"})],
    )
    target = {"write_mode": "in_place", "confirm_write": True, "expected_rows": len(rows)}
    sub = await submit_run(
        run_spec(rw.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    out = {
        r["contact_id"]: r["value"]
        for r in await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".contacts')
    }
    unchanged = sum(1 for r in rows if out[r["contact_id"]] == r["value"])
    assert len(out) == len(rows) and unchanged == 0, "contact values left in place"
    assert survivors([{"v": v} for v in out.values()], {r["value"] for r in rows}) == 0
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"]


async def test_a_personal_column_kept_as_not_personal_requires_review(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    """The owner overrides ``value`` to not personal: the copy holds raw contacts, and the pack,
    the job and the evidence list say review required — never verified, never 0 leaks."""
    obj, _ = await load(source_pg, "contacts")
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(
        handler_ctx,
        "t1",
        src.id,
        obj,
        overrides=[SnapshotOverride(path="$.value", entity_type=None)],
    )
    assert next(c for c in snap.columns if c.name == "value").entity_type is None
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "contacts_kept"},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, job.error_code
    assert job.progress.verification.get("kept_hits", 0) > 0
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None
    v = ev.pack.verification
    assert v["passed"] is False and v["review_required"] and v["status"] == "review_required"
    assert v["hits"] == 0 and v["post_sample"]["kept_columns"] == ["value"]
    items, _ = await list_evidence("t1", job_id=job.id)
    assert items and items[0].passed is False and items[0].review_required


# -- WP-I (M5 merge): the discovery fix holds for synthesize runs (D-A) -------------------------


def synth_spec(conn_id: str, obj: ObjectRef, snap_id: str, target: dict[str, Any]) -> RunSpec:
    spec = run_spec(conn_id, obj, snap_id, target).model_dump(mode="json", exclude_none=True)
    return RunSpec.model_validate({**spec, "mode": "synthesize"})


@pytest.mark.parametrize("table", sorted(PII))
async def test_no_source_pii_value_survives_a_synthetic_copy(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    table: str,
) -> None:
    """A mixed-PII column is never kept by synthesize either: it is planned (surrogate), and the
    synthetic table holds no source value of any personal column (counts only)."""
    obj, rows = await load(source_pg, table)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    cols = {c.name: c for c in snap.columns}
    for name in PII[table]:
        assert cols[name].entity_type is not None, f"{table}.{name} kept as not personal"
        assert cols[name].synthesize_method.method != "keep", f"{table}.{name}"
    if table == "contacts":
        assert cols["value"].entity_type == MIXED_PII
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": f"{table}_synth"},
    }
    sub = await submit_run(
        synth_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    out = [
        dict(r)
        for r in await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".{table}_synth')
    ]
    assert len(out) == len(rows)
    for name in PII[table]:
        source_values = {str(r[name]) for r in rows if r[name] is not None}
        assert len(source_values) > 0
        found = survivors(out, source_values)  # a count; the values are never printed
        if cols[name].entity_type == "PERSON_NAME":
            # surrogate names come from a finite name pool (the demo source was drawn from the
            # same pool): a few unrelated rows share a name by chance (M5 verify N1)
            assert found <= len(rows) // 20, f"{table}.{name}: {found} names survive"
        else:
            assert found == 0, f"{table}.{name}: {found} raw values in synthetic"
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None
    v = ev.pack.verification
    # whatever the verdict, it is never "passed" while a kept column held personal data
    if v["passed"]:
        assert v["status"] == "passed" and not v["review_required"], v["status"]
        assert v["post_sample"]["kept_column_hits"] == 0 and not v["review_columns"]


async def test_a_synthetic_copy_that_keeps_a_personal_column_requires_review(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    """Main's post-write gate and M5's D-A review agree on a synthesize run: ``value`` reviewed
    as not personal is copied as it is, so the pack says review required with the column named
    by both checks, and the evidence list carries the flag."""
    obj, _ = await load(source_pg, "contacts")
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(
        handler_ctx,
        "t1",
        src.id,
        obj,
        overrides=[SnapshotOverride(path="$.value", entity_type=None)],
    )
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "contacts_synth_kept"},
    }
    sub = await submit_run(
        synth_spec(src.id, obj, snap.id, target),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None
    v = ev.pack.verification
    assert v["passed"] is False and v["review_required"] is True
    assert v["status"] == "review_required" and v["hits"] == 0
    assert "value" in v["review_columns"]
    assert v["post_sample"]["kept_columns"] == ["value"]
    assert v["post_sample"]["synthetic_kept"]["kept_columns"] == ["value"]
    items, _ = await list_evidence("t1", job_id=job.id)
    assert items and items[0].passed is False and items[0].review_required
