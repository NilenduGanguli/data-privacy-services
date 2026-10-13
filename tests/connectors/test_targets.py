"""Run validation, column plans, PK strategies and created-table types (contract §5.3; offline)."""

from __future__ import annotations

from typing import Any

import pytest

from dps.auth import Principal
from dps.config import Settings
from dps.engine.policy import MethodSpec
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.crypto import generate_key_material
from dps.jobs_models import RunSpec, SpecError
from dps.layers.connectors.base import ColumnInfo, ObjectRef, PkGenerate, PkOffset
from dps.layers.connectors.targets import (
    TYPE_RE,
    PrepareFailure,
    RunPlan,
    apply_pk_strategy,
    build_column_plans,
    check_projected_methods,
    created_column_type,
    is_same_table,
    method_for_column,
    resolve_target,
    validate_run,
    worst_case_length,
)
from dps.secrets import seed_fingerprint
from tests.connectors.factories import accepted_snapshot, make_connection

ADMIN = Principal(key_id="k1", name="a", tenant_ids=["*"], scopes=["*"])
JOBS = Principal(key_id="k2", name="j", tenant_ids=["*"], scopes=["jobs"])
SRC = make_connection("c_src", roles=["source", "target"], tags=["production"])
UAT = make_connection("c_uat", roles=["target"])
S3 = make_connection("c_s3", engine="s3", roles=["target"], params={"bucket": "dps-exports"})
PVC = make_connection("c_pvc", engine="pvc", roles=["target"], params={"base_path": "exports"})
KEK = bytes(32)
UAT_TABLE: dict[str, Any] = {
    "write_mode": "table",
    "connection_id": "c_uat",
    "object": {"owner": "bank_uat", "name": "customers"},
}


def spec(**target: Any) -> RunSpec:
    """A run of ``bank.customers`` on ``c_src``; keyword arguments form the target (plus
    ``mode``, ``selection``, ``consistency``, ``group_id`` and ``options``)."""
    body: dict[str, Any] = {
        "mode": target.pop("mode", "obfuscate"),
        "snapshot_id": "snap_1",
        "source": {
            "connection_id": "c_src",
            "object": {"owner": "bank", "name": "customers"},
            "selection": target.pop("selection", {}),
        },
        "purpose": {"ticket": "CHG-1", "legal_basis": "legitimate interest"},
    }
    for extra in ("consistency", "group_id", "options"):
        if extra in target:
            body[extra] = target.pop(extra)
    body["target"] = target
    return RunSpec.model_validate(body)


CASES = [
    (dict(write_mode="in_place"), JOBS, SRC, None, "confirm_write_required", 422),
    (dict(write_mode="in_place", confirm_write=True), JOBS, SRC, None, "admin_scope_required", 403),
    (
        dict(write_mode="in_place", confirm_write=True, connection_id="c_uat"),
        ADMIN,
        SRC,
        UAT,
        "in_place_invalid",
        422,
    ),
    (
        dict(
            write_mode="in_place",
            confirm_write=True,
            selection={"query": "SELECT * FROM bank.customers"},
        ),
        ADMIN,
        SRC,
        None,
        "in_place_invalid",
        422,
    ),
    (dict(write_mode="append", confirm_write=True), ADMIN, SRC, None, "pk_strategy_required", 422),
    (
        dict(write_mode="append", confirm_write=True, pk_strategy={"kind": "token"}),
        ADMIN,
        SRC,
        None,
        "pk_strategy_invalid",
        422,
    ),
    (
        dict(
            write_mode="append", confirm_write=True, pk_strategy={"kind": "offset", "prefix": "X"}
        ),
        ADMIN,
        SRC,
        None,
        "pk_strategy_invalid",
        422,
    ),
    (dict(write_mode="table"), ADMIN, SRC, None, "target_invalid", 422),
    (
        dict(
            write_mode="table",
            connection_id="c_src",
            object={"owner": "bank", "name": "customers"},
        ),
        ADMIN,
        SRC,
        SRC,
        "target_invalid",
        422,
    ),
    (
        dict(**UAT_TABLE, pk_strategy={"kind": "generate"}),
        ADMIN,
        SRC,
        UAT,
        "target_invalid",
        422,
    ),
    (dict(write_mode="files", connection_id="c_uat"), ADMIN, SRC, UAT, "target_invalid", 422),
    (
        dict(write_mode="table", connection_id="c_s3", object={"owner": "x", "name": "y"}),
        ADMIN,
        SRC,
        S3,
        "target_invalid",
        422,
    ),
    (dict(**UAT_TABLE, mode="synthesize"), ADMIN, SRC, UAT, None, None),
]


