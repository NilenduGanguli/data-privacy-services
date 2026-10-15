"""WP-I (m3 merge): M5's post-write identifier check on MongoDB documents.

The check keyed identifier columns by name; a MongoDB leaf's name is its top-level field, so
``$.kyc.taxId`` (PAN) read the whole ``kyc`` sub-document as one "PAN" and ``$.accounts[*].iban``
the whole array — JSON text that is never in a never-issued range (every synthesize of a profile
read ``review_required``) and that can never match a source value (a survivor would be missed).
Leaves are keyed by path and read element by element now.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from dps.engine.batch import ColumnPlan
from dps.engine.policy import MethodSpec
from dps.layers.connectors.base import ColumnInfo, KeyRange, ObjectRef, TableShape
from dps.layers.connectors.identifier_check import check_identifiers, identifier_columns


class _Docs:
    """A connector over in-memory documents (the projection is top-level fields), one page in
    ``_id`` order after ``after_key`` as the connector protocol reads (the identifier check
    pages on after the last ``_id`` it read until a read returns nothing)."""

    engine = "mongo"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = sorted(rows, key=lambda r: r["_id"])

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
        assert set(selection.columns) <= {"_id", "kyc", "accounts", "segment"}
        page = [
            {k: v for k, v in r.items() if k in {*selection.columns, "_id"}}
            for r in self.rows
            if after_key is None or r["_id"] > after_key[0]
        ]
        if page:
            yield page


SHAPE = TableShape(
    object=ObjectRef(name="profiles"),
    key=["_id"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$._id", name="_id", data_type="objectId", kind="object_id", is_key=True),
        ColumnInfo(path="$.kyc.taxId", name="kyc", data_type="string", kind="text"),
        ColumnInfo(path="$.kyc.verifiedAt", name="kyc", data_type="date", kind="timestamp"),
        ColumnInfo(path="$.accounts[*].iban", name="accounts", data_type="string", kind="text"),
    ],
)


def _plan(path: str, name: str, entity: str) -> ColumnPlan:
    return ColumnPlan(
        path=path,
        name=name,
        entity_type=entity,
        method=MethodSpec(method="surrogate"),
        text_column=False,
        reversible=False,
        kind="text",
        length=None,
    )


PLANS = [_plan("$.kyc.taxId", "kyc", "PAN_IN"), _plan("$.accounts[*].iban", "accounts", "IBAN")]


def test_identifier_columns_key_document_leaves_by_path() -> None:
    cols = identifier_columns(PLANS, None, ["_id", "kyc", "accounts", "segment"])
    assert cols == {"$.accounts[*].iban": "IBAN", "$.kyc.taxId": "PAN_IN"}
    assert identifier_columns(PLANS, None, ["_id", "segment"]) == {}  # neither field written


def _doc(i: int, pan: str, ibans: list[str]) -> dict[str, Any]:
    return {
        "_id": i,
        "kyc": {"taxId": pan, "verifiedAt": "2024-05-01"},
        "accounts": [{"iban": x, "balance": 1} for x in ibans],
    }


def test_the_check_reads_each_leaf_value() -> None:
    # never-issued PANs (sequence 0000); one written IBAN equals a source IBAN
    target = _Docs(
        [
            _doc(1, "ABCPK0000F", ["GB33BUKB20201555555555", "GB94BARC10201530093459"]),
            _doc(2, "ABDPK0000G", ["GB82WEST12345698765432"]),
        ]
    )
    source = _Docs([_doc(9, "ABCPK1234F", ["GB82WEST12345698765432"]), _doc(8, "BBCPK1234F", [])])
    out = asyncio.run(
        check_identifiers(
            identifier_columns(PLANS, None, ["_id", "kyc", "accounts"]),
            target=target,  # type: ignore[arg-type]
            target_shape=SHAPE,
            source=source,  # type: ignore[arg-type]
            source_shape=SHAPE,
        )
    )
    pan, iban = out["columns"]["$.kyc.taxId"], out["columns"]["$.accounts[*].iban"]
    assert (pan["checked"], pan["issuable"], pan["in_source"]) == (2, 0, 0)
    assert (iban["checked"], iban["in_source"]) == (3, 1)
    assert out["review_columns"] == ["$.accounts[*].iban"]
    assert out["rows_read"] == {"target": 2, "source": 2}  # documents, not leaf values
    assert "GB82" not in repr(out) and "ABCPK" not in repr(out)  # counts only
