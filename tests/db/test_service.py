from __future__ import annotations

import asyncpg
import pytest

from tests.fixtures.services import require_service

pytestmark = pytest.mark.db
ROLES = {"dps_owner", "dps_app_rw", "dps_worker", "dps_app", "dps_worker_login"}


async def test_metadata_db_has_the_contract_roles() -> None:
    conn = await asyncpg.connect(require_service("dps-db"))
    try:
        rows = await conn.fetch(
            "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
            "WHERE rolname = ANY($1::text[])",
            list(ROLES),
        )
        assert {r["rolname"] for r in rows} == ROLES
        assert not any(r["rolsuper"] or r["rolbypassrls"] for r in rows)
        login = {r["rolname"] for r in rows if r["rolcanlogin"]}
        assert login == {"dps_owner", "dps_app", "dps_worker_login"}
        member = await conn.fetchval("SELECT pg_has_role('dps_app', 'dps_worker', 'MEMBER')")
        assert member is False
        assert await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'dps'") == 1
    finally:
        await conn.close()
