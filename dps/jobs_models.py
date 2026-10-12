"""Pydantic specs for connections, discovery, runs, scans and retention (contract §3.3, §5, §9).

Every model hides its input in validation errors (``hide_input_in_errors``): a selection's bound
values, inline credentials and caller key material are customer data or secrets, and pydantic's
default error text would quote them. Rule messages name fields and codes only.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from dps.engine.policy import MethodSpec
from dps.engine.synth.personas import SUPPORTED_LOCALES
from dps.engine.taxonomy import Sensitivity
from dps.layers.connectors.base import (  # noqa: F401 - re-exported (contract §1)
    Engine,
    FilesSpec,
    JsonScalar,
    ObjectRef,
    PkGenerate,
    PkOffset,
    PkStrategy,
    PkToken,
    TableShape,
    WriteMode,
)

OPTION_KEYS = frozenset(
    {
        "sslmode",
        "sslrootcert_ref",
        "connect_timeout",
        "authSource",
        "replicaSet",
        "directConnection",
        "tls",
    }
)
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_PARAM_RE = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")
_HIDDEN = ConfigDict(hide_input_in_errors=True)
ConnRole = Literal["source", "target"]


def _source_only() -> set[ConnRole]:
    return {"source"}


class SpecError(Exception):
    """A spec the service will not run; ``code`` and ``status`` map to ``{"detail", "code"}``.

    ``detail`` is value-free (field names, codes, counts).
    """

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.code, self.status, self.detail = code, status, detail


class ConnectionParams(BaseModel):
    """Where a connection points (``dps_connection.params``) — never credentials."""

    model_config = _HIDDEN
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=128)
    service_name: str | None = Field(default=None, max_length=128)
    options: dict[str, str] = Field(default_factory=dict)
    bucket: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9.-]{1,62}$")
    prefix: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9/_.-]*$")
    region: str | None = None
    endpoint_url: str | None = None
    base_path: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_][A-Za-z0-9/_.-]{0,200}$")

    @field_validator("options")
    @classmethod
    def _allowed_options(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = set(value) - OPTION_KEYS
        if unknown:
            raise ValueError(f"unsupported connection options: {sorted(unknown)}")
        return value


class InlineCredentials(BaseModel):
    """Dev-only inline credentials; sealed into ``secret_enc`` by the registry (contract §5.1)."""

    model_config = _HIDDEN
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr
    extra: dict[str, SecretStr] = Field(default_factory=dict)


class ConnectionSpec(BaseModel):
    """A connection as the API accepts it (contract §5.1)."""

    model_config = _HIDDEN
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    engine: Literal["postgres", "oracle", "mongo", "s3", "pvc"]
    params: ConnectionParams
    secret_ref: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9/_.-]{0,200}$")
    credentials: InlineCredentials | None = None
    roles: set[ConnRole] = Field(default_factory=_source_only)
    tags: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _rules(self) -> ConnectionSpec:
        if self.secret_ref is not None and ".." in self.secret_ref.split("/"):
            raise ValueError("secret_ref must not contain '..'")
        for tag in self.tags:
            if not _TAG_RE.fullmatch(tag):
                raise ValueError("tags must match ^[a-z0-9][a-z0-9-]{0,31}$")
        if not self.roles:
            raise ValueError("a connection needs at least one role")
        if self.engine == "pvc":
            if self.secret_ref or self.credentials:
                raise ValueError("pvc connections take no credentials")
            if not self.params.base_path:
                raise ValueError("pvc connections need params.base_path")
        elif (self.secret_ref is None) == (self.credentials is None):
            raise ValueError("exactly one of secret_ref or credentials is required")
        if self.engine in ("s3", "pvc") and self.roles != {"target"}:
            raise ValueError("file stores are target-only connections")
        if self.engine == "s3" and not self.params.bucket:
            raise ValueError("s3 connections need params.bucket")
        if self.engine in ("postgres", "oracle") and not self.params.host:
            raise ValueError("database connections need params.host")
        if self.engine == "postgres" and not self.params.database:
            raise ValueError("postgres connections need params.database")
        if "production" in self.tags and self.credentials is not None:
            raise ValueError("production-tagged connections must use secret_ref")
        return self


class SampleSpec(BaseModel):
    """Random sample of the selection (``TABLESAMPLE`` / ``SAMPLE`` / ``$sample``)."""

    model_config = _HIDDEN
    percent: float = Field(gt=0, le=100)
    seed: int | None = None


class Selection(BaseModel):
    """Which rows of the object to read (contract §5.2); validated further by ``selection.py``."""

    model_config = _HIDDEN
    where: str | None = Field(default=None, max_length=8000)
    params: dict[str, JsonScalar] = Field(default_factory=dict)
    query: str | None = Field(default=None, max_length=20000)
    columns: list[str] | None = Field(default=None, min_length=1, max_length=2000)
    max_rows: int | None = Field(default=None, ge=1)
    sample: SampleSpec | None = None
    mongo_filter: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _exclusive(self) -> Selection:
        if self.where is not None and self.query is not None:
            raise ValueError("where and query are mutually exclusive")
        if self.mongo_filter is not None and (self.where is not None or self.query is not None):
            raise ValueError("mongo_filter excludes where and query")
        for name in self.params:
            if not _PARAM_RE.fullmatch(name):
                raise ValueError("bind names must match ^[a-z_][a-z0-9_]{0,30}$")
        return self


class TargetSpec(BaseModel):
    """Where and how a run writes (contract §5.3)."""

    model_config = _HIDDEN
    write_mode: WriteMode
    connection_id: str | None = None
    object: ObjectRef | None = None
    create_if_missing: bool = True
    table_load: Literal["truncate_insert", "upsert"] = "truncate_insert"
    pk_strategy: PkStrategy | None = None
    files: FilesSpec | None = None
    ledger_owner: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_$#]{0,62}$")
    confirm_write: bool = False
    expected_rows: int | None = Field(default=None, ge=0)


class SourceSpec(BaseModel):
    """What a run reads and how wide it fans out."""

    model_config = _HIDDEN
    connection_id: str
    object: ObjectRef
    selection: Selection = Field(default_factory=Selection)
    parallelism: int = Field(default=4, ge=1, le=64)
    batch_rows: int | None = Field(default=None, ge=10, le=50_000)
    partition_rows: int | None = Field(default=None, ge=1_000)


class ConsistencySpec(BaseModel):
    """Token consistency and caller-held key material (never stored in ``dps_job.spec``)."""

    model_config = _HIDDEN
    scope: Literal["request", "key", "entity"] = "key"
    field_scoped: bool = False
    key_material: dict[str, Any] | None = None
    synthesis_seed: dict[str, Any] | None = None
    wrap_key: dict[str, str] | None = None


class Purpose(BaseModel):
    """Why the run happens (change ticket + legal basis), recorded in the evidence pack."""

    model_config = _HIDDEN
    ticket: str = Field(min_length=3, max_length=64)
    legal_basis: str = Field(min_length=3, max_length=256)


class RunOptions(BaseModel):
    """Dry run, verification strictness, unclassified columns, tabular synthesis (M5)."""

    model_config = _HIDDEN
    dry_run: bool = False
    verify: Literal["strict", "warn"] = "strict"
    unclassified: Literal["detect", "keep", "redact"] = "detect"
    tabular: Literal["none", "copula", "mostly"] = "none"


class RunSpec(BaseModel):
    """``POST /api/v1/jobs/run`` body (contract §5.3)."""

    model_config = _HIDDEN
    mode: Literal["obfuscate", "synthesize"]
    source: SourceSpec
    target: TargetSpec
    snapshot_id: str
    policy: str | dict[str, Any] | None = None
    consistency: ConsistencySpec = Field(default_factory=ConsistencySpec)
    group_id: str | None = None
    purpose: Purpose
    options: RunOptions = Field(default_factory=RunOptions)


class DiscoverSpec(BaseModel):
    """``POST /api/v1/jobs/discover`` body; ``mode`` is a console hint only (snapshots hold both)."""

    model_config = _HIDDEN
    connection_id: str
    objects: list[ObjectRef] = Field(min_length=1, max_length=50)
    selection: Selection = Field(default_factory=Selection)
    sample_rows: int | None = Field(default=None, ge=50, le=50_000)
    policy: str | dict[str, Any] | None = None
    mode: Literal["obfuscate", "synthesize"] = "obfuscate"

    @model_validator(mode="after")
    def _selection_single_object(self) -> DiscoverSpec:
        if self.selection != Selection() and len(self.objects) != 1:
            raise ValueError("a selection is allowed only when discovering one object")
        return self


#: A path glob of a scan scope (``/apply/*``); checked again by ``urls.validate_glob``.
PATH_GLOB = r"^/[A-Za-z0-9._~\-/*{}]*$"
#: A ``secret_ref`` (``DPS_SECRET_DIR/<ref>/…``); ``..`` segments are refused by the validators.
SECRET_REF = r"^[a-z0-9][a-z0-9/_.-]{0,200}$"
_SCAN = ConfigDict(extra="forbid", hide_input_in_errors=True)
PathGlob = Annotated[str, Field(pattern=PATH_GLOB, max_length=200)]


class ScanScope(BaseModel):
    """Where a scan may go (contract §9; M4 adds ``iframes``, ``shadow_dom``, ``spa_clicks``)."""

    model_config = _SCAN
    same_origin: bool = True
    max_pages: int | None = Field(default=None, ge=1)  # None -> scan_max_pages; > cap -> 422
    max_depth: int | None = Field(default=None, ge=0, le=5)  # None -> scan_max_depth
    allow: list[PathGlob] = Field(default_factory=list, max_length=50)
    deny: list[PathGlob] = Field(default_factory=list, max_length=100)
    iframes: bool = True  # same-origin iframes (mockup 12 switch)
    shadow_dom: bool = True  # open shadow roots (mockup 12 switch)
    spa_clicks: bool = True  # click in-scope links that have no plain href


class ScanAuth(BaseModel):
    """A logged-in session for the scan: a mounted ``storage_state.json`` reference (resolved under
    the tenant's secrets) or, in dev only, an inline storage state that is sealed into ``key_enc``
    and never stored in the spec row."""

    model_config = _SCAN
    storage_state_ref: str | None = Field(default=None, pattern=SECRET_REF)
    storage_state: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> ScanAuth:
        if self.storage_state_ref is not None and self.storage_state is not None:
            raise ValueError("give storage_state_ref or storage_state, not both")
        if self.storage_state_ref is not None and ".." in self.storage_state_ref.split("/"):
            raise ValueError("storage_state_ref must not contain '..'")
        return self


class FillSpec(BaseModel):
    """How forms are filled in the parallel view (never submitted)."""

    model_config = _SCAN
    personas: int = Field(default=1, ge=1, le=50)
    locale: str = "en_US"  # one of SUPPORTED_LOCALES
    display_persona: int = Field(default=1, ge=1)  # screenshots + dps_scan_field.fill_* use it

    @model_validator(mode="after")
    def _rules(self) -> FillSpec:
        if self.locale not in SUPPORTED_LOCALES:
            raise ValueError(f"fill.locale must be one of {list(SUPPORTED_LOCALES)}")
        if self.display_persona > self.personas:
            raise ValueError("fill.display_persona must not exceed fill.personas")
        return self


class CaptureSpec(BaseModel):
    """Screenshots on or off. ``har=true`` is refused at accept time (a HAR holds bodies)."""

    model_config = _SCAN
    screenshots: bool = True
    har: bool = False


class ScanSpec(BaseModel):
    """``POST /api/v1/scans`` (contract §9). There is no submit field anywhere in this model, and
    extra keys are refused at every level (decision D4).

    Both filled screens are produced whenever ``parallel_view`` is true, so every scan generates
    (or accepts) **both** a key and a synthesis seed and returns both once; ``mode`` selects how
    obfuscated captures replace rendered data (tokens with a reversible map for ``obfuscate``,
    persona surrogates for ``synthesize``), the parallel view's default side and the evidence
    label.
    """

    model_config = _SCAN
    url: str = Field(min_length=8, max_length=2048)
    scope: ScanScope = Field(default_factory=ScanScope)
    auth: ScanAuth = Field(default_factory=ScanAuth)
    mode: Literal["obfuscate", "synthesize"] = "synthesize"
    fill: FillSpec = Field(default_factory=FillSpec)
    parallel_view: bool = True
    capture: CaptureSpec = Field(default_factory=CaptureSpec)
    consistency: ConsistencySpec = Field(default_factory=ConsistencySpec)
    policy: str | dict[str, Any] | None = None  # None -> settings.default_policy
    purpose: Purpose | None = None


class RetentionSpec(BaseModel):
    """Retention job spec."""

    model_config = _HIDDEN
    dry_run: bool = False


class JobSecret(BaseModel):
    """What ``key_enc`` seals (AAD ``dps_job.key:<id>``); purged at the job's terminal state."""

    model_config = _HIDDEN
    key_material: dict[str, Any] | None = None
    synthesis_seed: dict[str, Any] | None = None
    scan_auth: dict[str, Any] | None = None
    tabular_model: str | None = None
    scan_start_url: str | None = (
        None  # M4: the raw start URL (may carry a query); purged with the key
    )


class SealedSelection(BaseModel):
    """What ``spec_enc`` seals (AAD ``dps_job.spec:<id>``): the full selection, kept for retries."""

    model_config = _HIDDEN
    where: str | None = None
    query: str | None = None
    params: dict[str, JsonScalar] = Field(default_factory=dict)
    mongo_filter: dict[str, Any] | None = None


class JobProgress(BaseModel):
    """``dps_job.progress`` (contract §3.3) — value-free, read by the console."""

    model_config = _HIDDEN
    outcome: Literal["", "succeeded", "failed", "canceled", "dry_run"] = ""
    stage: str = ""
    rows_estimated: int | None = None
    rows_read: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    partitions_total: int = 0
    partitions_done: int = 0
    partitions_failed: int = 0
    rows_per_second: float | None = None
    throughput: list[tuple[str, float]] = Field(default_factory=list)
    throughput_mark: list[Any] | None = None  # [iso ts, rows_written] of the last sample
    values_by_method: dict[str, int] = Field(default_factory=dict)
    verification: dict[str, int] = Field(default_factory=dict)
    snapshot_ids: list[str] = Field(default_factory=list)
    evidence_id: str | None = None
    pages: int | None = None
    fields: int | None = None
    blocked_requests: int | None = None


class ColumnClassification(BaseModel):
    """One column of a classification snapshot (contract §5.4); ``shapes`` are value shapes only."""

    model_config = _HIDDEN
    path: str
    name: str
    data_type: str
    kind: str
    is_key: bool
    entity_type: str | None
    confidence: float
    sensitivity: Sensitivity | None
    detectors: list[str]
    value_hit_rate: float
    null_rate: float
    distinct_ratio: float
    shapes: list[str]
    text_column: bool
    anchor: bool
    suggested: MethodSpec
    method: MethodSpec
    override: bool = False
    synthesize_suggested: MethodSpec | None = None
    synthesize_method: MethodSpec | None = None
    type_note: str | None = None


class SnapshotOverride(BaseModel):
    """An owner's change to one column during review."""

    model_config = _HIDDEN
    path: str
    entity_type: str | None = None
    method: MethodSpec | None = None
    synthesize_method: MethodSpec | None = None
    text_column: bool | None = None
    anchor: bool | None = None


class ClassificationSnapshot(BaseModel):
    """A ``dps_snapshot`` row (contract §5.4)."""

    model_config = _HIDDEN
    id: str
    tenant_id: str
    job_id: str | None
    connection_id: str
    engine: str
    object_ref: str
    status: Literal["proposed", "accepted", "superseded"]
    policy_name: str
    policy_hash: str
    shape: TableShape
    columns: list[ColumnClassification]
    sample_rows: int
    overrides: list[SnapshotOverride]
    content_hash: str
    created_at: datetime
    accepted_at: datetime | None
    accepted_by: str | None


def redact_spec(spec: RunSpec | DiscoverSpec, *, engine: Engine) -> dict[str, Any]:
    """Value-free copy for ``dps_job.spec`` (contract §3.3).

    Selection text becomes a template (literal values → ``:lN``), every bound parameter becomes
    ``"<bound>"``, a Mongo filter keeps its structure with ``"?"`` for values, and consistency keeps
    identifiers only (``kid``, a seed fingerprint, flags) — never key material, a seed or a wrap
    key. The full selection travels sealed in ``spec_enc`` (:class:`SealedSelection`).
    """
    from dps.layers.connectors.selection import (
        mongo_filter_template,
        query_template,
        where_template,
    )
    from dps.secrets import seed_fingerprint

    data = spec.model_dump(mode="json")
    sel = (data["source"] if isinstance(spec, RunSpec) else data)["selection"]
    if sel.get("where"):
        sel["where"] = where_template(sel["where"], engine)
    if sel.get("query"):
        sel["query"] = query_template(sel["query"], engine)
    if sel.get("mongo_filter") is not None:
        sel["mongo_filter"] = mongo_filter_template(sel["mongo_filter"])
    sel["params"] = {name: "<bound>" for name in sel.get("params") or {}}
    if isinstance(spec, RunSpec):
        cons = data["consistency"]
        km = cons.pop("key_material", None)
        seed = cons.pop("synthesis_seed", None)
        wrap = cons.pop("wrap_key", None)
        cons["kid"] = km.get("kid") if isinstance(km, dict) else None
        cons["caller_key"] = km is not None
        seed_b64 = seed.get("seed") if isinstance(seed, dict) else None
        cons["seed_fingerprint"] = seed_fingerprint(seed_b64) if seed_b64 else None
        cons["wrapped"] = wrap is not None
    return data
