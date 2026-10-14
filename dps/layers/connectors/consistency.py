"""Cross-system consistency check for a job group (spec §7, contract §6.5): an n-way join over the
members' masked targets.

The check reads only the members' **targets** — the masked copies — needs no key, and returns
counts. Sampled values exist in memory for the duration of the call and never reach a log, a
metric, the response or the database. Formats may differ across systems (Oracle ``NUMBER``
73104482, MongoDB ``"CUST-73104482"``): numeric and prefixed-digit forms compare as integers, and
lookups regenerate each member's own representation, including FF1 outputs whose leading zero a
``NUMBER`` column dropped (``"CUST-03104482"`` ↔ ``3104482``).

Members are run jobs of the group in a ready state that wrote rows (not dry runs, not ``files``
targets), each naming a column path of its target; no two members may name the same target column
(:func:`target_key`: the connection's endpoint, the object and the path). With ``pairs`` omitted the join entity is the
first of :data:`JOIN_PREFERENCE` every member's accepted snapshot maps (method ≠ ``keep``), else
the alphabetically first shared entity. The anchor (member 0) contributes up to ``sample``
distinct canonical join values from a random sample of its target; every member is probed with
``lookup_values`` in chunks of :data:`LOOKUP_CHUNK`, concurrently. Other entities mapped in ≥ 2
members are compared on the joined rows; only value-deterministic methods with the same
parameters everywhere count toward ``passed`` (anchor-dependent ones — ``date_shift``,
``surrogate`` — are informational, contract §6.2).

The result holds ids, object names, paths, entity types, method names, a 12-hex params hash,
forms, widths, the *length* of a prefix and counts — never a sampled value, token or prefix text.
Every connector is closed in a ``finally``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from dps.config import Settings
from dps.jobs_models import ColumnClassification, Selection
from dps.layers.connectors.base import ColumnInfo, Connector, ObjectRef, Row, TableShape
from dps.layers.connectors.docpaths import column_values, dotted, row_locator
from dps.layers.connectors.registry import open_connector
from dps.layers.connectors.targets import _owner
from dps.observability import CONSISTENCY_CHECKS, get_logger

if TYPE_CHECKING:
    from dps.connections import Connection
    from dps.groups import JobGroup
    from dps.jobs import Job
    from dps.jobs_models import ClassificationSnapshot

logger = get_logger(__name__)

JOIN_PREFERENCE: Final = (
    "CUSTOMER_ID",
    "ACCOUNT_NUMBER",
    "EMPLOYEE_ID",
    "CASE_ID",
    "USERNAME",
    "EMAIL",
)
#: Methods whose output depends only on the value (and the key/params): equal inputs agree.
VALUE_DETERMINISTIC: Final = frozenset(
    {"det_token", "fpe_digits", "fpe_text", "email", "phone", "hmac_pseudonym"}
)
READY_STATES: Final = frozenset({"running", "waiting", "paused", "succeeded"})
#: Problems that fail the check whatever the coverage.
BLOCKING: Final = frozenset({"field_scoped", "kid_mismatch", "method_mismatch"})
LOOKUP_CHUNK: Final = 500
#: Rows a non-anchor member samples to learn its join form.
FORM_SAMPLE: Final = 200
_PREFIXED: Final = re.compile(r"^(\D*?)(\d+)$")
_DIGIT_METHODS: Final = frozenset({"fpe_digits", "phone"})
_NUMERIC: Final = frozenset({"numeric", "prefixed_digits"})
FormKind = Literal["numeric", "prefixed_digits", "exact", "unknown"]


class CheckMember(BaseModel):
    """One member of the join: a run job of the group and a column path of its target."""

    model_config = ConfigDict(hide_input_in_errors=True)
    job_id: str = Field(min_length=3, max_length=64)
    path: str = Field(min_length=3, max_length=300)


class ConsistencyCheckRequest(BaseModel):
    """Body of ``POST /job-groups/{id}/consistency-check`` (contract §9).

    ``pairs``: at least two members naming **distinct target columns** (a repeated column, by the
    same job or by another job of the group that wrote the same target, is refused by the check
    as ``422 member_duplicate``); omitted, one member per distinct target is derived from the
    group's runs.
    """

    model_config = ConfigDict(hide_input_in_errors=True)
    pairs: list[CheckMember] | None = Field(default=None, min_length=2, max_length=8)
    sample: int = Field(default=1000, ge=10, le=10_000)
    min_coverage: float = Field(default=1.0, gt=0.0, le=1.0)
    attributes: bool = True


class MemberResult(BaseModel):
    """What one member contributed (no values: the prefix appears as its length only)."""

    job_id: str
    engine: str
    connection_id: str
    object: str
    path: str
    entity_type: str | None
    method: str | None
    params_hash: str | None
    form: FormKind
    width: int | None
    prefix_len: int | None
    found: int
    anchor: bool


class PairResult(BaseModel):
    """Join coverage of two members over the anchor's sampled values."""

    left: int
    right: int
    checked: int
    matched: int
    coverage: float
    consistent: bool


