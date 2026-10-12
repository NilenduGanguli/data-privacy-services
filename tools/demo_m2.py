"""The M2 milestone demo (spec §11 M2): mask a 10-table PostgreSQL schema into a UAT copy, then
mask one table in place and append to another — every job with an evidence pack.

    python tools/demo_m2.py [--group bank-uat-refresh] [--parallel-jobs 2] [--timeout 1800]

Against the compose stack (profile ``sources``, seeded):

1. ``POST /job-groups`` — the group's key and seed are written to ``./var/demo/<group>.json``
   (git-ignored, mode 0600, local only) and only the key id is printed; a rerun reuses the file;
2. each of the ten ``bank`` tables: discover → accept → ``table`` run (obfuscate,
   ``truncate_insert``) into ``bank_uat.<table>`` under the group key, parallelism 4, two jobs at a
   time;
3. a referential check through the source DSN (read-only ``count(*)``s): every foreign key of the
   UAT copy finds its parent — keys of one entity type under one key tokenise identically in
   every table;
4. in place: ``bank_restore.contacts`` (``confirm_write``, ``expected_rows`` from ``count``; the key
   is kept);
5. append: ``bank.transactions`` → ``bank_perf.transactions`` (synthesize under the group seed,
   ``pk_strategy: generate``);
6. a table ``step · job · rows · evidence · verification``; exit 1 on any failed job or orphan.

Nothing prints a value or a key; the key file is the operator's to keep (``tools/reveal_rows.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `python tools/demo_m2.py`

from tools.m2_api import ApiError, Client, source_dsn  # noqa: E402
from tools.seed_sources import TABLES  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parents[1] / "var" / "demo"
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest - UAT refresh"}
#: (child table, column, parent table, parent key) of the UAT copy.
FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("customers", "relationship_manager_id", "employees", "employee_id"),
    ("addresses", "customer_id", "customers", "customer_id"),
    ("contacts", "customer_id", "customers", "customer_id"),
    ("accounts", "customer_id", "customers", "customer_id"),
    ("cards", "account_id", "accounts", "account_id"),
    ("transactions", "account_id", "accounts", "account_id"),
    ("loans", "customer_id", "customers", "customer_id"),
    ("loan_applicants", "loan_id", "loans", "loan_id"),
    ("loan_applicants", "customer_id", "customers", "customer_id"),
    ("kyc_documents", "customer_id", "customers", "customer_id"),
    ("kyc_documents", "verified_by", "employees", "employee_id"),
)
#: The key of every table, kept in place.
KEYS = {
    "employees": "employee_id",
    "customers": "customer_id",
    "addresses": "address_id",
    "contacts": "contact_id",
    "accounts": "account_id",
    "cards": "card_id",
    "transactions": "txn_id",
    "loans": "loan_id",
    "loan_applicants": "applicant_id",
    "kyc_documents": "doc_ref",
}


@dataclass
class Step:
    """One row of the summary (value-free)."""

    step: str
    job_id: str = "-"
    state: str = "-"
    rows: int = 0
    evidence_id: str = "-"
    verification: str = "-"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The CLI (module docstring)."""
    p = argparse.ArgumentParser(prog="python tools/demo_m2.py")
    p.add_argument("--group", default="bank-uat-refresh")
    p.add_argument("--parallel-jobs", type=int, default=2)
    p.add_argument("--parallelism", type=int, default=4)
    p.add_argument("--timeout", type=float, default=1800.0)
    return p.parse_args(argv)


def ensure_group(client: Client, name: str) -> dict[str, Any]:
    """The demo job group with its key and seed: from ``var/demo/<name>.json`` when present (the
    key is returned once), else created; a name taken without its file gets a numbered suffix."""
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    for n in range(0, 100):
        candidate = name if n == 0 else f"{name}-{n}"
        path = DEMO_DIR / f"{candidate}.json"
        if path.is_file():
            saved: dict[str, Any] = json.loads(path.read_text("utf-8"))
            client.call("GET", f"/api/v1/job-groups/{saved['group']['id']}", "job group")
            return saved
        try:
            made = client.call(
                "POST", "/api/v1/job-groups", "job group", ok=(201,), json={"name": candidate}
            )
        except ApiError as exc:
            if exc.code == "group_name_taken":
                continue
            raise
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(made, fh)
        return dict(made)
    raise RuntimeError("no free job-group name")


def _summary(job: dict[str, Any], step: str) -> Step:
    progress = job.get("progress") or {}
    return Step(
        step=step,
        job_id=job["id"],
        state=job["state"],
        rows=int(progress.get("rows_written") or 0),
        evidence_id=str(progress.get("evidence_id") or "-"),
        verification="-",
    )


def _verification(client: Client, step: Step) -> Step:
    if step.evidence_id != "-":
        pack = client.call("GET", f"/api/v1/evidence/{step.evidence_id}", "evidence")["pack"]
        step.verification = "passed" if pack["verification"].get("passed") else "FAILED"
    return step


