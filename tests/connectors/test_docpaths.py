"""Wildcard JSON paths over nested documents and the discovery seams that use them (offline)."""

from __future__ import annotations

from dps.layers.connectors import docpaths
from dps.layers.connectors.base import ColumnInfo

DOC = {
    "_id": 1,
    "customerId": "CUST-10000001",
    "contact": {
        "email": "a@example.com",
        "addresses": [{"line1": "1 Main"}, {"line1": "2 High", "pin": "560001"}],
    },
    "accounts": [],
    "tags": ["vip", "salary"],
}


def test_generalise_and_expand() -> None:
    assert docpaths.generalise("$.contact.addresses[1].line1") == "$.contact.addresses[*].line1"
    assert docpaths.expand_wildcards(DOC, "$.contact.addresses[*].line1") == [
        "$.contact.addresses[0].line1",
        "$.contact.addresses[1].line1",
    ]
    assert docpaths.expand_wildcards(DOC, "$.contact.addresses[*].pin") == [
        "$.contact.addresses[1].pin"
    ]
    assert docpaths.expand_wildcards(DOC, "$.accounts[*].iban") == []
    assert docpaths.expand_wildcards(DOC, "$.missing.x") == []
    assert docpaths.expand_wildcards(DOC, "$.customerId.x") == []  # a scalar has no children
    assert docpaths.values_at(DOC, "$.tags[*]") == ["vip", "salary"]
    assert docpaths.values_at(DOC, "$.contact.email") == ["a@example.com"]


def test_column_values_for_sql_rows_and_documents() -> None:
    sql = ColumnInfo(path="$.FULL_NAME", name="FULL_NAME", data_type="VARCHAR2(120)", kind="text")
    assert docpaths.column_values([{"FULL_NAME": "A"}, {"FULL_NAME": None}, {}], sql) == ["A", None]
    odd = ColumnInfo(path="$['A$B']", name="A$B", data_type="NUMBER", kind="int")
    assert docpaths.top_level(odd) and docpaths.column_values([{"A$B": 7}], odd) == [7]
    nested = ColumnInfo(
        path="$.contact.addresses[*].line1", name="contact", data_type="string", kind="text"
    )
    assert not docpaths.top_level(nested)
    assert docpaths.column_values([DOC, DOC], nested) == ["1 Main", "2 High", "1 Main", "2 High"]


def test_forced_keep() -> None:
    rid = ColumnInfo(
        path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", is_key=True, pseudo=True
    )
    oid = ColumnInfo(path="$._id", name="_id", data_type="objectId", kind="object_id", is_key=True)
    blob = ColumnInfo(path="$.SCAN", name="SCAN", data_type="BLOB", kind="lob_binary")
    txt = ColumnInfo(path="$.N", name="N", data_type="string", kind="text")
    assert docpaths.forced_keep(rid) and docpaths.forced_keep(oid) and docpaths.forced_keep(blob)
    assert not docpaths.forced_keep(txt)


def test_row_locators_are_not_table_data() -> None:
    """The ROWID pseudo key and real ``ROWID``/``UROWID`` columns address rows: they are kept and
    never classified (not even by name); binary columns and ObjectIds are kept but still named."""
    rid = ColumnInfo(
        path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", is_key=True, pseudo=True
    )
    stored = ColumnInfo(path="$.ACCOUNT_REF", name="ACCOUNT_REF", data_type="ROWID", kind="other")
    urowid = ColumnInfo(path="$.SRC", name="SRC", data_type="UROWID(4000)", kind="other")
    blob = ColumnInfo(path="$.SCAN", name="SCAN", data_type="BLOB", kind="lob_binary")
    assert all(docpaths.row_locator(c) and docpaths.forced_keep(c) for c in (rid, stored, urowid))
    assert not docpaths.row_locator(blob) and docpaths.forced_keep(blob)


def test_dotted_and_leaf_updates() -> None:
    assert docpaths.dotted("$.contact.addresses[1].line1") == "contact.addresses.1.line1"
    assert docpaths.dotted("$.accounts[*].iban") == "accounts.iban"
    assert docpaths.dotted("$['a.b']") is None and docpaths.dotted("$['$x']") is None
    assert docpaths.dotted("$") is None
    upd = docpaths.leaf_updates(DOC)
    assert upd is not None and "_id" not in upd and upd["contact.addresses.1.pin"] == "560001"
    assert upd["tags.0"] == "vip" and "accounts" not in upd
    assert docpaths.leaf_updates({"_id": 1, "a.b": 1}) is None
    assert docpaths.leaf_updates({"_id": {"k": 1}, "a": 2}) == {"a": 2}