@pytest.mark.parametrize("target,principal,src,tgt,code,status", CASES)
def test_validation_table(
    target: dict[str, Any],
    principal: Principal,
    src: Any,
    tgt: Any,
    code: str | None,
    status: int | None,
    settings: Settings,
) -> None:
    snap = accepted_snapshot("c_src", "bank.customers")
    if code is None:
        validate_run(
            spec(**target),
            principal=principal,
            source_conn=src,
            target_conn=tgt,
            snapshot=snap,
            settings=settings,
            kek=KEK,
        )
        return
    with pytest.raises(SpecError) as err:
        validate_run(
            spec(**target),
            principal=principal,
            source_conn=src,
            target_conn=tgt,
            snapshot=snap,
            settings=settings,
            kek=KEK,
        )
    assert (err.value.code, err.value.status) == (code, status)


def _check(run: RunSpec, snap: Any = None, **kw: Any) -> None:
    validate_run(
        run,
        principal=kw.pop("principal", ADMIN),
        source_conn=kw.pop("source_conn", SRC),
        target_conn=kw.pop("target_conn", UAT),
        snapshot=snap or accepted_snapshot("c_src", "bank.customers"),
        settings=kw.pop("settings"),
        kek=kw.pop("kek", KEK),
        group=kw.pop("group", None),
    )


def _code(run: RunSpec, settings: Settings, snap: Any = None, **kw: Any) -> tuple[str, int]:
    with pytest.raises(SpecError) as err:
        _check(run, snap, settings=settings, **kw)
    return err.value.code, err.value.status


def test_kek_snapshot_key_and_text_rules(settings: Settings) -> None:
    snap = accepted_snapshot("c_src", "bank.customers")
    with pytest.raises(SpecError, match="DPS_JOB_KEK") as e1:
        _check(spec(**UAT_TABLE), snap, settings=settings, kek=None)
    assert (e1.value.code, e1.value.status) == ("kek_unconfigured", 503)
    proposed = snap.model_copy(update={"status": "proposed"})
    assert _code(spec(**UAT_TABLE), settings, proposed) == ("snapshot_not_accepted", 409)
    other = snap.model_copy(update={"object_ref": "bank.accounts"})
    assert _code(spec(**UAT_TABLE), settings, other) == ("snapshot_not_accepted", 409)
    with pytest.raises(SpecError) as missing:
        validate_run(
            spec(**UAT_TABLE),
            principal=ADMIN,
            source_conn=SRC,
            target_conn=UAT,
            snapshot=None,
            settings=settings,
            kek=KEK,
        )
    assert missing.value.code == "snapshot_not_accepted"
    hmac_key = accepted_snapshot(
        "c_src", "bank.customers", key_method=MethodSpec(method="hmac_pseudonym")
    )
    assert _code(spec(**UAT_TABLE), settings, hmac_key) == ("key_method_not_injective", 422)
    bad_text = accepted_snapshot(
        "c_src", "bank.customers", notes_method=MethodSpec(method="fpe_text")
    )
    assert _code(spec(**UAT_TABLE), settings, bad_text) == ("text_method_invalid", 422)


