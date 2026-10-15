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
    pseudonym_columns,
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
#: S1-M3 r1 (security B1): production-tagged write targets — a table connection and a pvc
PROD = make_connection("c_prod", roles=["target"], tags=["production"])
PROD_PVC = make_connection(
    "c_ppvc", engine="pvc", roles=["target"], tags=["production"], params={"base_path": "exports"}
)
PROD_TABLE: dict[str, Any] = {
    "write_mode": "table",
    "connection_id": "c_prod",
    "object": {"owner": "bank", "name": "customers_copy"},
}
PROD_FILES: dict[str, Any] = {
    "write_mode": "files",
    "connection_id": "c_ppvc",
    "files": {"format": "jsonl"},
}
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
    # S1-M3 r1 (security B1): every write into a production-tagged connection needs admin — a
    # table load truncates or upserts an existing table, files land in production storage
    (dict(**PROD_TABLE), JOBS, SRC, PROD, "admin_scope_required", 403),
    (dict(**PROD_TABLE, table_load="upsert"), JOBS, SRC, PROD, "admin_scope_required", 403),
    (dict(**PROD_TABLE, mode="synthesize"), JOBS, SRC, PROD, "admin_scope_required", 403),
    (dict(**PROD_FILES), JOBS, SRC, PROD_PVC, "admin_scope_required", 403),
    (dict(**PROD_TABLE), ADMIN, SRC, PROD, None, None),
    (dict(**PROD_TABLE, table_load="upsert"), ADMIN, SRC, PROD, None, None),
    (dict(**PROD_FILES), ADMIN, SRC, PROD_PVC, None, None),
    # reading a production source is not writing it: a jobs key may copy it to UAT
    (dict(**UAT_TABLE), JOBS, SRC, UAT, None, None),
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


