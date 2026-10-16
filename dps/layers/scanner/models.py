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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from dps.config import Settings
from dps.engine.synth.personas import SUPPORTED_LOCALES
from dps.engine.synth.surrogates import SynthesisSeed
from dps.engine.transform.crypto import KeyMaterial
from dps.jobs import Job
from dps.jobs_models import ScanSpec, policy_label
from dps.layers.scanner.urls import (
    Origin,
    UrlError,
    canonical,
    template_is_value_free,
    url_path_verdict,
    validate_glob,
)
from dps.secrets import SecretRefRefused, check_secret_ref

_HIDDEN = ConfigDict(hide_input_in_errors=True)
_STRICT = ConfigDict(extra="forbid", hide_input_in_errors=True)


def _page(default: Any = ...) -> Any:
    """A field that may hold page data: kept in memory, excluded from ``repr``/``str`` (so it
    never reaches a traceback or a log line)."""
    return Field(default=default, repr=False)


class Rect(BaseModel):
    """A box in document coordinates of the top-level page: those of the full-page screenshot,
    whose origin is the top-left corner of the document's scrollable area (not the scroll origin,
    which is the right edge of an RTL document wider than the viewport; ``dom.viewport_box``)."""

    model_config = _HIDDEN
    x: float
    y: float
    w: float
    h: float


# --- DOM facts: page data, worker memory only (never logged, never persisted as such) ---------
class OptionFacts(BaseModel):
    """One ``<option>`` of a select. ``label`` is the option's ``label`` IDL attribute (its label
    attribute when present, else its text); what a select draws for it is ``drawn``."""

    model_config = _HIDDEN
    value: str = _page()
    text: str = _page()
    label: str = _page("")
    disabled: bool
    selected: bool

    @property
    def drawn(self) -> str:
        """What a select draws for this option, HTML's "label of an option": its label attribute
        when that is not empty (S2 captures-privacy r2 B3), else the option's text — an empty
        ``label=""`` draws the text (S1-M4 captures-privacy r1 B1)."""
        return self.label or self.text


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
    options: list[OptionFacts] = Field(default_factory=list, repr=False)  # the first 300
    #: A select: what its box draws, as the extraction read it (S2 captures-privacy r3 B1) — a
    #: customizable select's button text first (its ``<selectedcontent>`` clone of the selected
    #: option's content, or the author's own text), then the label (``OptionFacts.drawn``) of every
    #: selected option, whatever its value and wherever it sits (``options`` stops at 300); a
    #: listbox: the label of every option, selected or not (S1-M4 captures-privacy r1 B3).
    #: ``None``: not read by an extraction (facts built in memory); ``classify.shown_texts`` then
    #: reads ``options``.
    drawn: list[str] | None = _page(None)
    #: A customizable select (``appearance: base-select``, or a ``<button>`` / ``<selectedcontent>``
    #: in it): it draws author content, so a capture boxes it whenever it draws text.
    custom_select: bool = False
    #: A listbox (``size > 1`` or ``multiple``): it draws every option in its box, selected or not
    #: (S1-M4 captures-privacy r1 B3).
    listbox: bool = False
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
    """A frame of the page. Only same-origin frames are read (``read``); every other rendered child
    frame is listed too, with ``read=False``, because its content is in a screenshot but in no
    finding.

    ``offset`` is, in document coordinates of the top page (zero-size: not rendered), the box a
    read frame's document is drawn in — the top page's viewport (``dom.viewport_box``) for the main
    frame, a child frame's content box, where the child's viewport coordinates start (S2
    captures-privacy r1 B1) — and an unread frame's border box. It is ``None`` only for
    an unread frame whose box could not be determined. A capture must black out every unread
    frame's box, and is withheld when one is ``None`` or a frame failed while it was read
    (``PageSnapshot.frames_failed``; ``dom.unread_frame_boxes``).
    """

    model_config = _HIDDEN
    frame_index: int
    same_origin: bool
    offset: Rect | None
    path: str = _page()
    read: bool = True


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
    #: the extraction did not read everything the page renders: more than ``max_fields`` fields,
    #: more than 5000 text nodes, a text node longer than ``dom.TEXT_READ_CAP`` (read up to it) or
    #: more text than ``dom.TEXT_READ_BUDGET`` — no capture of this page may be emitted
    truncated: bool = False
    #: same-origin child frames that failed while they were read (detached or navigated): what
    #: they rendered is in no finding, so no masked capture of this page may be emitted
    frames_failed: list[int] = Field(default_factory=list)
    #: the live identity of the extraction (``dom.Extraction``: the Frame objects read and the
    #: token stamped into each of them) — memory only, never serialised, copied by ``model_copy``
    _extraction: Any = PrivateAttr(default=None)

    @property
    def unread_frames(self) -> list[FrameFacts]:
        """Rendered child frames whose content was not read (cross-origin, iframes off, or failed)."""
        return [f for f in self.frames if not f.read]

    @property
    def extraction(self) -> Any:
        """The live identity of the extraction (``dom.Extraction``), ``None`` for a snapshot that
        was not produced by ``dom.extract_page`` (no capture of it may be emitted)."""
        return self._extraction

    def bind_extraction(self, extraction: Any) -> None:
        """Attach the live identity of the extraction (``dom.extract_page`` only)."""
        self._extraction = extraction


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

    @field_validator("template")
    @classmethod
    def _value_free_template(cls, value: str) -> str:
        # the column is value-free by design: only urls.template() output may land in it
        if not template_is_value_free(value):
            raise ValueError("template must be the value-free urls.template() form")
        return value


