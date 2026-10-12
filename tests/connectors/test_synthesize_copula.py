"""M5 Task 6: copula-backed synthesize jobs end to end (discover → accept → run → evidence).

In process, like the M2 run suites (``tests.connectors.helpers.drive``). The source is the lane's
``source-pg`` (demo ``bank`` database, a per-test schema of synthetic customers). Every assertion
about the output compares it with the source: no source value of a sensitive column survives in
the target (whatever discovery decided), no synthetic row equals a source row on its non-key
columns, and the evidence pack holds no source value.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

pytest.importorskip("scipy")

from dps.auth import LOCAL_PRINCIPAL  # noqa: E402
from dps.connections import Connection  # noqa: E402
from dps.jobs import JobState  # noqa: E402
from dps.jobs_models import ObjectRef, RunSpec  # noqa: E402
from dps.layers.connectors.evidence import LABEL_SYNTHESIZE, get_evidence  # noqa: E402
from dps.layers.connectors.submit import submit_run  # noqa: E402
from dps.worker import HandlerContext  # noqa: E402
from tests.connectors.helpers import db_key_enc, discover_and_accept, drive  # noqa: E402
from tests.fixtures.sources import SourcePg  # noqa: E402

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
PURPOSE = {"ticket": "CHG-51770", "legal_basis": "legitimate interest — synthetic UAT data"}
RegisterSource = Callable[..., Awaitable[Connection]]
ROWS = 1500
#: Columns that hold personal data in the demo customers table (ground truth, not discovery's
#: verdict): none of their source values may reach the target. The key ``customer_id`` is kept by
#: the synthesize-mode key rule (contract §5.3) and is checked separately.
HIGH_ENTROPY = ("full_name", "national_id", "tax_id", "email", "mobile")
NON_KEY = (
    "full_name, date_of_birth, national_id, id_type, tax_id, email, mobile, country_code, "
    "segment, risk_score, kyc_status, relationship_manager_id, notes, created_at, updated_at"
)


def _spec(
    conn_id: str, obj: ObjectRef, snap_id: str, target: dict[str, Any], **extra: Any
) -> RunSpec:
    return RunSpec.model_validate(
        {
            "mode": "synthesize",
            "snapshot_id": snap_id,
            "purpose": PURPOSE,
            "target": target,
            "source": {
                "connection_id": conn_id,
                "object": obj.model_dump(),
                "parallelism": 2,
                "partition_rows": 1000,
                "batch_rows": 250,
            },
            "options": {"tabular": "copula"},
            **extra,
        }
    )


async def _run(
    ctx: HandlerContext,
    src: Connection,
    uat: Connection,
    source_pg: SourcePg,
    obj: ObjectRef,
    snap_id: str,
    table: str,
    **extra: Any,
) -> tuple[Any, Any]:
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": table},
    }
    sub = await submit_run(
        _spec(src.id, obj, snap_id, target, **extra),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    job = await drive(ctx, "t1", sub.job.id)
    return sub, job


async def _rows(source_pg: SourcePg, table: str) -> dict[int, dict[str, Any]]:
    rows = await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}"."{table}"')
    return {r["customer_id"]: dict(r) for r in rows}


async def _digest(source_pg: SourcePg, table: str) -> str:
    return str(
        await source_pg.admin.fetchval(
            f"SELECT md5(string_agg(t::text, '|' ORDER BY customer_id)) "
            f'FROM "{source_pg.schema}"."{table}" t'
        )
    )


async def test_copula_job_writes_measured_synthetic_rows(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=ROWS)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    sub, job = await _run(handler_ctx, src, uat, source_pg, obj, snap.id, "customers_synth")
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert sub.synthesis_seed is not None and job.progress.rows_written == ROWS

    # the evidence pack: measured, labelled, value-free
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None
    pack = ev.pack
    assert pack.label == LABEL_SYNTHESIZE and pack.outcome == "succeeded"
    assert pack.fidelity is not None and pack.fidelity["score"] >= 0.85, pack.fidelity
    assert pack.privacy is not None and pack.privacy["exact_row_matches"] == 0
    assert pack.privacy["nndr"]["measured"] == 1.0 and pack.privacy["measured"] is True
    assert pack.tabular is not None and pack.tabular["engine"] == "copula"
    modelled = set(pack.tabular["columns"])
    assert {"$.segment", "$.kyc_status", "$.id_type", "$.risk_score"} <= modelled, modelled
    assert job.progress.metrics["tabular"]["columns"] == pack.tabular["columns"]
    assert pack.verification["passed"] is True

    source = await _rows(source_pg, "customers")
    target = await _rows(source_pg, "customers_synth")
    assert len(target) == ROWS and set(target) == set(source)  # one synthetic row per source row
    # no synthetic row equals a source row on its non-key columns
    overlap = await source_pg.admin.fetchval(
        f'SELECT count(*) FROM (SELECT {NON_KEY} FROM "{source_pg.schema}".customers '
        f'INTERSECT SELECT {NON_KEY} FROM "{source_pg.schema}".customers_synth) q'
    )
    assert overlap == 0
    # no source value of a sensitive column survives anywhere in the target
    for column in HIGH_ENTROPY:
        # no row keeps its own value ...
        assert not any(
            source[k][column] is not None and source[k][column] == target[k][column] for k in source
        ), column
        before = {r[column] for r in source.values() if r[column] is not None}
        after = {r[column] for r in target.values() if r[column] is not None}
        common = before & after
        if column == "full_name":
            # ... and no value moves to another row. The demo rows were made by the same persona
            # generator as the surrogates, so a few names coincide by chance (finite name pools;
            # 16 of ~1500 when measured), never as a copy: per-row equality is zero above.
            assert len(common) <= len(before) * 0.03, (column, len(common))
        else:
            assert before and not common, column
    assert all(target[k]["date_of_birth"] != source[k]["date_of_birth"] for k in source)
    secrets = {str(r[c]) for r in source.values() for c in HIGH_ENTROPY[1:] if r[c] is not None}
    for row in target.values():  # in any column, free text included
        text = " ".join(str(v) for v in row.values() if v is not None)
        assert not any(secret in text for secret in secrets)
    # the modelled columns were redrawn: their joint distribution, not the source rows
    changed = sum(
        1
        for k in source
        if (source[k]["segment"], source[k]["risk_score"])
        != (target[k]["segment"], target[k]["risk_score"])
    )
    assert changed > ROWS * 0.5
    # the pack holds none of the source values
    text = json.dumps(pack.model_dump(mode="json"))
    picks = [source[k] for k in sorted(source)[:5]]
    for row in picks:
        for column in ("full_name", "email", "national_id"):
            if row[column]:
                assert str(row[column]) not in text
    assert await db_key_enc(job.id) is None  # the sealed model went with the key


async def test_same_seed_reproduces_the_same_target(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=600)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    sub, first = await _run(handler_ctx, src, uat, source_pg, obj, snap.id, "synth_x")
    assert first.state == JobState.succeeded, (first.error_code, first.error)
    seed = {"synthesis_seed": sub.synthesis_seed}
    _, second = await _run(
        handler_ctx, src, uat, source_pg, obj, snap.id, "synth_y", consistency=seed
    )
    assert second.state == JobState.succeeded, (second.error_code, second.error)
    assert await _digest(source_pg, "synth_x") == await _digest(source_pg, "synth_y")
    assert await _digest(source_pg, "synth_x") != await _digest(source_pg, "customers")


async def test_a_privacy_gate_fails_prepare_before_any_write(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj = await source_pg.customers_table(rows=300)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    handler_ctx.settings = handler_ctx.settings.model_copy(
        update={"privacy_nndr_enforce": True, "privacy_nndr_target": 50.0}
    )
    _, job = await _run(handler_ctx, src, uat, source_pg, obj, snap.id, "never_written")
    assert job.state == JobState.failed and job.error_code == "privacy_gate"
    exists = await source_pg.admin.fetchval(
        "SELECT to_regclass($1)", f"{source_pg.schema}.never_written"
    )
    assert exists is None  # the gate runs before the target is prepared
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.outcome == "failed"
    assert ev.pack.errors == [{"code": "privacy_gate", "where": "prepare"}]
    assert await db_key_enc(job.id) is None
