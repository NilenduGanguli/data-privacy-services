"""UI-scanner models: page facts (memory only), persisted records, API models, accept-time checks.

Three kinds of model live here, and they must never be confused:

* **DOM facts** (:class:`PageSnapshot` and its parts) carry page data — labels, pre-filled values,
  rendered text, links. They exist only in worker memory for one page, are never logged and never
  persisted as such, and their ``repr`` hides every field that can hold page data.
* **Records** (:class:`PageRecord`, :class:`FieldRecord`, :class:`RequestCount`,
  :class:`ScanEvent`) are what ``dps_scan_*`` rows hold: value-free (types, counts, shapes,
  redacted URLs and labels) or synthetic (``fill_*`` = the display persona's values and tokens of
  them).
* **API models** answer the scans routes.

:func:`validate_scan` checks a :class:`~dps.jobs_models.ScanSpec` at accept time (codes only, never
the URL) and :func:`redact_scan_spec` produces the value-free ``dps_job.spec`` (contract §3.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dps.config import Settings
from dps.engine.synth.personas import SUPPORTED_LOCALES
from dps.engine.synth.surrogates import SynthesisSeed
from dps.engine.transform.crypto import KeyMaterial
from dps.jobs import Job
from dps.jobs_models import ScanSpec
from dps.layers.scanner.urls import Origin, UrlError, canonical, path_matches, validate_glob

_HIDDEN = ConfigDict(hide_input_in_errors=True)
_STRICT = ConfigDict(extra="forbid", hide_input_in_errors=True)


def _page(default: Any = ...) -> Any:
    """A field that may hold page data: kept in memory, excluded from ``repr``/``str`` (so it
    never reaches a traceback or a log line)."""
    return Field(default=default, repr=False)


class Rect(BaseModel):
    """A box in document coordinates of the top-level page."""

    model_config = _HIDDEN
    x: float
    y: float
    w: float
    h: float


# --- DOM facts: page data, worker memory only (never logged, never persisted as such) ---------
class OptionFacts(BaseModel):
    """One ``<option>`` of a select."""

    model_config = _HIDDEN
    value: str = _page()
    text: str = _page()
    disabled: bool
    selected: bool


class FieldFacts(BaseModel):
    """One form field as the page shows it (``key`` = ``data-dps-field`` marker)."""

    model_config = _HIDDEN
    key: str  # "<frame_index>:<field_no>"
    field_no: int
    form_index: int
    frame_index: int
    in_shadow: bool
    tag: str
    input_type: str
    name: str | None = _page()
    id: str | None = _page()
    label: str = _page()
    label_source: str
    placeholder: str | None = _page()
    title: str | None = _page()
    aria_label: str | None = _page()
    autocomplete: str | None
    pattern: str | None
    maxlength: int | None
    minlength: int | None
    required: bool
    readonly: bool
    disabled: bool
    visible: bool
    inputmode: str | None
    options: list[OptionFacts] = Field(default_factory=list, repr=False)
    value: str | None = _page(None)  # pre-filled value (never read for password inputs)
    checked: bool | None = None
    rect: Rect | None = None  # document coordinates in the top-level page
    selector: str = _page()  # human-readable locator, redacted before storage


class FormFacts(BaseModel):
    """One ``<form>`` (``form_index`` 0 = fields outside any form)."""

    model_config = _HIDDEN
    form_index: int
    method: str
    action: str = _page()
    frame_index: int
    in_shadow: bool
    field_count: int


class TextFacts(BaseModel):
    """One rendered text node (classified for ``pii_rendered_unmasked`` and captures)."""

    model_config = _HIDDEN
    frame_index: int
    i: int
    text: str = _page()
    in_table: bool
    label_hint: str | None = _page()


class LinkFacts(BaseModel):
    """One ``<a href>``."""

    model_config = _HIDDEN
    href: str = _page()
    in_form: bool
    download: bool


class ClickableFacts(BaseModel):
    """An in-page element that navigates only when clicked (SPA routes)."""

    model_config = _HIDDEN
    frame_index: int
    ordinal: int
    kind: Literal["role_link", "hash_link", "js_link"]


class FrameFacts(BaseModel):
    """A frame of the page (only same-origin frames are read)."""

    model_config = _HIDDEN
    frame_index: int
    same_origin: bool
    offset: Rect
    path: str = _page()


class PageSnapshot(BaseModel):
    """Everything extracted from one page (memory only)."""

    model_config = _HIDDEN
    url: str = _page()
    title: str = _page()
    forms: list[FormFacts]
    fields: list[FieldFacts]
    texts: list[TextFacts]
    links: list[LinkFacts]
    clickables: list[ClickableFacts]
    frames: list[FrameFacts]
    truncated: bool = False


# --- classification -----------------------------------------------------------------------
class FieldClassification(BaseModel):
    """What a field asks for; ``evidence`` is value-free (``autocomplete:cc-number``,
    ``label:aadhaar``, ``pattern:AADHAAR``, ``value:AADHAAR``)."""

    model_config = _HIDDEN
    entity_type: str | None
    sensitivity: str | None
    confidence: float
    detectors: list[str]  # families that fired: token, label, type, pattern, maxlength, value
    evidence: list[str]


class RenderedFinding(BaseModel):
    """A detector hit in rendered text (offsets into the memory-only text, never the text)."""

    model_config = _HIDDEN
    frame_index: int
    i: int
    start: int
    end: int
    entity_type: str
    score: float
    detectors: list[str]
    in_table: bool


RiskFlagKind = Literal[
    "autocomplete_enabled_sensitive",
    "password_without_autocomplete_token",
    "pii_in_query_string",
    "sensitive_form_uses_get",
    "pii_rendered_unmasked",
]


class RiskFlag(BaseModel):
    """A privacy risk found on a page (field numbers, entity types and query keys only)."""

    model_config = _HIDDEN
    flag: RiskFlagKind
    severity: Literal["high", "medium"]
    page_no: int
    field_nos: list[int] = Field(default_factory=list)
    entity_types: list[str] = Field(default_factory=list)
    query_keys: list[str] = Field(default_factory=list)
    where: Literal["table", "text", "url", "link", "form"] | None = None


class ValidationMessage(BaseModel):
    """What the page's own validation said about a filled field (text detector-redacted)."""

    model_config = _HIDDEN
    mode: Literal["synthesize", "obfuscate"]
    source: Literal["native", "aria", "aria-invalid"]
    codes: list[str]
    text: str = Field(max_length=200)


