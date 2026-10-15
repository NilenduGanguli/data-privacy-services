"""The synthesize run's post-write identifier check (M5 verify r2): counts only, every row."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from dps.engine.batch import ColumnPlan
from dps.engine.policy import MethodSpec
from dps.layers.connectors.base import ColumnInfo, ConnectorError, KeyRange, ObjectRef, TableShape
from dps.layers.connectors.identifier_check import (
    IdentifierTally,
    check_identifiers,
    identifier_columns,
    is_identifier,
    key_floor,
)


def test_identifier_family() -> None:
    for entity in ("NINO", "CURP", "NATIONAL_ID", "PASSPORT", "SSN", "IBAN", "CARD_PAN"):
        assert is_identifier(entity), entity
    for entity in ("PERSON_NAME", "EMAIL", "DATE_OF_BIRTH", "AMOUNT", None):
        assert not is_identifier(entity), entity


def _plan(name: str, entity: str, *, text: bool = False) -> ColumnPlan:
    return ColumnPlan(
        path=f"$.{name}",
        name=name,
        entity_type=entity,
        method=MethodSpec(method="surrogate"),
        text_column=text,
        reversible=False,
        kind="text",
        length=None,
    )


def test_identifier_columns_are_the_written_identifier_family_columns() -> None:
    planned = [
        _plan("national_id", "NATIONAL_ID"),
        _plan("email", "EMAIL"),
        _plan("notes", "NATIONAL_ID", text=True),  # free text: the sweep's business
        _plan("tax_id", "PAN_IN"),
    ]
    cols = identifier_columns(planned, None, ["national_id", "email", "notes", "customer_id"])
    assert cols == {"national_id": "NATIONAL_ID"}  # tax_id was not written


def test_the_tally_counts_source_survivors_and_issuable_values() -> None:
    tally = IdentifierTally({"national_id": "NATIONAL_ID", "iban": "IBAN"})
    for text in ("DF123456A", "FU 65 43 21 B", "AB123456C", "K1234567", None, ""):
        tally.add_target("national_id", text)
    tally.add_target("iban", "GB82WEST12345698765432")
    tally.seal()
    # layout differences do not hide a survivor (the sweep's normalised form)
    tally.add_source("national_id", ["ab 12 34 56 c", "QQ000001A", None])
    tally.add_source("iban", ["DE89370400440532013000"])
    out = tally.result(source_compared=True)
    assert out["columns"]["national_id"] == {
        "entity_type": "NATIONAL_ID",
        "checked": 4,
        "in_source": 1,
        "issuable": 2,  # AB… is an issuable NINO, K1234567 has no known never-issued range
    }
    assert out["columns"]["iban"] == {
        "entity_type": "IBAN",
        "checked": 1,
        "in_source": 0,
        "issuable": 0,  # not a government identifier: the ranges do not apply
    }
    assert out["review_columns"] == ["national_id"]
    clean = IdentifierTally({"national_id": "NATIONAL_ID"})
    clean.add_target("national_id", "DF123456A")
    clean.seal()
    clean.add_source("national_id", ["QQ123456A"])
    assert clean.result(source_compared=True)["review_columns"] == []
    assert "DF123456A" not in repr(out) and "ab 12" not in repr(out)  # counts only


class _Fake:
    """A connector over in-memory rows: keyset pages of 2, paged as Oracle's and MongoDB's
    ``read_partition`` paged before S1-M3 engines r3 B1 — the page it yields is its own list, and
    after the consumer resumes it, it reads that list's length and last key again to decide
    whether to page on. A consumer that empties a page therefore ends the read.

    ``one_page``: every call yields only its first page (a read that stops early: the check must
    page on by itself, after the last key it read). ``ignore_after``: the read starts at the first
    row whatever ``after_key`` says (a read that cannot advance).
    """

    engine = "postgres"

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        one_page: bool = False,
        ignore_after: bool = False,
    ) -> None:
        self.rows = rows
        self.one_page, self.ignore_after = one_page, ignore_after
        self.pages = 0
        self.calls = 0

    async def read_partition(
        self,
        shape: TableShape,
        selection: Any,
        params: dict[str, Any],
        key_range: KeyRange,
        *,
        after_key: Any,
        batch: int,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        assert selection.columns and key_range == KeyRange()
        self.calls += 1
        after = None if self.ignore_after else after_key
        size = 2
        while True:
            rows = [r for r in self.rows if after is None or r["id"] > after[0]]
            page = [{k: v for k, v in r.items() if k in {*selection.columns, "id"}} for r in rows]
            page = page[:size]
            if not page:
                return
            self.pages += 1
            yield page
            # the aliasing the review found: the consumer's list decides the next page
            if self.one_page or len(page) < size:
                return
            after = (page[-1]["id"],)

    async def count(self, shape: TableShape, selection: Any, params: dict[str, Any]) -> int:
        return len(self.rows)


def _shape(name: str, *, key: bool = True) -> TableShape:
    cols = [
        ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int", is_key=key),
        ColumnInfo(path="$.national_id", name="national_id", data_type="text", kind="text"),
    ]
    return TableShape(
        object=ObjectRef(name=name),
        columns=cols,
        key=["id"] if key else [],
        key_source="primary_key" if key else "none",
    )


def test_check_identifiers_reads_every_row_of_both_sides() -> None:
    target = _Fake([{"id": i, "national_id": f"DF{i:06d}A"} for i in range(5)])
    source = _Fake([{"id": i, "national_id": v} for i, v in enumerate(["QQ1", "DF000003A", None])])
    out = asyncio.run(
        check_identifiers(
            {"national_id": "NATIONAL_ID"},
            target=target,  # type: ignore[arg-type]
            target_shape=_shape("t"),
            source=source,  # type: ignore[arg-type]
            source_shape=_shape("s"),
        )
    )
    assert out["columns"]["national_id"]["checked"] == 5
    assert out["columns"]["national_id"]["in_source"] == 1
    assert out["review_columns"] == ["national_id"]
    assert out["scope"] == "whole_table"
    assert out["rows_read"] == {"target": 5, "source": 3}  # what was read, side by side
    assert target.pages == 3 and source.pages == 2
    in_place = asyncio.run(
        check_identifiers(
            {"national_id": "NATIONAL_ID"},
            target=target,  # type: ignore[arg-type]
            target_shape=_shape("t"),
            source=None,
            source_shape=None,
        )
    )
    assert in_place["source_compared"] is False
    assert in_place["columns"]["national_id"]["in_source"] is None
    assert in_place["review_columns"] == []
    assert in_place["rows_read"] == {"target": 5, "source": None}


# -- S1-M3 engines r3 B1: every row, whatever the connector's own paging does -----------------


def _hit_late() -> tuple[_Fake, _Fake]:
    """Nine target rows (pages of 2); the one source value among them sits at target id 7 and
    at source id 6 — both past the first page."""
    target = [{"id": i, "national_id": f"DF{i:06d}A"} for i in range(9)]
    source = [{"id": i, "national_id": f"QQ{i:06d}C"} for i in range(8)]
    source[6]["national_id"] = target[7]["national_id"]
    return _Fake(target), _Fake(source)


def _run(target: Any, source: Any, *, shape: TableShape | None = None) -> dict[str, Any]:
    return asyncio.run(
        check_identifiers(
            {"national_id": "NATIONAL_ID"},
            target=target,
            target_shape=shape or _shape("t"),
            source=source,
            source_shape=shape or _shape("s"),
        )
    )


def test_the_check_reads_past_a_page_a_read_stopped_after() -> None:
    """A read that yields one page (a connector that stops early, or one whose paging a
    consumer cut short) is continued by the check itself, after the last key it read, until a
    read after that key returns no row: the hit past the first page is found on both sides."""
    target, source = _hit_late()
    target.one_page = source.one_page = True
    out = _run(target, source)
    assert out["columns"]["national_id"] == {
        "entity_type": "NATIONAL_ID",
        "checked": 9,
        "in_source": 1,
        "issuable": 0,
    }
    assert out["review_columns"] == ["national_id"]
    assert out["scope"] == "whole_table" and out["rows_read"] == {"target": 9, "source": 8}
    assert target.calls == 6 and source.calls == 5  # 5 + 4 pages, then one empty read each


def test_a_read_that_does_not_advance_fails_closed() -> None:
    """A read that starts again at the first row cannot prove it read every row: the check
    raises (the run's evidence then says ``error`` and every identifier column needs review),
    it never reports ``whole_table`` on what it read."""
    for one_page in (True, False):
        target, source = _hit_late()
        target.ignore_after, target.one_page = True, one_page
        with pytest.raises(ConnectorError) as err:
            _run(target, source)
        assert err.value.code == "identifier_check_incomplete"
        target, source = _hit_late()
        source.ignore_after, source.one_page = True, one_page
        with pytest.raises(ConnectorError) as err:
            _run(target, source)
        assert err.value.code == "identifier_check_incomplete"


def test_a_page_row_without_its_key_fails_closed() -> None:
    class _Keyless(_Fake):
        async def read_partition(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            async for page in super().read_partition(*args, **kwargs):
                yield [{k: v for k, v in r.items() if k != "id"} for r in page]

    target, source = _hit_late()
    with pytest.raises(ConnectorError) as err:
        _run(_Keyless(target.rows), source)
    assert err.value.code == "identifier_check_incomplete"


def test_a_key_less_read_is_held_to_the_row_count() -> None:
    """A key-less object (a MongoDB collection whose ``_id``s cannot be range-paged) is read in
    one pass; fewer rows than the object holds is an incomplete read (fails closed)."""
    shape = _shape("t", key=False)
    target, source = _hit_late()
    out = _run(target, source, shape=shape)
    assert out["columns"]["national_id"]["checked"] == 9
    assert out["columns"]["national_id"]["in_source"] == 1
    assert out["rows_read"] == {"target": 9, "source": 8}
    target, source = _hit_late()
    target.one_page = True  # 2 of 9 rows
    with pytest.raises(ConnectorError) as err:
        _run(target, source, shape=shape)
    assert err.value.code == "identifier_check_incomplete"
    target, source = _hit_late()
    source.one_page = True  # 2 of 8 source rows
    with pytest.raises(ConnectorError) as err:
        _run(target, source, shape=shape)
    assert err.value.code == "identifier_check_incomplete"


def _bare(name: str) -> TableShape:
    """``_shape(name)`` without its national_id column."""
    shape = _shape(name)
    return shape.model_copy(
        update={"columns": [c for c in shape.columns if c.name != "national_id"]}
    )


def _check_shapes(
    target: Any, target_shape: TableShape, source: Any, source_shape: TableShape | None
) -> dict[str, Any]:
    return asyncio.run(
        check_identifiers(
            {"national_id": "NATIONAL_ID"},
            target=target,
            target_shape=target_shape,
            source=source,
            source_shape=source_shape,
        )
    )


def test_a_document_leaf_a_sampled_shape_missed_is_still_read() -> None:
    """A MongoDB shape is inferred from a sample of documents: a leaf the target's sample (or
    both sides' samples) missed is still a field the run wrote. It is read as the other side
    describes it, else as its key names it — never skipped as if it held nothing."""
    for target_shape, source_shape in ((_bare("t"), _shape("s")), (_bare("t"), _bare("s"))):
        target, source = _hit_late()
        target.engine = source.engine = "mongo"
        out = _check_shapes(target, target_shape, source, source_shape)
        assert out["columns"]["national_id"]["checked"] == 9
        assert out["columns"]["national_id"]["in_source"] == 1
        assert out["review_columns"] == ["national_id"]
    target, _ = _hit_late()
    target.engine = "mongo"
    in_place = _check_shapes(target, _bare("t"), None, None)
    assert in_place["columns"]["national_id"]["checked"] == 9


def test_a_sql_column_the_object_does_not_have_fails_closed() -> None:
    """A SQL shape lists every column of the object and a read projects only those: a written
    identifier column the target (or the source) does not list cannot be read, so the check
    fails closed instead of counting no value as a clean column."""
    for target_shape, source_shape in ((_bare("t"), _shape("s")), (_shape("t"), _bare("s"))):
        target, source = _hit_late()
        with pytest.raises(ConnectorError) as err:
            _check_shapes(target, target_shape, source, source_shape)
        assert err.value.code == "identifier_check_incomplete"
    target, _ = _hit_late()
    with pytest.raises(ConnectorError) as err:
        _check_shapes(target, _bare("t"), None, None)
    assert err.value.code == "identifier_check_incomplete"


# -- WP-I (M5 merge): an append checks the rows the run wrote, not the target's own rows ------


def _append_target() -> _Fake:
    """Ids 0-4: the target's own rows (they hold source values); 5-9: what the run appended."""
    own = [{"id": i, "national_id": f"DF{i:06d}A"} for i in range(5)]
    return _Fake(own + [{"id": i, "national_id": f"FU{i:06d}B"} for i in range(5, 10)])


def _source() -> _Fake:
    return _Fake([{"id": i, "national_id": f"DF{i:06d}A"} for i in range(5)])


def _check(target: _Fake, **kw: Any) -> dict[str, Any]:
    return asyncio.run(
        check_identifiers(
            {"national_id": "NATIONAL_ID"},
            target=target,  # type: ignore[arg-type]
            target_shape=_shape("t"),
            source=_source(),  # type: ignore[arg-type]
            source_shape=_shape("s"),
            **kw,
        )
    )


def test_an_append_reads_only_the_rows_after_the_pre_run_key() -> None:
    out = _check(_append_target(), after_key=(4,), expected_rows=5)
    assert out["scope"] == "written_rows"
    assert out["columns"]["national_id"]["checked"] == 5
    assert out["columns"]["national_id"]["in_source"] == 0
    assert out["rows_read"] == {"target": 5, "source": 5}
    # a read that stops after a page is paged on after the floor too
    stopping = _append_target()
    stopping.one_page = True
    out = _check(stopping, after_key=(4,), expected_rows=5)
    assert out["scope"] == "written_rows" and out["rows_read"]["target"] == 5
    # whole table (no floor): the target's own rows are counted, as before
    whole = _check(_append_target())
    assert whole["scope"] == "whole_table" and whole["columns"]["national_id"]["in_source"] == 5


def test_fewer_rows_after_the_floor_than_written_fails_closed_to_the_whole_table() -> None:
    """A run whose keys landed below the floor (a sequence behind the table's maximum) cannot be
    told apart from the target's own rows: every row is checked again."""
    out = _check(_append_target(), after_key=(7,), expected_rows=5)
    assert out["scope"] == "whole_table" and out["scope_reason"] == "rows_below_floor"
    assert out["columns"]["national_id"]["checked"] == 10
    assert out["columns"]["national_id"]["in_source"] == 5
    assert out["review_columns"] == ["national_id"]


def test_key_floor_is_the_largest_single_integer_key() -> None:
    assert asyncio.run(key_floor(_append_target(), _shape("t"))) == (9,)  # type: ignore[arg-type]
    stopping = _append_target()
    stopping.one_page = True  # every page of the key column, not the first one only
    assert asyncio.run(key_floor(stopping, _shape("t"))) == (9,)  # type: ignore[arg-type]
    assert stopping.calls == 6
    assert asyncio.run(key_floor(_Fake([]), _shape("t"))) is None  # type: ignore[arg-type]
    text_key = _shape("t").model_copy(
        update={
            "columns": [
                c.model_copy(update={"kind": "text"}) if c.is_key else c
                for c in _shape("t").columns
            ]
        }
    )
    assert asyncio.run(key_floor(_append_target(), text_key)) is None  # type: ignore[arg-type]
    composite = _shape("t").model_copy(update={"key": ["id", "national_id"]})
    assert asyncio.run(key_floor(_append_target(), composite)) is None  # type: ignore[arg-type]


def test_an_incomplete_check_reaches_the_evidence_as_an_error() -> None:
    """The run's finalize records a check that could not read every row as ``{"error": code}``
    — which makes every written identifier column a review column — never as counts."""
    from dps.layers.connectors.jobs_run import _best_effort

    target, source = _hit_late()
    target.ignore_after = True
    out = asyncio.run(
        _best_effort(
            check_identifiers(
                {"national_id": "NATIONAL_ID"},
                target=target,  # type: ignore[arg-type]
                target_shape=_shape("t"),
                source=source,  # type: ignore[arg-type]
                source_shape=_shape("s"),
            )
        )
    )
    assert out == {"error": "identifier_check_incomplete"}
