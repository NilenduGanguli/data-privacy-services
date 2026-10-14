"""A roll-up document for a day after today is 404 and writes nothing (M5 G3 review r2 N2).

``GET /reports/api-rollups/{day}`` refreshed the requested day before looking it up, so a future
day (``2999-01-01``) left a ``dps_report_refresh`` marker that no retention run ever ages out —
one row per request. No roll-up can exist for a day that has not happened: the route now answers
404 ``rollup_not_found`` without touching the roll-up tables.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from dps.config import Settings
from dps.reports import utc_today
from tests.fixtures.db import _truncate, api_settings

pytestmark = pytest.mark.db
TENANT = "r9"
H = {"X-API-KEY": "dps_full", "X-DPS-Tenant": TENANT}


@pytest.fixture
def app(db_settings: Settings, tmp_path: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from dps.app import create_app

    try:
        with TestClient(create_app(api_settings(db_settings, tmp_path))) as client:
            yield client
    finally:
        asyncio.run(_truncate(db_settings))


async def _markers(settings: Settings) -> int:
    conn = await asyncpg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        user=settings.pg_migration_login[0],
        password=settings.pg_migration_login[1],
        database=settings.pg_database,
    )
    try:
        # The table forces row-level security: read as the request's tenant.
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", TENANT)
        return int(
            await conn.fetchval(
                f"SELECT count(*) FROM {settings.pg_schema}.dps_report_refresh WHERE day > $1",
                utc_today(),
            )
        )
    finally:
        await conn.close()


def test_a_future_day_is_not_found_and_leaves_no_marker(app, db_settings: Settings) -> None:  # noqa: ANN001
    for day in ("2999-01-01", (utc_today() + timedelta(days=1)).isoformat()):
        for fmt in ("json", "pdf"):
            got = app.get(
                f"/api/v1/reports/api-rollups/{day}",
                params={"action": "obfuscate", "format": fmt},
                headers=H,
            )
            assert (got.status_code, got.json()["code"]) == (404, "rollup_not_found"), got.text
    assert asyncio.run(_markers(db_settings)) == 0
    today = app.get(
        f"/api/v1/reports/api-rollups/{utc_today().isoformat()}",
        params={"action": "obfuscate"},
        headers=H,
    )
    assert today.status_code in (200, 404)  # today is a real day: looked up as before
