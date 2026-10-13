"""Run validation, the run plan, PK strategies and created-table types (contract §5.3).

Pure functions, no I/O: :func:`validate_run` is what ``POST /jobs/run`` (and ``submit_run``)
checks before a job row exists; :func:`build_column_plans`, :func:`resolve_target` and the DDL
helpers are what ``prepare`` turns into the stored, value-free :class:`RunPlan`.

**Validation order** (each rule raises :class:`~dps.jobs_models.SpecError` with a value-free
detail): ``kek_unconfigured`` · ``engine_unavailable`` · ``snapshot_not_accepted`` ·
``connection_role`` · ``confirm_write_required`` · ``admin_scope_required`` ·
``in_place_invalid`` · ``pk_transform_in_place`` · ``pk_strategy_required`` ·
``pk_strategy_invalid`` (token key not reversible, synthesize token, same-table token/prefix or a
key that is not one integer column, an offset on a non-integer key or a prefix on a non-text key)
· ``target_invalid`` (table, files) · ``key_method_not_injective`` · ``method_needs_key`` ·
``text_method_invalid`` · ``group_not_found`` / ``group_key_mismatch`` · ``tabular_invalid``
(M5: tabular synthesis outside synthesize mode, into an ``in_place``/``append`` write, or with an
engine this deployment has not enabled; differential privacy or linked generation without the
``mostly`` engine; a linked generation that does not write files, names the source object as its
child, or lacks the child's accepted snapshot).

A same-table append re-reads its own new rows unless every new key sorts above every selected
key; only one integer key with ``generate`` (an owned, ascending, non-cycling sequence ahead of the
table, checked at prepare) or ``offset`` (prepare requires ``min(selected key) + offset >= max(key)
+ 1`` and clips the read range to ``max(key) + 1``) guarantees that.

**Created tables** keep the source type of every kept column (validated against
:data:`TYPE_RE` before it reaches generated SQL) and of planned numeric/date columns; planned
text-like columns become ``text`` because a fallback (``det_token`` instead of FPE) is longer than
the value. Key columns are ``NOT NULL`` with a ``PRIMARY KEY``; no foreign keys, indexes or
defaults are copied. **Existing targets** must hold every projected column with a compatible
kind, a key on exactly the key columns (``table`` upserts), and room for the worst-case output of
``det_token`` / ``hmac_pseudonym`` (``length_overflow``); other methods are measured by the
preflight.
"""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dps.auth import Principal
from dps.config import Settings
from dps.connections import Connection
from dps.engine.batch import KEYLESS_METHODS, ColumnPlan, PseudonymColumn
from dps.engine.policy import MethodSpec
from dps.engine.synth.keys import INT_DIGITS, LINK_ENTITIES, MIN_PSEUDONYM_DIGITS
from dps.engine.taxonomy import Sensitivity, entity_info
from dps.engine.transform.methods import METHODS
from dps.jobs_models import ClassificationSnapshot, ColumnClassification, RunSpec, SpecError
from dps.layers.connectors.base import (
    ColumnInfo,
    ObjectRef,
    PkGenerate,
    PkOffset,
    PkStrategy,
    PkToken,
    ResolvedTarget,
    Row,
    TableShape,
    WriteMode,
)
from dps.secrets import seed_fingerprint

#: Catalogue type strings (``format_type``) allowed in created-table DDL and SQL casts.
TYPE_RE = re.compile(r'^[a-z][a-z0-9 _(),\[\]".]*$')
#: ``(kid, seed_fingerprint)`` of a job group.
GroupKeys = tuple[str, str]
FREE_TEXT_METHOD = "det_token"
#: Source kinds whose planned output is created as ``text``.
_TEXT_OUTPUT_KINDS = frozenset({"text", "char", "lob_text", "json", "other"})
_TEXTUAL = frozenset({"text", "char", "lob_text"})
_FILE_ENGINES = frozenset({"s3", "pvc"})
_DATABASE_WRITES = frozenset({"in_place", "append"})
_HIDDEN = ConfigDict(hide_input_in_errors=True)


