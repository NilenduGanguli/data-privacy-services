"""``python -m dps.keys_cli`` (M5 Task 9): one JSON document on stdout, nothing key-like on
stderr, one value-free ``_system`` audit row per mutating command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from dps import db

pytestmark = pytest.mark.db


def _run(db_settings, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:  # noqa: ANN001
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("DPS_")},
        "DPS_PG_HOST": db_settings.pg_host,
        "DPS_PG_PORT": str(db_settings.pg_port),
        "DPS_PG_DATABASE": db_settings.pg_database,
        "DPS_PG_SCHEMA": db_settings.pg_schema,
        "DPS_PG_USER": db_settings.pg_user,
        "DPS_PG_PASSWORD": db_settings.pg_password,
        "DPS_MIGRATIONS_MODE": "off",
    }
    # cwd = tmp_path: a .env in the working tree cannot change the CLI's settings
    return subprocess.run(
        [sys.executable, "-m", "dps.keys_cli", *args],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _cli(db_settings, tmp_path: Path, *args: str) -> dict[str, Any]:  # noqa: ANN001
    out = _run(db_settings, tmp_path, *args)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stderr.count("dps_") == 0  # nothing key-like on stderr
    doc: dict[str, Any] = json.loads(out.stdout)
    return doc


def test_create_list_disable(migrated_db, db_settings, tmp_path) -> None:  # noqa: ANN001
    created = _cli(
        db_settings,
        tmp_path,
        "create",
        "--name",
        "platform-admin",
        "--tenants",
        "*",
        "--scopes",
        "*",
        "--expires-in-days",
        "30",
    )
    assert created["key"].startswith("dps_") and created["record"]["name"] == "platform-admin"
    listed = _cli(db_settings, tmp_path, "list")
    assert any(k["id"] == created["record"]["id"] for k in listed["items"])
    assert created["key"] not in json.dumps(listed)
    assert _cli(db_settings, tmp_path, "disable", created["record"]["id"])["status"] == "disabled"
    assert _cli(db_settings, tmp_path, "enable", created["record"]["id"])["status"] == "active"


def test_rotate_prints_the_new_key_once(migrated_db, db_settings, tmp_path) -> None:  # noqa: ANN001
    created = _cli(
        db_settings,
        tmp_path,
        "create",
        "--name",
        "rot-me",
        "--tenants",
        "retail",
        "--scopes",
        "detect",
        "--expires-in-days",
        "30",
    )
    rotated = _cli(db_settings, tmp_path, "rotate", created["record"]["id"], "--grace-hours", "0")
    assert rotated["new"]["key"].startswith("dps_") and rotated["old"]["status"] == "disabled"
    assert "key" not in rotated["old"]


async def test_refusals_exit_2_value_free_and_mutations_are_audited(
    migrated_db, db_settings, tmp_path
) -> None:  # noqa: ANN001
    bad = _run(db_settings, tmp_path, "create", "--name", "Bad Name", "--tenants", "retail",
               "--scopes", "detect", "--expires-in-days", "30")  # fmt: skip
    assert bad.returncode == 2 and json.loads(bad.stdout)["error"] == "invalid_name"
    missing = _run(db_settings, tmp_path, "disable", "key_nope")
    assert missing.returncode == 2 and json.loads(missing.stdout)["error"] == "key_not_found"
    created = _cli(
        db_settings,
        tmp_path,
        "create",
        "--name",
        "audited",
        "--tenants",
        "retail",
        "--scopes",
        "detect",
        "--expires-in-days",
        "5",
    )
    _cli(db_settings, tmp_path, "disable", created["record"]["id"])
    async with db.acquire("_system") as conn:
        rows = await conn.fetch(
            f"SELECT action, key_id, route, outcome, counts FROM {db.schema_ident()}.dps_audit_log "
            "ORDER BY id"
        )
    actions = [r["action"] for r in rows]
    assert actions == ["admin.key.create", "admin.key.disable"]
    assert all(r["key_id"] == "cli" and r["route"] == "cli" and r["outcome"] == "ok" for r in rows)
    assert "dps_" not in json.dumps([r["counts"] for r in rows])
