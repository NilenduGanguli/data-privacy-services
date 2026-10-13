"""Engine-agnostic connector conformance suite (contract §12.3).

An engine binding subclasses :class:`ConnectorConformance` (setting ``pytestmark`` itself) and
provides one fixture, ``env``, implementing :class:`ConformanceEnv` against a live database. M2
binds PostgreSQL (``test_conformance_postgres.py``); M3 binds Oracle and MongoDB to the same
methods. Every table the suite makes holds synthetic rows only: ``name`` = ``Name <i>``,
``note`` = ``note-<i>@example.test``.

The suite checks what every connector promises (contract §4.2): describe finds the paging key;
keyset paging visits every row once in the database's own order (never Python's); sampled
partition ranges cover every key exactly once; bad selections are refused before a query runs; a
``table`` reload is idempotent; an ``in_place`` batch whose checkpoint was lost is never applied
twice (the ledger mark wins); an append offset pre-check counts exactly the clashing keys; and
nothing a connector logs carries a row value.

Engines differ in names, not in promises (M3): a binding may set the optional members of
:class:`ConformanceEnv` — ``names`` (logical → physical column names: Oracle folds to upper case,
MongoDB's key is ``_id``), ``keyless`` (what ``describe`` reports for a table without a key:
Oracle pages it on ``ROWID``), ``composite_keys`` (MongoDB has ``_id`` only; the composite cases then
use the engine's text key or skip) and ``equals_selection`` (a bound equality filter in the engine's
selection language). Defaults are PostgreSQL's, so its binding needs none of them. Every assertion
is the same on every engine.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.transform.crypto import generate_key_material
from dps.jobs_models import Selection
from dps.layers.connectors.base import (
    Connector,
    KeyRange,
    LedgerMark,
    ObjectRef,
    PkOffset,
    ResolvedTarget,
    Row,
    TableShape,
    WriteMode,
    decode_key,
    encode_key,
    key_tuple,
)
from dps.layers.connectors.partitions import plan_partitions
from dps.layers.connectors.selection import (
    SelectionError,
    parameterize_literals,
    validate_selection,
)
from dps.layers.connectors.targets import apply_pk_strategy
from dps.observability import configure_logging

KeyKind = Literal["int", "composite", "text", "none"]
_ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
)


class ConformanceEnv(Protocol):
    """What an engine binding provides (one live database, two logins)."""

    engine: str
    source: Connector  # opened, read-only login
    rw: Connector  # opened, read-write login (same database)

    async def make_table(self, name: str, *, key: KeyKind, rows: int) -> ObjectRef:
        """A fresh table: key ``int`` (``id``), ``composite`` (``a``, ``b``), ``text``
        (``code``, values whose collation order differs from Python's) or ``none``; plus
        ``name`` and ``note`` text columns; ``rows`` synthetic rows (``id`` = 1..rows)."""
        ...

    async def insert(self, obj: ObjectRef, rows: list[Row]) -> None:
        """Insert rows as they are (an admin login)."""
        ...

    async def read_all(self, obj: ObjectRef) -> list[Row]:
        """Every row, ordered by the key **in the database** (never sorted in Python)."""
        ...

    def target(
        self, obj: ObjectRef, shape: TableShape, write_mode: WriteMode, **kw: Any
    ) -> ResolvedTarget:
        """A resolved ``write_mode`` target writing ``obj`` with the columns of ``shape``."""
        ...

    def rejected_selections(self) -> list[tuple[Selection, str]]:
        """Selections the engine must refuse, with the code each one gets."""
        ...

    # Optional members (read with ``getattr``; defaults in parentheses):
    # names: dict[str, str]              logical column → physical name ({} = the same names)
    # keyless: tuple[list[str], str]     describe() of a key-less table (([], "none"))
    # composite_keys: bool               composite primary keys exist (True)
    # def equals_selection(column: str, value: str) -> Selection   (SQL ``<column> = :n``)


def _n(env: ConformanceEnv, logical: str) -> str:
    """The engine's name for a logical column of the suite's tables (``id``, ``name`` …)."""
    names: dict[str, str] = getattr(env, "names", {})
    return names.get(logical, logical)


def _composite(env: ConformanceEnv) -> bool:
    return bool(getattr(env, "composite_keys", True))


def _equals(env: ConformanceEnv, column: str, value: str) -> Selection:
    make = getattr(env, "equals_selection", None)
    if make is not None:
        selection: Selection = make(column, value)
        return selection
    return Selection(where=f"{column} = :n", params={"n": value})


def sql_rejected_selections() -> list[tuple[Selection, str]]:
    """The SQL engines' cases (PostgreSQL, Oracle): lock, two statements, a denied function, an
    unknown column."""
    return [
        (Selection(query="SELECT * FROM t FOR UPDATE"), "selection_for_update"),
        (Selection(where="id > 0; DELETE FROM t"), "selection_multiple_statements"),
        (Selection(where="pg_sleep(1) IS NOT NULL"), "selection_function_denied"),
        (Selection(where="no_such_column = 1"), "selection_unknown_column"),
    ]


def _job() -> str:
    return f"j_conf{secrets.token_hex(6)}"


async def _all_batches(
    c: Connector, shape: TableShape, key_range: KeyRange, *, batch: int
) -> list[list[Row]]:
    out: list[list[Row]] = []
    async for rows in c.read_partition(
        shape, Selection(), {}, key_range, after_key=None, batch=batch
    ):
        out.append(rows)
    return out


def _mark(job: str, part: int, batch: int, last: Row, shape: TableShape, rows: int) -> LedgerMark:
    return LedgerMark(
        job_id=job,
        partition_no=part,
        batch_no=batch,
        last_key=encode_key(key_tuple(last, shape), shape.key_columns()),
        rows=rows,
    )


def _name_plan(shape: TableShape, name: str) -> list[ColumnPlan]:
    col = shape.column(name)
    return [
        ColumnPlan(
            path=col.path,
            name=name,
            entity_type="PERSON_NAME",
            method=MethodSpec(method="det_token"),
            text_column=False,
            reversible=True,
            kind=col.kind,
            length=col.length,
        )
    ]


class ConnectorConformance:
    """The suite; an engine binding subclasses it and provides the ``env`` fixture."""

    async def test_describe_finds_the_key(self, env: ConformanceEnv) -> None:
        single = await env.source.describe(await env.make_table("k_int", key="int", rows=3))
        assert single.key == [_n(env, "id")] and single.key_source == "primary_key"
        assert single.column(_n(env, "id")).is_key and not single.column(_n(env, "name")).is_key
        if _composite(env):
            pair = await env.source.describe(
                await env.make_table("k_pair", key="composite", rows=3)
            )
            assert pair.key == [_n(env, "a"), _n(env, "b")] and pair.key_source == "primary_key"
        bare = await env.source.describe(await env.make_table("k_none", key="none", rows=3))
        assert (bare.key, bare.key_source) == getattr(env, "keyless", ([], "none"))

    @pytest.mark.parametrize("key", ["int", "composite", "text"])
    async def test_keyset_paging_visits_each_row_once(
        self, env: ConformanceEnv, key: KeyKind
    ) -> None:
        if key == "composite" and not _composite(env):
            pytest.skip(f"{env.engine}: no composite keys (the text key case covers strings)")
        obj = await env.make_table(f"page_{key}", key=key, rows=53)
        shape = await env.source.describe(obj)
        batches = await _all_batches(env.source, shape, KeyRange(), batch=7)
        assert all(0 < len(b) <= 7 for b in batches) and len(batches) == 8
        paged = [key_tuple(r, shape) for b in batches for r in b]
        expected = [key_tuple(r, shape) for r in await env.read_all(obj)]
        assert paged == expected and len(set(paged)) == 53  # the database's order, once each
        # resuming after any key continues with exactly the rows after it
        middle = paged[20]
        rest: list[tuple[Any, ...]] = []
        async for rows in env.source.read_partition(
            shape, Selection(), {}, KeyRange(), after_key=middle, batch=10
        ):
            rest += [key_tuple(r, shape) for r in rows]
        assert rest == expected[21:]

    async def test_partitions_cover_every_row_exactly_once(self, env: ConformanceEnv) -> None:
        obj = await env.make_table(
            "parts", key="composite" if _composite(env) else "text", rows=200
        )
        shape = await env.source.describe(obj)
        sample = await env.source.sample_keys(shape, Selection(), {}, 40)
        ranges = plan_partitions(sample, count=5, columns=shape.key_columns())
        assert 1 < len(ranges) <= 5
        seen: list[tuple[Any, ...]] = []
        for key_range in ranges:
            for rows in await _all_batches(env.source, shape, key_range, batch=50):
                seen += [key_tuple(r, shape) for r in rows]
        everything = [key_tuple(r, shape) for r in await env.read_all(obj)]
        assert len(seen) == len(set(seen)) == 200 and set(seen) == set(everything)

    async def test_selection_rejections(self, env: ConformanceEnv) -> None:
        shape = await env.source.describe(await env.make_table("sel", key="int", rows=5))
        engine: Any = env.engine
        for selection, code in env.rejected_selections():
            with pytest.raises(SelectionError) as err:
                validate_selection(parameterize_literals(selection, engine), engine, shape)
            assert err.value.code == code
            if code != "selection_unknown_column":  # refused by the connector too, unrun
                with pytest.raises(SelectionError):
                    await env.source.count(shape, selection, selection.params)

    async def test_table_reload_is_idempotent(self, env: ConformanceEnv) -> None:
        obj = await env.make_table("reload_src", key="int", rows=40)
        shape = await env.source.describe(obj)
        dest = obj.model_copy(update={"name": "reload_dest"})
        job = _job()
        writer = await env.rw.writer(env.target(dest, shape, "table", job_id=job), shape)
        try:
            await writer.prepare(dry_run=False)
            rows = await env.read_all(obj)
            first, second = rows[:25], rows[25:]
            for _ in range(2):  # the same two batches twice: a resumed table load
                await writer.write_batch(first, mark=_mark(job, 0, 1, first[-1], shape, 25))
                await writer.write_batch(second, mark=_mark(job, 0, 2, second[-1], shape, 40))
        finally:
            await writer.close()
        assert await env.read_all(dest) == rows

    async def test_in_place_crash_between_write_and_checkpoint_never_double_applies(
        self, env: ConformanceEnv
    ) -> None:
        obj = await env.make_table("inplace", key="int", rows=30)
        shape = await env.rw.describe(obj)
        before = {key_tuple(r, shape): r for r in await env.read_all(obj)}
        name, note = _n(env, "name"), _n(env, "note")
        transformer = BatchTransformer(
            _ENGINE,
            policy=load_preset("gdpr-pseudonymise"),
            mode="obfuscate",
            columns=_name_plan(shape, name),
            shape=shape,
            key=generate_key_material("key"),
            seed=None,
            locale="en_US",
        )
        once = {
            key_tuple(r, shape): r[name] for r in transformer.transform(list(before.values())).rows
        }
        job = _job()
        target = env.target(obj, shape, "in_place", job_id=job, write_columns=[name])

        async def batches(after: tuple[Any, ...] | None) -> AsyncIterator[list[Row]]:
            async for rows in env.rw.read_partition(
                shape, Selection(), {}, KeyRange(), after_key=after, batch=10
            ):
                yield rows

        crashed = await env.rw.writer(target, shape)
        await crashed.prepare(dry_run=False)
        assert await crashed.read_ledger(job, 0) is None
        first_read = batches(None)
        rows = await anext(first_read)  # batch 1 commits with its mark; the checkpoint is lost
        await first_read.aclose()
        out = transformer.transform(rows).rows
        assert await crashed.write_batch(out, mark=_mark(job, 0, 1, rows[-1], shape, 10)) == 10
        await crashed.close()
        fresh = await env.rw.writer(target, shape)
        try:
            mark = await fresh.read_ledger(job, 0)
            assert mark is not None and mark.batch_no == 1 and mark.rows == 10
            after = decode_key(mark.last_key, shape.key_columns())
            batch_no, total = mark.batch_no, mark.rows
            async for rows in batches(after):  # the resume continues after the ledger mark
                out = transformer.transform(rows).rows
                batch_no, total = batch_no + 1, total + len(rows)
                await fresh.write_batch(out, mark=_mark(job, 0, batch_no, rows[-1], shape, total))
            assert batch_no == 3 and total == 30
            await fresh.clear_ledger(job)
        finally:
            await fresh.close()
        after_rows = await env.read_all(obj)
        assert {key_tuple(r, shape): r[name] for r in after_rows} == once  # no token of a token
        assert all(r[note] == before[key_tuple(r, shape)][note] for r in after_rows)

    async def test_append_offset_and_precheck(self, env: ConformanceEnv) -> None:
        obj = await env.make_table("appender", key="int", rows=20)
        shape = await env.rw.describe(obj)
        ident, name, note = _n(env, "id"), _n(env, "name"), _n(env, "note")
        await env.insert(
            obj, [{ident: 1005, name: "x", note: "x"}, {ident: 1012, name: "y", note: "y"}]
        )
        strategy = PkOffset(offset=1000)
        job = _job()
        target = env.target(
            obj, shape, "append", job_id=job, same_as_source=True, pk_strategy=strategy
        )
        writer = await env.rw.writer(target, shape)
        try:
            report = await writer.prepare(dry_run=False)
            assert report.append_upper == 1013

            async def shifted() -> AsyncIterator[list[tuple[Any, ...]]]:
                yield [(i + 1000,) for i in range(1, 11)]
                yield []
                yield [(i + 1000,) for i in range(11, 21)]

            assert await writer.precheck_collisions(shifted()) == 2  # exactly 1005 and 1012
            originals = [r for r in await env.read_all(obj) if r[ident] <= 3]
            out = apply_pk_strategy([dict(r) for r in originals], strategy=strategy, key=shape.key)
            assert await writer.read_ledger(job, 0) is None
            assert (
                await writer.write_batch(out, mark=_mark(job, 0, 1, originals[-1], shape, 3)) == 3
            )
            await writer.clear_ledger(job)
        finally:
            await writer.close()
        ids = [r[ident] for r in await env.read_all(obj)]
        assert ids == [*range(1, 21), 1001, 1002, 1003, 1005, 1012]

    async def test_no_values_in_logs(
        self, env: ConformanceEnv, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging("DEBUG", json_output=True)
        try:
            obj = await env.make_table("quiet", key="int", rows=25)
            shape = await env.source.describe(obj)
            rows = [
                r for b in await _all_batches(env.source, shape, KeyRange(), batch=10) for r in b
            ]
            job = _job()
            dest = obj.model_copy(update={"name": "quiet_dest"})
            writer = await env.rw.writer(env.target(dest, shape, "table", job_id=job), shape)
            try:
                await writer.prepare(dry_run=False)
                await writer.write_batch(rows, mark=_mark(job, 0, 1, rows[-1], shape, len(rows)))
            finally:
                await writer.close()
            with pytest.raises(SelectionError):
                await env.source.count(shape, Selection(where="name = 'Name 7'; SELECT 1"), {})
            equals = _equals(env, _n(env, "name"), "Name 7")
            assert await env.source.count(shape, equals, dict(equals.params)) == 1
        finally:
            configure_logging("INFO", json_output=False)
        captured = capsys.readouterr()
        logged = captured.out + captured.err
        for row in rows:
            assert row[_n(env, "name")] not in logged and row[_n(env, "note")] not in logged