def test_engine_roles_and_in_place_key_rules(settings: Settings) -> None:
    # M3 Task 19: every database engine runs; a file store is never a run source
    store = make_connection("c_src", engine="s3", roles=["source"], params={"bucket": "dps-in"})
    assert _code(spec(**UAT_TABLE), settings, source_conn=store) == ("engine_unavailable", 422)
    target_only = make_connection("c_src", roles=["target"])
    assert _code(spec(**UAT_TABLE), settings, source_conn=target_only) == ("connection_role", 422)
    reader = make_connection("c_uat", roles=["source"])
    assert _code(spec(**UAT_TABLE), settings, target_conn=reader) == ("connection_role", 422)
    source_only = make_connection("c_src", roles=["source"])
    in_place = spec(write_mode="in_place", confirm_write=True)
    assert _code(in_place, settings, source_conn=source_only, target_conn=None) == (
        "connection_role",
        422,
    )
    fpe_key = accepted_snapshot(
        "c_src", "bank.customers", key_method=MethodSpec(method="fpe_digits")
    )
    assert _code(in_place, settings, fpe_key, target_conn=None) == ("pk_transform_in_place", 422)
    _check(in_place, settings=settings, target_conn=None)  # key kept: allowed with admin
    staging = make_connection("c_src", roles=["source", "target"])
    _check(in_place, settings=settings, principal=JOBS, source_conn=staging, target_conn=None)


def test_append_rules(settings: Settings) -> None:
    same = dict(write_mode="append", confirm_write=True)
    _check(spec(**same, pk_strategy={"kind": "offset", "offset": 1000}), settings=settings)
    _check(spec(**same, pk_strategy={"kind": "generate"}), settings=settings)
    reversible = accepted_snapshot(
        "c_src", "bank.customers", key_method=MethodSpec(method="fpe_digits")
    )
    other_table = dict(same, object={"owner": "bank", "name": "customers_copy"})
    _check(spec(**other_table, pk_strategy={"kind": "token"}), reversible, settings=settings)
    # same table: a token key could land in a range not read yet
    assert _code(spec(**same, pk_strategy={"kind": "token"}), settings, reversible) == (
        "pk_strategy_invalid",
        422,
    )
    to_s3 = spec(**same, connection_id="c_s3", pk_strategy={"kind": "offset", "offset": 9})
    assert _code(to_s3, settings, target_conn=S3) == ("target_invalid", 422)
    synth_token = spec(**other_table, pk_strategy={"kind": "token"}, mode="synthesize")
    assert _code(synth_token, settings, reversible) == ("pk_strategy_invalid", 422)
    text_key = reversible.model_copy(
        update={
            "shape": reversible.shape.model_copy(
                update={
                    "columns": [
                        c.model_copy(update={"kind": "text", "data_type": "text"})
                        if c.name == "customer_id"
                        else c
                        for c in reversible.shape.columns
                    ]
                }
            )
        }
    )
    offset = spec(**same, pk_strategy={"kind": "offset", "offset": 5})
    assert _code(offset, settings, text_key) == ("pk_strategy_invalid", 422)


