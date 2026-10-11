"""PostgreSQL connector, read side, against the lane's source-pg (contract §4.2)."""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg
import pytest

from dps.config import Settings
from dps.jobs_models import Selection
from dps.layers.connectors.base import ConnectorError, KeyRange, encode_key
from dps.layers.connectors.postgres import PostgresConnector
from dps.layers.connectors.selection import parameterize_literals
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.integration, pytest.mark.pg_source]


async def _connector(
    source_pg: SourcePg, settings: Settings, user: str = "dps_reader", **overrides: Any
) -> PostgresConnector:
    c = PostgresConnector(
        source_pg.connection(user=user),
        source_pg.credentials(user),
        settings.model_copy(update=overrides),
        action="test",
        job_id="j_test",
    )
    await c.open()
    return c


async def test_describe_types_and_key_detection(source_pg: SourcePg, settings: Settings) -> None:
    obj = await source_pg.table(
        "typed",
        "id bigint PRIMARY KEY, name varchar(120) NOT NULL, amt numeric(14,2), "
        "code char(10), at timestamptz, u uuid, doc jsonb, raw bytea",
        [],
    )
    await source_pg.table("uniq", "a int NOT NULL, b text NOT NULL, c text, UNIQUE (a, b)", [])
    await source_pg.table("nokey", "a int, b text", [])
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        kinds = {col.name: col.kind for col in shape.columns}
        assert kinds == {
            "id": "int",
            "name": "text",
            "amt": "decimal",
            "code": "char",
            "at": "timestamp",
            "u": "uuid",
            "doc": "json",
            "raw": "binary",
        }
        assert shape.key == ["id"] and shape.key_source == "primary_key"
        assert shape.column("name").length == 120
        assert (shape.column("amt").precision, shape.column("amt").scale) == (14, 2)
        assert shape.column("code").length == 10 and shape.column("at").ts_precision == 6
        assert shape.column("id").is_key and not shape.column("id").nullable
        uniq = await c.describe(obj.model_copy(update={"name": "uniq"}))
        assert (uniq.key, uniq.key_source) == (["a", "b"], "unique_index")
        assert (await c.describe(obj.model_copy(update={"name": "nokey"}))).key_source == "none"
        with pytest.raises(ConnectorError) as err:
            await c.describe(obj.model_copy(update={"name": "missing"}))
        assert err.value.code == "object_not_found"
        listed = await c.list_objects(source_pg.schema)
        assert sorted(o.name for o in listed) == ["nokey", "typed", "uniq"]
    finally:
        await c.close()


async def test_keyset_paging_visits_every_row_once_in_db_order(
    source_pg: SourcePg, settings: Settings
) -> None:
    names = ["b", "B", "a", "ä", "Z", "aa", "ab", "a b", "_x", "10", "9"] * 40
    obj = await source_pg.table(
        "strkeys", "k text PRIMARY KEY, n int", [(f"{v}-{i:04d}", i) for i, v in enumerate(names)]
    )
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        got = [
            r["k"]
            async for batch in c.read_partition(
                shape, Selection(), {}, KeyRange(), after_key=None, batch=37
            )
            for r in batch
        ]
        expected = [
            r["k"]
            for r in await source_pg.admin.fetch(
                f'SELECT k FROM "{source_pg.schema}".strkeys ORDER BY k'
            )
        ]
        assert got == expected and len(got) == len(names)
    finally:
        await c.close()


async def test_ranges_after_key_and_composite_keys(source_pg: SourcePg, settings: Settings) -> None:
    obj = await source_pg.table(
        "pairs",
        "a int, b int, v text, PRIMARY KEY (a, b)",
        [(a, b, f"v{a}{b}") for a in range(10) for b in range(10)],
    )
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        cols = shape.key_columns()
        rng = KeyRange(lower=encode_key((2, 5), cols), upper=encode_key((4, 0), cols))
        rows = [
            (r["a"], r["b"])
            async for batch in c.read_partition(
                shape, Selection(), {}, rng, after_key=(3, 1), batch=4
            )
            for r in batch
        ]
        assert rows == [(3, b) for b in range(2, 10)]
        whole = [
            (r["a"], r["b"])
            async for batch in c.read_partition(
                shape, Selection(), {}, rng, after_key=None, batch=1000
            )
            for r in batch
        ]
        assert whole == [(2, b) for b in range(5, 10)] + [(3, b) for b in range(10)]
    finally:
        await c.close()


