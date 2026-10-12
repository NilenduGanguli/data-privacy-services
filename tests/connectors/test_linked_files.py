"""M5 Task 7B: MOSTLY linked generation end to end — parent and child tables of the lane's
``source-pg`` to a PVC files target (discover both → run → two partitions → one manifest).

The ``synth-ml`` extra (torch) is not installed in this lane, so the SDK trainer is replaced by a
stand-in that draws every column independently from the training sample (the job flow, sealing,
writing, manifest and evidence are the real ones). The SDK itself is exercised by the ``ml`` tests
of ``tests/test_mostly_linked.py`` where the extra is installed. Every assertion about the output
compares it with the source: no source key, name, e-mail, national id or free text reaches the files.
"""

from __future__ import annotations

import gzip
import json
import random
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("scipy")

import dps.engine.synth.linked as linked_module  # noqa: E402
import dps.engine.synth.mostly as mostly_module  # noqa: E402
from dps.auth import LOCAL_PRINCIPAL  # noqa: E402
from dps.connections import Connection  # noqa: E402
from dps.jobs import JobState  # noqa: E402
from dps.jobs_models import RunSpec  # noqa: E402
from dps.layers.connectors.evidence import LABEL_SYNTHESIZE, get_evidence  # noqa: E402
from dps.layers.connectors.submit import submit_run  # noqa: E402
from dps.worker import HandlerContext  # noqa: E402
from tests.connectors.helpers import (  # noqa: E402
    db_key_enc,
    discover_and_accept,
    drive,
    json_from_export,
    register_pvc,
)
from tests.fixtures.sources import SourcePg  # noqa: E402

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
RegisterSource = Callable[..., Awaitable[Connection]]
PURPOSE = {"ticket": "CHG-51800", "legal_basis": "legitimate interest — synthetic test data"}
TXN_DDL = (
    "txn_id bigint PRIMARY KEY, customer_id bigint NOT NULL, amount numeric(12,2) NOT NULL, "
    "channel varchar(8) NOT NULL, ts timestamptz NOT NULL, memo text"
)


