"""The run handler's same-table append read bound (``jobs_run.append_read_bound``): M2's integer
``append_upper`` unchanged, and M3's encoded ``append_upper_key`` (ObjectId, text prefix)."""

from __future__ import annotations

from dps.layers.connectors.base import ColumnInfo, PrepareReport, encode_key
from dps.layers.connectors.jobs_run import append_read_bound

INT = [ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int")]
OID = [ColumnInfo(path="$._id", name="_id", data_type="objectId", kind="object_id")]
TEXT = [ColumnInfo(path="$._id", name="_id", data_type="string", kind="text")]


class FakeOid:
    """Stands in for ``bson.ObjectId``: ``str()`` is the 24-digit hex text."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text


def test_integer_bounds_are_m2s() -> None:
    report = PrepareReport(append_upper=1013)
    assert append_read_bound(None, report, INT) == (1013,)
    assert append_read_bound((500,), report, INT) == (500,)
    assert append_read_bound((2000,), report, INT) == (1013,)
    assert append_read_bound((7,), PrepareReport(), INT) == (7,)
    assert append_read_bound(None, PrepareReport(), INT) is None


def test_object_id_bounds_compare_in_database_order() -> None:
    bound = "6720000000000000000000ff"
    report = PrepareReport(append_upper_key=encode_key((bound,), OID))
    assert append_read_bound(None, report, OID) == (bound,)
    below = FakeOid("671fffffffffffffffffffff")
    above = FakeOid("672000000000000000000100")
    assert append_read_bound((below,), report, OID) == (below,)  # max_rows ends earlier
    assert append_read_bound((above,), report, OID) == (bound,)


def test_text_prefix_bounds() -> None:
    report = PrepareReport(append_upper_key=encode_key(("Z-",), TEXT))
    assert append_read_bound(None, report, TEXT) == ("Z-",)
    assert append_read_bound(("K010",), report, TEXT) == ("K010",)