def test_files_synthesize_group_and_tabular_rules(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    # M5 Task 6: copula synthesis into a table or files target is accepted ...
    copula = {"tabular": "copula"}
    _check(spec(**UAT_TABLE, mode="synthesize", options=copula), settings=settings)
    _check(spec(**files, mode="synthesize", options=copula), settings=settings, target_conn=PVC)
    # ... but not in obfuscate mode, not into a database write, not with the engine switched off
    assert _code(spec(**UAT_TABLE, options=copula), settings) == ("tabular_invalid", 422)
    append = dict(write_mode="append", confirm_write=True, pk_strategy={"kind": "generate"})
    assert _code(spec(**append, mode="synthesize", options=copula), settings) == (
        "tabular_invalid",
        422,
    )
    off = settings.model_copy(update={"synth_tabular_engine": ""})
    assert _code(spec(**UAT_TABLE, mode="synthesize", options=copula), off) == (
        "tabular_invalid",
        422,
    )
    mostly = spec(**UAT_TABLE, mode="synthesize", options={"tabular": "mostly"})
    assert _code(mostly, settings) == ("tabular_invalid", 422)  # not enabled
    with_mostly = settings.model_copy(update={"synth_tabular_engine": "copula,mostly"})
    # Whether the synth-ml extra is installed is pinned both ways, so the result does not
    # depend on which extras this environment has.
    import dps.engine.synth.mostly as mostly_module

    monkeypatch.setattr(mostly_module, "mostly_available", lambda: False)
    assert _code(mostly, with_mostly) == ("tabular_invalid", 422)  # the extra is not installed
    assert _code(mostly, settings) == ("tabular_invalid", 422)  # still not enabled
    monkeypatch.setattr(mostly_module, "mostly_available", lambda: True)
    _check(mostly, settings=with_mostly)  # enabled and installed: accepted
    assert _code(mostly, settings) == ("tabular_invalid", 422)  # installed but not enabled


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


def _with(snap: Any, name: str, **update: Any) -> Any:
    """``snap`` with column ``name`` of its classification changed."""
    return snap.model_copy(
        update={
            "columns": [c.model_copy(update=update) if c.name == name else c for c in snap.columns]
        }
    )


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


def test_in_place_and_same_table_append_refuse_outputs_that_cannot_fit(
    settings: Settings,
) -> None:
    """M2 verify r2 issue 2: a method whose output cannot fit the column it writes back into is
    refused when the run is submitted (and by POST /jobs/run/validate), naming every such column,
    instead of failing the job at prepare with length_overflow."""
    snap = accepted_snapshot("c_src", "bank.customers")
    token = MethodSpec(method="det_token")
    bad = _with(_with(snap, "full_name", method=token), "email", method=token)
    in_place = spec(write_mode="in_place", confirm_write=True)
    with pytest.raises(SpecError) as err:
        _check(in_place, bad, settings=settings, target_conn=None)
    assert (err.value.code, err.value.status) == ("length_overflow", 422)
    detail = err.value.detail
    assert "$.full_name" in detail and "$.email" in detail and "det_token" in detail
    assert "794" in detail and "character varying(120)" in detail and "step 3" in detail
    same = spec(write_mode="append", confirm_write=True, pk_strategy={"kind": "generate"})
    assert _code(same, settings, bad) == ("length_overflow", 422)
    # a copy to a new table or files has unbounded text columns: not refused here
    _check(spec(**UAT_TABLE), bad, settings=settings)
    # discovery flagged a method that falls back to tokens on these values (PAN fpe_text into
    # char(10)): refused while the reviewer keeps the suggestion, allowed once it is changed
    flagged = _with(snap, "full_name", type_note="length_overflow")
    assert _code(in_place, settings, flagged, target_conn=None) == ("length_overflow", 422)
    changed = _with(flagged, "full_name", method=MethodSpec(method="mask_partial"))
    _check(in_place, changed, settings=settings, target_conn=None)
    # an unbounded column takes any token
    unbounded = _with(snap, "notes", method=token)
    _check(in_place, unbounded, settings=settings, target_conn=None)


def test_prepare_names_every_column_that_cannot_fit_an_existing_target() -> None:
    snap = accepted_snapshot("c_src", "bank.customers")
    token = MethodSpec(method="det_token")
    bad = _with(_with(snap, "full_name", method=token), "email", method=token)
    projection = [c.name for c in bad.shape.columns]
    plans = build_column_plans(
        bad, mode="obfuscate", write_mode="table", pk_strategy=None, projection=projection
    )
    with pytest.raises(PrepareFailure) as err:
        resolve_target(
            spec(**UAT_TABLE),
            job_id="j_1",
            source_conn=SRC,
            target_conn=UAT,
            source_shape=bad.shape,
            target_shape=bad.shape,
            plans=plans,
            projection=projection,
        )
    assert err.value.code == "length_overflow"
    assert "$.full_name" in err.value.detail and "$.email" in err.value.detail


# -- D-A: synthetic tables never carry linkable real identifiers ---------------------------------


def test_synthesize_pseudonymises_sensitive_keys_and_kept_identifiers() -> None:
    snap = accepted_snapshot("c_src", "bank.customers")
    projection = [c.name for c in snap.columns]
    got = pseudonym_columns(
        snap, mode="synthesize", write_mode="table", pk_strategy=None, projection=projection
    )
    assert [(p.name, p.entity_type, p.kind, p.digits) for p in got] == [
        ("customer_id", "CUSTOMER_ID", "int", 18)
    ]
    # a kept MEDIUM+ column (an owner override to keep) is pseudonymised, a LOW attribute is not
    keep = MethodSpec(method="keep")
    kept_email = _with(snap, "email", synthesize_method=keep)
    names = [
        p.name
        for p in pseudonym_columns(
            kept_email,
            mode="synthesize",
            write_mode="files",
            pk_strategy=None,
            projection=projection,
        )
    ]
    assert names == ["customer_id", "email"]
    city = _with(snap, "country_code", entity_type="CITY", synthesize_method=keep)
    assert [
        p.name
        for p in pseudonym_columns(
            city, mode="synthesize", write_mode="table", pk_strategy=None, projection=projection
        )
    ] == ["customer_id"]
    # obfuscate runs, keys the target generates and columns outside the projection: none
    assert not pseudonym_columns(
        snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=projection
    )
    assert not pseudonym_columns(
        snap,
        mode="synthesize",
        write_mode="append",
        pk_strategy=PkGenerate(),
        projection=projection,
    )
    assert not pseudonym_columns(
        snap, mode="synthesize", write_mode="table", pk_strategy=None, projection=["email"]
    )


def test_synthesize_refuses_to_copy_or_shift_a_sensitive_key(settings: Settings) -> None:
    in_place = dict(write_mode="in_place", confirm_write=True)
    assert _code(spec(**in_place, mode="synthesize"), settings, target_conn=None) == (
        "synthesize_in_place_sensitive_key",
        422,
    )
    offset = dict(
        write_mode="append", confirm_write=True, pk_strategy={"kind": "offset", "offset": 10**8}
    )
    assert _code(spec(**offset, mode="synthesize"), settings, target_conn=None) == (
        "pk_strategy_invalid",
        422,
    )
    generate = dict(write_mode="append", confirm_write=True, pk_strategy={"kind": "generate"})
    _check(spec(**generate, mode="synthesize"), settings=settings, target_conn=None)
    # a key without an entity type is not an identifier of a person: in place stays possible
    plain = _with(
        accepted_snapshot("c_src", "bank.customers"),
        "customer_id",
        entity_type=None,
        sensitivity=None,
        anchor=False,
    )
    _check(spec(**in_place, mode="synthesize"), plain, settings=settings, target_conn=None)
    # an identifier the pseudonym cannot fit (a smallint key; a float join column)
    snap = accepted_snapshot("c_src", "bank.customers")
    small = _with(snap, "customer_id", data_type="smallint")
    shape = small.shape.model_copy(
        update={
            "columns": [
                c.model_copy(update={"data_type": "smallint"}) if c.name == "customer_id" else c
                for c in small.shape.columns
            ]
        }
    )
    small = small.model_copy(update={"shape": shape})
    assert _code(spec(**UAT_TABLE, mode="synthesize"), settings, small) == (
        "synthesize_pseudonym_unsupported",
        422,
    )
    # D-A: a pseudonym of a key below 10^9 lies in [10^9, 2*10^9) — an integer or a numeric of
    # at least 10 digits holds it, a numeric(9, 0) does not
    for data_type, precision, fits in (
        ("integer", None, True),
        ("numeric(10,0)", 10, True),
        ("numeric(9,0)", 9, False),
    ):
        typed = snap.model_copy(
            update={
                "shape": snap.shape.model_copy(
                    update={
                        "columns": [
                            c.model_copy(
                                update={
                                    "data_type": data_type,
                                    "kind": "decimal" if precision else "int",
                                    "precision": precision,
                                    "scale": 0 if precision else None,
                                }
                            )
                            if c.name == "customer_id"
                            else c
                            for c in snap.shape.columns
                        ]
                    }
                )
            }
        )
        if fits:
            _check(spec(**UAT_TABLE, mode="synthesize"), typed, settings=settings)
            got = pseudonym_columns(
                typed,
                mode="synthesize",
                write_mode="table",
                pk_strategy=None,
                projection=[c.name for c in typed.columns],
            )
            assert got[0].max_magnitude() == (2**31 - 1 if precision is None else 10**10 - 1)
        else:
            assert _code(spec(**UAT_TABLE, mode="synthesize"), settings, typed) == (
                "synthesize_pseudonym_unsupported",
                422,
            )
    dated = _with(snap, "dob", synthesize_method=MethodSpec(method="keep"))
    assert _code(spec(**UAT_TABLE, mode="synthesize"), settings, dated) == (
        "synthesize_pseudonym_unsupported",
        422,
    )


def test_in_place_writes_the_pseudonymised_join_columns() -> None:
    snap = _with(
        accepted_snapshot("c_src", "bank.customers"),
        "customer_id",
        entity_type=None,
        sensitivity=None,
        anchor=False,
    )
    snap = _with(snap, "country_code", entity_type="CUSTOMER_ID", sensitivity="medium")
    run = spec(write_mode="in_place", confirm_write=True, mode="synthesize")
    projection = [c.name for c in snap.columns]
    plans = build_column_plans(
        snap, mode="synthesize", write_mode="in_place", pk_strategy=None, projection=projection
    )
    pseudonyms = pseudonym_columns(
        snap, mode="synthesize", write_mode="in_place", pk_strategy=None, projection=projection
    )
    assert [p.name for p in pseudonyms] == ["country_code"]
    resolved, _ = resolve_target(
        run,
        job_id="job_1",
        source_conn=SRC,
        target_conn=None,
        source_shape=snap.shape,
        target_shape=snap.shape,
        plans=plans,
        projection=projection,
        pseudonyms=[p.name for p in pseudonyms],
    )
    assert "country_code" in resolved.write_columns


def _referencing(snap: Any, name: str, references: str) -> Any:
    """``snap`` whose shape declares ``name`` a foreign key to ``references`` (owner.table.col)."""
    columns = [
        c.model_copy(update={"references": references}) if c.name == name else c
        for c in snap.shape.columns
    ]
    return snap.model_copy(update={"shape": snap.shape.model_copy(update={"columns": columns})})


def test_synthesize_pseudonymises_every_written_key_and_its_foreign_keys() -> None:
    """B2 (G2 review r1): an unclassified key is never written with its real value next to a
    pseudonymised identifier — it gets a pseudonym under a stable per-key label, and a declared
    foreign key to it gets the same label (so joins inside a job group hold)."""
    plain = _with(
        accepted_snapshot("c_src", "bank.customers"),
        "customer_id",
        entity_type=None,
        sensitivity=None,
        anchor=False,
    )
    projection = [c.name for c in plain.columns]
    for write_mode in ("table", "files"):
        got = pseudonym_columns(
            plain, mode="synthesize", write_mode=write_mode, pk_strategy=None, projection=projection
        )
        assert [(p.name, p.entity_type, p.label, p.kind, p.digits) for p in got] == [
            ("customer_id", "KEY", "KEY|bank.customers.customer_id", "int", 18)
        ]
    # a key the target generates is not written; in place the key is the row it rewrites
    assert not pseudonym_columns(
        plain,
        mode="synthesize",
        write_mode="append",
        pk_strategy=PkGenerate(),
        projection=projection,
    )
    assert not pseudonym_columns(
        plain, mode="synthesize", write_mode="in_place", pk_strategy=None, projection=projection
    )
    # a declared foreign key (unclassified) is labelled by the key it references
    fk = _referencing(plain, "country_code", "bank.countries.code")
    got = pseudonym_columns(
        fk, mode="synthesize", write_mode="table", pk_strategy=None, projection=projection
    )
    assert [(p.name, p.label) for p in got] == [
        ("customer_id", "KEY|bank.customers.customer_id"),
        ("country_code", "KEY|bank.countries.code"),
    ]
    # the live shape at prepare is what names the foreign keys
    live = _referencing(plain, "country_code", "bank.countries.code").shape
    got = pseudonym_columns(
        plain,
        mode="synthesize",
        write_mode="table",
        pk_strategy=None,
        projection=projection,
        shape=live,
    )
    assert [p.name for p in got] == ["customer_id", "country_code"]
    # in place a foreign key keeps pointing at the (real) parent rows of the same database
    assert not pseudonym_columns(
        fk, mode="synthesize", write_mode="in_place", pk_strategy=None, projection=projection
    )
    # a classified key keeps its entity label (joins with other columns of that entity)
    snap = accepted_snapshot("c_src", "bank.customers")
    got = pseudonym_columns(
        snap, mode="synthesize", write_mode="table", pk_strategy=None, projection=projection
    )
    assert [(p.entity_type, p.label) for p in got] == [("CUSTOMER_ID", None)]


def test_synthesize_never_shifts_or_copies_any_written_key(settings: Settings) -> None:
    """B2: an offset append writes ``real key + offset`` — linkable whatever the key's class."""
    plain = _with(
        accepted_snapshot("c_src", "bank.customers"),
        "customer_id",
        entity_type=None,
        sensitivity=None,
        anchor=False,
    )
    offset = dict(
        write_mode="append", confirm_write=True, pk_strategy={"kind": "offset", "offset": 10**8}
    )
    assert _code(spec(**offset, mode="synthesize"), settings, plain, target_conn=None) == (
        "pk_strategy_invalid",
        422,
    )
    generate = dict(write_mode="append", confirm_write=True, pk_strategy={"kind": "generate"})
    _check(spec(**generate, mode="synthesize"), plain, settings=settings, target_conn=None)
    # an unclassified key no pseudonym fits is refused for a written key (table), not in place
    small = plain.model_copy(
        update={
            "shape": plain.shape.model_copy(
                update={
                    "columns": [
                        c.model_copy(update={"data_type": "smallint"})
                        if c.name == "customer_id"
                        else c
                        for c in plain.shape.columns
                    ]
                }
            )
        }
    )
    assert _code(spec(**UAT_TABLE, mode="synthesize"), settings, small) == (
        "synthesize_pseudonym_unsupported",
        422,
    )
    in_place = dict(write_mode="in_place", confirm_write=True)
    _check(spec(**in_place, mode="synthesize"), small, settings=settings, target_conn=None)


def test_synthesize_never_keeps_a_classified_attribute() -> None:
    """B1 (G2 review r1): a LOW attribute a policy keeps (``keep_non_sensitive``: city, gender,
    age, postal code, date …) is personal data — a synthesize run plans a surrogate for it (the
    tabular engine draws it instead when it models the column); obfuscate keeps it."""
    keep = MethodSpec(method="keep")
    snap = _with(
        accepted_snapshot("c_src", "bank.customers"),
        "country_code",
        entity_type="CITY",
        sensitivity="low",
        synthesize_method=keep,
    )
    projection = [c.name for c in snap.columns]
    for write_mode in ("table", "files", "in_place"):
        plans = {
            p.name: p.method.method
            for p in build_column_plans(
                snap,
                mode="synthesize",
                write_mode=write_mode,  # type: ignore[arg-type]
                pk_strategy=None,
                projection=projection,
            )
        }
        assert plans["country_code"] == "surrogate", (write_mode, plans)
    obfuscate = build_column_plans(
        snap, mode="obfuscate", write_mode="table", pk_strategy=None, projection=projection
    )
    assert "country_code" not in {p.name for p in obfuscate}
    # a column the owner reviewed as not personal stays kept (the kept review covers it)
    unclassified = _with(snap, "country_code", entity_type=None, sensitivity=None)
    plans = build_column_plans(
        unclassified, mode="synthesize", write_mode="table", pk_strategy=None, projection=projection
    )
    assert "country_code" not in {p.name for p in plans}


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


def test_tabular_synthesis_is_refused_for_a_document_source(settings: Settings) -> None:
    """WP-I (m3 merge): M5's tabular engines (copula, MOSTLY, linked) model the flat columns of
    one table; a MongoDB document's leaves (nested fields, arrays) have no such column, and a
    sampled value would replace a whole top-level field. Refused at submit, before any read;
    surrogate synthesis (``tabular: none``) of MongoDB stays available."""
    mdb = make_connection(
        "c_src", engine="mongo", roles=["source", "target"], params={"database": "crm"}
    )
    snap = accepted_snapshot("c_src", "profiles").model_copy(
        update={"engine": "mongo", "object_ref": "profiles"}
    )

    def run(**options: Any) -> RunSpec:
        r = spec(**UAT_TABLE, mode="synthesize", options=options)
        return r.model_copy(
            update={"source": r.source.model_copy(update={"object": ObjectRef(name="profiles")})}
        )

    assert _code(run(tabular="copula"), settings, snap, source_conn=mdb, target_conn=mdb) == (
        "tabular_invalid",
        422,
    )
    _check(run(), snap, settings=settings, source_conn=mdb, target_conn=mdb)