async def test_selection_binds_count_sample_and_offsets(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table(
        "cust",
        "id bigint PRIMARY KEY, country char(2), created date, risk int",
        [
            (10_000_000 + i, ["IN", "US", "GB", "MX"][i % 4], date(2024, (i % 12) + 1, 1), i % 900)
            for i in range(2000)
        ],
    )
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        sel = parameterize_literals(
            Selection(
                where="created >= DATE '2024-07-01' AND country IN ('IN', 'US') "
                "AND risk > :min_risk",
                params={"min_risk": 100},
            ),
            "postgres",
        )
        expected = await source_pg.admin.fetchval(
            f"SELECT count(*) FROM \"{source_pg.schema}\".cust WHERE created >= '2024-07-01' "
            "AND country IN ('IN','US') AND risk > 100"
        )
        assert await c.count(shape, sel, sel.params) == expected
        read = [
            r["id"]
            async for batch in c.read_partition(
                shape, sel, sel.params, KeyRange(), after_key=None, batch=97
            )
            for r in batch
        ]
        assert len(read) == expected and read == sorted(read)
        keys = await c.sample_keys(shape, Selection(), {}, 50, upper=(10_001_000,))
        assert len(keys) <= 50 and keys == sorted(keys) and all(k[0] < 10_001_000 for k in keys)
        assert len(keys) >= 40  # quantiles of the bounded selection, not of the whole table
        assert await c.key_at_offset(shape, Selection(), {}, 10) == (10_000_010,)
        assert await c.key_at_offset(shape, Selection(), {}, 5000) is None
        sample = await c.sample(shape, Selection(), {}, 100)
        assert 1 <= len(sample) <= 100 and set(sample[0]) == {"id", "country", "created", "risk"}
        capped = Selection(max_rows=25)
        assert await c.count(shape, capped, {}) == 25
        projected = Selection(columns=["risk"])
        first = await c.sample(shape, projected, {}, 5)
        assert set(first[0]) == {"id", "risk"}
    finally:
        await c.close()


async def test_string_binds_are_coerced_by_parameter_type(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table(
        "typed_binds",
        "id int PRIMARY KEY, d date, amount numeric(10,2), at timestamptz",
        [(i, date(2024, 1, 1 + i), i * 10, None) for i in range(10)],
    )
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        sel = Selection(
            where="d >= :since AND amount > :floor", params={"since": "2024-01-05", "floor": "35.5"}
        )
        assert await c.count(shape, sel, sel.params) == 6
        bad = Selection(where="d >= :since", params={"since": "999-45-6789"})
        with pytest.raises(ConnectorError) as err:
            await c.count(shape, bad, bad.params)
        assert err.value.code == "selection_param_invalid" and "6789" not in str(err.value)
        assert err.value.retryable is False
    finally:
        await c.close()


async def test_query_selection_reads_through_the_wrapped_query(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table(
        "q", "id int PRIMARY KEY, grp text, v int", [(i, "ab"[i % 2], i) for i in range(50)]
    )
    c = await _connector(source_pg, settings)
    try:
        shape = await c.describe(obj)
        sel = parameterize_literals(
            Selection(query=f"SELECT id, grp, v FROM {source_pg.schema}.q WHERE grp = 'a'"),
            "postgres",
        )
        assert await c.count(shape, sel, sel.params) == 25
        ids = [
            r["id"]
            async for batch in c.read_partition(
                shape, sel, sel.params, KeyRange(), after_key=(10,), batch=6
            )
            for r in batch
        ]
        assert ids == list(range(12, 50, 2))
    finally:
        await c.close()


async def test_sessions_are_read_only_tagged_and_time_limited(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table("ro", "id int PRIMARY KEY", [(1,)])
    c = await _connector(
        source_pg, settings, user="postgres", connector_statement_timeout_seconds=1
    )
    try:
        async with c.read_transaction() as conn:
            assert await conn.fetchval("SHOW transaction_read_only") == "on"
            assert await conn.fetchval("SHOW statement_timeout") == "1s"
            app = await conn.fetchval("SELECT current_setting('application_name')")
            assert app.startswith("dps:test:j_test")
            with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                await conn.execute(f'UPDATE "{source_pg.schema}".ro SET id = 2')
        with pytest.raises(ConnectorError) as err:
            await c.fetch_internal("SELECT pg_sleep(2)", [])
        assert err.value.code == "statement_timeout"
        assert obj.name == "ro"
        assert await source_pg.admin.fetchval(f'SELECT id FROM "{source_pg.schema}".ro') == 1
    finally:
        await c.close()


async def test_source_test_checks_and_value_free_failures(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table("chk", "id int PRIMARY KEY", [])
    reader = await _connector(source_pg, settings)
    try:
        result = await reader.test(role="source", obj=obj)
        checks = {c["name"]: c for c in result.checks}
        assert result.ok and checks["select_source"]["ok"] and checks["no_dml_on_source"]["ok"]
        assert checks["connect"]["ok"] and checks["session_tagged"]["ok"]
        assert result.capabilities["copy"] and result.server_version
        missing = await reader.test(role="source", obj=obj.model_copy(update={"name": "nope"}))
        assert (
            not missing.ok
            and {c["name"]: c for c in missing.checks}["select_source"]["ok"] is False
        )
    finally:
        await reader.close()
    owner = await _connector(source_pg, settings, user="postgres")
    try:
        result = await owner.test(role="source", obj=obj)
        assert (
            result.ok and {c["name"]: c for c in result.checks}["no_dml_on_source"]["ok"] is False
        )
    finally:
        await owner.close()
    writer = await _connector(source_pg, settings, user="dps_writer")
    try:
        target = await writer.test(role="target", obj=obj, write_mode="in_place")
        tchecks = {c["name"]: c for c in target.checks}
        assert target.ok and tchecks["update_target"]["ok"] and tchecks["ledger_schema"]["ok"]
        fresh = await writer.test(
            role="target", obj=obj.model_copy(update={"name": "new_table"}), write_mode="table"
        )
        assert fresh.ok and {c["name"]: c for c in fresh.checks}["create_target"]["ok"]
    finally:
        await writer.close()
    bad = PostgresConnector(
        source_pg.connection(),
        source_pg.credentials("dps_reader").model_copy(
            update={"password": asyncpg_secret("wrong-secret-pw")}
        ),
        settings,
        action="test",
    )
    with pytest.raises(ConnectorError) as err:
        await bad.open()
    assert err.value.code == "connection_failed" and "wrong-secret-pw" not in str(err.value)
    assert err.value.retryable is False  # a refused login is not transient
    failed = await bad.test(role="source", obj=obj)
    assert not failed.ok and failed.error_code == "connection_failed"
    assert "wrong-secret-pw" not in failed.model_dump_json()


def asyncpg_secret(text: str) -> Any:
    """A ``SecretStr`` for a test password."""
    from pydantic import SecretStr

    return SecretStr(text)


async def test_sql_string_functions_are_reported_as_advisory(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table("adv", "id int PRIMARY KEY", [])
    reader = await _connector(source_pg, settings)
    try:
        before = {c["name"]: c for c in (await reader.test(role="source", obj=obj)).checks}
        assert before["no_sql_string_functions"]["ok"]
        # tablefunc (contrib) grants EXECUTE on crosstab/connectby to PUBLIC; the schema drop at
        # teardown removes the extension again.
        await source_pg.admin.execute(f'CREATE EXTENSION tablefunc SCHEMA "{source_pg.schema}"')
        result = await reader.test(role="source", obj=obj)
        checks = {c["name"]: c for c in result.checks}
        assert checks["no_sql_string_functions"]["ok"] is False
        assert checks["no_sql_string_functions"]["severity"] == "advisory" and result.ok
    finally:
        await reader.close()
