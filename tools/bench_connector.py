"""Measure the job platform's throughput (spec §7 "Throughput", §11 M2 exit criterion).

    python tools/bench_connector.py --rows 200000 [--mode obfuscate|synthesize]
        [--write-mode table|in_place|files] [--parallelism 4] [--batch 1000]
        [--partition-rows 50000] [--base-url http://127.0.0.1:8400] [--api-key …]
        [--source-dsn postgresql://…/bank] [--label 1-worker] [--out docs/technical/bench]

Against the compose stack (profile ``sources``): (1) creates ``bench.customers_bench`` in the source
(the customers DDL; rows recycle a pool of 5 000 surrogate-engine customers — throughput does not
depend on distinct values) unless it already holds ``--rows`` rows; in-place runs use
``bench_restore.customers_bench``, a copy recreated before each run; (2) registers or reuses the
demo connections; (3) discovers and accepts (in place: the key kept, outputs that fit); (4) runs
the job and polls ``GET /jobs/{id}`` every second; (5) reads its partitions and ``GET
/jobs/stats``; (6) prints one line and writes
``<out>/m2-<date>-<mode>-<write_mode>-<label>.json``. The measured number is the result: nothing
is promised. Never prints a value, a key or a DSN; the run's key material is discarded unread.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `python tools/bench_connector.py`

from tools.m2_api import IN_PLACE_REVIEW, ApiError, Client, base_url, source_dsn  # noqa: E402
from tools.seed_sources import TABLE_DDL, columns_of, generate_rows  # noqa: E402

BENCH_SCHEMA, RESTORE_SCHEMA, TABLE = "bench", "bench_restore", "customers_bench"
POOL = 5000
PURPOSE = {"ticket": "BENCH-0001", "legal_basis": "legitimate interest - capacity measurement"}
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "docs" / "technical" / "bench"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The CLI (module docstring)."""
    p = argparse.ArgumentParser(prog="python tools/bench_connector.py")
    p.add_argument("--rows", type=int, default=200_000)
    p.add_argument("--mode", choices=("obfuscate", "synthesize"), default="obfuscate")
    p.add_argument("--write-mode", choices=("table", "in_place", "files"), default="table")
    p.add_argument("--parallelism", type=int, default=4)
    p.add_argument("--batch", type=int, default=1000)
    p.add_argument("--partition-rows", type=int, default=50_000)
    p.add_argument("--base-url", default=None, help="default: DPS_BASE_URL / DPS_APP_PORT")
    p.add_argument("--api-key", default=None, help="default: DPS_API_KEY / the local demo key")
    p.add_argument("--source-dsn", default=None, help="default: DPS_TEST_SOURCE_PG_DSN")
    p.add_argument("--label", default="1-worker")
    p.add_argument("--timeout", type=float, default=1800.0)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)
    if args.rows < 1 or args.parallelism < 1 or args.batch < 10:
        p.error("--rows ≥ 1, --parallelism ≥ 1 and --batch ≥ 10 are required")
    if args.write_mode == "in_place" and args.mode == "synthesize":
        p.error("in-place writes obfuscate (synthetic rows replace nothing in place)")
    return args


def _rate(p: dict[str, Any]) -> float | None:
    value = p.get("rows_per_second")
    return float(value) if isinstance(value, int | float) else None


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 1)
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return round(cuts[int(q) - 1], 1)


def summarise(
    job: dict[str, Any],
    partitions: list[dict[str, Any]],
    started: float,
    finished: float,
    **meta: Any,
) -> dict[str, Any]:
    """The result document: wall-clock rows/s (rows written ÷ elapsed) and the p50/p95 of the
    partitions' own rates, plus the run's parameters (``meta``)."""
    progress = job.get("progress") or {}
    rows = int(progress.get("rows_written") or 0)
    wall = max(finished - started, 1e-9)
    rates = [r for r in (_rate(p) for p in partitions) if r is not None]
    return {
        "rows": rows,
        **meta,
        "partitions": len(partitions),
        "wall_seconds": round(wall, 2),
        "rows_per_second": round(rows / wall, 1),
        "partition_rows_per_second": {
            "p50": _percentile(rates, 50),
            "p95": _percentile(rates, 95),
        },
        "values_by_method": {
            k: v
            for k, v in sorted((progress.get("values_by_method") or {}).items())
            if not k.startswith("col:")
        },
    }


def result_path(out: Path, mode: str, write_mode: str, label: str, when: datetime) -> Path:
    """``<out>/m2-<YYYY-MM-DD>-<mode>-<write_mode>-<label>.json``."""
    return out / f"m2-{when:%Y-%m-%d}-{mode}-{write_mode}-{label}.json"


# -- source preparation (host-side, superuser DSN) ------------------------------------------------


