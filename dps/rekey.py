"""KEK rotation: re-seal every sealed blob with the current job KEK (M5 Task 16).

``python -m dps.rekey [--dry-run]``. For every sealed blob — ``dps_job.key_enc`` (AAD
``dps_job.key:<id>``) and ``dps_job.spec_enc`` (``dps_job.spec:<id>``) through the worker pool
(the ``worker_claim`` policy sees every tenant), and ``dps_connection.secret_enc``
(``dps_connection.secret:<id>``) per tenant of the tenant directory under that tenant's GUC — it
opens the blob with ``DPS_JOB_KEK``; a blob that only ``DPS_JOB_KEK_PREVIOUS`` opens is re-sealed
with the current KEK. Each update is optimistic (``... WHERE id = $1 AND <col> = $old``), so a blob
a worker rewrote meanwhile is left alone.

Prints one JSON line ``{"rows", "resealed", "already_current", "unreadable"}`` (counts only — never
a key, an id or a value). Exit codes: 0 done, 3 some blobs open with neither KEK (those rows are
unchanged), 2 configuration error (no KEK, no database).

Runbook (HANDOFF §9): mount the new KEK as ``DPS_JOB_KEK_FILE`` and the old one as
``DPS_JOB_KEK_PREVIOUS_FILE``, roll the deployments, run ``python -m dps.rekey``, confirm
``unreadable == 0``, remove the previous KEK, roll again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from dps import db
from dps.config import Settings, get_settings
from dps.observability import configure_logging, get_logger
from dps.secrets import SealError, kek_ring, open_sealed, seal

logger = get_logger(__name__)

EXIT_UNREADABLE = 3
EXIT_CONFIG = 2
#: ``(column, AAD prefix)`` of the sealed ``dps_job`` columns.
JOB_BLOBS = (("key_enc", "dps_job.key"), ("spec_enc", "dps_job.spec"))
CONNECTION_AAD = "dps_connection.secret"


@dataclass
class RekeyCounts:
    """What one run found and did (counts only)."""

    rows: int = 0
    resealed: int = 0
    already_current: int = 0
    unreadable: int = 0


def _reseal(
    blob: bytes, aad: str, current: bytes, previous: tuple[bytes, ...], counts: RekeyCounts
) -> bytes | None:
    """The blob re-sealed with ``current`` when only a previous KEK opens it, else ``None``."""
    counts.rows += 1
    try:
        open_sealed(current, blob, aad=aad)
        counts.already_current += 1
        return None
    except SealError:
        pass
    for old in previous:
        try:
            plain = open_sealed(old, blob, aad=aad)
        except SealError:
            continue
        return seal(current, plain, aad=aad)
    counts.unreadable += 1
    return None


async def rekey(settings: Settings, *, dry_run: bool = False) -> RekeyCounts:
    """Re-seal every blob that only ``DPS_JOB_KEK_PREVIOUS`` opens (pools must be initialised).

    Raises:
        ValueError: ``DPS_JOB_KEK`` is not set.
    """
    ring = kek_ring(settings)
    if not settings.job_kek or not ring:
        raise ValueError("DPS_JOB_KEK (DPS_JOB_KEK_FILE) must be set")
    current, previous = ring[0], ring[1:]
    counts = RekeyCounts()
    s = db.schema_ident(settings)
    async with db.acquire_queue(None) as conn:
        rows = await conn.fetch(
            f"SELECT id, key_enc, spec_enc FROM {s}.dps_job "
            "WHERE key_enc IS NOT NULL OR spec_enc IS NOT NULL ORDER BY id"
        )
        for row in rows:
            for column, prefix in JOB_BLOBS:
                old = row[column]
                if old is None:
                    continue
                new = _reseal(bytes(old), f"{prefix}:{row['id']}", current, previous, counts)
                if new is None:
                    continue
                if dry_run:
                    counts.resealed += 1
                    continue
                status = await conn.execute(
                    f"UPDATE {s}.dps_job SET {column} = $2 WHERE id = $1 AND {column} = $3",
                    row["id"],
                    new,
                    old,
                )
                if db.rowcount(status) == 1:
                    counts.resealed += 1
                else:  # rewritten meanwhile (by a worker holding the current KEK)
                    counts.already_current += 1
    from dps.tenants import tenant_directory

    for tenant in await tenant_directory(include_system=True):
        async with db.acquire(tenant) as conn:
            rows = await conn.fetch(
                f"SELECT id, secret_enc FROM {s}.dps_connection "
                "WHERE tenant_id = $1 AND secret_enc IS NOT NULL ORDER BY id",
                tenant,
            )
            for row in rows:
                old = row["secret_enc"]
                new = _reseal(
                    bytes(old), f"{CONNECTION_AAD}:{row['id']}", current, previous, counts
                )
                if new is None:
                    continue
                if dry_run:
                    counts.resealed += 1
                    continue
                status = await conn.execute(
                    f"UPDATE {s}.dps_connection SET secret_enc = $3 "
                    "WHERE tenant_id = $1 AND id = $2 AND secret_enc = $4",
                    tenant,
                    row["id"],
                    new,
                    old,
                )
                if db.rowcount(status) == 1:
                    counts.resealed += 1
                else:
                    counts.already_current += 1
    logger.info("rekey_done", dry_run=dry_run, **asdict(counts))
    return counts


async def amain(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    """Parse ``argv``, run :func:`rekey`, print the JSON line; the exit code."""
    parser = argparse.ArgumentParser(prog="python -m dps.rekey", description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count, change nothing")
    args = parser.parse_args(argv)
    settings = settings or get_settings()
    if not settings.pg_host:
        print(json.dumps({"error": "DPS_PG_HOST is not set"}), file=sys.stderr)
        return EXIT_CONFIG
    await db.init_pool(settings)
    await db.init_worker_pool(settings)
    try:
        counts = await rekey(settings, dry_run=args.dry_run)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return EXIT_CONFIG
    finally:
        await db.close_pools()
    print(json.dumps(asdict(counts), sort_keys=True))
    return EXIT_UNREADABLE if counts.unreadable else 0


def main() -> None:
    """Console entry point (``python -m dps.rekey``)."""
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    sys.exit(asyncio.run(amain(settings=settings)))


if __name__ == "__main__":
    main()