class RequestCount(BaseModel):
    """A ``dps_scan_request`` row: guard decisions counted per page, method, type, reason, host."""

    model_config = _HIDDEN
    page_no: int | None
    method: str
    resource_type: str | None
    decision: Literal["allowed", "blocked"]
    reason: str
    host: str
    count: int = Field(ge=1)  # 0 or less would cancel other rows in summary_counts()


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


def validate_scan(
    spec: ScanSpec, *, settings: Settings, hosts: frozenset[str], tenant_id: str
) -> ValidatedScan:
    """Check ``spec`` against the settings, the tenant's registered ``hosts`` and, for a
    ``storage_state_ref``, the tenant's secret folder (SEC-5).

    Raises :class:`ScanSpecError` (422) with ``url_invalid`` (not an http(s) URL, or carrying
    userinfo), ``host_not_registered``, ``scope_invalid`` (more pages than ``scan_max_pages``, a
    depth outside 0..5, a bad glob), ``start_url_denied`` (the start path or its hash route is
    denied, or the path is outside a non-empty allow list), ``inline_auth_forbidden`` (inline storage state where inline secrets are
    not allowed), ``auth_invalid``, ``har_not_supported`` or ``fill_invalid``. The model refuses
    most of these already (``POST /scans`` answers those refusals with the same codes, see
    :func:`spec_refusal_code`); the re-check covers programmatic construction. A
    ``storage_state_ref`` gets a connection ``secret_ref``'s rule for ``tenant_id``
    (:func:`dps.secrets.check_secret_ref`, contract §5.1): 403 ``secret_ref_foreign_tenant`` (its
    first segment names another tenant's folder), 403 ``secret_ref_not_shared`` (a shared folder
    that does not list the tenant), 422 ``secret_ref_invalid`` (a hidden or reserved segment).
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
    if url_path_verdict(start, scope.allow, scope.deny) != "ok":
        raise ScanSpecError("start_url_denied")

    auth = spec.auth
    if auth.storage_state is not None:
        if auth.storage_state_ref is not None:
            raise ScanSpecError("auth_invalid")
        if not settings.inline_secrets_allowed:
            raise ScanSpecError("inline_auth_forbidden")
    if auth.storage_state_ref is not None:
        if ".." in auth.storage_state_ref.split("/"):
            raise ScanSpecError("auth_invalid")
        try:  # SEC-5: the connection rule, for the caller's tenant (no secret is read)
            check_secret_ref(tenant_id, auth.storage_state_ref, settings)
        except SecretRefRefused as exc:
            raise ScanSpecError(exc.code, exc.status) from None
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


#: ``ScanSpec`` sections whose model-level refusals carry a contract §9 code, in the order
#: :func:`validate_scan` checks them (the first refused section names the code).
_SECTION_CODES: tuple[tuple[str, str], ...] = (
    ("url", "url_invalid"),
    ("scope", "scope_invalid"),
    ("auth", "auth_invalid"),
    ("fill", "fill_invalid"),
)


def spec_refusal_code(errors: Sequence[Mapping[str, Any]]) -> str | None:
    """The contract §9 code for a ``POST /scans`` body the ``ScanSpec`` model refused, or ``None``.

    The model refuses most bounds and cross-field rules before :func:`validate_scan` runs (a
    ``max_depth`` above 5, a malformed glob, ``display_persona`` above ``personas``, a ref with
    ``..``…), so the route maps the request's validation errors by location: ``url`` →
    ``url_invalid``, ``scope.*`` → ``scope_invalid``, ``auth.*`` → ``auth_invalid``, ``fill.*`` →
    ``fill_invalid``; with several refused sections, the first in :func:`validate_scan`'s order.
    ``None`` (FastAPI's field list stays) when any error has no code: an unknown key at any level
    (decision D4, ``extra_forbidden``), a refused field of another section, or a body that is not
    a JSON object. Reads only ``type`` and ``loc``, never the input.
    """
    sections: set[str] = set()
    for error in errors:
        loc = tuple(error.get("loc", ()))
        if len(loc) < 2 or loc[0] != "body" or error.get("type") == "extra_forbidden":
            return None
        if loc[1] not in dict(_SECTION_CODES):
            return None
        sections.add(str(loc[1]))
    return next((code for name, code in _SECTION_CODES if name in sections), None)


def redact_scan_spec(v: ValidatedScan, url_redacted: str) -> dict[str, Any]:
    """Value-free copy of the spec for ``dps_job.spec`` (contract §3.3).

    ``url`` becomes the redacted start URL (``url_origin`` is added), the resolved ``max_pages`` /
    ``max_depth`` are recorded, an inline storage state becomes ``{"inline": true}``, and
    ``consistency`` loses ``key_material``, ``synthesis_seed`` and ``wrap_key``; it keeps flags only
    (``key_wrapped``, and ``wrapped`` as M2's ``redact_spec`` names it: the returned key is
    wrapped; ``caller_key`` / ``caller_seed``: the caller supplied them). The raw start URL
    travels sealed in ``JobSecret.scan_start_url``. A policy body (inline, or an ``@name``
    reference resolved at accept) becomes its value-free label (name, version, sha256): the body
    travels sealed in ``spec_enc`` (``dps.layers.scanner.seal_scan_policy``, S3 security review
    F-18).
    """
    data = v.spec.model_dump(mode="json")
    data["url"] = url_redacted
    if isinstance(data.get("policy"), dict):
        data["policy"] = policy_label(data["policy"])
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
    cons["wrapped"] = wrap is not None  # M2's redact_spec name, which its key custody reads
    cons["caller_key"] = key is not None
    cons["caller_seed"] = seed is not None
    return data