def test_files_synthesize_group_and_tabular_rules(settings: Settings) -> None:
    files = dict(write_mode="files", connection_id="c_pvc", files={"format": "csv"})
    _check(spec(**files), settings=settings, target_conn=PVC)
    assert _code(spec(write_mode="files", connection_id="c_pvc"), settings, target_conn=PVC) == (
        "target_invalid",
        422,
    )
    det = accepted_snapshot("c_src", "bank.customers")
    needs_key = det.model_copy(
        update={
            "columns": [
                c.model_copy(update={"synthesize_method": MethodSpec(method="det_token")})
                if c.name == "full_name"
                else c
                for c in det.columns
            ]
        }
    )
    assert _code(spec(**UAT_TABLE, mode="synthesize"), settings, needs_key) == (
        "method_needs_key",
        422,
    )
    km = generate_key_material("key")
    seed = generate_synthesis_seed()
    group = (km.kid, seed_fingerprint(seed.seed))
    with_key = {"key_material": km.model_dump(mode="json")}
    _check(
        spec(**UAT_TABLE, group_id="grp_1", consistency=with_key), settings=settings, group=group
    )
    other_key = {"key_material": generate_key_material("key").model_dump(mode="json")}
    for run in (
        spec(**UAT_TABLE, group_id="grp_1"),
        spec(**UAT_TABLE, group_id="grp_1", consistency=other_key),
        spec(**UAT_TABLE, group_id="grp_1", consistency={**with_key, "field_scoped": True}),
        spec(**UAT_TABLE, group_id="grp_1", mode="synthesize"),
    ):
        assert _code(run, settings, group=group) == ("group_key_mismatch", 409)
    synth_seed = {"synthesis_seed": seed.model_dump(mode="json")}
    _check(
        spec(**UAT_TABLE, group_id="grp_1", mode="synthesize", consistency=synth_seed),
        settings=settings,
        group=group,
    )
    assert _code(spec(**UAT_TABLE, group_id="grp_1", consistency=with_key), settings) == (
        "group_not_found",
        404,
    )
    tabular = spec(**UAT_TABLE, mode="synthesize", options={"tabular": "copula"})
    assert _code(tabular, settings) == ("tabular_invalid", 422)


def test_column_plans_per_write_mode() -> None:
    snap = accepted_snapshot("c_src", "bank.customers", key_method=MethodSpec(method="fpe_digits"))
    every = [c.name for c in snap.shape.columns]
    table = {
        p.name
        for p in build_column_plans(
            snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=every
        )
    }
    assert "customer_id" in table and "country_code" not in table and "notes" in table
    gen = {
        p.name
        for p in build_column_plans(
            snap,
            mode="obfuscate",
            write_mode="append",
            pk_strategy={"kind": "generate"},
            projection=every,
        )
    }
    assert "customer_id" not in gen
    narrowed = {
        p.name
        for p in build_column_plans(
            snap,
            mode="obfuscate",
            write_mode="table",
            pk_strategy=None,
            projection=["customer_id", "email"],
        )
    }
    assert narrowed <= {"customer_id", "email"}
    plans = build_column_plans(
        snap, mode="synthesize", write_mode="table", pk_strategy=None, projection=every
    )
    by_name = {p.name: p for p in plans}
    assert "customer_id" not in by_name and by_name["full_name"].method.method == "surrogate"
    assert not any(p.reversible for p in plans)
    key = next(c for c in snap.columns if c.name == "customer_id")
    assert method_for_column(key, "obfuscate").method == "fpe_digits"
    assert method_for_column(key, "synthesize").method == "keep"
    blank = key.model_copy(update={"synthesize_method": None, "synthesize_suggested": None})
    assert method_for_column(blank, "synthesize").method == "surrogate"
    fpe = next(
        p
        for p in build_column_plans(
            snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=every
        )
        if p.name == "customer_id"
    )
    assert fpe.method.params == {"leading_zero": "preserve"} and fpe.reversible


def test_created_types_and_worst_case() -> None:
    name = ColumnInfo(
        path="$.full_name",
        name="full_name",
        data_type="character varying(120)",
        kind="text",
        length=120,
    )
    dob = ColumnInfo(path="$.dob", name="dob", data_type="date", kind="date")
    snap = accepted_snapshot("c_src", "bank.customers")
    plans = {
        p.name: p
        for p in build_column_plans(
            snap,
            mode="obfuscate",
            write_mode="table",
            pk_strategy=None,
            projection=[c.name for c in snap.shape.columns],
        )
    }
    assert created_column_type(name, plans["full_name"]) == "text"
    assert created_column_type(name, None) == "character varying(120)"
    assert created_column_type(dob, plans.get("dob")) == "date"
    token = plans["full_name"].model_copy(update={"method": MethodSpec(method="det_token")})
    assert worst_case_length(token, name) == 794
    pseudo = plans["full_name"].model_copy(
        update={"method": MethodSpec(method="hmac_pseudonym", params={"length": 20})}
    )
    assert worst_case_length(pseudo, name) == 20
    assert worst_case_length(plans["full_name"], name) is None
    odd = name.model_copy(update={"data_type": 'text; DROP TABLE "x"'})
    with pytest.raises(PrepareFailure) as err:
        created_column_type(odd, None)
    assert err.value.code == "target_invalid"
    assert TYPE_RE.fullmatch("timestamp with time zone")
    assert TYPE_RE.fullmatch("numeric(14,2)") and TYPE_RE.fullmatch("character varying(20)[]")
    assert not TYPE_RE.fullmatch("text; select 1") and not TYPE_RE.fullmatch("text -- x")


