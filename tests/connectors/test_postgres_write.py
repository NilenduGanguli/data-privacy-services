"""PostgreSQL writer against the lane's source-pg: in_place, append, table, ledger (contract §4.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from dps.config import Settings
from dps.layers.connectors.base import (
    ConnectorError,
    FilesSpec,
    LedgerMark,
    PkGenerate,
    PkOffset,
    ResolvedTarget,
    TableShape,
    encode_key,
)
from dps.layers.connectors.postgres import (
    LEDGER_TABLE,
    LedgerConflict,
    PkCollision,
    PostgresConnector,
)
from dps.layers.connectors.targets import PrepareFailure
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.integration, pytest.mark.pg_source]


async def _rw(
    source_pg: SourcePg, settings: Settings, user: str = "dps_writer"
) -> PostgresConnector:
    c = PostgresConnector(
        source_pg.connection(user=user, roles=("source", "target")),
        source_pg.credentials(user),
        settings,
        action="run",
        job_id="j_w",
    )
    await c.open()
    return c


def _target(shape: TableShape, **kw: Any) -> ResolvedTarget:
    base: dict[str, Any] = dict(
        job_id="j_w",
        write_mode="table",
        connection_id="c",
        engine="postgres",
        object=shape.object.model_copy(update={"name": "copy"}),
        key=shape.key,
        write_columns=[c.name for c in shape.columns],
        target_shape=None,
        column_types={c.name: c.data_type for c in shape.columns},
    )
    base.update(kw)
    return ResolvedTarget(**base)


def _mark(p: int, b: int, last: Any, shape: TableShape, rows: int, job: str = "j_w") -> LedgerMark:
    values = last if isinstance(last, tuple) else (last,)
    return LedgerMark(
        job_id=job,
        partition_no=p,
        batch_no=b,
        last_key=encode_key(values, shape.key_columns()),
        rows=rows,
    )


async def _count(source_pg: SourcePg, table: str) -> int:
    return int(await source_pg.admin.fetchval(f'SELECT count(*) FROM "{source_pg.schema}".{table}'))


async def test_table_create_truncate_and_idempotent_upsert(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table(
        "src", "id bigint PRIMARY KEY, name text", [(i, f"n{i}") for i in range(10)]
    )
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        writer = await c.writer(_target(shape), shape)
        report = await writer.prepare(dry_run=False)
        assert report.created and not report.ledger
        rows = [{"id": i, "name": f"t{i}"} for i in range(10)]
        assert await writer.write_batch(rows[:5], mark=_mark(0, 1, 4, shape, 5)) == 5
        assert await writer.write_batch(rows[:5], mark=_mark(0, 1, 4, shape, 5)) == 5  # replay
        assert await writer.write_batch(rows[5:], mark=_mark(0, 2, 9, shape, 10)) == 5
        assert await _count(source_pg, "copy") == 10
        assert await writer.read_ledger("j_w", 0) is None  # table loads keep no ledger
        assert await writer.finish_partition("j_w", 0) == {}
        assert await writer.write_batch([], mark=_mark(0, 3, 9, shape, 10)) == 0
        again = await c.writer(_target(shape), shape)
        assert (await again.prepare(dry_run=False)).truncated
        assert await _count(source_pg, "copy") == 0
        pk = await source_pg.admin.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = $1::regclass AND contype = 'p'",
            f'"{source_pg.schema}".copy',
        )
        assert pk == "PRIMARY KEY (id)"
    finally:
        await c.close()


async def test_upsert_load_keeps_rows_and_existing_target_privileges(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table("src2", "id bigint PRIMARY KEY, name text", [])
    await source_pg.table("dest2", "id bigint PRIMARY KEY, name text", [(1, "old"), (2, "keep")])
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        dest = await c.describe(shape.object.model_copy(update={"name": "dest2"}))
        target = _target(shape, object=dest.object, target_shape=dest, table_load="upsert")
        writer = await c.writer(target, shape)
        report = await writer.prepare(dry_run=False)
        assert not report.created and not report.truncated and report.existing_rows is not None
        await writer.write_batch(
            [{"id": 1, "name": "new"}, {"id": 3, "name": "n3"}], mark=_mark(0, 1, 3, shape, 2)
        )
        got = dict(
            await source_pg.admin.fetch(
                f'SELECT id, name FROM "{source_pg.schema}".dest2 ORDER BY id'
            )
        )
        assert got == {1: "new", 2: "keep", 3: "n3"}
    finally:
        await c.close()


async def test_in_place_updates_only_planned_columns_with_a_ledger(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table(
        "people",
        "id bigint PRIMARY KEY, name text, city text",
        [(i, f"n{i}", "Pune") for i in range(6)],
    )
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)

        def in_place() -> ResolvedTarget:
            return _target(
                shape, write_mode="in_place", object=obj, write_columns=["name"], target_shape=shape
            )

        writer = await c.writer(in_place(), shape)
        assert (await writer.prepare(dry_run=False)).ledger
        assert await writer.read_ledger("j_w", 0) is None
        n = await writer.write_batch(
            [{"id": i, "name": f"x{i}", "city": "IGNORED"} for i in range(3)],
            mark=_mark(0, 1, 2, shape, 3),
        )
        assert n == 3
        rows = await source_pg.admin.fetch(
            f'SELECT id, name, city FROM "{source_pg.schema}".people ORDER BY id'
        )
        assert [r["name"] for r in rows] == ["x0", "x1", "x2", "n3", "n4", "n5"]
        assert {r["city"] for r in rows} == {"Pune"}
        mark = await writer.read_ledger("j_w", 0)
        assert mark is not None and (mark.batch_no, mark.rows) == (1, 3)
        assert mark.last_key == encode_key((2,), shape.key_columns())
        stale = await c.writer(in_place(), shape)
        with pytest.raises(LedgerConflict) as conflict:  # a zombie that never saw batch 1
            await stale.write_batch([{"id": 3, "name": "zz"}], mark=_mark(0, 1, 3, shape, 1))
        assert conflict.value.code == "ledger_conflict"
        name3 = f'SELECT name FROM "{source_pg.schema}".people WHERE id = 3'
        assert await source_pg.admin.fetchval(name3) == "n3"
        # the live writer continues; another partition has its own mark
        await writer.write_batch([{"id": 3, "name": "x3"}], mark=_mark(0, 2, 3, shape, 4))
        assert await writer.read_ledger("j_w", 1) is None
        await writer.write_batch([{"id": 5, "name": "x5"}], mark=_mark(1, 1, 5, shape, 1))
        assert await source_pg.admin.fetchval(name3) == "x3"
        assert await writer.clear_ledger("j_w") == 2
        assert await writer.read_ledger("j_w", 0) is None
    finally:
        await c.close()


async def test_composite_keys_update_and_precheck(source_pg: SourcePg, settings: Settings) -> None:
    obj = await source_pg.table(
        "pairs",
        "a int, b varchar(10), v text, PRIMARY KEY (a, b)",
        [(1, "x", "v1"), (1, "y", "v2"), (2, "x", "v3")],
    )
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        writer = await c.writer(
            _target(
                shape, write_mode="in_place", object=obj, write_columns=["v"], target_shape=shape
            ),
            shape,
        )
        await writer.prepare(dry_run=False)
        await writer.read_ledger("j_w", 0)
        n = await writer.write_batch(
            [{"a": 1, "b": "y", "v": "new"}], mark=_mark(0, 1, (1, "y"), shape, 1)
        )
        assert n == 1
        got = await source_pg.admin.fetch(
            f'SELECT a, b, v FROM "{source_pg.schema}".pairs ORDER BY a, b'
        )
        assert [r["v"] for r in got] == ["v1", "new", "v3"]

        async def keys() -> AsyncIterator[list[tuple[Any, ...]]]:
            yield [(1, "x"), (3, "z")]
            yield []
            yield [(2, "x")]

        assert await writer.precheck_collisions(keys()) == 2
    finally:
        await c.close()


async def test_append_offset_precheck_and_collision(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table(
        "acct", "id bigint PRIMARY KEY, v text", [(i, "a") for i in (1, 2, 1001)]
    )
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        writer = await c.writer(
            _target(
                shape,
                write_mode="append",
                object=obj,
                same_as_source=True,
                pk_strategy=PkOffset(offset=1000),
                target_shape=shape,
            ),
            shape,
        )
        report = await writer.prepare(dry_run=False)
        assert report.append_upper == 1002 and report.ledger

        async def keys() -> AsyncIterator[list[tuple[Any, ...]]]:
            yield [(1001,), (1002,)]

        assert await writer.precheck_collisions(keys()) == 1
        await writer.read_ledger("j_w", 0)
        assert (
            await writer.write_batch([{"id": 1002, "v": "b"}], mark=_mark(0, 1, 2, shape, 1)) == 1
        )
        with pytest.raises(PkCollision) as err:
            await writer.write_batch([{"id": 1001, "v": "dup"}], mark=_mark(0, 2, 2, shape, 2))
        assert err.value.code == "pk_collision"
        mark = await writer.read_ledger("j_w", 0)
        assert mark is not None and mark.batch_no == 1  # the refused batch left no mark
        assert await _count(source_pg, "acct") == 4
    finally:
        await c.close()


async def test_append_generate_uses_identity_and_refuses_a_lagging_sequence(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table(
        "gen", "id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, v text", []
    )
    await source_pg.admin.execute(f"INSERT INTO \"{source_pg.schema}\".gen (v) VALUES ('a'), ('b')")
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)

        def gen() -> ResolvedTarget:
            return _target(
                shape,
                write_mode="append",
                object=obj,
                pk_strategy=PkGenerate(),
                generated_key=True,
                write_columns=["v"],
                target_shape=shape,
            )

        writer = await c.writer(gen(), shape)
        await writer.prepare(dry_run=False)
        await writer.read_ledger("j_w", 0)
        await writer.write_batch([{"v": "c"}], mark=_mark(0, 1, 2, shape, 1))
        assert await source_pg.admin.fetchval(f'SELECT max(id) FROM "{source_pg.schema}".gen') == 3
        await source_pg.admin.execute(
            f"INSERT INTO \"{source_pg.schema}\".gen (id, v) VALUES (100, 'x')"
        )
        with pytest.raises(PrepareFailure) as err:
            await (await c.writer(gen(), shape)).prepare(dry_run=False)
        assert err.value.code == "pk_strategy_invalid"
        plain = await source_pg.table("plain", "id bigint PRIMARY KEY, v text", [])
        plain_shape = await c.describe(plain)
        no_default = _target(
            plain_shape,
            write_mode="append",
            object=plain,
            pk_strategy=PkGenerate(),
            generated_key=True,
            write_columns=["v"],
            target_shape=plain_shape,
        )
        with pytest.raises(PrepareFailure) as missing:
            await (await c.writer(no_default, plain_shape)).prepare(dry_run=False)
        assert missing.value.code == "pk_strategy_invalid"
    finally:
        await c.close()


async def test_dry_run_and_privilege_failures(source_pg: SourcePg, settings: Settings) -> None:
    obj = await source_pg.table("dry", "id bigint PRIMARY KEY, v text", [(1, "a")])
    rw = await _rw(source_pg, settings)
    try:
        shape = await rw.describe(obj)
        report = await (await rw.writer(_target(shape), shape)).prepare(dry_run=True)
        assert report.created is False
        exists = f"SELECT to_regclass('\"{source_pg.schema}\".copy')"
        assert await source_pg.admin.fetchval(exists) is None
        in_place = _target(
            shape, write_mode="in_place", object=obj, write_columns=["v"], target_shape=shape
        )
        dry_ledger = await (await rw.writer(in_place, shape)).prepare(dry_run=True)
        assert not dry_ledger.ledger
        ledger = f"SELECT to_regclass('\"{source_pg.schema}\".{LEDGER_TABLE}')"
        assert await source_pg.admin.fetchval(ledger) is None
        missing = _target(
            shape,
            write_mode="table",
            object=shape.object.model_copy(update={"name": "absent"}),
            create_if_missing=False,
        )
        with pytest.raises(PrepareFailure) as gone:
            await (await rw.writer(missing, shape)).prepare(dry_run=False)
        assert gone.value.code == "target_invalid"
        with pytest.raises(ConnectorError) as files:
            await rw.writer(
                _target(shape, write_mode="files", object=None, files=FilesSpec()), shape
            )
        assert files.value.code == "target_invalid"
    finally:
        await rw.close()
    ro = await _rw(source_pg, settings, user="dps_reader")
    try:
        with pytest.raises(PrepareFailure) as err:
            await (
                await ro.writer(
                    _target(
                        shape,
                        write_mode="in_place",
                        object=obj,
                        write_columns=["v"],
                        target_shape=shape,
                    ),
                    shape,
                )
            ).prepare(dry_run=False)
        assert err.value.code == "target_invalid"
    finally:
        await ro.close()


async def test_write_errors_are_coded_and_value_free(
    source_pg: SourcePg, settings: Settings
) -> None:
    obj = await source_pg.table("narrow", "id bigint PRIMARY KEY, v varchar(3)", [])
    c = await _rw(source_pg, settings)
    try:
        shape = await c.describe(obj)
        writer = await c.writer(
            _target(shape, object=obj, target_shape=shape, table_load="upsert"), shape
        )
        await writer.prepare(dry_run=False)
        secret = "Ananya-Krishnan-secret"
        with pytest.raises(ConnectorError) as err:
            await writer.write_batch([{"id": 1, "v": secret}], mark=_mark(0, 1, 1, shape, 1))
        assert err.value.code == "target_error" and not err.value.retryable
        assert secret not in str(err.value) and "Ananya" not in err.value.detail
    finally:
        await c.close()