class AttributeResult(BaseModel):
    """Agreement of one other shared entity on the joined rows."""

    entity_type: str
    method: str
    members: list[int]
    counts_toward_pass: bool
    compared: int
    agreed: int
    ambiguous: int
    consistent: bool


class CheckProblem(BaseModel):
    """A value-free finding (code, member index, pair)."""

    code: str
    member: int | None = None
    pair: list[int] | None = None


class ConsistencyCheckResult(BaseModel):
    """The check's outcome; stored on the group as ``last_check`` (counts and codes only)."""

    group_id: str
    checked_at: datetime
    sample_requested: int
    sampled: int
    anchor: int
    members: list[MemberResult]
    pairs: list[PairResult]
    all_members_matched: int
    attributes: list[AttributeResult]
    methods_agree: bool
    problems: list[CheckProblem]
    passed: bool
    consistent_pairs: int
    total_pairs: int
    min_coverage: float
    duration_ms: float


class ConsistencyError(Exception):
    """A request the check cannot run; ``status``/``code`` map to ``DpsHttpError``."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


@runtime_checkable
class ValueLookup(Protocol):
    """A connector that can look rows up by a set of join values (all three engines)."""

    async def lookup_values(
        self, shape: TableShape, join: ColumnInfo, values: list[Any], attributes: list[ColumnInfo]
    ) -> list[Row]:
        """Rows whose ``join`` value is in ``values``, with ``join`` and ``attributes`` read."""
        ...


@dataclass(frozen=True)
class JoinForm:
    """How a member stores its join values (``prefix`` stays in memory; results carry its length)."""

    kind: FormKind
    prefix: str = ""
    width: int | None = None


def _text(value: Any) -> str:
    return value.rstrip() if isinstance(value, str) else str(value)


def _integral(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return False
    return d.is_finite() and d == d.to_integral_value()


def detect_form(column: ColumnInfo, values: Sequence[Any]) -> JoinForm:
    """``numeric`` (numeric column or all-integer values), ``prefixed_digits`` (one constant
    non-digit prefix + digits; ``width`` when constant), else ``exact``; ``unknown`` when no value
    is present."""
    present = [v for v in values if v is not None]
    if not present:
        return JoinForm("unknown")
    if column.kind in ("int", "decimal") and all(_integral(v) for v in present):
        return JoinForm("numeric")
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return JoinForm("numeric")
    matches = [_PREFIXED.match(_text(v)) for v in present]
    if all(m is not None for m in matches):
        prefixes = {m.group(1) for m in matches if m}
        widths = {len(m.group(2)) for m in matches if m}
        if len(prefixes) == 1:
            return JoinForm(
                "prefixed_digits", prefixes.pop(), widths.pop() if len(widths) == 1 else None
            )
    return JoinForm("exact")


def canonical(value: Any, form: JoinForm) -> int | str | None:
    """The comparable form of one stored value (``None`` when it does not fit the form)."""
    if value is None:
        return None
    if form.kind == "numeric":
        return int(Decimal(str(value))) if _integral(value) else None
    text = _text(value)
    if form.kind == "prefixed_digits":
        m = _PREFIXED.match(text)
        return int(m.group(2)) if m and m.group(1) == form.prefix else None
    return text


def comparable(a: JoinForm, b: JoinForm) -> bool:
    """Numeric and prefixed-digit forms compare as integers; ``exact`` joins only ``exact``."""
    return (a.kind in _NUMERIC and b.kind in _NUMERIC) or (a.kind == b.kind == "exact")


def candidates(c: int | str, form: JoinForm, column: ColumnInfo) -> list[Any]:
    """The member's own stored representations of canonical value ``c``."""
    if form.kind == "numeric":
        return [c] if isinstance(c, int) else []
    if form.kind == "prefixed_digits":
        if not isinstance(c, int):
            return []
        digits = str(c)
        if form.width is None:
            return [form.prefix + digits]
        return [form.prefix + digits.zfill(form.width)] if len(digits) <= form.width else []
    if isinstance(c, str):
        return [c.ljust(column.length) if column.kind == "char" and column.length else c]
    return []


