"""Seeding a fresh schema of the lane's source-pg (never the demo ``bank`` schema)."""

from __future__ import annotations

import asyncio

import pytest

from tests.fixtures.sources import SourcePg
from tools.seed_sources import TABLES, main, satellite_schemas, seed

pytestmark = [pytest.mark.integration, pytest.mark.pg_source]


async def _drop_satellites(pg: SourcePg) -> None:
    for schema in satellite_schemas(pg.schema):
        await pg.admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


async def test_seed_is_idempotent_and_resettable(
    source_pg: SourcePg, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        first = await seed(source_pg.dsn, rows=200, seed=7, schema=source_pg.schema)
        assert not first.already and set(first.counts) == set(TABLES)
        assert first.counts["customers"] == 200 and first.counts["transactions"] == 2000
        assert first.counts["accounts"] == 300 and first.counts["contacts"] == 400
        assert first.counts["loans"] == 50 and first.counts["employees"] == 50
        sample = await source_pg.admin.fetch(
            f'SELECT * FROM "{source_pg.schema}".customers ORDER BY customer_id LIMIT 5'
        )
        again = await seed(source_pg.dsn, rows=200, seed=7, schema=source_pg.schema)
        assert again.already and again.counts == first.counts
        same = await source_pg.admin.fetch(
            f'SELECT * FROM "{source_pg.schema}".customers ORDER BY customer_id LIMIT 5'
        )
        assert same == sample  # nothing changed
        readable = await source_pg.admin.fetchval(
            "SELECT has_table_privilege('dps_reader', $1, 'SELECT')",
            f'"{source_pg.schema}".customers',
        )
        assert readable
        reset = await seed(source_pg.dsn, rows=100, seed=9, schema=source_pg.schema, reset=True)
        assert not reset.already and reset.counts["customers"] == 100
        restored = await seed(
            source_pg.dsn, rows=100, seed=9, schema=source_pg.schema, restore_copy=True
        )
        assert restored.already and restored.restored
        restore, perf = satellite_schemas(source_pg.schema)
        copy = await source_pg.admin.fetchval(f'SELECT count(*) FROM "{restore}".contacts')
        assert copy == reset.counts["contacts"]
        owner = await source_pg.admin.fetchval(
            "SELECT tableowner FROM pg_tables WHERE schemaname = $1 AND tablename = 'contacts'",
            restore,
        )
        assert owner == "dps_writer"
        assert await source_pg.admin.fetchval(f'SELECT count(*) FROM "{perf}".transactions') == 1000
        unchanged = await seed(
            source_pg.dsn, rows=100, seed=9, schema=source_pg.schema, restore_copy=True
        )
        assert not unchanged.restored  # the copy is current: a rerun keeps in-place results
        code = await asyncio.to_thread(  # the CLI runs its own event loop
            main,
            [
                "--target",
                "postgres",
                "--dsn",
                source_pg.dsn,
                "--schema",
                source_pg.schema,
                "--rows",
                "100",
                "--seed",
                "9",
            ],
        )
        out = capsys.readouterr().out
        assert code == 0 and "already seeded" in out and source_pg.dsn not in out
        conflict = await asyncio.to_thread(
            main, ["--target", "postgres", "--dsn", source_pg.dsn, "--schema", source_pg.schema]
        )
        assert conflict == 3  # seeded with other parameters
    finally:
        await _drop_satellites(source_pg)
