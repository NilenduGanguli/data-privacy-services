"""M5 Task 7B: MOSTLY differential privacy and linked (multi-table, sequential) generation.

Offline with an injected trainer that stands in for the SDK; one ``ml`` test runs the real SDK and
skips without the ``synth-ml`` extra.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("scipy")

from dps.engine.synth.linked import (  # noqa: E402
    PARENT_INDEX,
    LinkedLayout,
    LinkedPool,
    LinkedTable,
    SensitiveColumn,
    _sdk_linked_trainer,
    sequence_lengths,
)
from dps.engine.synth.mostly import DpConfig, TabularUnavailable, _dp_configuration  # noqa: E402

SEED = bytes(range(32))
LAYOUT = LinkedLayout(parent_key="CUST_ID", foreign_key="CUST_ID", order_by="TS")
SOURCE_EMAILS = [f"real.person{i}@corp-mail.in" for i in range(40)]


class FakeLinkedTrainer:
    """Stands in for the SDK: records what it was given, returns 2–4 children per parent."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        parent: dict[str, list[Any]],
        child: dict[str, list[Any]],
        layout: LinkedLayout,
        subjects: int,
        minutes: int,
        dp: DpConfig | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        self.calls.append({"parent": parent, "child": child, "dp": dp, "subjects": subjects})
        parents = [
            {"SEGMENT": "AB"[i % 2], "OPENED": f"2021-0{1 + i % 9}-0{1 + i % 9}"}
            for i in range(subjects)
        ]
        children: list[dict[str, Any]] = []
        for i in range(subjects):
            # generated out of order on purpose: the pool sorts each sequence by order_by
            for k in reversed(range(2 + i % 3)):
                children.append(
                    {
                        PARENT_INDEX: i,
                        "AMOUNT": float(10 * i + k) + 0.5,
                        "TS": (datetime(2022, 1, 1) + timedelta(days=k, hours=i)).isoformat(),
                    }
                )
        return parents, children


def _tables(n_parents: int = 40) -> tuple[LinkedTable, LinkedTable]:
    keys = [10_000_001 + i for i in range(n_parents)]
    parent = LinkedTable(
        role="parent",
        columns={
            "CUST_ID": keys,
            "SEGMENT": ["AB"[i % 2] for i in range(n_parents)],
            "OPENED": [date(2020, 1, 1 + i % 28) for i in range(n_parents)],
        },
        kinds={"SEGMENT": "categorical", "OPENED": "date"},
        output=["CUST_ID", "FULL_NAME", "SEGMENT", "OPENED", "NOTES"],
        key="CUST_ID",
        key_kind="int",
        sensitive={"FULL_NAME": SensitiveColumn("PERSON_NAME", "text")},
    )
    child_fk: list[Any] = []
    amounts: list[Any] = []
    stamps: list[Any] = []
    emails: list[Any] = []
    for i, key in enumerate(keys):
        for k in range(1 + i % 5):
            child_fk.append(key)
            amounts.append(float(k))
            stamps.append(datetime(2023, 1, 1) + timedelta(days=k))
            emails.append(SOURCE_EMAILS[i])
    child = LinkedTable(
        role="child",
        columns={
            "TXN_ID": [90_000_000 + j for j in range(len(child_fk))],
            "CUST_ID": child_fk,
            "AMOUNT": amounts,
            "TS": stamps,
            "EMAIL": emails,
        },
        kinds={"AMOUNT": "float", "TS": "timestamp"},
        output=["TXN_ID", "CUST_ID", "AMOUNT", "TS", "EMAIL"],
        key="TXN_ID",
        key_kind="int",
        sensitive={"EMAIL": SensitiveColumn("EMAIL", "text")},
    )
    return parent, child


def _fit(
    trainer: FakeLinkedTrainer, *, subjects: int = 50, dp: DpConfig | None = None
) -> LinkedPool:
    parent, child = _tables()
    return LinkedPool.fit(
        parent,
        child,
        layout=LAYOUT,
        subjects=subjects,
        seed=SEED,
        max_training_minutes=1,
        dp=dp,
        trainer=trainer,
    )