def stand_in_trainer(
    parent: dict[str, list[Any]],
    child: dict[str, list[Any]],
    layout: Any,
    subjects: int,
    minutes: int,
    dp: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Independent draws from each training column, text categories seen fewer than five times
    left out (MOSTLY's rare-category protection), as pandas would hand them back."""
    rng = random.Random(subjects)

    def pool(values: list[Any]) -> list[Any]:
        counts = Counter(values)
        kept = [v for v in values if not isinstance(v, str) or counts[v] >= 5]
        return kept or [None]

    pcols = {c: pool(v) for c, v in parent.items() if c != layout.parent_key}
    ccols = {c: pool(v) for c, v in child.items() if c != layout.foreign_key}
    parents = [{c: rng.choice(v) for c, v in pcols.items()} for _ in range(subjects)]
    children = [
        {"__parent": i, **{c: rng.choice(v) for c, v in ccols.items()}}
        for i in range(subjects)
        for _ in range(1 + i % 4)
    ]
    return parents, children


async def _transactions(source_pg: SourcePg, customers: int) -> Any:
    rows = []
    txn = 0
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(customers):
        for k in range(1 + i % 5):
            txn += 1
            rows.append(
                (
                    50_000_000 + txn,
                    10_000_001 + i,
                    Decimal(f"{(i * 37 + k * 11) % 5000}.{k:02d}"),
                    ["upi", "card", "neft"][(i + k) % 3],
                    start + timedelta(days=k, hours=i % 24),
                    f"ref {i}-{k}" if k % 2 else None,
                )
            )
    return await source_pg.table("transactions", TXN_DDL, rows)


async def test_linked_generation_writes_two_tables_and_one_manifest(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mostly_module, "_find_spec", lambda name: object())  # "installed"
    monkeypatch.setattr(linked_module, "_sdk_linked_trainer", stand_in_trainer)
    handler_ctx.settings = handler_ctx.settings.model_copy(
        update={"synth_tabular_engine": "copula,mostly"}
    )
    customers = await source_pg.customers_table(rows=300)
    transactions = await _transactions(source_pg, 300)
    src = await register_source("t1")
    pvc = await register_pvc("t1", handler_ctx, base_path="exports")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, customers)
    child_snap = await discover_and_accept(handler_ctx, "t1", src.id, transactions)
    spec = RunSpec.model_validate(
        {
            "mode": "synthesize",
            "snapshot_id": snap.id,
            "purpose": PURPOSE,
            "source": {"connection_id": src.id, "object": customers.model_dump()},
            "target": {
                "write_mode": "files",
                "connection_id": pvc.id,
                "files": {"format": "jsonl", "compression": "gzip", "prefix": "linked"},
            },
            "options": {
                "tabular": "mostly",
                "tabular_dp": True,
                "linked": {
                    "child": transactions.model_dump(),
                    "child_snapshot_id": child_snap.id,
                    "parent_key": "customer_id",
                    "foreign_key": "customer_id",
                    "order_by": "ts",
                    "subjects": 60,
                },
            },
        }
    )
    sub = await submit_run(
        spec,
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    assert job.progress.partitions_total == 2

    manifest = json_from_export(handler_ctx, f"exports/linked/{job.id}/manifest.json")
    assert set(manifest["tables"]) == {"parent", "child"}
    assert manifest["tables"]["parent"]["object"] == customers.fqn()
    assert manifest["tables"]["child"]["object"] == transactions.fqn()
    root = Path(handler_ctx.settings.export_root) / "exports"
    out: dict[str, list[dict[str, Any]]] = {}
    for role, table in manifest["tables"].items():
        rows: list[dict[str, Any]] = []
        for item in table["objects"]:
            assert item["key"].startswith(f"linked/{table['dir']}/{job.id}/part-")
            with gzip.open(root / item["key"], "rt", encoding="utf-8") as fh:
                rows += [json.loads(line) for line in fh]
        assert len(rows) == table["rows_total"]
        out[role] = rows
    parents, children = out["parent"], out["child"]
    ev = await get_evidence("t1", job.progress.evidence_id or "")
    assert ev is not None and ev.pack.privacy is not None
    # the stand-in draws columns from real values, so a generated transaction can equal a real
    # one on every written column by chance: those are rejected (dropped and counted)
    rejected = ev.pack.privacy["exact_row_rejections"]
    assert len(parents) == 60 and len(children) + rejected == sum(1 + i % 4 for i in range(60))
    assert rejected <= 10 and ev.pack.privacy["exact_row_matches"] == 0
    assert manifest["rows_total"] == len(parents) + len(children)
    # new keys, referential integrity, ordered sequences
    keys = [p["customer_id"] for p in parents]
    assert sorted(keys) == list(range(1, 61))  # no parent can equal a real one: names differ
    assert {c["customer_id"] for c in children} <= set(keys)
    assert len({c["txn_id"] for c in children}) == len(children)
    by_parent: dict[int, list[str]] = {}
    for c in children:
        by_parent.setdefault(c["customer_id"], []).append(c["ts"])
    assert all(ts == sorted(ts) for ts in by_parent.values())

    # nothing of the source survives: keys, names, e-mails, national ids, free text
    source = await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".customers')
    source_txn = await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".transactions')
    source_keys = {r["customer_id"] for r in source} | {r["txn_id"] for r in source_txn}
    written_keys = {p["customer_id"] for p in parents} | {
        v for c in children for v in (c["customer_id"], c["txn_id"])
    }
    assert not source_keys & written_keys
    secrets = {
        str(r[c])
        for r in source
        for c in ("email", "national_id", "tax_id", "mobile")
        if r[c] is not None
    } | {r["memo"] for r in source_txn if r["memo"]}
    written = json.dumps(out)
    leaked = [secret for secret in secrets if secret in written]
    assert not leaked, len(leaked)
    assert all(p["notes"] is None for p in parents)  # free text is not generated
    # names: the surrogate input is a placeholder, never a source value, but the demo rows and
    # the surrogates draw on the same persona name pools, so a few coincide by chance (Task 6)
    names = {r["full_name"] for r in source}
    generated = [p["full_name"] for p in parents]
    assert all(generated) and sum(n in names for n in generated) <= len(generated) * 0.1

    assert ev.pack.label == LABEL_SYNTHESIZE
    tabular = ev.pack.tabular
    assert tabular is not None and tabular["engine"] == "mostly"
    assert tabular["linked"]["subjects"] == 60 and tabular["linked"]["ordered"] is True
    assert tabular["linked"]["child"] == transactions.fqn()
    assert tabular["dp"]["enabled"] is True and tabular["dp"]["max_epsilon"] == 5.0
    assert ev.pack.privacy is not None and ev.pack.privacy["exact_row_matches"] == 0
    assert ev.pack.fidelity is not None and set(ev.pack.fidelity["tables"]) == {"parent", "child"}
    assert ev.pack.target["files"]["objects"] == len(manifest["tables"]["parent"]["objects"]) + len(
        manifest["tables"]["child"]["objects"]
    )
    assert not any(secret in json.dumps(ev.pack.model_dump(mode="json")) for secret in names)
    assert await db_key_enc(job.id) is None