async def _prepare_source(dsn: str, rows: int, *, in_place: bool) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        ddl = TABLE_DDL["customers"].replace("REFERENCES {s}.employees", "")
        cols = columns_of("customers")
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{BENCH_SCHEMA}"')
        await conn.execute(f'CREATE TABLE IF NOT EXISTS "{BENCH_SCHEMA}".{TABLE} ({ddl})')
        have = int(await conn.fetchval(f'SELECT count(*) FROM "{BENCH_SCHEMA}".{TABLE}'))
        if have != rows:
            await conn.execute(f'TRUNCATE "{BENCH_SCHEMA}".{TABLE}')
            pool = await asyncio.to_thread(lambda: generate_rows(rows=POOL, seed=11)["customers"])
            base = 10_000_001
            for start in range(0, rows, 50_000):
                chunk = []
                for k in range(start, min(rows, start + 50_000)):
                    row = dict(pool[k % POOL])
                    row["customer_id"] = base + k
                    row["relationship_manager_id"] = None
                    chunk.append(tuple(row[c] for c in cols))
                await conn.copy_records_to_table(
                    TABLE, records=chunk, columns=cols, schema_name=BENCH_SCHEMA
                )
            await conn.execute(f'ANALYZE "{BENCH_SCHEMA}".{TABLE}')
        await conn.execute(f'GRANT USAGE ON SCHEMA "{BENCH_SCHEMA}" TO dps_reader, dps_writer')
        await conn.execute(f'GRANT SELECT ON "{BENCH_SCHEMA}".{TABLE} TO dps_reader, dps_writer')
        if in_place:
            await conn.execute(
                f'CREATE SCHEMA IF NOT EXISTS "{RESTORE_SCHEMA}" AUTHORIZATION dps_writer'
            )
            await conn.execute(f'DROP TABLE IF EXISTS "{RESTORE_SCHEMA}".{TABLE}')
            await conn.execute(
                f'CREATE TABLE "{RESTORE_SCHEMA}".{TABLE} '
                f'(LIKE "{BENCH_SCHEMA}".{TABLE} INCLUDING ALL)'
            )
            await conn.execute(
                f'INSERT INTO "{RESTORE_SCHEMA}".{TABLE} SELECT * FROM "{BENCH_SCHEMA}".{TABLE}'
            )
            await conn.execute(f'ALTER TABLE "{RESTORE_SCHEMA}".{TABLE} OWNER TO dps_writer')
            await conn.execute(f'ANALYZE "{RESTORE_SCHEMA}".{TABLE}')
    finally:
        await conn.close()


# -- the run --------------------------------------------------------------------------------------


def _spec(args: argparse.Namespace, ids: dict[str, str], snap_id: str) -> dict[str, Any]:
    in_place = args.write_mode == "in_place"
    source_conn = ids["bank-restore-rw"] if in_place else ids["bank-pg-ro"]
    owner = RESTORE_SCHEMA if in_place else BENCH_SCHEMA
    target: dict[str, Any]
    if in_place:
        target = {"write_mode": "in_place", "confirm_write": True, "expected_rows": args.rows}
    elif args.write_mode == "files":
        target = {
            "write_mode": "files",
            "connection_id": ids["exports-s3"],
            "files": {"format": "parquet", "prefix": f"bench/{args.label}"},
        }
    else:
        target = {
            "write_mode": "table",
            "connection_id": ids["bank-uat"],
            "object": {"owner": "bank_uat", "name": f"{TABLE}_{args.mode}"},
            "table_load": "truncate_insert",
        }
    return {
        "mode": args.mode,
        "snapshot_id": snap_id,
        "purpose": PURPOSE,
        "source": {
            "connection_id": source_conn,
            "object": {"owner": owner, "name": TABLE},
            "parallelism": args.parallelism,
            "batch_rows": args.batch,
            "partition_rows": args.partition_rows,
        },
        "target": target,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Prepare, discover, run and summarise one benchmark (module docstring)."""
    in_place = args.write_mode == "in_place"
    asyncio.run(_prepare_source(args.source_dsn or source_dsn(), args.rows, in_place=in_place))
    client = Client(args.base_url or base_url(), args.api_key)
    ids = client.ensure_connections()
    snap_id = client.discover_and_accept(
        ids["bank-restore-rw"] if in_place else ids["bank-pg-ro"],
        RESTORE_SCHEMA if in_place else BENCH_SCHEMA,
        TABLE,
        overrides=IN_PLACE_REVIEW if in_place else None,
    )
    started = time.monotonic()
    submitted = client.submit_run(_spec(args, ids, snap_id))
    job_id = submitted["job"]["id"]
    del submitted  # holds the job's key material: never kept or printed
    job = client.wait(job_id, args.timeout)
    finished = time.monotonic()
    partitions = client.call("GET", f"/api/v1/jobs/{job_id}/partitions", "partitions")["items"]
    stats = client.call("GET", "/api/v1/jobs/stats", "stats")
    ready = client.call("GET", "/readyz", "readyz")
    fpe = (ready.get("components") or {}).get("fpe", {}).get("ok")
    result = summarise(
        job,
        partitions,
        started,
        finished,
        mode=args.mode,
        write_mode=args.write_mode,
        parallelism=args.parallelism,
        batch_rows=args.batch,
        workers_online=int(stats.get("workers_online") or 0),
        engine_version=str((ready.get("components") or {}).get("api-core", {}).get("version")),
        fpe_enabled=bool(fpe),
        host={"cpus": os.cpu_count(), "machine": platform.machine()},
        label=args.label,
        job_id=job_id,
        state=job["state"],
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; 0 when the job succeeded, 1 otherwise."""
    args = parse_args(argv)
    when = datetime.now(UTC)
    try:
        result = run(args)
    except ApiError as exc:  # status and code only
        print(f"benchmark failed ({exc})")
        return 1
    except Exception as exc:  # noqa: BLE001 - class name only (a message could quote a DSN)
        print(f"benchmark failed ({type(exc).__name__})")
        return 1
    path = result_path(args.out, args.mode, args.write_mode, args.label, when)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"{args.mode}/{args.write_mode} {args.label}: {result['rows']} rows in "
        f"{result['wall_seconds']}s = {result['rows_per_second']} rows/s "
        f"({result['partitions']} partitions, {result['workers_online']} workers, "
        f"p50 {result['partition_rows_per_second']['p50']} rows/s per partition) "
        f"[{result['state']}] -> {path.name}"
    )
    return 0 if result["state"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