def test_linked_pool_is_deterministic_and_referentially_intact() -> None:
    a, b = _fit(FakeLinkedTrainer()), _fit(FakeLinkedTrainer())
    assert a.parent_rows == b.parent_rows and a.child_rows == b.child_rows
    keys = [r["CUST_ID"] for r in a.parent_rows]
    assert keys == list(range(1, 51))
    assert all(r["CUST_ID"] in set(keys) for r in a.child_rows)
    assert len({r["TXN_ID"] for r in a.child_rows}) == len(a.child_rows)
    assert all(r["NOTES"] is None for r in a.parent_rows)  # neither modelled nor sensitive
    assert isinstance(a.parent_rows[0]["OPENED"], date) and a.parent_rows[0]["SEGMENT"] in "AB"


def test_sequences_keep_their_order() -> None:
    pool = _fit(FakeLinkedTrainer())
    by_parent: dict[int, list[datetime]] = {}
    for r in pool.child_rows:
        by_parent.setdefault(r["CUST_ID"], []).append(r["TS"])
    assert all(stamps == sorted(stamps) for stamps in by_parent.values())
    expected = [2 + i % 3 for i in range(50)]
    assert sequence_lengths(pool.child_rows, "CUST_ID", list(range(1, 51))) == expected
    assert sorted(sequence_lengths(pool.child_rows, "CUST_ID")) == sorted(expected)


def test_no_source_key_or_sensitive_value_reaches_the_trainer_or_the_output() -> None:
    trainer = FakeLinkedTrainer()
    pool = _fit(trainer)
    seen = trainer.calls[0]
    assert "EMAIL" not in seen["child"] and "FULL_NAME" not in seen["parent"]
    assert "TXN_ID" not in seen["child"]  # the child's own key never reaches the trainer
    link_values = seen["parent"]["CUST_ID"] + seen["child"]["CUST_ID"]
    assert max(link_values) <= 40 and min(link_values) >= 1  # re-numbered 1..n
    assert seen["parent"]["CUST_ID"] != sorted(seen["parent"]["CUST_ID"])  # not the real order
    # the child's sequences reach the trainer sorted by (parent, order_by)
    pairs = list(zip(seen["child"]["CUST_ID"], seen["child"]["TS"], strict=True))
    assert pairs == sorted(pairs)
    emails = [r["EMAIL"] for r in pool.child_rows]
    assert all(e and e.split("@")[1].startswith("example") for e in emails), emails[:3]
    assert not set(emails) & set(SOURCE_EMAILS)
    names = {r["FULL_NAME"] for r in pool.parent_rows}
    assert all(isinstance(n, str) and n for n in names)
    output_keys = {r["CUST_ID"] for r in pool.parent_rows} | {r["TXN_ID"] for r in pool.child_rows}
    assert not output_keys & set(_tables()[0].columns["CUST_ID"])
    assert max(int(k) for k in output_keys) < 10_000_001


def test_text_keys_are_generated_with_a_prefix() -> None:
    parent, child = _tables()
    parent.key_kind = child.key_kind = "text"
    pool = LinkedPool.fit(
        parent,
        child,
        layout=LAYOUT,
        subjects=3,
        seed=SEED,
        max_training_minutes=1,
        dp=None,
        trainer=FakeLinkedTrainer(),
    )
    assert [r["CUST_ID"] for r in pool.parent_rows] == ["S00000001", "S00000002", "S00000003"]
    assert pool.child_rows[0]["TXN_ID"] == "C00000001"
    assert {r["CUST_ID"] for r in pool.child_rows} <= {"S00000001", "S00000002", "S00000003"}


def test_dp_configuration_is_passed_and_recorded() -> None:
    trainer = FakeLinkedTrainer()
    dp = DpConfig(max_epsilon=5.0, delta=1e-5)
    pool = _fit(trainer, dp=dp)
    assert trainer.calls[0]["dp"] == dp
    assert _dp_configuration(dp) == {"differential_privacy": {"max_epsilon": 5.0, "delta": 1e-5}}
    assert pool.dp == {"enabled": True, "max_epsilon": 5.0, "delta": 1e-5, "achieved_epsilon": None}
    off = FakeLinkedTrainer()
    assert _fit(off).dp == {"enabled": False} and off.calls[0]["dp"] is None