# --- persisted records (value-free; fill_* are synthetic) -------------------------------------
FillStatus = Literal[
    "filled",
    "neutral",
    "not_filled",
    "skipped_file",
    "skipped_hidden",
    "skipped_readonly",
    "failed",
]


class FieldRecord(BaseModel):
    """A ``dps_scan_field`` row."""

    model_config = _HIDDEN
    page_no: int
    form_index: int
    field_no: int
    selector: str
    tag: str
    input_type: str | None
    name_attr: str | None
    id_attr: str | None
    label: str | None
    label_source: str
    autocomplete: str | None
    pattern: str | None
    maxlength: int | None
    required: bool
    options_count: int | None
    entity_type: str | None
    sensitivity: str | None
    confidence: float | None
    detectors: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    fill_synthesize: str | None = None
    fill_obfuscate: str | None = None
    validation_messages: list[ValidationMessage] = Field(default_factory=list)
    frame_index: int = 0
    in_shadow: bool = False
    placeholder_shape: str | None = None
    prefilled: bool = False
    persona_attr: str | None = None
    obfuscate_method: str | None = None
    fill_format: str | None = None
    fill_status: FillStatus = "not_filled"
    risk_codes: list[str] = Field(default_factory=list)


PageStatus = Literal["queued", "visiting", "done", "redirected", "skipped", "failed"]
DiscoveredVia = Literal["start", "link", "spa_click", "redirect"]
CaptureStatus = Literal["captured", "withheld", "not_needed"]


class PageRecord(BaseModel):
    """A ``dps_scan_page`` row."""

    model_config = _HIDDEN
    page_no: int
    url_redacted: str
    title_redacted: str | None = None
    depth: int
    parent_page_no: int | None
    status: PageStatus
    status_reason: str | None = None
    discovered_via: DiscoveredVia
    template: str
    http_status: int | None = None
    final_url_redacted: str | None = None
    form_count: int = 0
    field_count: int = 0
    personal_field_count: int = 0
    critical_field_count: int = 0
    forms: list[dict[str, Any]] = Field(default_factory=list)
    artefacts: dict[str, str] = Field(default_factory=dict)
    rendered_findings: dict[str, int] = Field(default_factory=dict)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    validation: dict[str, dict[str, int]] = Field(default_factory=dict)
    capture_status: CaptureStatus | None = None
    timings: dict[str, int] = Field(default_factory=dict)


