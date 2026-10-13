"""The cross-system consistency check, offline (fake connectors, M2 job/snapshot models)."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dps.connections import Connection
from dps.engine.policy import MethodSpec
from dps.groups import JobGroup
from dps.jobs import Job, JobKind, JobPhase, JobState
from dps.jobs_models import (
    ClassificationSnapshot,
    ColumnClassification,
    ConnectionParams,
    JobProgress,
    Selection,
)
from dps.layers.connectors.base import ColumnInfo, ObjectRef, Row, TableShape
from dps.layers.connectors.consistency import (
    LOOKUP_CHUNK,
    CheckMember,
    ConsistencyCheckRequest,
    ConsistencyError,
    JoinForm,
    attribute_text,
    candidates,
    canonical,
    comparable,
    derive_members,
    detect_form,
    run_consistency_check,
)
from dps.layers.connectors.docpaths import column_values

INT = ColumnInfo(path="$.CUSTOMER_ID", name="CUSTOMER_ID", data_type="NUMBER(12)", kind="int", is_key=True)  # fmt: skip
TXT = ColumnInfo(path="$.customerId", name="customerId", data_type="string", kind="text")
NOW = datetime(2026, 10, 13, tzinfo=UTC)
GROUP = JobGroup(
    id="grp_1",
    tenant_id="t_1",
    name="crm-uat",
    kid="k_1",
    seed_fingerprint="f" * 16,
    created_at=NOW,
)


def test_detect_form() -> None:
    assert detect_form(INT, [3104482, 73104482]).kind == "numeric"
    f = detect_form(TXT, ["CUST-03104482", "CUST-73104482"])
    assert (f.kind, f.prefix, f.width) == ("prefixed_digits", "CUST-", 8)
    assert detect_form(TXT, ["CUST-1", "CUST-22"]).width is None
    assert detect_form(TXT, ["A-1", "B-2"]).kind == "exact"
    assert detect_form(TXT, ["00123", "04567"]) == JoinForm("prefixed_digits", "", 5)
    assert detect_form(TXT, [None]).kind == "unknown"


def test_leading_zeros_join_numbers_and_prefixed_digits() -> None:
    num, pre = JoinForm("numeric"), JoinForm("prefixed_digits", "CUST-", 8)
    assert canonical(3104482, num) == canonical("CUST-03104482", pre) == 3104482
    assert candidates(3104482, pre, TXT) == ["CUST-03104482"]
    assert candidates(123456789, pre, TXT) == []  # wider than the member's ids
    assert comparable(num, pre) and not comparable(num, JoinForm("exact"))
    assert canonical("OTHER-03104482", pre) is None
    char = ColumnInfo(path="$.REF", name="REF", data_type="CHAR(6)", kind="char", length=6)
    assert candidates("K17", JoinForm("exact"), char) == ["K17   "]


def test_attribute_text() -> None:
    assert attribute_text("+91 07311 0448", "phone") == attribute_text("9107311 0448", "phone")
    assert attribute_text("A.B@Example.COM", "email") == "a.b@example.com"
    assert attribute_text("1987-03-14T00:00:00", "date_shift") == "1987-03-14"
    assert attribute_text("ABCDE1234F  ", "fpe_text") == "ABCDE1234F"
    assert attribute_text(None, "email") is None


# -- fakes ----------------------------------------------------------------------------------------


class FakeConnector:
    """A target holding ``rows``; ``lookup_values`` filters on the join path."""

    def __init__(self, engine: str, shape: TableShape, rows: list[Row]) -> None:
        self.engine = engine
        self.shape = shape
        self.rows = rows
        self.closed = False
        self.max_lookup = 0
        self.fail_lookup = False

    async def describe(self, obj: ObjectRef) -> TableShape:
        return self.shape

    async def sample(
        self, shape: TableShape, selection: Selection, params: dict[str, Any], n: int
    ) -> list[Row]:
        return self.rows[:n]

    async def lookup_values(
        self, shape: TableShape, join: ColumnInfo, values: list[Any], attributes: list[ColumnInfo]
    ) -> list[Row]:
        self.max_lookup = max(self.max_lookup, len(values))
        if self.fail_lookup:
            raise RuntimeError("boom")
        wanted = set(values)
        return [r for r in self.rows if any(v in wanted for v in column_values([r], join))]

    async def close(self) -> None:
        self.closed = True


def col(path: str, name: str, kind: str = "text", *, key: bool = False) -> ColumnInfo:
    return ColumnInfo(path=path, name=name, data_type=kind, kind=kind, is_key=key)  # type: ignore[arg-type]


def make_snapshot(
    sid: str, shape: TableShape, mapping: dict[str, tuple[str, str, dict[str, Any]]]
) -> ClassificationSnapshot:
    columns = []
    for c in shape.columns:
        entity, method, params = mapping.get(c.path, (None, "keep", {}))
        spec = MethodSpec(method=method, params=params)
        columns.append(
            ColumnClassification(
                path=c.path, name=c.name, data_type=c.data_type, kind=c.kind, is_key=c.is_key,
                entity_type=entity, confidence=0.9, sensitivity=None, detectors=[],
                value_hit_rate=1.0, null_rate=0.0, distinct_ratio=1.0, shapes=[],
                text_column=False, anchor=False, suggested=spec, method=spec,
            )
        )  # fmt: skip
    return ClassificationSnapshot(
        id=sid, tenant_id="t_1", job_id=None, connection_id="c", engine="x",
        object_ref=shape.object.fqn(), status="accepted", policy_name="p", policy_hash="h",
        shape=shape, columns=columns, sample_rows=10, overrides=[], content_hash="c",
        created_at=NOW, accepted_at=NOW, accepted_by="k",
    )  # fmt: skip


def make_job(
    jid: str,
    conn: str,
    obj: ObjectRef,
    snapshot_id: str,
    *,
    kid: str = "k_1",
    state: str = "succeeded",
    write_mode: str = "table",
    rows_written: int = 100,
    group_id: str = "grp_1",
    field_scoped: bool = False,
    dry_run: bool = False,
) -> Job:
    spec = {
        "mode": "obfuscate",
        "source": {"connection_id": "src", "object": {"owner": "S", "name": "SRC"}},
        "target": {
            "write_mode": write_mode,
            "connection_id": conn,
            "object": obj.model_dump(mode="json"),
        },
        "consistency": {"scope": "key", "field_scoped": field_scoped},
        "options": {"dry_run": dry_run},
    }
    return Job(
        id=jid, tenant_id="t_1", kind=JobKind.run, state=JobState(state),
        phase=JobPhase.finalize, group_id=group_id, snapshot_id=snapshot_id, spec=spec, kid=kid,
        progress=JobProgress(rows_written=rows_written, outcome="dry_run" if dry_run else ""),
        created_at=NOW, updated_at=NOW,
    )  # fmt: skip


ORA_OBJ = ObjectRef(owner="CRM_MASKED", name="CUSTOMERS")
MDB_OBJ = ObjectRef(name="profiles")
PG_OBJ = ObjectRef(owner="bank_uat", name="payments")
ORA_SHAPE = TableShape(
    object=ORA_OBJ,
    columns=[
        col("$.CUSTOMER_ID", "CUSTOMER_ID", "int", key=True),
        col("$.REFERRER_ID", "REFERRER_ID", "int"),
        col("$.FULL_NAME", "FULL_NAME"),
        col("$.DOB", "DOB", "date"),
    ],
    key=["CUSTOMER_ID"],
    key_source="primary_key",
)
MDB_SHAPE = TableShape(
    object=MDB_OBJ,
    columns=[
        col("$._id", "_id", "object_id", key=True),
        col("$.customerId", "customerId"),
        col("$.profile.fullName", "profile"),
        col("$.profile.dob", "profile", "date"),
    ],
    key=["_id"],
    key_source="primary_key",
)
PG_SHAPE = TableShape(
    object=PG_OBJ,
    columns=[
        col("$.payment_id", "payment_id", "int", key=True),
        col("$.customer_id", "customer_id", "int"),
        col("$.payer_name", "payer_name"),
        col("$.payer_dob", "payer_dob", "date"),
    ],
    key=["payment_id"],
    key_source="primary_key",
)
JOIN = ("CUSTOMER_ID", "fpe_digits", {})
NAME = ("PERSON_NAME", "fpe_text", {})
DOB = ("DATE_OF_BIRTH", "date_shift", {"window_days": 365})


class World:
    """Three members over ``n`` customers whose masked id is ``tokens[k]`` (some with a leading
    zero in 8 digits) and whose masked name is ``names[k]``."""

    def __init__(self, n: int = 500, *, prefix: str = "N-") -> None:
        rng = random.Random(7)
        self.tokens = rng.sample(range(1_000_000, 100_000_000), n)
        self.names = [f"{prefix}{k:05d}" for k in range(n)]
        self.dobs = [f"1980-01-{1 + k % 28:02d}" for k in range(n)]
        self.mapping = {
            "ora": {
                "$.CUSTOMER_ID": JOIN,
                "$.REFERRER_ID": JOIN,
                "$.FULL_NAME": NAME,
                "$.DOB": DOB,
            },
            "mdb": {"$.customerId": JOIN, "$.profile.fullName": NAME, "$.profile.dob": DOB},
            "pg": {"$.customer_id": JOIN, "$.payer_name": NAME, "$.payer_dob": DOB},
        }
        self.mongo_tokens = list(self.tokens)
        self.pg_keep = list(range(n))
        self.mongo_strings: list[str] | None = None
        self.mongo_dobs = list(self.dobs)

    def connectors(self) -> dict[str, FakeConnector]:
        ora = [
            {"CUSTOMER_ID": t, "REFERRER_ID": None, "FULL_NAME": nm, "DOB": d}
            for t, nm, d in zip(self.tokens, self.names, self.dobs, strict=True)
        ]
        mdb = [
            {
                "_id": k,
                "customerId": (self.mongo_strings[k] if self.mongo_strings else f"CUST-{t:08d}"),
                "profile": {"fullName": self.names[k], "dob": self.mongo_dobs[k]},
            }
            for k, t in enumerate(self.mongo_tokens)
        ]
        pg = [
            {
                "payment_id": 3 * k + r,
                "customer_id": self.tokens[k],
                "payer_name": self.names[k],
                "payer_dob": self.dobs[k],
            }
            for k in self.pg_keep
            for r in range(3)
        ]
        return {
            "c_ora": FakeConnector("oracle", ORA_SHAPE, ora),
            "c_mdb": FakeConnector("mongo", MDB_SHAPE, mdb),
            "c_pg": FakeConnector("postgres", PG_SHAPE, pg),
        }

    def snapshots(self) -> dict[str, ClassificationSnapshot]:
        return {
            "s_ora": make_snapshot("s_ora", ORA_SHAPE, self.mapping["ora"]),
            "s_mdb": make_snapshot("s_mdb", MDB_SHAPE, self.mapping["mdb"]),
            "s_pg": make_snapshot("s_pg", PG_SHAPE, self.mapping["pg"]),
        }

    def jobs(self, **overrides: dict[str, Any]) -> list[Job]:
        return [
            make_job("j_ora", "c_ora", ORA_OBJ, "s_ora", **overrides.get("j_ora", {})),
            make_job("j_mdb", "c_mdb", MDB_OBJ, "s_mdb", **overrides.get("j_mdb", {})),
            make_job("j_pg", "c_pg", PG_OBJ, "s_pg", **overrides.get("j_pg", {})),
        ]

    async def run(
        self,
        request: ConsistencyCheckRequest | None = None,
        *,
        jobs: list[Job] | None = None,
        fakes: dict[str, FakeConnector] | None = None,
    ) -> tuple[Any, dict[str, FakeConnector]]:
        fakes = fakes or self.connectors()

        async def opener(conn: Any, settings: Any, **kw: Any) -> FakeConnector:
            return fakes[conn]

        result = await run_consistency_check(
            group=GROUP,
            jobs=jobs or self.jobs(),
            snapshots=self.snapshots(),
            connections={k: k for k in fakes},  # type: ignore[misc]
            request=request or ConsistencyCheckRequest(),
            settings=None,  # type: ignore[arg-type]
            kek=None,
            opener=opener,
        )
        return result, fakes


# -- behaviour ------------------------------------------------------------------------------------


def test_derive_members_prefers_customer_id_key_columns() -> None:
    w = World(10)
    members = derive_members(w.jobs(), w.snapshots())
    assert [m.path for m in members] == ["$.CUSTOMER_ID", "$.customerId", "$.customer_id"]
    w.mapping = {"ora": {"$.FULL_NAME": NAME}, "mdb": {"$.profile.dob": DOB}, "pg": {}}
    with pytest.raises(ConsistencyError) as exc:
        derive_members(w.jobs(), w.snapshots())
    assert (exc.value.status, exc.value.code) == (422, "no_shared_entity")


async def test_three_way_check_passes() -> None:
    w = World(500)
    result, fakes = await w.run()
    assert result.passed and result.methods_agree and result.sampled == 500
    assert [(p.left, p.right) for p in result.pairs] == [(0, 1), (0, 2), (1, 2)]
    assert all(p.matched == p.checked == 500 and p.coverage == 1.0 for p in result.pairs)
    assert result.all_members_matched == 500 and result.consistent_pairs == result.total_pairs == 3
    forms = [(m.engine, m.form, m.width, m.prefix_len) for m in result.members]
    assert forms == [
        ("oracle", "numeric", None, None),
        ("mongo", "prefixed_digits", 8, 5),
        ("postgres", "numeric", None, None),
    ]
    names = next(a for a in result.attributes if a.entity_type == "PERSON_NAME")
    assert names.counts_toward_pass and names.compared == names.agreed == 500
    assert names.ambiguous == 0 and names.members == [0, 1, 2]
    assert all(f.closed for f in fakes.values())


async def test_token_drift_fails_the_pair() -> None:
    w = World(500)
    for k in range(0, 500, 10):  # 10 % of the Mongo member's tokens drifted
        w.mongo_tokens[k] = w.tokens[k] + 1 if w.tokens[k] + 1 not in w.tokens else w.tokens[k] - 1
    result, _ = await w.run()
    cov = {(p.left, p.right): (p.coverage, p.consistent) for p in result.pairs}
    assert cov[(0, 1)] == (0.9, False) and cov[(1, 2)] == (0.9, False)
    assert cov[(0, 2)] == (1.0, True)
    assert result.passed is False


async def test_min_coverage_allows_partial_tables() -> None:
    w = World(500)
    w.pg_keep = [k for k in range(500) if k % 5]  # the PG member lacks 20 % of the customers
    strict, _ = await w.run()
    assert strict.passed is False
    lenient, _ = await w.run(ConsistencyCheckRequest(min_coverage=0.75))
    assert lenient.passed
    assert {(p.left, p.right): p.coverage for p in lenient.pairs}[(0, 2)] == 0.8


async def test_method_mismatch() -> None:
    w = World(100)
    w.mapping["mdb"]["$.customerId"] = ("CUSTOMER_ID", "det_token", {})
    result, _ = await w.run()
    assert result.methods_agree is False and result.passed is False
    assert any(p.code == "method_mismatch" for p in result.problems)
    assert not any(p.consistent for p in result.pairs)


async def test_join_form_mismatch() -> None:
    w = World(100)
    w.mongo_strings = [f"tok_{k}x" for k in range(100)]
    result, _ = await w.run()
    assert [(p.code, p.member) for p in result.problems] == [("join_form_mismatch", 1)]
    by_pair = {(p.left, p.right): p.consistent for p in result.pairs}
    assert by_pair == {(0, 1): False, (0, 2): True, (1, 2): False}
    assert result.members[1].form == "exact" and result.passed is False


async def test_anchor_dependent_attributes_are_informational() -> None:
    w = World(200)
    w.mongo_dobs = [f"1990-02-{1 + k % 28:02d}" for k in range(200)]  # disagrees everywhere
    result, _ = await w.run()
    dob = next(a for a in result.attributes if a.entity_type == "DATE_OF_BIRTH")
    assert dob.counts_toward_pass is False and dob.consistent is False
    assert result.passed


async def test_member_validation() -> None:
    w = World(20)

    async def code(jobs: list[Job], pairs: list[CheckMember] | None = None) -> tuple[int, str]:
        with pytest.raises(ConsistencyError) as exc:
            await w.run(ConsistencyCheckRequest(pairs=pairs), jobs=jobs)
        return exc.value.status, exc.value.code

    two = [CheckMember(job_id="j_ora", path="$.CUSTOMER_ID"), CheckMember(job_id="j_mdb", path="$.customerId")]  # fmt: skip
    assert await code(w.jobs(j_mdb={"group_id": "grp_other"}), two) == (422, "member_not_in_group")
    assert await code(w.jobs(j_mdb={"write_mode": "files"}), two) == (422, "member_unsupported")
    assert await code(w.jobs(j_mdb={"dry_run": True}), two) == (409, "member_not_ready")
    assert await code(w.jobs(j_mdb={"state": "failed"}), two) == (409, "member_not_ready")
    absent = [two[0], CheckMember(job_id="j_mdb", path="$.nowhere")]
    assert await code(w.jobs(), absent) == (422, "path_unknown")


async def test_field_scoped_and_kid_problems_block() -> None:
    w = World(50)
    jobs = w.jobs(j_mdb={"field_scoped": True}, j_pg={"kid": "k_other"})
    result, _ = await w.run(jobs=jobs)
    codes = {(p.code, p.member) for p in result.problems}
    assert codes == {("field_scoped", 1), ("kid_mismatch", 2)}
    assert all(p.consistent for p in result.pairs) and result.passed is False


async def test_lookups_are_chunked_and_connectors_closed() -> None:
    w = World(2000)
    result, fakes = await w.run(ConsistencyCheckRequest(sample=2000))
    assert result.sampled == 2000 and result.passed
    assert all(0 < f.max_lookup <= LOOKUP_CHUNK for f in fakes.values())
    assert all(f.closed for f in fakes.values())
    failing = w.connectors()
    failing["c_pg"].fail_lookup = True
    with pytest.raises(RuntimeError):
        await w.run(fakes=failing)
    assert all(f.closed for f in failing.values())


async def test_result_is_value_free() -> None:
    w = World(100, prefix="SENTINEL-")
    result, _ = await w.run()
    text = json.dumps(result.model_dump(mode="json"))
    assert "SENTINEL" not in text and "CUST-" not in text
    assert not any(str(t) in text for t in w.tokens)
    assert result.members[1].prefix_len == 5


async def test_a_counted_attribute_that_disagrees_fails_the_check() -> None:
    """A value-deterministic attribute (``fpe_text`` names) must agree on every joined row."""
    w = World(100)
    fakes = w.connectors()
    for k, doc in enumerate(fakes["c_mdb"].rows):
        if k % 4 == 0:
            doc["profile"]["fullName"] = f"other-{k}"
    result, _ = await w.run(fakes=fakes)
    names = next(a for a in result.attributes if a.entity_type == "PERSON_NAME")
    assert names.counts_toward_pass and names.compared == 100 and names.agreed == 75
    assert names.consistent is False and result.passed is False
    assert all(p.consistent for p in result.pairs)  # the join itself still lines up


async def test_a_repeated_member_is_refused() -> None:
    """The same ``(job_id, path)`` twice would join a target with itself: 1/1 pairs at coverage
    1.0 and ``passed`` for systems that do not join (M3 G4 review B2). Refused before any
    connector opens; one job with two different paths (a self-join) stays allowed."""
    w = World(100)
    for k in range(100):  # the Mongo member drifted on every row: the real check fails
        w.mongo_tokens[k] = w.tokens[k] + 1 if w.tokens[k] + 1 not in w.tokens else w.tokens[k] - 1
    derived, _ = await w.run()
    assert derived.passed is False
    mdb = CheckMember(job_id="j_mdb", path="$.customerId")
    ora = CheckMember(job_id="j_ora", path="$.CUSTOMER_ID")
    fakes = w.connectors()

    for pairs in ([mdb, mdb], [ora, mdb, ora.model_copy()]):
        with pytest.raises(ConsistencyError) as exc:
            await w.run(ConsistencyCheckRequest(pairs=pairs), fakes=fakes)
        assert (exc.value.status, exc.value.code) == (422, "member_duplicate")
    assert not any(f.closed for f in fakes.values())  # no connector was opened (or closed)
    self_join = [ora, CheckMember(job_id="j_ora", path="$.REFERRER_ID")]
    result, _ = await w.run(ConsistencyCheckRequest(pairs=self_join))
    assert [m.path for m in result.members] == ["$.CUSTOMER_ID", "$.REFERRER_ID"]


def _rerun(jid: str, conn: str, obj: ObjectRef, sid: str, minutes: int = 5) -> Job:
    """A later run of the group writing the same target (a refresh: the same spec again)."""
    job = make_job(jid, conn, obj, sid)
    later = NOW + timedelta(minutes=minutes)
    return job.model_copy(update={"created_at": later, "updated_at": later})


def _connection(cid: str, engine: str, **params: Any) -> Connection:
    return Connection(
        id=cid, tenant_id="t_1", name=cid.replace("_", "-"), engine=engine,  # type: ignore[arg-type]
        params=ConnectionParams(**params), secret_ref=None, has_secret=True,
        secret_expires_at=None, roles=["source", "target"], tags=[], capabilities={},
        last_test=None, created_by=None, created_at=NOW, updated_at=NOW,
    )  # fmt: skip


async def test_two_jobs_writing_one_target_are_one_member() -> None:
    """A member is a target **column**, not a job (M3 G4 review r2, B2-r2): a re-run of the group
    that writes the same target would join that target with itself and pass 1/1 at coverage 1.0
    while the real cross-system member fails. Explicit pairs are refused before any connector
    opens; the derived check keeps the newest job per target."""
    w = World(100)
    for k in range(100):  # the Mongo member drifted on every row: the real check fails
        w.mongo_tokens[k] = w.tokens[k] + 1 if w.tokens[k] + 1 not in w.tokens else w.tokens[k] - 1
    jobs = [*w.jobs(), _rerun("j_mdb_rerun", "c_mdb", MDB_OBJ, "s_mdb")]
    mdb = CheckMember(job_id="j_mdb", path="$.customerId")
    rerun = CheckMember(job_id="j_mdb_rerun", path="$.customerId")
    ora = CheckMember(job_id="j_ora", path="$.CUSTOMER_ID")
    fakes = w.connectors()
    for pairs in ([mdb, rerun], [ora, mdb, rerun], [rerun, ora, mdb]):
        with pytest.raises(ConsistencyError) as exc:
            await w.run(ConsistencyCheckRequest(pairs=pairs), jobs=jobs, fakes=fakes)
        assert (exc.value.status, exc.value.code) == (422, "member_duplicate")
    assert not any(f.closed for f in fakes.values())  # no connector was opened (or closed)

    # derived: one member per target, the newest run (in list order); Mongo still fails
    members = derive_members(jobs, w.snapshots())
    assert [m.job_id for m in members] == ["j_ora", "j_pg", "j_mdb_rerun"]
    derived, _ = await w.run(jobs=jobs)
    assert [m.job_id for m in derived.members] == ["j_ora", "j_pg", "j_mdb_rerun"]
    assert derived.passed is False and derived.total_pairs == 3
    # a group whose only runs are refreshes of one target has one system: nothing to join
    only_mongo = [j for j in jobs if j.id in {"j_mdb", "j_mdb_rerun"}]
    with pytest.raises(ConsistencyError) as exc:
        derive_members(only_mongo, w.snapshots())
    assert (exc.value.status, exc.value.code) == (409, "member_not_ready")
    with pytest.raises(ConsistencyError) as exc:
        await w.run(jobs=only_mongo)
    assert (exc.value.status, exc.value.code) == (409, "member_not_ready")


async def test_two_connection_records_for_one_database_are_one_target() -> None:
    """Members are keyed on the connection's endpoint (engine, host, port, database/service),
    so a second connection record for the same database does not make the target distinct; a
    different database on the same server does."""
    w = World(50)
    snapshots = w.snapshots()
    jobs = [
        make_job("j_mdb", "c_mdb", MDB_OBJ, "s_mdb"),
        make_job("j_mdb_b", "c_mdb_b", MDB_OBJ, "s_mdb"),
        make_job("j_mdb_c", "c_mdb_c", MDB_OBJ, "s_mdb"),
        make_job("j_ora", "c_ora", ORA_OBJ, "s_ora"),
        make_job("j_ora_b", "c_ora_b", ObjectRef(owner="crm_masked", name="customers"), "s_ora"),
    ]
    connections = {
        "c_mdb": _connection("c_mdb", "mongo", host="mongo.uat", port=27017, database="crm_uat"),
        # the same server and database, spelt differently and with the default port implied
        "c_mdb_b": _connection("c_mdb_b", "mongo", host="MONGO.UAT", database="crm_uat"),
        "c_mdb_c": _connection("c_mdb_c", "mongo", host="mongo.uat", database="crm_uat2"),
        "c_ora": _connection("c_ora", "oracle", host="ora.uat", port=1521, service_name="UAT"),
        "c_ora_b": _connection("c_ora_b", "oracle", host="ora.uat", service_name="uat"),
    }
    fakes = w.connectors()
    fakes["c_mdb_b"] = fakes["c_mdb_c"] = fakes["c_mdb"]
    fakes["c_ora_b"] = fakes["c_ora"]
    opened: list[str] = []

    async def opener(conn: Connection, settings: Any, **kw: Any) -> FakeConnector:
        opened.append(conn.id)
        return fakes[conn.id]

    async def check(pairs: list[tuple[str, str]]) -> Any:
        return await run_consistency_check(
            group=GROUP, jobs=jobs, snapshots=snapshots, connections=connections,
            request=ConsistencyCheckRequest(
                pairs=[CheckMember(job_id=j, path=p) for j, p in pairs]
            ),
            settings=None, kek=None, opener=opener,  # type: ignore[arg-type]
        )  # fmt: skip

    for pairs in (
        [("j_mdb", "$.customerId"), ("j_mdb_b", "$.customerId")],
        # Oracle folds unquoted identifiers: crm_masked.customers is CRM_MASKED.CUSTOMERS
        [("j_ora", "$.CUSTOMER_ID"), ("j_ora_b", "$.CUSTOMER_ID")],
    ):
        with pytest.raises(ConsistencyError) as exc:
            await check(pairs)
        assert (exc.value.status, exc.value.code) == (422, "member_duplicate")
    assert opened == []
    result = await check([("j_mdb", "$.customerId"), ("j_mdb_c", "$.customerId")])
    assert [m.job_id for m in result.members] == ["j_mdb", "j_mdb_c"]
    # derived: the two records for crm_uat collapse to the newest; crm_uat2 and Oracle stay
    members = derive_members(jobs, snapshots, connections)
    assert sorted(m.job_id for m in members) == ["j_mdb_b", "j_mdb_c", "j_ora_b"]