def test_bad_generator_output_fails_without_values() -> None:
    def orphan(*args: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return [{"SEGMENT": "A", "OPENED": "2021-01-01"}], [{PARENT_INDEX: 5, "AMOUNT": 1.0}]

    parent, child = _tables()
    with pytest.raises(TabularUnavailable, match="without its parent"):
        LinkedPool.fit(
            parent,
            child,
            layout=LAYOUT,
            subjects=1,
            seed=SEED,
            max_training_minutes=1,
            dp=None,
            trainer=orphan,
        )
    with pytest.raises(TabularUnavailable, match="no parent"):
        LinkedPool.fit(
            parent,
            child,
            layout=LAYOUT,
            subjects=1,
            seed=SEED,
            max_training_minutes=1,
            dp=None,
            trainer=lambda *a: ([], []),
        )


def test_round_trip_bytes() -> None:
    pool = _fit(FakeLinkedTrainer(), dp=DpConfig(max_epsilon=2.0, delta=1e-6))
    again = LinkedPool.from_bytes(pool.to_bytes(), SEED)
    assert again.parent_rows == pool.parent_rows and again.child_rows == pool.child_rows
    assert again.layout == LAYOUT and again.dp == pool.dp
    for blob in (b"", b"junk"):
        with pytest.raises(TabularUnavailable):
            LinkedPool.from_bytes(blob, SEED)


def test_sequence_lengths_count_parents_without_children() -> None:
    rows = [{"FK": 1}, {"FK": 1}, {"FK": 3}]
    assert sequence_lengths(rows, "FK", [1, 2, 3]) == [2, 0, 1]
    assert Counter(sequence_lengths(rows, "FK")) == Counter([2, 1])


@pytest.mark.ml
def test_real_sdk_trains_linked_tables() -> None:
    pytest.importorskip("mostlyai")
    pytest.importorskip("pandas")
    parent = {"CUST_ID": list(range(1, 121)), "SEGMENT": ["A", "B", "C"] * 40}
    child: dict[str, list[Any]] = {"CUST_ID": [], "AMOUNT": []}
    for i in range(1, 121):
        for k in range(1 + i % 5):
            child["CUST_ID"].append(i)
            child["AMOUNT"].append(float(k))
    parents, children = _sdk_linked_trainer(
        parent, child, LinkedLayout(parent_key="CUST_ID", foreign_key="CUST_ID"), 20, 1, None
    )
    assert len(parents) == 20 and all(0 <= c[PARENT_INDEX] < 20 for c in children)


# -- run validation (contract §5.3) ------------------------------------------------------------


def _validate(
    run: Any, settings: Any, child: Any, target_conn: Any = None
) -> tuple[str, int] | None:
    from dps.jobs_models import SpecError
    from dps.layers.connectors.targets import validate_run
    from tests.connectors.factories import accepted_snapshot
    from tests.connectors.test_targets import ADMIN, KEK, PVC, SRC

    try:
        validate_run(
            run,
            principal=ADMIN,
            source_conn=SRC,
            target_conn=target_conn or PVC,
            snapshot=accepted_snapshot("c_src", "bank.customers"),
            settings=settings,
            kek=KEK,
            child_snapshot=child,
        )
    except SpecError as exc:
        return exc.code, exc.status
    return None


def test_linked_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    import dps.engine.synth.mostly as mostly_module
    from dps.config import Settings
    from tests.connectors.factories import accepted_snapshot
    from tests.connectors.test_targets import UAT, UAT_TABLE, spec

    monkeypatch.setattr(mostly_module, "_find_spec", lambda name: object())  # "installed"
    settings = Settings(
        _env_file=None, env="local", llm_provider="stub", synth_tabular_engine="copula,mostly"
    )
    child = accepted_snapshot("c_src", "bank.accounts")
    linked = {
        "child": {"owner": "bank", "name": "accounts"},
        "child_snapshot_id": child.id,
        "parent_key": "customer_id",
        "foreign_key": "customer_id",
        "order_by": "dob",
        "subjects": 100,
    }
    files = dict(write_mode="files", connection_id="c_pvc", files={"format": "parquet"})
    ok = {"tabular": "mostly", "linked": linked}
    assert _validate(spec(**files, mode="synthesize", options=ok), settings, child) is None
    with_dp = {**ok, "tabular_dp": True}
    assert _validate(spec(**files, mode="synthesize", options=with_dp), settings, child) is None
    bad = "tabular_invalid", 422
    # a table target, the copula engine, the source object as the child, no or a foreign snapshot
    table = spec(**UAT_TABLE, mode="synthesize", options=ok)
    assert _validate(table, settings, child, target_conn=UAT) == bad
    assert (
        _validate(
            spec(**files, mode="synthesize", options={**ok, "tabular": "copula"}), settings, child
        )
        == bad
    )
    same = {**ok, "linked": {**linked, "child": {"owner": "bank", "name": "customers"}}}
    assert _validate(spec(**files, mode="synthesize", options=same), settings, child) == bad
    assert _validate(spec(**files, mode="synthesize", options=ok), settings, None) == bad
    proposed = child.model_copy(update={"status": "proposed"})
    assert _validate(spec(**files, mode="synthesize", options=ok), settings, proposed) == bad
    other = accepted_snapshot("c_other", "bank.accounts")
    assert _validate(spec(**files, mode="synthesize", options=ok), settings, other) == bad
    wrong_id = child.model_copy(update={"id": "snap_other"})
    assert _validate(spec(**files, mode="synthesize", options=ok), settings, wrong_id) == bad
    # differential privacy is a MOSTLY option only
    dp_copula = {"tabular": "copula", "tabular_dp": True}
    assert (
        _validate(spec(**UAT_TABLE, mode="synthesize", options=dp_copula), settings, None, UAT)
        == bad
    )
    dp_none = {"tabular_dp": True}
    assert (
        _validate(spec(**UAT_TABLE, mode="synthesize", options=dp_none), settings, None, UAT) == bad
    )
    # tabular_dp: false is always allowed (it only switches the deployment default off)
    off = {"tabular": "copula", "tabular_dp": False}
    assert _validate(spec(**UAT_TABLE, mode="synthesize", options=off), settings, None, UAT) is None


# -- prepare (tabular.prepare_linked) ----------------------------------------------------------


def _shapes() -> tuple[Any, Any, Any, Any]:
    from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
    from tests.test_tabular_prepare import _cc, _snapshot

    def col(name: str, kind: str, data_type: str = "", **kw: Any) -> ColumnInfo:
        return ColumnInfo(path=f"$.{name}", name=name, data_type=data_type or kind, kind=kind, **kw)  # type: ignore[arg-type]

    parent = TableShape(
        object=ObjectRef(owner="bank", name="customers"),
        key=["CUST_ID"],
        key_source="primary_key",
        columns=[
            col("CUST_ID", "int", "bigint", is_key=True),
            col("FULL_NAME", "text"),
            col("SEGMENT", "text"),
            col("OPENED", "date"),
            col("NOTES", "text"),
        ],
    )
    child = TableShape(
        object=ObjectRef(owner="bank", name="transactions"),
        key=["TXN_ID"],
        key_source="primary_key",
        columns=[
            col("TXN_ID", "int", "bigint", is_key=True),
            col("CUST_ID", "int", "bigint"),
            col("AMOUNT", "float"),
            col("TS", "timestamp"),
            col("EMAIL", "text"),
        ],
    )
    psnap = _snapshot(
        parent,
        [
            _cc("CUST_ID", "int", "CUSTOMER_ID", 1.0),
            _cc("FULL_NAME", "text", "PERSON_NAME", 0.9),
            _cc("SEGMENT", "text", None, 0.01),
            _cc("OPENED", "date", None, 0.2),
            _cc("NOTES", "text", "FREE_TEXT", 0.9, text=True),
        ],
    )
    csnap = _snapshot(
        child,
        [
            _cc("TXN_ID", "int", None, 1.0),
            _cc("CUST_ID", "int", "CUSTOMER_ID", 0.3),
            _cc("AMOUNT", "float", None, 0.5),
            _cc("TS", "timestamp", None, 0.9),
            _cc("EMAIL", "text", "EMAIL", 0.3),
        ],
    )
    return parent, psnap, child, csnap


def _source_rows(n: int = 300) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parents, children = [], []
    txn = 0
    for i in range(n):
        key = 10_000_001 + i
        parents.append(
            {
                "CUST_ID": key,
                "FULL_NAME": f"Realname Person{i}",
                "SEGMENT": "AB"[i % 2],
                "OPENED": date(2020, 1, 1 + i % 28),
                "NOTES": f"call {SOURCE_EMAILS[i % 40]}",
            }
        )
        for k in range(1 + i % 4):
            txn += 1
            children.append(
                {
                    "TXN_ID": 90_000_000 + txn,
                    "CUST_ID": key,
                    "AMOUNT": float(k) + 0.25,
                    "TS": datetime(2023, 1, 1) + timedelta(days=k),
                    "EMAIL": SOURCE_EMAILS[i % 40],
                }
            )
    # a child of a parent outside the sample is never read into the training data
    children.append(
        {"TXN_ID": 1, "CUST_ID": 99, "AMOUNT": 1.0, "TS": datetime(2023, 1, 1), "EMAIL": "x@y.in"}
    )
    return parents, children


class FakeSource:
    engine = "postgres"

    def __init__(self, parents: list[dict[str, Any]], children: list[dict[str, Any]]) -> None:
        self.parents, self.children = parents, children

    async def sample(self, shape: Any, selection: Any, params: Any, n: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.parents[:n]]

    async def read_partition(
        self, shape: Any, selection: Any, params: Any, key_range: Any, *, after_key: Any, batch: int
    ):  # noqa: ANN201
        for start in range(0, len(self.children), batch):
            yield [dict(r) for r in self.children[start : start + batch]]


async def _prepare_linked(trainer: FakeLinkedTrainer, **kw: Any) -> Any:
    from dps.jobs_models import LinkedSpec
    from dps.layers.connectors.tabular import prepare_linked
    from tests.test_tabular_prepare import _settings

    parent, psnap, child, csnap = kw.pop("shapes", None) or _shapes()
    linked = LinkedSpec.model_validate(
        {
            "child": {"owner": "bank", "name": "transactions"},
            "child_snapshot_id": "snap_c",
            "parent_key": "CUST_ID",
            "foreign_key": "CUST_ID",
            "order_by": kw.pop("order_by", "TS"),
            "subjects": 40,
        }
    )
    parents, children = kw.pop("rows", None) or _source_rows()
    dp = kw.pop("dp", None)
    return await prepare_linked(
        settings=_settings(synth_tabular_engine="copula,mostly", **kw),
        connector=FakeSource(parents, children),  # type: ignore[arg-type]
        parent_shape=parent,
        parent_snapshot=psnap,
        child_shape=child,
        child_snapshot=csnap,
        linked=linked,
        selection=None,
        params={},
        seed=SEED,
        dp=dp,
        locale="en_IN",
        trainer=trainer,
    )


async def test_prepare_linked_measures_seals_and_records_dp() -> None:
    import base64
    import json

    trainer = FakeLinkedTrainer()
    dp = DpConfig(max_epsilon=5.0, delta=1e-5)
    prepared = await _prepare_linked(trainer, dp=dp)
    assert trainer.calls[0]["dp"] == dp and trainer.calls[0]["subjects"] == 40
    m = prepared.metrics
    linked = m["tabular"]["linked"]
    assert linked["parent"] == "bank.customers" and linked["child"] == "bank.transactions"
    assert linked["subjects"] == 40 and linked["child_rows"] == sum(2 + i % 3 for i in range(40))
    assert linked["ordered"] is True and set(linked["sequence_length"]) == {
        "source_mean",
        "synthetic_mean",
        "ks",
    }
    assert m["tabular"]["dp"] == {
        "enabled": True,
        "max_epsilon": 5.0,
        "delta": 1e-5,
        "achieved_epsilon": None,
    }
    assert m["tabular"]["engine"] == "mostly" and m["privacy"]["exact_row_matches"] == 0
    assert set(m["fidelity"]["tables"]) == {"parent", "child"} and 0 <= m["fidelity"]["score"] <= 1
    assert m["utility"]["run"] is False
    # the training data: no sensitive column, no free text, no real key, children of sampled parents
    seen = trainer.calls[0]
    assert set(seen["parent"]) == {"CUST_ID", "SEGMENT", "OPENED"}
    assert set(seen["child"]) == {"CUST_ID", "AMOUNT", "TS"}
    assert max(seen["child"]["CUST_ID"]) <= 300
    text = json.dumps(m)
    assert not any(s in text for s in [*SOURCE_EMAILS, "Realname", "10000001", "90000001"])
    pool = LinkedPool.from_bytes(base64.b64decode(prepared.model_b64), SEED)
    assert len(pool.parent_rows) == 40 and pool.parent_rows[0]["NOTES"] is None
    assert [c["name"] for c in prepared.parent_columns] == [
        "CUST_ID",
        "FULL_NAME",
        "SEGMENT",
        "OPENED",
        "NOTES",
    ]
    assert {c["name"]: c["kind"] for c in prepared.child_columns}["TS"] == "timestamp"


async def test_prepare_linked_without_dp_records_it_off() -> None:
    trainer = FakeLinkedTrainer()
    prepared = await _prepare_linked(trainer)
    assert trainer.calls[0]["dp"] is None and prepared.metrics["tabular"]["dp"] == {
        "enabled": False
    }


async def test_prepare_linked_refusals() -> None:
    from dps.layers.connectors.tabular import TabularGateError

    parent, psnap, child, csnap = _shapes()
    sensitive_ts = csnap.model_copy(
        update={
            "columns": [
                c.model_copy(update={"entity_type": "DATE"}) if c.name == "TS" else c
                for c in csnap.columns
            ]
        }
    )
    composite = parent.model_copy(update={"key": ["CUST_ID", "SEGMENT"]})
    cases = [
        {"shapes": (parent, psnap, child, sensitive_ts)},  # order_by classified sensitive
        {"shapes": (composite, psnap, child, csnap)},  # composite parent key
        {"order_by": "MISSING"},  # a link column the child does not have
        {"rows": (_source_rows(20)[0], _source_rows(20)[1])},  # too few parents
    ]
    for case in cases:
        with pytest.raises(TabularGateError) as err:
            await _prepare_linked(FakeLinkedTrainer(), **case)
        assert err.value.code == "tabular_invalid", case
        assert "10000001" not in err.value.detail and "@" not in err.value.detail


async def test_prepare_linked_privacy_gate() -> None:
    from dps.layers.connectors.tabular import TabularGateError

    with pytest.raises(TabularGateError) as err:
        await _prepare_linked(
            FakeLinkedTrainer(), privacy_nndr_enforce=True, privacy_nndr_target=50.0
        )
    assert err.value.code == "privacy_gate"


async def test_generated_rows_equal_to_real_rows_are_rejected() -> None:
    """A generated row equal to a sampled real row on every written non-key column is dropped
    (rejection, like the copula path's redraws) and counted; none reaches the pool."""
    import base64

    parent, psnap, child, csnap = _shapes()
    child = child.model_copy(update={"columns": [c for c in child.columns if c.name != "EMAIL"]})
    csnap = csnap.model_copy(update={"columns": [c for c in csnap.columns if c.name != "EMAIL"]})
    parents, children = _source_rows()
    for row in children:
        row.pop("EMAIL", None)

    def copying(*args: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ps = [{"SEGMENT": "A", "OPENED": "2021-01-01"} for _ in range(10)]
        cs = [
            # (0.25, 2023-01-01) is a real (AMOUNT, TS) pair; (7.5, 1999-01-01) is not
            {
                PARENT_INDEX: i,
                "AMOUNT": 0.25 if i % 2 else 7.5,
                "TS": "2023-01-01T00:00:00" if i % 2 else "1999-01-01T00:00:00",
            }
            for i in range(10)
        ]
        return ps, cs

    prepared = await _prepare_linked(
        copying, shapes=(parent, psnap, child, csnap), rows=(parents, children)
    )
    privacy = prepared.metrics["privacy"]
    assert privacy["exact_row_matches"] == 0 and privacy["exact_row_rejections"] == 5
    pool = LinkedPool.from_bytes(base64.b64decode(prepared.model_b64), SEED)
    assert len(pool.child_rows) == 5 and all(r["AMOUNT"] == 7.5 for r in pool.child_rows)
    assert prepared.child_rows == 5 and prepared.metrics["tabular"]["linked"]["child_rows"] == 5

    def all_real(*args: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ps = [{"SEGMENT": "A", "OPENED": "2021-01-01"}]
        return ps, [{PARENT_INDEX: 0, "AMOUNT": 0.25, "TS": "2023-01-01T00:00:00"}]

    from dps.layers.connectors.tabular import TabularGateError

    with pytest.raises(TabularGateError) as err:
        await _prepare_linked(
            all_real, shapes=(parent, psnap, child, csnap), rows=(parents, children)
        )
    assert err.value.code == "leakage"