class RequestCount(BaseModel):
    """A ``dps_scan_request`` row: guard decisions counted per page, method, type, reason, host."""

    model_config = _HIDDEN
    page_no: int | None
    method: str
    resource_type: str | None
    decision: Literal["allowed", "blocked"]
    reason: str
    host: str
    count: int


EventKind = Literal[
    "navigate", "request", "fill", "capture", "skip", "spa", "popup", "dialog", "error"
]


class ScanEvent(BaseModel):
    """A ``dps_scan_event`` row (``target`` = redacted URL or origin, ``""`` for page events)."""

    model_config = _HIDDEN
    seq: int
    ts: datetime
    kind: EventKind
    method: str | None = None
    result: str
    page_no: int | None = None
    target: str = ""
    note_code: str
    counts: dict[str, int] = Field(default_factory=dict)


# --- API models -------------------------------------------------------------------------
class ScanPageOut(PageRecord):
    """A page as the API answers it."""

    created_at: datetime
    updated_at: datetime


class ScanFieldOut(FieldRecord):
    """A field as the API answers it."""


class NumberedRiskFlag(RiskFlag):
    """A risk flag numbered ``A1``… in (page_no, discovery) order, with the page's redacted path."""

    code: str
    path: str


class ScanSummary(BaseModel):
    """The scan's counters (``non_get_allowed`` is measured from the request log and must be 0)."""

    model_config = _HIDDEN
    pages_by_status: dict[str, int]
    pages_visited: int
    pages_discovered: int
    pages_queued: int
    pages_classified: int
    pages_filled: int
    forms: int
    fields: int
    personal_fields: int
    critical_fields: int
    risk_flags: list[NumberedRiskFlag]
    blocked_requests: dict[str, int]  # by method
    non_get_allowed: int
    navigations: int
    captures: int
    validation: dict[str, dict[str, int]]
    personas: int
    display_persona: int
    mode: str
    locale: str
    export_artefacts: dict[str, str]  # {"json": id, "csv": id} once finalized
    evidence_id: str | None
    elapsed_s: float | None


class ScanCreated(BaseModel):
    """``POST /scans`` answer: the key and the seed are returned once, never stored."""

    model_config = _HIDDEN
    scan: Job
    key_material: KeyMaterial | None = None
    synthesis_seed: SynthesisSeed | None = None
    replay: bool = False


class ScanDetail(BaseModel):
    """``GET /scans/{id}``."""

    model_config = _HIDDEN
    scan: Job
    summary: ScanSummary


class ScanPageDetail(BaseModel):
    """``GET /scans/{id}/pages/{page_no}``."""

    model_config = _HIDDEN
    page: ScanPageOut
    fields: list[ScanFieldOut]
    requests: list[RequestCount]


class PersonaPreviewRequest(BaseModel):
    """``POST /scans/personas/preview``."""

    model_config = _STRICT
    locale: str = "en_US"
    count: int = Field(default=1, ge=1, le=5)
    synthesis_seed: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _locale(self) -> PersonaPreviewRequest:
        if self.locale not in SUPPORTED_LOCALES:
            raise ValueError(f"locale must be one of {list(SUPPORTED_LOCALES)}")
        return self


class PersonaPreview(BaseModel):
    """Synthetic personas for the New-scan page (the seed is the caller's or a fresh one)."""

    model_config = _HIDDEN
    synthesis_seed: SynthesisSeed
    personas: list[dict[str, Any]]
    notes: list[str]


class InventoryRow(BaseModel):
    """One row of the field inventory export (value-free)."""

    model_config = _HIDDEN
    page_no: int
    path: str
    form_index: int
    field_no: int
    label: str | None
    tag: str
    input_type: str | None
    name_attr: str | None
    autocomplete: str | None
    pattern: str | None
    maxlength: int | None
    required: bool
    entity_type: str | None
    sensitivity: str | None
    confidence: float | None
    detectors: list[str]
    risk_codes: list[str]
    fill_status: str
    persona_attr: str | None
    obfuscate_method: str | None