def mask_table(
    table: str, ids: dict[str, str], group: dict[str, Any], args: argparse.Namespace
) -> tuple[Step, str]:
    """Discover, accept and copy one table into ``bank_uat`` under the group key."""
    client = Client()
    snap = client.discover_and_accept(ids["bank-pg-ro"], "bank", table)
    out = client.submit_run(
        {
            "mode": "obfuscate",
            "snapshot_id": snap,
            "purpose": PURPOSE,
            "group_id": group["group"]["id"],
            "consistency": {"key_material": group["key_material"]},
            "source": {
                "connection_id": ids["bank-pg-ro"],
                "object": {"owner": "bank", "name": table},
                "parallelism": args.parallelism,
            },
            "target": {
                "write_mode": "table",
                "connection_id": ids["bank-uat"],
                "object": {"owner": "bank_uat", "name": table},
                "table_load": "truncate_insert",
            },
        }
    )
    job = client.wait(out["job"]["id"], args.timeout)
    return _verification(client, _summary(job, f"table {table}")), snap


async def orphans(dsn: str) -> dict[str, int]:
    """``{"child.column": orphan count}`` over the UAT copy (read-only counts)."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        out: dict[str, int] = {}
        for child, column, parent, key in FOREIGN_KEYS:
            out[f"{child}.{column}"] = int(
                await conn.fetchval(
                    f"SELECT count(*) FROM bank_uat.{child} c WHERE c.{column} IS NOT NULL "
                    f"AND NOT EXISTS (SELECT 1 FROM bank_uat.{parent} p WHERE p.{key} = c.{column})"
                )
            )
        return out
    finally:
        await conn.close()


def in_place(
    client: Client, ids: dict[str, str], group: dict[str, Any], args: argparse.Namespace
) -> Step:
    """Mask ``bank_restore.contacts`` in place (the key kept)."""
    conn_id = ids["bank-restore-rw"]
    snap = client.discover_and_accept(
        conn_id,
        "bank_restore",
        "contacts",
        overrides=[{"path": "$.contact_id", "method": {"method": "keep"}}],
    )
    rows = client.count(conn_id, "bank_restore.contacts")
    out = client.submit_run(
        {
            "mode": "obfuscate",
            "snapshot_id": snap,
            "purpose": PURPOSE,
            "group_id": group["group"]["id"],
            "consistency": {"key_material": group["key_material"]},
            "source": {
                "connection_id": conn_id,
                "object": {"owner": "bank_restore", "name": "contacts"},
                "parallelism": args.parallelism,
            },
            "target": {"write_mode": "in_place", "confirm_write": True, "expected_rows": rows},
        }
    )
    job = client.wait(out["job"]["id"], args.timeout)
    return _verification(client, _summary(job, "in place bank_restore.contacts"))


def append(
    client: Client, ids: dict[str, str], group: dict[str, Any], snap: str, args: argparse.Namespace
) -> Step:
    """Synthesize ``bank.transactions`` and append it to ``bank_perf.transactions``."""
    out = client.submit_run(
        {
            "mode": "synthesize",
            "snapshot_id": snap,
            "purpose": PURPOSE,
            "group_id": group["group"]["id"],
            "consistency": {"synthesis_seed": group["synthesis_seed"]},
            "source": {
                "connection_id": ids["bank-pg-ro"],
                "object": {"owner": "bank", "name": "transactions"},
                "parallelism": args.parallelism,
            },
            "target": {
                "write_mode": "append",
                "connection_id": ids["bank-restore-rw"],
                "object": {"owner": "bank_perf", "name": "transactions"},
                "confirm_write": True,
                "pk_strategy": {"kind": "generate"},
            },
        }
    )
    job = client.wait(out["job"]["id"], args.timeout)
    return _verification(client, _summary(job, "append bank_perf.transactions"))


def print_table(steps: list[Step]) -> None:
    """The summary (ids, states, counts, verdicts)."""
    print(f"{'step':<34} {'job':<30} {'state':<10} {'rows':>9}  {'evidence':<30} verification")
    for s in steps:
        print(
            f"{s.step:<34} {s.job_id:<30} {s.state:<10} {s.rows:>9}  {s.evidence_id:<30} "
            f"{s.verification}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the demo (module docstring); 0 when every job succeeded with passed verification."""
    args = parse_args(argv)
    client = Client()
    try:
        ids = client.ensure_connections()
        group = ensure_group(client, args.group)
        print(
            f"job group {group['group']['id']} (key id {group['group']['kid']}); "
            f"key and seed kept in var/demo/ (local only)"
        )
        steps: list[Step] = []
        snaps: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max(1, args.parallel_jobs)) as pool:
            for table, (step, snap) in zip(
                TABLES, pool.map(lambda t: mask_table(t, ids, group, args), TABLES), strict=True
            ):
                steps.append(step)
                snaps[table] = snap
        counts = asyncio.run(orphans(source_dsn()))
        bad = {k: v for k, v in counts.items() if v}
        steps.append(
            Step(
                step="referential check",
                state="ok" if not bad else "FAILED",
                rows=sum(counts.values()),
            )
        )
        steps.append(in_place(client, ids, group, args))
        steps.append(append(client, ids, group, snaps["transactions"], args))
    except ApiError as exc:
        print(f"demo failed ({exc})")
        return 1
    print_table(steps)
    print("orphans:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    failed = [s for s in steps if s.state not in ("succeeded", "ok") or s.verification == "FAILED"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