def attribute_text(value: Any, method: str) -> str | None:
    """Canonical text of an attribute value per method (digits for ``fpe_digits``/``phone``,
    lower case for ``email``, the date part for ``date_shift``, exact otherwise)."""
    if value is None:
        return None
    text = value.isoformat() if isinstance(value, datetime | date) else _text(value)
    if method in _DIGIT_METHODS:
        return re.sub(r"\D", "", text).lstrip("0") or "0"
    if method == "email":
        return text.lower()
    if method == "date_shift":
        return text[:10]
    return text


def params_hash(params: Mapping[str, Any]) -> str:
    """12 hex characters of the canonical JSON of a method's parameters."""
    canon = json.dumps(dict(params), sort_keys=True, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()[:12]


def _joinable(col: ColumnClassification) -> bool:
    """A whole-value mapping (free-text columns are span-transformed, never joined or compared)."""
    return (
        col.entity_type is not None
        and col.method.method != "keep"
        and not col.text_column
        and col.entity_type != "FREE_TEXT"
    )


def _mapped(snapshot: ClassificationSnapshot) -> dict[str, ColumnClassification]:
    """entity type → preferred mapped column (key, then anchor, then the shortest path)."""
    out: dict[str, ColumnClassification] = {}
    ordered = sorted(
        snapshot.columns, key=lambda c: (not c.is_key, not c.anchor, len(c.path), c.path)
    )
    for col in ordered:
        if _joinable(col) and col.entity_type is not None:
            out.setdefault(col.entity_type, col)
    return out


def _eligible(job: Job) -> bool:
    target = job.spec.get("target") or {}
    return (
        job.state in READY_STATES
        and job.progress.rows_written > 0
        and job.progress.outcome != "dry_run"
        and not (job.spec.get("options") or {}).get("dry_run")
        and target.get("write_mode") != "files"
    )


def derive_members(
    jobs: list[Job],
    snapshots: Mapping[str, ClassificationSnapshot],
    connections: Mapping[str, Connection] | None = None,
) -> list[CheckMember]:
    """Members for a request without ``pairs``: one per distinct target, joined on the preferred
    entity.

    Eligible jobs that wrote the same target (:func:`target_key` without the path — a refresh of
    the group re-runs the same spec) collapse to the newest one (``created_at``, then id), so a
    target never joins itself and one system is never reported as a cross-system pass.

    Raises:
        ConsistencyError: 409 ``member_not_ready`` (fewer than two distinct eligible targets),
            422 ``no_shared_entity`` / ``member_unsupported``.
    """
    newest: dict[tuple[object, ...], Job] = {}
    for job in (j for j in jobs if _eligible(j) and j.snapshot_id in snapshots):
        conn_id, obj = _target(job)
        key = target_key(conn_id, (connections or {}).get(conn_id), obj)
        kept = newest.get(key)
        if kept is None or (job.created_at, job.id) > (kept.created_at, kept.id):
            newest[key] = job
    chosen = {j.id for j in newest.values()}
    usable = [j for j in jobs if j.id in chosen]
    if len(usable) < 2:
        raise ConsistencyError(409, "member_not_ready")
    mapped = {j.id: _mapped(snapshots[str(j.snapshot_id)]) for j in usable}
    shared = set.intersection(*(set(m) for m in mapped.values()))
    join = next((e for e in JOIN_PREFERENCE if e in shared), None) or (
        min(shared) if shared else None
    )
    if join is None:
        raise ConsistencyError(422, "no_shared_entity")
    return [CheckMember(job_id=j.id, path=mapped[j.id][join].path) for j in usable]


@dataclass
class _Member:
    index: int
    job: Job
    connection_id: str
    obj: ObjectRef
    connector: Connector
    shape: TableShape
    join: ColumnInfo
    entity: str | None
    method: str | None
    params_hash: str | None
    #: entity → (target column, method, params hash)
    attributes: dict[str, tuple[ColumnInfo, str, str]] = field(default_factory=dict)
    form: JoinForm = JoinForm("unknown")


Opener = Callable[..., Awaitable[Connector]]


def _select_column(column: ColumnInfo, engine: str) -> str:
    if engine != "mongo":
        return column.name
    name = dotted(column.path)
    if name is None:
        raise ConsistencyError(422, "member_unsupported")
    return name


def _target(job: Job) -> tuple[str, ObjectRef]:
    target = job.spec.get("target") or {}
    source = job.spec.get("source") or {}
    conn_id = target.get("connection_id") or source.get("connection_id")
    raw = target.get("object") or source.get("object")
    if not conn_id or not raw:
        raise ConsistencyError(422, "member_unsupported")
    return str(conn_id), ObjectRef.model_validate(raw)


#: Port an endpoint listens on when its connection record leaves ``port`` unset.
_DEFAULT_PORT: Final = {"postgres": 5432, "oracle": 1521, "mongo": 27017}


def target_key(
    conn_id: str, connection: Connection | None, obj: ObjectRef, path: str | None = None
) -> tuple[object, ...]:
    """Identity of a member's target (column when ``path`` is given) for duplicate detection.

    The endpoint is the connection's engine, host (case-folded), port (the engine default when
    unset) and database / service name, so two connection records for one database name one
    target. A connection without a host (or not a stored record) is identified by its id.
    The object is normalised as its engine resolves it: a PostgreSQL object without an owner is
    ``public.<name>`` (:func:`targets._owner`, as the writer resolves it), so ``{name: T}`` and
    ``{owner: public, name: T}`` are one target. Only Oracle folds case (DPS upper-cases Oracle
    identifiers); PostgreSQL names are quoted as given and MongoDB names are case-sensitive, so
    ``public."Cust"`` and ``public."cust"`` are two targets, and so are their paths. Limit: host
    aliases (a DNS name and its address, two listeners of one instance) are not resolved and count
    as different endpoints; an Oracle object without an owner is not resolved to the user's schema.
    """
    params = getattr(connection, "params", None)
    engine = getattr(connection, "engine", None)
    host = getattr(params, "host", None)
    endpoint: tuple[object, ...]
    if params is None or not host:
        endpoint = ("connection", conn_id)
    else:
        endpoint = (
            engine,
            str(host).casefold(),
            params.port or _DEFAULT_PORT.get(str(engine)),
            (params.database or "").casefold() or None,
            (params.service_name or "").casefold() or None,
        )
    fold = engine == "oracle"
    if engine == "postgres":
        obj = ObjectRef(owner=_owner(obj), name=obj.name)
    name = obj.fqn().casefold() if fold else obj.fqn()
    if path is None:
        return (*endpoint, name)
    return (*endpoint, name, path.casefold() if fold else path)


def _validate(
    group: JobGroup,
    wanted: list[CheckMember],
    in_group: Mapping[str, Job],
    snapshots: Mapping[str, ClassificationSnapshot],
    connections: Mapping[str, Connection],
    problems: list[CheckProblem],
) -> list[tuple[CheckMember, Job, str, ObjectRef]]:
    # A member is a target *column*: the same column twice (one job named twice, or two jobs of
    # the group that wrote the same target, e.g. a refresh) would join a target with itself and
    # pass for any data. One target with two different paths is a self-join and stays allowed.
    if len({(m.job_id, m.path) for m in wanted}) != len(wanted):
        raise ConsistencyError(422, "member_duplicate")
    seen: set[tuple[object, ...]] = set()
    out = []
    for i, m in enumerate(wanted):
        job = in_group.get(m.job_id)
        if job is None:
            raise ConsistencyError(422, "member_not_in_group")
        target = job.spec.get("target") or {}
        if target.get("write_mode") == "files":
            raise ConsistencyError(422, "member_unsupported")
        if not _eligible(job) or job.snapshot_id not in snapshots:
            raise ConsistencyError(409, "member_not_ready")
        if (job.spec.get("consistency") or {}).get("field_scoped"):
            problems.append(CheckProblem(code="field_scoped", member=i))
        if job.kid and job.kid != group.kid:
            problems.append(CheckProblem(code="kid_mismatch", member=i))
        conn_id, obj = _target(job)
        if conn_id not in connections:
            raise ConsistencyError(409, "member_not_ready")
        key = target_key(conn_id, connections[conn_id], obj, m.path)
        if key in seen:
            raise ConsistencyError(422, "member_duplicate")
        seen.add(key)
        out.append((m, job, conn_id, obj))
    return out


async def run_consistency_check(
    *,
    group: JobGroup,
    jobs: list[Job],
    snapshots: Mapping[str, ClassificationSnapshot],
    connections: Mapping[str, Connection],
    request: ConsistencyCheckRequest,
    settings: Settings,
    kek: bytes | None,
    opener: Opener = open_connector,
) -> ConsistencyCheckResult:
    """Run the n-way join over the members' masked targets; counts only.

    Raises:
        ConsistencyError: ``member_duplicate`` / ``member_not_in_group`` / ``member_unsupported``
            / ``path_unknown`` / ``no_shared_entity`` (422), ``member_not_ready`` (409).
        ConnectorError: a member target could not be opened or read (the connector's code).
    """
    t0 = time.perf_counter()
    in_group = {j.id: j for j in jobs if j.kind == "run" and j.group_id == group.id}
    wanted = request.pairs or derive_members(list(in_group.values()), snapshots, connections)
    problems: list[CheckProblem] = []
    plan = _validate(group, wanted, in_group, snapshots, connections, problems)
    members: list[_Member] = []
    try:
        for i, (m, job, conn_id, obj) in enumerate(plan):
            connector = await opener(
                connections[conn_id], settings, action="consistency", kek=kek, job_id=job.id
            )
            try:
                shape = await connector.describe(obj)
            except BaseException:
                await connector.close()
                raise
            join = next((c for c in shape.columns if c.path == m.path), None)
            if join is None or row_locator(join):
                await connector.close()
                raise ConsistencyError(422, "path_unknown")
            snapshot = snapshots[str(job.snapshot_id)]
            sc = next((c for c in snapshot.columns if c.path == m.path), None)
            member = _Member(
                index=i,
                job=job,
                connection_id=conn_id,
                obj=obj,
                connector=connector,
                shape=shape,
                join=join,
                entity=sc.entity_type if sc else None,
                method=sc.method.method if sc else None,
                params_hash=params_hash(sc.method.params) if sc else None,
            )
            members.append(member)
            if request.attributes:
                by_path = {c.path: c for c in shape.columns}
                for entity, col in _mapped(snapshot).items():
                    if entity != member.entity and col.path in by_path and col.path != m.path:
                        member.attributes[entity] = (
                            by_path[col.path],
                            col.method.method,
                            params_hash(col.method.params),
                        )
        return await _check(group, members, request, problems, t0)
    finally:
        await asyncio.gather(*(m.connector.close() for m in members), return_exceptions=True)


async def _forms(members: list[_Member], request: ConsistencyCheckRequest) -> list[int | str]:
    """Learn every member's join form; the anchor's distinct canonical sampled values."""
    anchor = members[0]
    sel = Selection(columns=[_select_column(anchor.join, anchor.connector.engine)])
    rows = await anchor.connector.sample(anchor.shape, sel, {}, request.sample)
    values = column_values(rows, anchor.join)
    del rows
    anchor.form = detect_form(anchor.join, values)
    sample = list(dict.fromkeys(c for v in values if (c := canonical(v, anchor.form)) is not None))
    del values
    for m in members[1:]:
        sel_m = Selection(columns=[_select_column(m.join, m.connector.engine)])
        sampled = await m.connector.sample(m.shape, sel_m, {}, FORM_SAMPLE)
        m.form = detect_form(m.join, column_values(sampled, m.join))
    return sample[: request.sample]


async def _probe(
    m: _Member, anchor: _Member, sample: list[int | str], problems: list[CheckProblem]
) -> dict[int | str, dict[str, set[str]]]:
    """canonical join value → entity → canonical attribute texts, for the values ``m`` holds."""
    if not isinstance(m.connector, ValueLookup):
        raise ConsistencyError(422, "member_unsupported")
    if not comparable(anchor.form, m.form):
        problems.append(CheckProblem(code="join_form_mismatch", member=m.index))
        return {}
    wanted = set(sample)
    by_candidate: dict[Any, int | str] = {}
    for c in sample:
        for cand in candidates(c, m.form, m.join):
            by_candidate[cand] = c
    found: dict[int | str, dict[str, set[str]]] = {}
    items = list(by_candidate)
    attrs = [(entity, col, meth) for entity, (col, meth, _) in m.attributes.items()]
    for start in range(0, len(items), LOOKUP_CHUNK):
        chunk = items[start : start + LOOKUP_CHUNK]
        rows = await m.connector.lookup_values(m.shape, m.join, chunk, [a[1] for a in attrs])
        for row in rows:
            for v in column_values([row], m.join):
                key = canonical(v, m.form)
                if key is None or key not in wanted:
                    continue
                rec = found.setdefault(key, {})
                for entity, col, meth in attrs:
                    texts = {t for x in column_values([row], col) if (t := attribute_text(x, meth))}
                    rec.setdefault(entity, set()).update(texts)
    return found


def _attributes(
    members: list[_Member],
    sets: list[set[int | str]],
    results: list[dict[int | str, dict[str, set[str]]]],
) -> list[AttributeResult]:
    out: list[AttributeResult] = []
    for entity in sorted({e for m in members for e in m.attributes}):
        holders = [m for m in members if entity in m.attributes]
        if len(holders) < 2:
            continue
        methods = {(m.attributes[entity][1], m.attributes[entity][2]) for m in holders}
        method = sorted(methods)[0][0]
        counts = len(methods) == 1 and method in VALUE_DETERMINISTIC
        common = set.intersection(*(sets[m.index] for m in holders))
        compared = agreed = ambiguous = 0
        for c in common:
            texts = [results[m.index].get(c, {}).get(entity, set()) for m in holders]
            if any(not t for t in texts):
                continue
            compared += 1
            if any(len(t) > 1 for t in texts):
                ambiguous += 1
            elif len({next(iter(t)) for t in texts}) == 1:
                agreed += 1
        out.append(
            AttributeResult(
                entity_type=entity,
                method=method,
                members=[m.index for m in holders],
                counts_toward_pass=counts,
                compared=compared,
                agreed=agreed,
                ambiguous=ambiguous,
                consistent=agreed == compared - ambiguous,
            )
        )
    return out


async def _check(
    group: JobGroup,
    members: list[_Member],
    request: ConsistencyCheckRequest,
    problems: list[CheckProblem],
    t0: float,
) -> ConsistencyCheckResult:
    anchor = members[0]
    sample = await _forms(members, request)
    results = list(await asyncio.gather(*(_probe(m, anchor, sample, problems) for m in members)))
    sets = [set(r) for r in results]
    methods_agree = len({(m.entity, m.method, m.params_hash) for m in members}) == 1
    if not methods_agree:
        problems.append(CheckProblem(code="method_mismatch"))
    blocked = {p.member for p in problems if p.code == "join_form_mismatch"}
    pairs: list[PairResult] = []
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            checked = len(sample)
            matched = len(sets[i] & sets[j])
            coverage = round(matched / checked, 4) if checked else 0.0
            ok = (
                checked > 0
                and coverage >= request.min_coverage
                and methods_agree
                and i not in blocked
                and j not in blocked
            )
            pairs.append(
                PairResult(
                    left=i, right=j, checked=checked, matched=matched, coverage=coverage,
                    consistent=ok,
                )
            )  # fmt: skip
    attributes = _attributes(members, sets, results) if request.attributes else []
    del results
    passed = (
        bool(sample)
        and all(p.consistent for p in pairs)
        and methods_agree
        and all(a.consistent for a in attributes if a.counts_toward_pass)
        and not any(p.code in BLOCKING for p in problems)
    )
    result = ConsistencyCheckResult(
        group_id=group.id,
        checked_at=datetime.now(UTC),
        sample_requested=request.sample,
        sampled=len(sample),
        anchor=0,
        members=[
            MemberResult(
                job_id=m.job.id,
                engine=m.connector.engine,
                connection_id=m.connection_id,
                object=m.obj.fqn(),
                path=m.join.path,
                entity_type=m.entity,
                method=m.method,
                params_hash=m.params_hash,
                form=m.form.kind,
                width=m.form.width,
                prefix_len=len(m.form.prefix) if m.form.kind == "prefixed_digits" else None,
                found=len(sets[m.index]),
                anchor=m.index == 0,
            )
            for m in members
        ],
        pairs=pairs,
        all_members_matched=len(set.intersection(*sets)) if sets else 0,
        attributes=attributes,
        methods_agree=methods_agree,
        problems=problems,
        passed=passed,
        consistent_pairs=sum(1 for p in pairs if p.consistent),
        total_pairs=len(pairs),
        min_coverage=request.min_coverage,
        duration_ms=round((time.perf_counter() - t0) * 1000, 1),
    )
    CONSISTENCY_CHECKS.labels("passed" if passed else "failed").inc()
    logger.info(
        "consistency_checked",
        group_id=group.id,
        members=len(members),
        pairs=len(pairs),
        sampled=len(sample),
        passed=passed,
    )
    return result