def test_pk_strategies_and_existing_target_checks() -> None:
    rows = [{"customer_id": 10_000_001, "x": 1}]
    shifted = apply_pk_strategy(
        [dict(r) for r in rows], strategy=PkOffset(offset=90_000_000), key=["customer_id"]
    )
    assert shifted[0]["customer_id"] == 100_000_001
    prefixed = apply_pk_strategy(
        [{"doc_ref": "K-1"}], strategy=PkOffset(prefix="S-"), key=["doc_ref"]
    )
    assert prefixed[0]["doc_ref"] == "S-K-1"
    assert apply_pk_strategy([{"id": 1, "v": 2}], strategy=PkGenerate(), key=["id"]) == [{"v": 2}]
    snap = accepted_snapshot("c_src", "bank.customers")
    projection = [c.name for c in snap.shape.columns]
    plans = build_column_plans(
        snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=projection
    )
    missing = snap.shape.model_copy(update={"columns": snap.shape.columns[:-2]})
    with pytest.raises(PrepareFailure) as err:
        resolve_target(
            spec(
                write_mode="table",
                connection_id="c_uat",
                object={"owner": "bank_uat", "name": "c"},
                create_if_missing=False,
            ),
            job_id="j_1",
            source_conn=SRC,
            target_conn=UAT,
            source_shape=snap.shape,
            target_shape=missing,
            plans=plans,
            projection=projection,
        )
    assert err.value.code == "target_invalid"
    resolved, out = resolve_target(
        spec(write_mode="table", connection_id="c_uat", object={"owner": "bank_uat", "name": "c"}),
        job_id="j_1",
        source_conn=SRC,
        target_conn=UAT,
        source_shape=snap.shape,
        target_shape=None,
        plans=plans,
        projection=projection,
    )
    assert resolved.column_types["full_name"] == "text" and resolved.key == ["customer_id"]
    assert resolved.column_types["customer_id"] == "bigint" and resolved.connection_id == "c_uat"
    assert next(p for p in out if p.name == "full_name").length is None  # created as text


def _resolve(run: RunSpec, target_shape: Any, plans: Any, *, tgt: Any = UAT) -> Any:
    snap = accepted_snapshot("c_src", "bank.customers")
    projection = [c.name for c in snap.shape.columns]
    return resolve_target(
        run,
        job_id="j_1",
        source_conn=SRC,
        target_conn=tgt,
        source_shape=snap.shape,
        target_shape=target_shape,
        plans=plans,
        projection=projection,
    )