# --- validation of a ScanSpec at accept time --------------------------------------------
class ScanSpecError(ValueError):
    """A scan the service will not run. ``str()`` is the code, never the URL or a value."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ValidatedScan:
    """An accepted scan: the canonical start URL (memory only), its origin and resolved limits."""

    spec: ScanSpec
    start_url: str
    origin: Origin
    max_pages: int
    max_depth: int
    hosts: frozenset[str]

    def __repr__(self) -> str:  # the start URL may carry personal data in its query
        return (
            f"ValidatedScan(origin={self.origin.text()!r}, max_pages={self.max_pages}, "
            f"max_depth={self.max_depth})"
        )


_MAX_DEPTH = 5


def validate_scan(spec: ScanSpec, *, settings: Settings, hosts: frozenset[str]) -> ValidatedScan:
    """Check ``spec`` against the settings and the tenant's registered ``hosts``.

    Raises :class:`ScanSpecError` (422) with ``url_invalid`` (not an http(s) URL, or carrying
    userinfo), ``host_not_registered``, ``scope_invalid`` (more pages than ``scan_max_pages``, a
    depth outside 0..5, a bad glob), ``start_url_denied`` (the start path is denied or outside a
    non-empty allow list), ``inline_auth_forbidden`` (inline storage state where inline secrets are
    not allowed), ``auth_invalid``, ``har_not_supported`` or ``fill_invalid``. The model refuses
    most of these already; the re-check covers programmatic construction.
    """
    raw = spec.url.strip()
    if "@" in urlsplit(raw).netloc:
        raise ScanSpecError("url_invalid")
    start = canonical(raw)
    if start is None:
        raise ScanSpecError("url_invalid")
    try:
        origin = Origin.of(start)
    except UrlError:
        raise ScanSpecError("url_invalid") from None
    allowed = frozenset(h.strip().lower() for h in hosts if h and h.strip())
    if origin.host not in allowed:
        raise ScanSpecError("host_not_registered")

    scope = spec.scope
    max_pages = scope.max_pages if scope.max_pages is not None else settings.scan_max_pages
    if not 1 <= max_pages <= settings.scan_max_pages:
        raise ScanSpecError("scope_invalid")
    max_depth = scope.max_depth if scope.max_depth is not None else settings.scan_max_depth
    if not 0 <= max_depth <= _MAX_DEPTH:
        raise ScanSpecError("scope_invalid")
    try:
        for glob in [*scope.allow, *scope.deny]:
            validate_glob(glob)
    except UrlError:
        raise ScanSpecError("scope_invalid") from None
    path = urlsplit(start).path or "/"
    if path_matches(path, scope.deny) or (scope.allow and not path_matches(path, scope.allow)):
        raise ScanSpecError("start_url_denied")

    auth = spec.auth
    if auth.storage_state is not None:
        if auth.storage_state_ref is not None:
            raise ScanSpecError("auth_invalid")
        if not settings.inline_secrets_allowed:
            raise ScanSpecError("inline_auth_forbidden")
    if auth.storage_state_ref is not None and ".." in auth.storage_state_ref.split("/"):
        raise ScanSpecError("auth_invalid")
    if spec.capture.har:
        raise ScanSpecError("har_not_supported")
    fill = spec.fill
    if (
        not 1 <= fill.personas <= 50
        or not 1 <= fill.display_persona <= fill.personas
        or fill.locale not in SUPPORTED_LOCALES
    ):
        raise ScanSpecError("fill_invalid")
    return ValidatedScan(
        spec=spec,
        start_url=start,
        origin=origin,
        max_pages=max_pages,
        max_depth=max_depth,
        hosts=allowed,
    )


def redact_scan_spec(v: ValidatedScan, url_redacted: str) -> dict[str, Any]:
    """Value-free copy of the spec for ``dps_job.spec`` (contract §3.3).

    ``url`` becomes the redacted start URL (``url_origin`` is added), the resolved ``max_pages`` /
    ``max_depth`` are recorded, an inline storage state becomes ``{"inline": true}``, and
    ``consistency`` loses ``key_material``, ``synthesis_seed`` and ``wrap_key``; it keeps flags only
    (``key_wrapped``: the returned key is wrapped; ``caller_key`` / ``caller_seed``: the caller
    supplied them). The raw start URL travels sealed in ``JobSecret.scan_start_url``.
    """
    data = v.spec.model_dump(mode="json")
    data["url"] = url_redacted
    data["url_origin"] = v.origin.text()
    data["scope"]["max_pages"] = v.max_pages
    data["scope"]["max_depth"] = v.max_depth
    auth = data["auth"]
    if auth.get("storage_state") is not None:
        auth["storage_state"] = {"inline": True}
    cons = data["consistency"]
    key = cons.pop("key_material", None)
    seed = cons.pop("synthesis_seed", None)
    wrap = cons.pop("wrap_key", None)
    cons["key_wrapped"] = wrap is not None
    cons["caller_key"] = key is not None
    cons["caller_seed"] = seed is not None
    return data
