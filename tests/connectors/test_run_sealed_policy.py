"""FINAL r1 B1: a run whose policy is an inline body or a stored ``@name`` reaches ``succeeded``
with its evidence pack.

Since S3 review F-18 the job row keeps only a value-free label of such a policy (``sealed``,
``sha256``, ``name``, ``version``) and the body travels sealed in ``spec_enc``. Finalize validated
the redacted row spec, so the post-write review asked the engine for the label as a policy,
failed ``policy_invalid`` on every retry and dead-lettered the job after its whole target was
written — without an evidence pack. Presets (``spec.policy`` ``None`` or a preset name) never
met it, which is why every earlier suite passed. Each test here runs through the in-process
worker (``drive``) and asserts the terminal state, the pack, and the policy name and version
the pack records; the source-vs-target checks are counts only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection
from dps.engine.policy import load_preset
from dps.jobs import JobState
from dps.jobs_models import ObjectRef, is_policy_label
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.policy_store import create_policy, publish, resolve_ref, save_draft
from dps.worker import HandlerContext
from tests.connectors.helpers import db_key_enc, discover_and_accept, drive
from tests.connectors.test_run_modes import run_spec
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
RegisterSource = Callable[..., Awaitable[Connection]]
ROWS = 1200


def inline_policy(preset: str, *, name: str, version: int, **extra: Any) -> dict[str, Any]:
    """A full policy body (a preset's, renamed and re-versioned): what a caller posts inline."""
    body = load_preset(preset).model_dump(mode="json")
    body.update(name=name, version=version, **extra)
    return body


async def stored_policy(tenant: str, name: str, preset: str) -> dict[str, Any]:
    """Adopt ``preset`` as ``name`` (v1), publish a tuned draft as v2 and resolve ``@name`` as the
    API does at accept time (``policy_refs.resolve``): the stored v2 body."""
    await create_policy(tenant, name, preset, actor="tester")
    body = load_preset(preset).model_dump(mode="json")
    body["detect"]["min_score"] = 0.6
    await save_draft(tenant, name, body, actor="tester")
    await publish(tenant, name, change_ticket="CHG-90001", note="tuned", actor="tester")
    resolved = await resolve_ref(f"@{name}", tenant)
    assert isinstance(resolved, dict) and resolved["version"] == 2
    return resolved


async def _table_run(
    ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    *,
    table: str,
    mode: str,
    policy: dict[str, Any],
    **extra: Any,
) -> tuple[Any, ObjectRef]:
    obj = await source_pg.customers_table(rows=ROWS)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(ctx, "t1", src.id, obj)
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": table},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap.id, target, mode, policy=policy, **extra),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    assert is_policy_label(sub.job.spec["policy"])  # F-18: the row keeps the label only
    return await drive(ctx, "t1", sub.job.id), obj


async def _pack(job: Any) -> Any:
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.evidence_id is not None
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.outcome == "succeeded"
    assert await db_key_enc(job.id) is None  # the key is purged at the terminal state
    return ev.pack


async def _survivors(source_pg: SourcePg, table: str, column: str) -> int:
    """Target values of ``column`` equal to a source value of it (counts only)."""
    s = source_pg.schema
    return int(
        await source_pg.admin.fetchval(
            f'SELECT count(*) FROM "{s}"."{table}" t '
            f'JOIN (SELECT DISTINCT {column} AS v FROM "{s}".customers) c ON t.{column} = c.v'
        )
    )


async def test_an_inline_policy_run_finalizes_with_its_pack(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    policy = inline_policy("gdpr-pseudonymise", name="kyc-inline", version=3)
    job, _ = await _table_run(
        handler_ctx,
        source_pg,
        register_source,
        table="customers_inline",
        mode="obfuscate",
        policy=policy,
    )
    pack = await _pack(job)
    assert pack.policy["name"] == "kyc-inline" and pack.policy["version"] == "3"
    assert pack.verification["passed"] is True and pack.verification["status"] == "passed"
    assert pack.verification["post_sample"]["rows"] == 200  # the review ran under the body
    assert "error" not in pack.verification["post_sample"]
    assert job.progress.rows_written == ROWS
    for column in ("email", "national_id", "mobile"):
        assert await _survivors(source_pg, "customers_inline", column) == 0, column


async def test_a_stored_policy_run_finalizes_with_its_name_and_version(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    policy = await stored_policy("t1", "retail-kyc-r1", "gdpr-pseudonymise")
    job, _ = await _table_run(
        handler_ctx,
        source_pg,
        register_source,
        table="customers_stored",
        mode="obfuscate",
        policy=policy,
    )
    pack = await _pack(job)
    assert pack.policy["name"] == "retail-kyc-r1" and pack.policy["version"] == "2"
    assert pack.verification["passed"] is True
    assert "error" not in pack.verification["post_sample"]
    events = [e for e in handler_ctx.audit.events if e.action == "job.finalize"]  # type: ignore[attr-defined]
    assert events and events[-1].job_id == job.id and events[-1].outcome == "succeeded"
    assert events[-1].policy == "retail-kyc-r1"
    for column in ("email", "national_id"):
        assert await _survivors(source_pg, "customers_stored", column) == 0, column


async def test_a_copula_run_with_an_inline_policy_shows_utility_in_its_pack(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    pytest.importorskip("scipy")
    policy = inline_policy(
        "synthetic-realistic",
        name="synth-utility",
        version=4,
        synthesize={
            **load_preset("synthetic-realistic").model_dump(mode="json")["synthesize"],
            "utility_label": "$.segment",
        },
    )
    job, _ = await _table_run(
        handler_ctx,
        source_pg,
        register_source,
        table="customers_copula",
        mode="synthesize",
        policy=policy,
        options={"tabular": "copula"},
    )
    pack = await _pack(job)
    assert pack.policy["name"] == "synth-utility" and pack.policy["version"] == "4"
    assert pack.utility is not None and pack.utility["run"] is True, pack.utility
    assert pack.privacy is not None and pack.privacy["membership_inference"]["run"] is True
    post = pack.verification["post_sample"]
    assert "error" not in post and post["rows"] == 200
    assert "identifier_check" in post and "error" not in post["identifier_check"]
    for column in ("email", "national_id"):
        assert await _survivors(source_pg, "customers_copula", column) == 0, column


async def test_a_stored_policy_synthesize_run_finalizes(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    policy = await stored_policy("t1", "synth-kyc-r1", "synthetic-realistic")
    job, _ = await _table_run(
        handler_ctx,
        source_pg,
        register_source,
        table="customers_people",
        mode="synthesize",
        policy=policy,
    )
    pack = await _pack(job)
    assert pack.policy["name"] == "synth-kyc-r1" and pack.policy["version"] == "2"
    assert "error" not in pack.verification["post_sample"]
    for column in ("email", "national_id"):
        assert await _survivors(source_pg, "customers_people", column) == 0, column
