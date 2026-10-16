"""``MongoConnector``'s ``$sample`` when the server's pseudo-random cursor gives up (code 28799).

Review S6b r1 B1: a ``table`` target emptied and refilled by every run is mostly deleted records;
the pseudo-random cursor lands among them, walks to the same live document and fails after 100
duplicates in a row — every time, not "sporadically". The sample is retried, then taken without
that cursor. Offline: a fake collection stands in for the server (the live reproduction is
``test_mongo_connector.py::test_describe_and_sample_survive_a_collection_of_deleted_records``).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("pymongo")
from pymongo.errors import OperationFailure  # noqa: E402

from dps.jobs_models import Selection  # noqa: E402
from dps.layers.connectors.base import ConnectorError, ObjectRef, TableShape  # noqa: E402
from dps.layers.connectors.mongo import (  # noqa: E402
    ALL_DOCUMENTS,
    NATURAL_ORDER,
    MongoConnector,
    sample_pipeline,
)
from tests.connectors.mongo_fixtures import connector  # noqa: E402

DUPLICATES = 28799
DOCS = [{"_id": i, "customerId": f"CUST-{10_000_000 + i}"} for i in range(3)]


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]], error: Exception | None) -> None:
        self._docs, self._error = docs, error

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        if self._error is not None:
            raise self._error  # a getMore past the first batch fails the same way
        return list(self._docs)


class FakeCollection:
    """``aggregate`` fails with ``code`` while the pipeline would use the random cursor."""

    def __init__(self, failures: int, *, code: int = DUPLICATES, on_get_more: bool = False):
        self.failures, self.code, self.on_get_more = failures, code, on_get_more
        self.calls: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []

    async def aggregate(self, pipeline: list[dict[str, Any]], **kwargs: Any) -> FakeCursor:
        self.calls.append((pipeline, kwargs))
        random_cursor = "$sample" in pipeline[0]
        error: Exception | None = None
        if self.failures and (random_cursor or self.code != DUPLICATES):
            self.failures -= 1
            error = OperationFailure("could not find a non-duplicate document", code=self.code)
        if error is not None and not self.on_get_more:
            raise error
        return FakeCursor(DOCS, error)


def _connector(monkeypatch: pytest.MonkeyPatch, coll: FakeCollection) -> MongoConnector:
    conn = connector("mongodb://127.0.0.1:1/?directConnection=true", "dps_t_fake")
    monkeypatch.setattr(conn, "_client", object())  # open: nothing to connect
    monkeypatch.setattr(conn, "read_collection", lambda name: coll)
    return conn


SHAPE = TableShape(object=ObjectRef(name="profiles_demo"), columns=[], key=[], key_source="none")


@pytest.mark.parametrize("on_get_more", [False, True], ids=["aggregate", "getMore"])
async def test_a_random_cursor_failure_is_retried(
    monkeypatch: pytest.MonkeyPatch, on_get_more: bool
) -> None:
    coll = FakeCollection(failures=1, on_get_more=on_get_more)
    docs = await _connector(monkeypatch, coll).sample(SHAPE, Selection(), {}, 500)
    assert docs == DOCS
    assert [p for p, _ in coll.calls] == [[{"$sample": {"size": 500}}]] * 2
    assert all("hint" not in kw for _, kw in coll.calls)


@pytest.mark.parametrize("on_get_more", [False, True], ids=["aggregate", "getMore"])
async def test_a_persistent_random_cursor_failure_samples_without_it(
    monkeypatch: pytest.MonkeyPatch, on_get_more: bool
) -> None:
    coll = FakeCollection(failures=99, on_get_more=on_get_more)
    conn = _connector(monkeypatch, coll)
    docs = await conn.sample(SHAPE, Selection(), {}, 500)
    assert docs == DOCS
    *tries, (fallback, kwargs) = coll.calls
    assert len(tries) == 2 and all(p == [{"$sample": {"size": 500}}] for p, _ in tries)
    assert fallback == [ALL_DOCUMENTS, {"$sample": {"size": 500}}]
    assert kwargs["hint"] == NATURAL_ORDER == {"$natural": 1}
    # the fallback keeps the server-side time limit and the operation's comment
    assert all(kw["maxTimeMS"] == conn.max_time_ms for _, kw in coll.calls)
    assert all(kw["comment"] == "dps:test:j_test" for _, kw in coll.calls)


async def test_describe_samples_through_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    coll = FakeCollection(failures=99)
    conn = _connector(monkeypatch, coll)

    async def collections() -> set[str]:
        return {"profiles_demo"}

    async def estimated(**_kw: Any) -> int:
        return 20_000

    async def bounds(_name: str) -> list[Any]:
        return [0, 2]

    monkeypatch.setattr(conn, "collections", collections)
    monkeypatch.setattr(coll, "estimated_document_count", estimated, raising=False)
    monkeypatch.setattr(conn, "_id_bounds", bounds)
    shape = await conn.describe(ObjectRef(name="profiles_demo"))
    assert {c.path for c in shape.columns} == {"$._id", "$.customerId"}
    assert coll.calls[-1][0][0] == ALL_DOCUMENTS and len(coll.calls) == 3


async def test_another_server_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    coll = FakeCollection(failures=1, code=50)  # MaxTimeMSExpired
    with pytest.raises(ConnectorError) as err:
        await _connector(monkeypatch, coll).sample(SHAPE, Selection(), {}, 500)
    assert err.value.code == "statement_timeout" and err.value.detail == "mongo:50"
    assert len(coll.calls) == 1


async def test_a_failing_fallback_is_a_value_free_source_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coll = FakeCollection(failures=99)

    async def broken(pipeline: list[dict[str, Any]], **kwargs: Any) -> FakeCursor:
        coll.calls.append((pipeline, kwargs))
        raise OperationFailure("dup key: { customerId: CUST-10000001 }", code=DUPLICATES)

    monkeypatch.setattr(coll, "aggregate", broken)
    with pytest.raises(ConnectorError) as err:
        await _connector(monkeypatch, coll).sample(SHAPE, Selection(), {}, 500)
    assert err.value.code == "source_error" and err.value.detail == "mongo:28799"
    assert "CUST" not in str(err.value) and len(coll.calls) == 3


def test_sample_pipelines() -> None:
    flt = {"segment": "retail"}
    assert sample_pipeline({}, 500) == [{"$sample": {"size": 500}}]
    assert sample_pipeline({}, 500, random_cursor=False) == [
        {"$match": {"_id": {"$exists": True}}},
        {"$sample": {"size": 500}},
    ]
    # a filter already keeps $sample off the first stage: both pipelines lead with it
    for random_cursor in (True, False):
        assert sample_pipeline(
            flt, 7, project={"_id": 1}, then=[{"$sort": {"_id": 1}}], random_cursor=random_cursor
        ) == [
            {"$match": flt},
            {"$sample": {"size": 7}},
            {"$project": {"_id": 1}},
            {"$sort": {"_id": 1}},
        ]
    assert sample_pipeline({}, 0) == [{"$sample": {"size": 1}}]