class PrepareFailure(Exception):
    """A prepare-time refusal (``target_invalid``, ``snapshot_stale``, ``length_overflow``,
    ``pk_collision``, ``row_count_mismatch``, ``no_key`` …); ``detail`` is value-free."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


class RunPlan(BaseModel):
    """What ``prepare`` stores in ``dps_job.plan`` and every partition rebuilds from (value-free).

    ``anchor`` is ``[column, entity type]`` of the snapshot's anchor column; ``prepare`` records
    what the target preparation did (``created``, ``truncated``, ``collisions``,
    ``existing_rows``).
    """

    model_config = _HIDDEN
    columns: list[ColumnPlan]
    anchor: list[str] | None = None
    key: list[str]
    projection: list[str]
    target: ResolvedTarget
    source_object: str
    policy_name: str
    policy_hash: str
    snapshot_id: str
    snapshot_hash: str
    locale: str
    batch_rows: int
    same_table_append: bool = False
    prepare: dict[str, Any] = Field(default_factory=dict)
    #: D-A (synthesize): identifier columns replaced by keyed pseudonyms, never copied
    pseudonyms: list[PseudonymColumn] = Field(default_factory=list)


def _fail(code: str, status: int, detail: str) -> SpecError:
    return SpecError(code, status, detail)


def _owner(obj: ObjectRef) -> str:
    return obj.owner or "public"


def _same_object(a: ObjectRef, b: ObjectRef) -> bool:
    return (_owner(a), a.name) == (_owner(b), b.name)


def _object_ref(obj: ObjectRef, engine: str) -> str:
    """The ``object_ref`` text discovery stored (PostgreSQL resolves no owner to ``public``)."""
    return f"{_owner(obj)}.{obj.name}" if engine == "postgres" else obj.fqn()


def is_same_table(spec: RunSpec) -> bool:
    """True when the run writes the object it reads (same connection, same object)."""
    target = spec.target
    same_connection = target.connection_id in (None, spec.source.connection_id)
    same_object = target.object is None or _same_object(target.object, spec.source.object)
    return same_connection and same_object


def method_for_column(col: ColumnClassification, mode: str) -> MethodSpec:
    """The accepted method of ``col`` in ``mode`` (synthesize falls back to ``surrogate``)."""
    if mode == "synthesize":
        return col.synthesize_method or col.synthesize_suggested or MethodSpec(method="surrogate")
    return col.method


def _identifies(c: ColumnClassification, keys: set[str]) -> bool:
    """A classified column a synthesize run must not copy *as an identifier*: a key, a join
    identifier (:data:`~dps.engine.synth.keys.LINK_ENTITIES`) or MEDIUM+ sensitivity."""
    assert c.entity_type is not None
    rank = entity_info(c.entity_type).sensitivity.rank
    return (
        c.is_key or c.name in keys or c.entity_type in LINK_ENTITIES
    ) or rank >= Sensitivity.MEDIUM.rank


def kept_attribute(c: ColumnClassification, keys: set[str] | None = None) -> bool:
    """D-A (G2 review r1, B1): a classified, non-free-text column a synthesize run would keep
    although it is personal data — a LOW attribute such as a city, postal code, gender, age or
    date that ``keep_non_sensitive`` (or an owner) set to ``keep``. It is never copied: the
    tabular engine draws it when it models the column, otherwise it gets a ``surrogate``
    (:func:`build_column_plans`). Identifiers are pseudonymised instead (:func:`_identifiers`)."""
    if c.entity_type is None or c.text_column:
        return False
    if method_for_column(c, "synthesize").method != "keep":
        return False
    return not _identifies(c, keys or set())


def _label(reference: str) -> str:
    """The pseudonym label of an unclassified key ``owner.table.column`` (and of every declared
    foreign key to it): the same customer of a job group gets the same pseudonym in both."""
    return f"KEY|{reference}"


def _identifiers(
    snapshot: ClassificationSnapshot,
    *,
    write_mode: WriteMode,
    pk_strategy: PkStrategy | dict[str, Any] | None,
    projection: list[str] | set[str],
    shape: TableShape | None = None,
) -> list[tuple[ColumnClassification, str | None]]:
    """D-A: the projected columns a synthesize run would copy although they identify someone,
    each with the label its pseudonym is keyed by (``None``: the entity type).

    * kept (``keep``) classified columns that are a key, a join identifier
      (:data:`~dps.engine.synth.keys.LINK_ENTITIES`) or of MEDIUM or higher sensitivity;
    * (G2 review r1, B2) every **unclassified key** a ``table``, ``files`` or ``append`` run
      writes, labelled ``KEY|owner.table.column``, and every unclassified **declared foreign
      key** (``ColumnInfo.references``, from ``shape``: the live shape at prepare, else the
      snapshot's) labelled by the key it references — so a real key never sits next to a
      pseudonymised identifier, and the tables of one job group still join.

    Keys the target generates (``append`` + ``generate``) are not written at all; in place an
    unclassified key is the row being rewritten (and a sensitive one is refused), and foreign
    keys keep pointing at the real parent rows of the same database. LOW attributes are
    :func:`kept_attribute` (modelled or surrogated, never pseudonymised)."""
    live = shape or snapshot.shape
    keys = set(live.key) | set(snapshot.shape.key)
    wanted = set(projection)
    generated = write_mode == "append" and _strategy_kind(pk_strategy) == "generate"
    owner = live.object.owner or "public"
    out: list[tuple[ColumnClassification, str | None]] = []
    for c in snapshot.columns:
        if c.name not in wanted or c.text_column:
            continue
        is_key = c.is_key or c.name in keys
        if is_key and generated:
            continue
        if c.entity_type is not None:
            if method_for_column(c, "synthesize").method == "keep" and _identifies(c, keys):
                out.append((c, None))
            continue
        if write_mode == "in_place":
            continue
        try:
            reference = live.column(c.name).references
        except KeyError:
            reference = None
        if reference:
            out.append((c, _label(reference)))
        elif is_key:
            out.append((c, _label(f"{owner}.{live.object.name}.{c.name}")))
    return out


def _pseudonym_of(
    c: ColumnClassification, shape: TableShape, label: str | None = None
) -> PseudonymColumn | None:
    """The pseudonym form of ``c``, or ``None`` when no keyed pseudonym fits its type."""
    try:
        info = shape.column(c.name)
    except KeyError:
        info = None
    kind = info.kind if info is not None else c.kind
    data_type = (info.data_type if info is not None else c.data_type).strip().lower()
    entity = str(c.entity_type) if c.entity_type is not None else "KEY"
    common: dict[str, Any] = {"path": c.path, "name": c.name, "entity_type": entity, "label": label}
    if kind in ("int", "decimal") and (kind == "int" or not (info and info.scale)):
        digits = (info.precision if info is not None else None) or INT_DIGITS.get(data_type)
        if digits is not None and digits < MIN_PSEUDONYM_DIGITS:
            return None
        return PseudonymColumn(**common, kind="int", digits=digits)
    if kind in _TEXTUAL:
        return PseudonymColumn(**common, kind="text")
    if kind == "uuid":
        return PseudonymColumn(**common, kind="uuid")
    return None


def pseudonym_columns(
    snapshot: ClassificationSnapshot,
    *,
    mode: str,
    write_mode: WriteMode,
    pk_strategy: PkStrategy | dict[str, Any] | None,
    projection: list[str],
    shape: TableShape | None = None,
) -> list[PseudonymColumn]:
    """D-A: the identifier columns a synthesize run replaces by keyed pseudonyms (none in
    obfuscate mode); ``shape`` is the live shape at prepare (its declared foreign keys).

    Raises:
        PrepareFailure: ``synthesize_pseudonym_unsupported`` — an identifier column whose type
            no pseudonym fits (``validate_run`` refuses the run first).
    """
    if mode != "synthesize":
        return []
    out: list[PseudonymColumn] = []
    for c, label in _identifiers(
        snapshot, write_mode=write_mode, pk_strategy=pk_strategy, projection=projection, shape=shape
    ):
        p = _pseudonym_of(c, shape or snapshot.shape, label)
        if p is None:
            raise PrepareFailure(
                "synthesize_pseudonym_unsupported",
                f"{c.path}: no keyed pseudonym fits this column type",
            )
        out.append(p)
    return out


def _check_identifiers(spec: RunSpec, snapshot: ClassificationSnapshot) -> None:
    """D-A at submit: a synthesize run never copies a sensitive key or identifier."""
    target = spec.target
    sensitive_key = any(c.entity_type for c in _key_columns(snapshot))
    if target.write_mode == "in_place" and sensitive_key:
        raise _fail(
            "synthesize_in_place_sensitive_key",
            422,
            "a synthesize run in place would keep the real key of every row: write a table, "
            "files or an append with generated keys",
        )
    if target.write_mode == "append" and isinstance(target.pk_strategy, PkOffset):
        # G2 review r1 (B2): ``real key + offset`` (or a prefix) links back whatever the class
        raise _fail(
            "pk_strategy_invalid",
            422,
            "a real key is never copied into synthetic rows, shifted or prefixed: use generate",
        )
    for c, label in _identifiers(
        snapshot,
        write_mode=target.write_mode,
        pk_strategy=target.pk_strategy,
        projection=_projection(spec, snapshot),
    ):
        if _pseudonym_of(c, snapshot.shape, label) is None:
            raise _fail(
                "synthesize_pseudonym_unsupported",
                422,
                f"{c.path}: an identifier or key that synthetic rows must not copy, and no keyed "
                "pseudonym fits its type (integers of at least 9 digits, text, uuid)",
            )


def _strategy_kind(strategy: PkStrategy | dict[str, Any] | None) -> str | None:
    if isinstance(strategy, dict):
        kind = strategy.get("kind")
        return str(kind) if kind is not None else None
    return strategy.kind if strategy is not None else None


def _reversible(method: MethodSpec) -> bool:
    spec = METHODS.get(method.method)
    return bool(spec and spec.reversible)


def _projection(spec: RunSpec, snapshot: ClassificationSnapshot) -> set[str]:
    columns = spec.source.selection.columns
    if not columns:
        return {c.name for c in snapshot.columns}
    return set(columns) | set(snapshot.shape.key)


def _key_columns(snapshot: ClassificationSnapshot) -> list[ColumnClassification]:
    keys = set(snapshot.shape.key)
    return [c for c in snapshot.columns if c.name in keys]


def _written_connection(
    spec: RunSpec, source_conn: Connection, target_conn: Connection | None
) -> Connection | None:
    if spec.target.write_mode in _DATABASE_WRITES and spec.target.connection_id in (
        None,
        source_conn.id,
    ):
        return source_conn
    return target_conn


def _check_group(spec: RunSpec, group: GroupKeys | None) -> None:
    if spec.group_id is None:
        return
    if group is None:
        raise _fail("group_not_found", 404, "job group not found")
    kid, fingerprint = group
    consistency = spec.consistency
    mismatch = _fail(
        "group_key_mismatch", 409, "the run must carry the job group's key material and seed"
    )
    if consistency.field_scoped:
        raise _fail("group_key_mismatch", 409, "job groups need field_scoped: false")
    if spec.mode == "obfuscate":
        km = consistency.key_material
        if not isinstance(km, dict) or km.get("kid") != kid:
            raise mismatch
    else:
        seed = consistency.synthesis_seed
        text = seed.get("seed") if isinstance(seed, dict) else None
        try:
            matches = isinstance(text, str) and seed_fingerprint(text) == fingerprint
        except ValueError:
            matches = False
        if not matches:
            raise mismatch


def _check_append(spec: RunSpec, snapshot: ClassificationSnapshot) -> None:
    strategy = spec.target.pk_strategy
    if strategy is None:
        raise _fail("pk_strategy_required", 422, "append needs a pk_strategy")
    keys = _key_columns(snapshot)
    key_info = [snapshot.shape.column(k) for k in snapshot.shape.key]
    invalid = "pk_strategy_invalid"
    if isinstance(strategy, PkToken):
        if spec.mode == "synthesize":
            raise _fail(invalid, 422, "token keys need a reversible key method (obfuscate only)")
        if not keys or any(not _reversible(method_for_column(c, spec.mode)) for c in keys):
            raise _fail(invalid, 422, "token keys need a reversible method on every key column")
    if isinstance(strategy, PkOffset):
        wanted = ("int",) if strategy.offset is not None else ("text", "char")
        if any(c.kind not in wanted for c in key_info):
            raise _fail(
                invalid,
                422,
                "offset shifts integer keys; prefix applies to text keys",
            )
    if is_same_table(spec):
        if isinstance(strategy, PkToken) or (
            isinstance(strategy, PkOffset) and strategy.prefix is not None
        ):
            raise _fail(invalid, 422, "a same-table append takes generate or a numeric offset only")
        if len(key_info) != 1 or key_info[0].kind != "int":
            raise _fail(invalid, 422, "a same-table append needs a single integer key column")


def _check_target(spec: RunSpec, source_conn: Connection, target_conn: Connection | None) -> None:
    target = spec.target
    invalid = "target_invalid"
    if target.write_mode == "table":
        if target.connection_id is None or target.object is None or target_conn is None:
            raise _fail(invalid, 422, "a table target needs a connection_id and an object")
        if target_conn.engine in _FILE_ENGINES:
            raise _fail(invalid, 422, "a table target must be a database connection")
        if is_same_table(spec):
            raise _fail(invalid, 422, "a table target cannot be the source object")
        if target.pk_strategy is not None:
            raise _fail(invalid, 422, "pk_strategy applies to append only")
    elif target.write_mode == "files":
        if target_conn is None or target_conn.engine not in _FILE_ENGINES:
            raise _fail(invalid, 422, "a files target must be an s3 or pvc connection")
        if target.files is None:
            raise _fail(invalid, 422, "a files target needs files settings")
    elif target.write_mode == "append" and target.connection_id not in (None, source_conn.id):
        if target_conn is None or target_conn.engine in _FILE_ENGINES:
            raise _fail(invalid, 422, "an append target must be a database connection")


def _check_tabular(
    spec: RunSpec,
    settings: Settings,
    *,
    source_conn: Connection,
    child_snapshot: ClassificationSnapshot | None,
) -> None:
    """M5 tabular synthesis: synthesize mode, a table or files target, an enabled engine;
    differential privacy and linked generation with the MOSTLY engine only (Task 7B)."""
    options = spec.options
    engine = options.tabular
    if options.tabular_dp and engine != "mostly":
        raise _fail(
            "tabular_invalid", 422, "differential privacy is an option of the mostly engine"
        )
    if options.linked is not None and engine != "mostly":
        raise _fail("tabular_invalid", 422, "linked generation needs the mostly engine")
    if engine == "none":
        return
    if spec.mode != "synthesize" or spec.target.write_mode in _DATABASE_WRITES:
        raise _fail(
            "tabular_invalid",
            422,
            "tabular synthesis runs in synthesize mode into a table or files target",
        )
    if engine not in settings.tabular_engines:
        raise _fail(
            "tabular_invalid", 422, f"tabular engine {engine} is not enabled on this deployment"
        )
    if engine == "mostly":
        from dps.engine.synth.mostly import mostly_available  # needs the tabular extra only

        if not mostly_available():
            raise _fail(
                "tabular_invalid", 422, "the synth-ml extra is not installed on this deployment"
            )
    if options.linked is not None:
        _check_linked(spec, source_conn=source_conn, child_snapshot=child_snapshot)


def _check_linked(
    spec: RunSpec, *, source_conn: Connection, child_snapshot: ClassificationSnapshot | None
) -> None:
    """Linked generation writes files and needs the accepted snapshot of another table."""
    linked = spec.options.linked
    assert linked is not None
    if spec.target.write_mode != "files":
        raise _fail("tabular_invalid", 422, "linked generation writes a files target")
    if _same_object(linked.child, spec.source.object):
        raise _fail("tabular_invalid", 422, "the linked child must be another table")
    if (
        child_snapshot is None
        or child_snapshot.status != "accepted"
        or child_snapshot.id != linked.child_snapshot_id
        or child_snapshot.connection_id != spec.source.connection_id
        or child_snapshot.object_ref != _object_ref(linked.child, source_conn.engine)
    ):
        raise _fail(
            "tabular_invalid",
            422,
            "linked generation needs the accepted snapshot of the child table",
        )


def validate_run(
    spec: RunSpec,
    *,
    principal: Principal,
    source_conn: Connection,
    target_conn: Connection | None,
    snapshot: ClassificationSnapshot | None,
    settings: Settings,
    kek: bytes | None = None,
    group: GroupKeys | None = None,
    child_snapshot: ClassificationSnapshot | None = None,
) -> None:
    """Refuse a run DPS must not start (module docstring for the order and codes).

    ``child_snapshot`` is the accepted snapshot a linked generation names (M5 Task 7B).

    Raises:
        SpecError: the first rule the spec breaks, with its code and HTTP status.
    """
    target = spec.target
    mode = target.write_mode
    if kek is None:
        raise _fail("kek_unconfigured", 503, "DPS_JOB_KEK is not configured")
    if source_conn.engine != "postgres":
        raise _fail("engine_unavailable", 422, "M2 runs read PostgreSQL sources only")
    if (
        snapshot is None
        or snapshot.status != "accepted"
        or snapshot.connection_id != spec.source.connection_id
        or snapshot.object_ref != _object_ref(spec.source.object, source_conn.engine)
    ):
        raise _fail(
            "snapshot_not_accepted",
            409,
            "the run needs the accepted snapshot of this connection and object",
        )
    if "source" not in source_conn.roles:
        raise _fail("connection_role", 422, "the source connection lacks the source role")
    written = _written_connection(spec, source_conn, target_conn)
    if written is not None and "target" not in written.roles:
        raise _fail("connection_role", 422, "the written connection lacks the target role")
    if mode in _DATABASE_WRITES:
        if not target.confirm_write:
            raise _fail("confirm_write_required", 422, f"{mode} writes need confirm_write: true")
        if written is not None and written.production and not principal.has_scope("admin"):
            raise _fail(
                "admin_scope_required",
                403,
                f"{mode} on a production-tagged connection needs the admin scope",
            )
    keys = _key_columns(snapshot)
    if spec.mode == "synthesize":
        _check_identifiers(spec, snapshot)
    if mode == "in_place":
        if (
            target.connection_id not in (None, source_conn.id)
            or (target.object is not None and not _same_object(target.object, spec.source.object))
            or spec.source.selection.query is not None
        ):
            raise _fail(
                "in_place_invalid",
                422,
                "in_place writes the source object itself and takes no query selection",
            )
        if any(method_for_column(c, spec.mode).method != "keep" for c in keys):
            raise _fail(
                "pk_transform_in_place", 422, "key columns are never rewritten in place: keep them"
            )
    if mode == "append":
        _check_append(spec, snapshot)
    _check_target(spec, source_conn, target_conn)
    token_keys = mode == "append" and isinstance(target.pk_strategy, PkToken)
    if mode in ("table", "files") or token_keys:
        for c in keys:
            method = method_for_column(c, spec.mode)
            if method.method != "keep" and not _reversible(method):
                raise _fail(
                    "key_method_not_injective",
                    422,
                    f"key column {c.path}: the method must be keep or reversible",
                )
    projected = _projection(spec, snapshot)
    for c in snapshot.columns:
        if c.name not in projected:
            continue
        method = method_for_column(c, spec.mode)
        if spec.mode == "synthesize" and not c.text_column and method.method not in KEYLESS_METHODS:
            raise _fail(
                "method_needs_key",
                422,
                f"{c.path}: {method.method} needs key material, which synthesize mode has none of",
            )
        if spec.mode == "obfuscate" and c.text_column and method.method != FREE_TEXT_METHOD:
            raise _fail(
                "text_method_invalid", 422, f"{c.path}: free-text columns are tokenized (det_token)"
            )
    _check_group(spec, group)
    options = spec.options
    if options.tabular != "none" or options.tabular_dp or options.linked is not None:
        _check_tabular(spec, settings, source_conn=source_conn, child_snapshot=child_snapshot)


def build_column_plans(
    snapshot: ClassificationSnapshot,
    *,
    mode: str,
    write_mode: WriteMode,
    pk_strategy: PkStrategy | dict[str, Any] | None,
    projection: list[str],
) -> list[ColumnPlan]:
    """One :class:`ColumnPlan` per projected column that is transformed in ``mode``.

    Kept columns and columns without an entity get no plan; key columns get none in place
    (never rewritten) or on an append whose keys come from ``generate`` / ``offset``. In
    synthesize mode a classified attribute the policy keeps (:func:`kept_attribute`, D-A B1) is
    planned ``surrogate`` — the tabular engine replaces that plan by its draw when it models the
    column (``jobs_run``).
    """
    kind = _strategy_kind(pk_strategy)
    wanted = set(projection)
    keys = set(snapshot.shape.key)
    plans: list[ColumnPlan] = []
    for col in snapshot.columns:
        if col.name not in wanted:
            continue
        method = method_for_column(col, mode)
        if col.is_key and (
            write_mode == "in_place" or (write_mode == "append" and kind in ("generate", "offset"))
        ):
            continue
        if mode == "synthesize" and kept_attribute(col, keys):
            method = MethodSpec(method="surrogate")
        elif not col.text_column and (col.entity_type is None or method.method == "keep"):
            continue
        info = snapshot.shape.column(col.name)
        plans.append(
            ColumnPlan(
                path=col.path,
                name=col.name,
                entity_type=col.entity_type or "FREE_TEXT",
                method=method,
                text_column=col.text_column,
                reversible=_reversible(method),
                kind=info.kind,
                length=info.length,
                precision=info.precision,
                scale=info.scale,
            )
        )
    return plans


def created_column_type(column: ColumnInfo, plan: ColumnPlan | None) -> str:
    """DDL type of ``column`` in a created table (``plan`` None = kept).

    Raises:
        PrepareFailure: ``target_invalid`` when the catalogue type cannot be used in SQL.
    """
    if plan is not None and column.kind in _TEXT_OUTPUT_KINDS:
        return "text"
    if not TYPE_RE.fullmatch(column.data_type):
        raise PrepareFailure("target_invalid", f"{column.path}: the column type cannot be copied")
    return column.data_type


def worst_case_length(plan: ColumnPlan, source: ColumnInfo) -> int | None:
    """Longest possible output for methods whose output length is known statically.

    ``det_token``: ``ceil((4·L + 16)·8/5)`` — every character as 4 UTF-8 bytes plus the AES-SIV
    tag, in base32. ``hmac_pseudonym``: its ``length`` (default 16). Others: ``None`` (the
    preflight measures them).
    """
    if plan.method.method == "det_token" and source.length:
        return math.ceil((4 * source.length + 16) * 8 / 5)
    if plan.method.method == "hmac_pseudonym":
        return int(plan.method.params.get("length", 16))
    return None


def apply_pk_strategy(rows: list[Row], *, strategy: PkStrategy, key: list[str]) -> list[Row]:
    """Append keys: shift / prefix them (``offset``) or drop them for the target (``generate``).

    ``token`` keys were already transformed by the column plans; rows are changed in place.
    """
    if isinstance(strategy, PkOffset):
        for row in rows:
            for k in key:
                if strategy.offset is not None:
                    row[k] = row[k] + strategy.offset
                else:
                    row[k] = f"{strategy.prefix}{row[k]}"
    elif isinstance(strategy, PkGenerate):
        for row in rows:
            for k in key:
                row.pop(k, None)
    return rows


def _compatible(source_kind: str, target_kind: str, planned: bool) -> bool:
    if source_kind == target_kind:
        return True
    if source_kind in _TEXTUAL and target_kind in _TEXTUAL:
        return True
    if planned and target_kind in ("text", "lob_text"):
        return True  # the engine's output is text: a text column takes it
    return source_kind == "int" and target_kind in ("decimal", "float")


def _check_existing(
    target_shape: TableShape,
    source_shape: TableShape,
    columns: list[str],
    planned: set[str],
) -> None:
    for name in columns:
        try:
            target_col = target_shape.column(name)
        except KeyError:
            raise PrepareFailure(
                "target_invalid", f"the target has no column {source_shape.column(name).path}"
            ) from None
        source_col = source_shape.column(name)
        if not _compatible(source_col.kind, target_col.kind, name in planned):
            raise PrepareFailure(
                "target_invalid",
                f"{source_col.path}: a {source_col.kind} value cannot go to a "
                f"{target_col.kind} column",
            )


def _target_plans(plans: list[ColumnPlan], target_shape: TableShape) -> list[ColumnPlan]:
    out = []
    for p in plans:
        t = target_shape.column(p.name)
        out.append(
            p.model_copy(
                update={
                    "kind": t.kind,
                    "length": t.length,
                    "precision": t.precision,
                    "scale": t.scale,
                }
            )
        )
    return out


def _check_lengths(plans: list[ColumnPlan], source_shape: TableShape) -> None:
    for p in plans:
        worst = worst_case_length(p, source_shape.column(p.name))
        if worst is not None and p.length is not None and worst > p.length:
            raise PrepareFailure(
                "length_overflow",
                f"{p.path}: the worst-case output ({worst}) exceeds the column length ({p.length})",
            )


def _unbounded_plans(plans: list[ColumnPlan], source_shape: TableShape) -> list[ColumnPlan]:
    """Planned text-like columns become unbounded ``text`` (created tables, file targets)."""
    out = []
    for p in plans:
        if source_shape.column(p.name).kind in _TEXT_OUTPUT_KINDS:
            p = p.model_copy(
                update={"kind": "text", "length": None, "precision": None, "scale": None}
            )
        out.append(p)
    return out


def resolve_target(
    spec: RunSpec,
    *,
    job_id: str,
    source_conn: Connection,
    target_conn: Connection | None,
    source_shape: TableShape,
    target_shape: TableShape | None,
    plans: list[ColumnPlan],
    projection: list[str],
    pseudonyms: list[str] | None = None,
) -> tuple[ResolvedTarget, list[ColumnPlan]]:
    """The :class:`ResolvedTarget` of a validated run and the plans with the **target**
    column's kind and length (created text columns are unbounded). ``pseudonyms`` (D-A) are
    written in place like planned columns.

    Raises:
        PrepareFailure: ``target_invalid`` (missing target, missing or incompatible columns, no
            key on the key columns, nothing to update in place) or ``length_overflow``.
    """
    t = spec.target
    conn = target_conn or source_conn
    planned = {p.name for p in plans}
    common: dict[str, Any] = {
        "job_id": job_id,
        "write_mode": t.write_mode,
        "connection_id": conn.id,
        "engine": conn.engine,
        "create_if_missing": t.create_if_missing,
        "table_load": t.table_load,
        "pk_strategy": t.pk_strategy,
        "ledger_owner": t.ledger_owner,
        "key": list(source_shape.key),
    }
    if t.write_mode == "files":
        # file columns are unbounded: a planned text-like column is ``text`` (a fallback token is
        # longer than the value); the types are for the manifest only, never SQL
        types = {
            n: "text"
            if n in planned and source_shape.column(n).kind in _TEXT_OUTPUT_KINDS
            else source_shape.column(n).data_type
            for n in projection
        }
        return (
            ResolvedTarget(
                **common, files=t.files, write_columns=list(projection), column_types=types
            ),
            _unbounded_plans(plans, source_shape),
        )
    if t.write_mode == "table":
        if t.object is None:
            raise PrepareFailure("target_invalid", "a table target needs an object")
        if target_shape is None:
            if not t.create_if_missing:
                raise PrepareFailure("target_invalid", "the target table does not exist")
            by_name = {p.name: p for p in plans}
            types = {
                n: created_column_type(source_shape.column(n), by_name.get(n)) for n in projection
            }
            resolved = ResolvedTarget(
                **common,
                object=t.object,
                write_columns=list(projection),
                column_types=types,
            )
            return resolved, _unbounded_plans(plans, source_shape)
        _check_existing(target_shape, source_shape, projection, planned)
        if not target_shape.key or set(target_shape.key) != set(source_shape.key):
            raise PrepareFailure(
                "target_invalid",
                "the target needs a primary key or unique index on exactly the key columns",
            )
        out = _target_plans(plans, target_shape)
        _check_lengths(out, source_shape)
        resolved = ResolvedTarget(
            **common,
            object=target_shape.object,
            write_columns=list(projection),
            target_shape=target_shape,
        )
        return resolved, out
    # in_place / append write an existing table
    if target_shape is None:
        raise PrepareFailure("target_invalid", "the target table does not exist")
    generated = isinstance(t.pk_strategy, PkGenerate)
    if t.write_mode == "in_place":
        write_columns = [p.name for p in plans] + [n for n in pseudonyms or [] if n not in planned]
        if not write_columns:
            raise PrepareFailure("target_invalid", "no column of this run is transformed in place")
        checked = list(source_shape.key) + write_columns
    else:
        write_columns = [n for n in projection if not (generated and n in source_shape.key)]
        checked = write_columns
    _check_existing(target_shape, source_shape, checked, planned)
    out = _target_plans(plans, target_shape)
    _check_lengths(out, source_shape)
    resolved = ResolvedTarget(
        **common,
        object=target_shape.object,
        same_as_source=is_same_table(spec),
        write_columns=write_columns,
        generated_key=generated,
        target_shape=target_shape,
    )
    return resolved, out