def test_resolve_existing_targets_per_write_mode() -> None:
    snap = accepted_snapshot("c_src", "bank.customers")
    projection = [c.name for c in snap.shape.columns]

    def plans_for(mode: str, strategy: Any = None) -> Any:
        return build_column_plans(
            snap, mode="obfuscate", write_mode=mode, pk_strategy=strategy, projection=projection
        )

    shape = snap.shape
    in_place = spec(write_mode="in_place", confirm_write=True)
    resolved, out = _resolve(in_place, shape, plans_for("in_place"), tgt=SRC)
    assert resolved.object == shape.object and resolved.same_as_source
    assert set(resolved.write_columns) == {"full_name", "email", "dob", "notes"}
    assert next(p for p in out if p.name == "full_name").length == 120
    # a det_token column cannot fit an existing bounded column
    token = [
        p.model_copy(update={"method": MethodSpec(method="det_token")})
        if p.name == "full_name"
        else p
        for p in plans_for("in_place")
    ]
    with pytest.raises(PrepareFailure) as overflow:
        _resolve(in_place, shape, token, tgt=SRC)
    assert overflow.value.code == "length_overflow"
    gen = spec(write_mode="append", confirm_write=True, pk_strategy={"kind": "generate"})
    resolved, _ = _resolve(gen, shape, plans_for("append", {"kind": "generate"}), tgt=SRC)
    assert resolved.generated_key and "customer_id" not in resolved.write_columns
    assert resolved.same_as_source and resolved.object == shape.object
    with pytest.raises(PrepareFailure) as gone:
        _resolve(gen, None, plans_for("append", {"kind": "generate"}), tgt=SRC)
    assert gone.value.code == "target_invalid"
    wrong_kind = shape.model_copy(
        update={
            "columns": [
                c.model_copy(update={"kind": "int", "data_type": "bigint"})
                if c.name == "country_code"
                else c
                for c in shape.columns
            ]
        }
    )
    table = spec(**UAT_TABLE)
    with pytest.raises(PrepareFailure) as kind:
        _resolve(table, wrong_kind, plans_for("table"))
    assert kind.value.code == "target_invalid"
    no_key = shape.model_copy(update={"key": [], "key_source": "none"})
    with pytest.raises(PrepareFailure) as keyless:
        _resolve(table, no_key, plans_for("table"))
    assert keyless.value.code == "target_invalid"
    files = spec(write_mode="files", connection_id="c_pvc", files={"format": "jsonl"})
    resolved, out = _resolve(files, None, plans_for("files"), tgt=PVC)
    assert resolved.object is None and resolved.files is not None and resolved.engine == "pvc"
    assert resolved.write_columns == projection
    assert resolved.column_types["customer_id"] == "bigint"  # declared types for the manifest
    assert resolved.column_types["full_name"] == "text"  # planned text: unbounded in a file
    assert next(p for p in out if p.name == "full_name").length is None


def test_same_table_and_run_plan_round_trip() -> None:
    assert is_same_table(spec(write_mode="in_place", confirm_write=True))
    assert is_same_table(
        spec(
            write_mode="append",
            connection_id="c_src",
            object={"owner": "bank", "name": "customers"},
        )
    )
    assert not is_same_table(spec(**UAT_TABLE))
    snap = accepted_snapshot("c_src", "bank.customers")
    projection = [c.name for c in snap.shape.columns]
    plans = build_column_plans(
        snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=projection
    )
    resolved, out = _resolve(spec(**UAT_TABLE), None, plans)
    plan = RunPlan(
        columns=out,
        anchor=["customer_id", "CUSTOMER_ID"],
        key=["customer_id"],
        projection=projection,
        target=resolved,
        source_object="bank.customers",
        policy_name=snap.policy_name,
        policy_hash=snap.policy_hash,
        snapshot_id=snap.id,
        snapshot_hash=snap.content_hash,
        locale="en_IN",
        batch_rows=1000,
    )
    again = RunPlan.model_validate(plan.model_dump(mode="json"))
    assert again == plan and again.prepare == {}


def test_an_oracle_source_passes_the_engine_gate(settings: Settings) -> None:
    """M3 wires the Oracle connector into runs (the M2 gate admitted PostgreSQL only)."""
    ora = make_connection(
        "c_src",
        engine="oracle",
        roles=["source", "target"],
        params={"host": "oracle", "port": 1521, "service_name": "FREEPDB1"},
    )
    _check(spec(**UAT_TABLE), settings=settings, source_conn=ora)  # raises nothing


