"""FINAL r1 synthetic B3 (Layer 2): a surname that is a name-cue STOPWORD (``Date``, ``Le``,
``Best``, ``Branch``, ``Holder``) never survives a free-text column.

The name-cue run ended at such a word, so ``Mr. Rahul Date`` was found as ``Rahul`` only: the
synthetic and the obfuscated memo kept ``Date`` in the aligned row while the pack said verified.
Source vs target, row-aligned (counts only): no target memo keeps any name word of its own
source memo.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection
from dps.engine.synth.keys import KeyPseudonyms
from dps.engine.synth.surrogates import seed_bytes, seed_from_caller
from dps.jobs import JobState
from dps.jobs_models import ObjectRef, SnapshotOverride
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive
from tests.connectors.test_run_modes import run_spec
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
RegisterSource = Callable[..., Awaitable[Connection]]
SURNAMES = ("Date", "Le", "Best", "Branch", "Holder")
FIRST = ("Rahul", "Priya", "Meera", "Vikram", "Anita", "Arjun", "Kavya", "Sanjay")
OTHER = ("Sharma", "Iyer", "Nair", "Gupta", "Reddy", "Menon")
ROWS = 120
MEMO = re.compile(
    r"^Branch visit: Mr\. (.+?) met Mrs\. (.+?); Dr\. (.+?) witnessed the nomination\.$"
)


def _memo(i: int) -> str:
    a = f"{FIRST[i % 8]} {SURNAMES[i % 5]}"
    b = f"{FIRST[(i + 3) % 8]} {SURNAMES[(i + 2) % 5]}"
    c = f"{FIRST[(i + 5) % 8]} {OTHER[i % 6]}"
    return f"Branch visit: Mr. {a} met Mrs. {b}; Dr. {c} witnessed the nomination."


def _words(memo: str) -> set[str]:
    """The name words of a memo (its three people)."""
    m = MEMO.match(memo)
    assert m is not None
    return {w.casefold() for g in m.groups() for w in g.split()}


async def _setup(
    ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> tuple[ObjectRef, Connection, Connection, Any, dict[int, str]]:
    rows = [(43_000_000 + i, _memo(i)) for i in range(ROWS)]
    obj = await source_pg.table("memos", "memo_id bigint PRIMARY KEY, memo text NOT NULL", rows)
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    review = [SnapshotOverride(path="$.memo_id", entity_type=None, anchor=False)]
    snap = await discover_and_accept(ctx, "t1", src.id, obj, overrides=review)
    memo = next(c for c in snap.columns if c.name == "memo")
    assert memo.text_column, "the memo column is reviewed as free text"
    return obj, src, uat, snap, dict(rows)


async def _job(
    ctx: HandlerContext,
    src: Connection,
    uat: Connection,
    source_pg: SourcePg,
    obj: ObjectRef,
    snap_id: str,
    mode: str,
    table: str,
) -> tuple[Any, Any]:
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": table},
    }
    sub = await submit_run(
        run_spec(src.id, obj, snap_id, target, mode),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=ctx.settings,
        kek=ctx.kek,
    )
    job = await drive(ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.verification["passed"], ev and ev.pack.verification
    return sub, job


def _aligned_survivors(source: dict[int, str], target: dict[int, str]) -> int:
    """Target memos holding a name word of their own source memo (counts only)."""
    assert len(target) == len(source)
    return sum(1 for k, memo in target.items() if _words(memo) & _words(source[k]))


async def _target(source_pg: SourcePg, table: str) -> dict[int, str]:
    rows = await source_pg.admin.fetch(f'SELECT memo_id, memo FROM "{source_pg.schema}"."{table}"')
    return {int(r["memo_id"]): str(r["memo"]) for r in rows}


async def test_a_synthetic_memo_keeps_no_stopword_surname(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj, src, uat, snap, source = await _setup(handler_ctx, source_pg, register_source)
    sub, job = await _job(
        handler_ctx, src, uat, source_pg, obj, snap.id, "synthesize", "memos_synth"
    )
    keys = KeyPseudonyms(seed_bytes(seed_from_caller(sub.synthesis_seed)))
    assert job.plan is not None
    entry = next(p for p in job.plan["pseudonyms"] if p["name"] == "memo_id")
    label = str(entry.get("label") or entry["entity_type"])
    back = {keys.integer(label, k): k for k in source}
    target = {back[k]: memo for k, memo in (await _target(source_pg, "memos_synth")).items()}
    assert _aligned_survivors(source, target) == 0


async def test_an_obfuscated_memo_keeps_no_stopword_surname(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    obj, src, uat, snap, source = await _setup(handler_ctx, source_pg, register_source)
    await _job(handler_ctx, src, uat, source_pg, obj, snap.id, "obfuscate", "memos_obf")
    target = await _target(source_pg, "memos_obf")  # the unclassified key is kept
    kept = sum(
        1
        for k, memo in target.items()
        if any(
            re.search(rf"\b{re.escape(w)}\b", memo.removeprefix("Branch visit: "))
            for w in _source_names(source[k])
        )
    )
    assert len(target) == ROWS and kept == 0


def _source_names(memo: str) -> list[str]:
    """The source memo's name words, capitalised as written."""
    m = MEMO.match(memo)
    assert m is not None
    return [w for g in m.groups() for w in g.split()]
