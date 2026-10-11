"""``python -m dps.migrate [--verify] [--maintain] [--months-ahead N]`` (contract §2.1).

Exit codes: 0 ok · 1 drift/pending (verify) or a failed migration · 2 no database configured.
Prints file names and counts only — never a DSN, a password or a row.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import asyncpg

from dps import db
from dps.config import get_settings
from dps.observability import configure_logging


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        if args.verify:
            await db.verify_migrations(settings)
            print("migrations: verified")
            return 0
        conn = await db.open_migration_connection(settings)
        try:
            applied = await db.run_migrations(settings, connection=conn)
            print(f"migrations: applied {len(applied)} ({', '.join(applied) or 'none pending'})")
            if args.maintain:
                await db.ensure_audit_partitions(conn, settings, months_ahead=args.months_ahead)
                dropped = await db.drop_expired_audit_partitions(conn, settings)
                print(
                    f"audit partitions: ensured {args.months_ahead} months ahead, "
                    f"dropped {len(dropped)}"
                )
        finally:
            await conn.close()
        return 0
    except db.MigrationDrift as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        # Class name and SQLSTATE only: a server message can quote a DSN, a role or (for a
        # failing statement) a literal, and a traceback quotes source lines.
        sqlstate = getattr(exc, "sqlstate", None)
        detail = f"{type(exc).__name__}{f' sqlstate={sqlstate}' if sqlstate else ''}"
        print(f"migrations: failed ({detail})", file=sys.stderr)
        return 1
    finally:
        await db.close_pools()


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, check a database is configured, run; returns the exit code."""
    parser = argparse.ArgumentParser(prog="python -m dps.migrate")
    parser.add_argument("--verify", action="store_true", help="check only; never runs DDL")
    parser.add_argument(
        "--maintain", action="store_true", help="audit partitions ahead + drop expired"
    )
    parser.add_argument("--months-ahead", type=int, default=3)
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    if not db.db_configured(settings):
        print("DPS_PG_HOST is not set: no metadata database configured", file=sys.stderr)
        return 2
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