def test_created_column_types_follow_the_target_engine() -> None:
    """M2 validated every created type with PostgreSQL's catalogue pattern, so an Oracle target
    (``NUMBER(12)``) failed ``target_invalid``; the Oracle writer derives its DDL from the closed
    type table and reads only the ``text`` marker (open question 14)."""
    num = ColumnInfo(path="$.CUSTOMER_ID", name="CUSTOMER_ID", data_type="NUMBER(12)", kind="int")
    name = ColumnInfo(
        path="$.FULL_NAME",
        name="FULL_NAME",
        data_type="VARCHAR2(120 CHAR)",
        kind="text",
        length=120,
    )
    zoned = ColumnInfo(
        path="$.AT", name="AT", data_type="TIMESTAMP(6) WITH TIME ZONE", kind="timestamp"
    )
    snap = accepted_snapshot("c_src", "bank.customers")
    plan = next(
        p
        for p in build_column_plans(
            snap,
            mode="obfuscate",
            write_mode="table",
            pk_strategy=None,
            projection=[c.name for c in snap.shape.columns],
        )
        if p.name == "full_name"
    )
    assert created_column_type(num, None, engine="oracle") == "NUMBER(12)"
    assert created_column_type(zoned, None, engine="oracle") == "TIMESTAMP(6) WITH TIME ZONE"
    assert created_column_type(name, plan, engine="oracle") == "text"
    for bad in ('NUMBER(12); DROP TABLE "X"', "number(12)", "VARCHAR2(10) -- x"):
        with pytest.raises(PrepareFailure) as err:
            created_column_type(num.model_copy(update={"data_type": bad}), None, engine="oracle")
        assert err.value.code == "target_invalid"
    with pytest.raises(PrepareFailure):  # PostgreSQL keeps its own (lower-case) pattern
        created_column_type(num, None)


def _oracle(snap: Any) -> Any:
    """``snap`` as discovery stores an Oracle table: upper-case owner, table and column names."""
    shape = snap.shape
    columns = [
        c.model_copy(update={"name": c.name.upper(), "path": f"$.{c.name.upper()}"})
        for c in shape.columns
    ]
    upper_shape = shape.model_copy(
        update={
            "object": ObjectRef(owner="BANK", name="CUSTOMERS"),
            "columns": columns,
            "key": [k.upper() for k in shape.key],
        }
    )
    classified = [
        c.model_copy(update={"name": c.name.upper(), "path": f"$.{c.name.upper()}"})
        for c in snap.columns
    ]
    return snap.model_copy(
        update={
            "engine": "oracle",
            "object_ref": "BANK.CUSTOMERS",
            "shape": upper_shape,
            "columns": classified,
        }
    )


ORA_SRC = make_connection(
    "c_src",
    engine="oracle",
    roles=["source", "target"],
    params={"host": "oracle", "port": 1521, "service_name": "FREEPDB1"},
)


def _ora_spec(selection: dict[str, Any], **target: Any) -> RunSpec:
    run = spec(**(target or UAT_TABLE), selection=selection)
    source = run.source.model_copy(update={"object": ObjectRef(owner="BANK", name="CUSTOMERS")})
    return run.model_copy(update={"source": source})


