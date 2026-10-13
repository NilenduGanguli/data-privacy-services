"""The synthesize run's post-write identifier check (M5 verify r2): counts only, every row."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from dps.engine.batch import ColumnPlan
from dps.engine.policy import MethodSpec
from dps.layers.connectors.base import ColumnInfo, KeyRange, ObjectRef, TableShape
from dps.layers.connectors.identifier_check import (
    IdentifierTally,
    check_identifiers,
    identifier_columns,
    is_identifier,
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
    """A connector over in-memory rows (keyset pages of 2)."""

    engine = "postgres"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.pages = 0

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
        assert selection.columns and key_range == KeyRange() and after_key is None
        for i in range(0, len(self.rows), 2):
            self.pages += 1
            yield [
                {k: v for k, v in r.items() if k in {*selection.columns, "id"}}
                for r in self.rows[i : i + 2]
            ]


def _shape(name: str) -> TableShape:
    cols = [
        ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int", is_key=True),
        ColumnInfo(path="$.national_id", name="national_id", data_type="text", kind="text"),
    ]
    return TableShape(
        object=ObjectRef(name=name), columns=cols, key=["id"], key_source="primary_key"
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
