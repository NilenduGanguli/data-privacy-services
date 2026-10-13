"""Operator CLI for DB-backed API keys (M5, contract §8.3) — the bootstrap path.

``python -m dps.keys_cli create --name N --tenants a,b|* --scopes s1,s2|* --expires-in-days D``
``python -m dps.keys_cli list | rotate ID [--grace-hours H] | disable ID | enable ID``

The operator already holds the metadata database's credentials, so the CLI acts as a wildcard
principal (``cli``). stdout carries exactly one JSON document — ``CreatedKey`` for create (the raw
key, once), ``{"new": CreatedKey, "old": ApiKeyView}`` for rotate, ``{"items": [...]}`` for list,
``ApiKeyView`` otherwise; a refusal prints ``{"error": code, "detail": detail}`` and exits 2. Logs go
to stderr at WARNING and never carry a key. Every mutating command writes one value-free audit row
owned by the system tenant ``_system`` (``key_id = route = 'cli'``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import structlog

from dps import db
from dps.auth import Principal
from dps.auth_db import GrantError, create_key, disable_key, enable_key, list_keys, rotate_key
from dps.config import Settings
from dps.jobs import SYSTEM_TENANT
from dps.observability import configure_logging, get_logger

logger = get_logger(__name__)
CLI_PRINCIPAL = Principal(key_id="cli", name="operator-cli", tenant_ids=["*"], scopes=["*"])


def _split(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dps.keys_cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create", help="mint a key (printed once)")
    create.add_argument("--name", required=True)
    create.add_argument("--tenants", required=True, help="comma list or *")
    create.add_argument("--scopes", required=True, help="comma list or *")
    create.add_argument("--expires-in-days", type=int, required=True)
    sub.add_parser("list", help="list keys (never the key)")
    rotate = sub.add_parser("rotate", help="mint the successor (printed once)")
    rotate.add_argument("key_id")
    rotate.add_argument("--grace-hours", type=int, default=24)
    for name in ("disable", "enable"):
        sub.add_parser(name).add_argument("key_id")
    return parser


async def _audit(cmd: str) -> None:
    async with db.acquire(SYSTEM_TENANT) as conn:
        await conn.execute(
            f"INSERT INTO {db.schema_ident()}.dps_audit_log (tenant_id, key_id, route, action, "
            "outcome, counts) VALUES ($1, 'cli', 'cli', $2, 'ok', '{}'::jsonb)",
            SYSTEM_TENANT,
            f"admin.key.{cmd}",
        )


async def _run(args: argparse.Namespace, settings: Settings) -> dict[str, Any]:
    actor = CLI_PRINCIPAL
    out: dict[str, Any]
    if args.cmd == "create":
        created = await create_key(
            name=args.name,
            tenant_ids=_split(args.tenants),
            scopes=_split(args.scopes),
            expires_in_days=args.expires_in_days,
            actor=actor,
            settings=settings,
        )
        out = created.model_dump(mode="json")
    elif args.cmd == "list":
        views = await list_keys(actor=actor, static=None, settings=settings)
        return {"items": [v.model_dump(mode="json") for v in views]}
    elif args.cmd == "rotate":
        new, old = await rotate_key(
            args.key_id, grace_hours=args.grace_hours, actor=actor, settings=settings
        )
        out = {"new": new.model_dump(mode="json"), "old": old.model_dump(mode="json")}
    elif args.cmd == "disable":
        out = (await disable_key(args.key_id, actor=actor, settings=settings)).model_dump(
            mode="json"
        )
    else:
        out = (await enable_key(args.key_id, actor=actor, settings=settings)).model_dump(
            mode="json"
        )
    await _audit(args.cmd)
    return out


async def amain(argv: list[str] | None = None) -> int:
    """Run one command; returns the process exit code (0 ok, 2 refused)."""
    args = _parser().parse_args(argv)
    settings = Settings()
    await db.init_pool(settings)
    try:
        try:
            doc = await _run(args, settings)
        except GrantError as exc:
            print(json.dumps({"error": exc.code, "detail": exc.detail}))
            return 2
        print(json.dumps(doc))
        return 0
    finally:
        await db.close_pools()


def main(argv: list[str] | None = None) -> int:
    """Entry point: logs to stderr at WARNING, one JSON document on stdout."""
    configure_logging("WARNING", json_output=True)
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    return asyncio.run(amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