def test_submit_method_checks_cover_what_the_run_reads(settings: Settings) -> None:
    """M3 G2 B4: the submit-time method checks run over the projection the run itself reads
    (``read_projection``): an Oracle ``columns`` entry in lower case folds to the same column,
    so a kept free-text column cannot pass unchecked."""
    kept_notes = _oracle(
        accepted_snapshot("c_src", "BANK.CUSTOMERS", notes_method=MethodSpec(method="keep"))
    )
    for columns in (["NOTES"], ["notes"], ["Notes"]):
        run = _ora_spec({"columns": columns})
        assert _code(run, settings, kept_notes, source_conn=ORA_SRC) == (
            "text_method_invalid",
            422,
        ), columns
    # a column outside the projection is not checked (it is neither read nor written)
    _check(_ora_spec({"columns": ["email"]}), kept_notes, settings=settings, source_conn=ORA_SRC)
    # synthesize mode: a key-needing method on a lower-case Oracle column is refused too
    det = _oracle(accepted_snapshot("c_src", "BANK.CUSTOMERS"))
    needs_key = det.model_copy(
        update={
            "columns": [
                c.model_copy(update={"synthesize_method": MethodSpec(method="det_token")})
                if c.name == "FULL_NAME"
                else c
                for c in det.columns
            ]
        }
    )
    for columns in (["FULL_NAME"], ["full_name"]):
        run = _ora_spec({"columns": columns}, **UAT_TABLE, mode="synthesize")
        assert _code(run, settings, needs_key, source_conn=ORA_SRC) == (
            "method_needs_key",
            422,
        ), columns
    # a query's own select list is the projection too (PostgreSQL and Oracle alike)
    pg_notes = accepted_snapshot("c_src", "bank.customers", notes_method=MethodSpec(method="keep"))
    with_notes = spec(
        **UAT_TABLE, selection={"query": "SELECT customer_id, notes FROM bank.customers"}
    )
    assert _code(with_notes, settings, pg_notes) == ("text_method_invalid", 422)
    without = spec(
        **UAT_TABLE, selection={"query": "SELECT customer_id, email FROM bank.customers"}
    )
    _check(without, pg_notes, settings=settings)


def test_projected_method_checks_are_shared_with_prepare() -> None:
    """The same rules ``prepare`` re-applies to its final projection (defence in depth)."""
    kept_notes = accepted_snapshot(
        "c_src", "bank.customers", notes_method=MethodSpec(method="keep")
    )
    with pytest.raises(SpecError) as err:
        check_projected_methods(kept_notes, "obfuscate", ["customer_id", "notes"])
    assert err.value.code == "text_method_invalid"
    check_projected_methods(kept_notes, "obfuscate", ["customer_id", "email"])


def test_a_keyless_upsert_is_refused_at_submit(settings: Settings) -> None:
    """M3 G2 B3: an upsert needs a key to be idempotent; a key-less (ROWID-paged) source would
    append on every run. Refused at submit; ``truncate_insert`` stays allowed."""
    snap = _oracle(accepted_snapshot("c_src", "BANK.CUSTOMERS"))
    keyless = snap.model_copy(
        update={"shape": snap.shape.model_copy(update={"key_source": "rowid"})}
    )
    upsert = _ora_spec({}, **UAT_TABLE, table_load="upsert")
    assert _code(upsert, settings, keyless, source_conn=ORA_SRC) == ("no_key", 422)
    _check(_ora_spec({}), keyless, settings=settings, source_conn=ORA_SRC)
    _check(upsert, snap, settings=settings, source_conn=ORA_SRC)  # keyed: upsert allowed


def test_column_plans_carry_the_timestamp_precision() -> None:
    """M3: an Oracle TIMESTAMP(9) output keeps its nine fractional digits (FF9 text), so a
    date shift is written and revealed exactly instead of failing ``incompatible_method``."""
    snap = accepted_snapshot("c_src", "bank.customers")
    every = [c.name for c in snap.shape.columns]
    planned = {
        p.name
        for p in build_column_plans(
            snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=every
        )
    }
    name = next(
        c.name for c in snap.shape.columns if c.name in planned and c.kind in ("date", "timestamp")
    )
    shape = snap.shape.model_copy(
        update={
            "columns": [
                c.model_copy(update={"kind": "timestamp", "ts_precision": 9})
                if c.name == name
                else c
                for c in snap.shape.columns
            ]
        }
    )
    plans = build_column_plans(
        snap.model_copy(update={"shape": shape}),
        mode="obfuscate",
        write_mode="table",
        pk_strategy=None,
        projection=every,
    )
    plan = next(p for p in plans if p.name == name)
    assert plan.ts_precision == 9 and plan.output_column().ts_precision == 9
    assert all(p.ts_precision is None for p in plans if p.name != name)
