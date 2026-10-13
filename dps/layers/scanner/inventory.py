"""Risk flags of a scanned page (spec §8 "Risk flags"), the value-free field records the crawler
stores, and (Task 16) the inventory, the exports and the scan evidence pack.

Every flag is value-free: field numbers, entity types and query key names that are plain names
(:func:`dps.layers.scanner.urls.safe_key`) — never a value, a label or a URL. Each rule yields at
most one flag per kind per page.

The exports (``dps.scan-export.v1``) carry synthetic persona values and tokens of them only; the
inventory and the evidence pack are value-free.

Nothing here imports Playwright or Pillow (the API imports this module).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from dps import __version__
from dps.config import Settings
from dps.engine.detect.ensemble import Detector
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import seed_bytes
from dps.engine.taxonomy import Sensitivity, entity_info
from dps.engine.transform.methods import METHODS
from dps.jobs import Job
from dps.jobs_models import Purpose
from dps.layers.connectors.evidence import LABEL_OBFUSCATE, LABEL_SYNTHESIZE, EvidencePack
from dps.layers.scanner.classify import (
    CREDENTIAL_ENTITIES,
    autocomplete_token,
    key_candidates,
    placeholder_shape,
    redact_text,
)
from dps.layers.scanner.models import (
    FieldClassification,
    FieldFacts,
    FieldRecord,
    FormFacts,
    InventoryRow,
    NumberedRiskFlag,
    RenderedFinding,
    RiskFlag,
    RiskFlagKind,
    ScanFieldOut,
    ScanPageOut,
    ValidationMessage,
)
from dps.layers.scanner.personas_fill import (
    ATTR_ENTITY,
    FieldPlan,
    FillResult,
    LeakageError,
    PersonaFill,
    apply_format,
    tokens_for,
    validation_records,
)

if TYPE_CHECKING:
    from dps.layers.scanner.runtime import RunOutcome, ScanRuntime
from dps.layers.scanner.urls import safe_key

#: Numbering order inside a page (``A1``, ``A2``… in this order, page by page).
KIND_ORDER: tuple[RiskFlagKind, ...] = (
    "autocomplete_enabled_sensitive",
    "password_without_autocomplete_token",
    "sensitive_form_uses_get",
    "pii_in_query_string",
    "pii_rendered_unmasked",
)
#: Inputs a person types text into (autofill applies to them).
TEXT_ENTRY_TYPES = frozenset(
    {
        "text",
        "email",
        "tel",
        "number",
        "search",
        "url",
        "date",
        "month",
        "week",
        "time",
        "datetime-local",
        "textarea",
    }
)
_PASSWORD_TOKENS = frozenset({"new-password", "current-password"})
#: Structural score a query key needs before a non-empty value under it counts as personal.
QUERY_KEY_FLOOR = 0.7


def _rank(entity: str | None) -> int:
    if entity is None:
        return -1
    try:
        return entity_info(entity).sensitivity.rank
    except KeyError:
        return -1


def _at_least(entity: str | None, level: Sensitivity) -> bool:
    return _rank(entity) >= level.rank


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def field_risks(
    page_no: int,
    fields: list[tuple[FieldFacts, FieldClassification]],
    forms: list[FormFacts],
) -> list[RiskFlag]:
    """``autocomplete_enabled_sensitive``, ``password_without_autocomplete_token`` and
    ``sensitive_form_uses_get`` for one page's fields (in that order)."""
    flags: list[RiskFlag] = []
    auto = [
        (f, c)
        for f, c in fields
        if f.visible
        and f.input_type in TEXT_ENTRY_TYPES
        and c.entity_type is not None
        and c.entity_type not in CREDENTIAL_ENTITIES
        and _at_least(c.entity_type, Sensitivity.HIGH)
        and autocomplete_token(f.autocomplete)[1] in ("absent", "on")
    ]
    if auto:
        flags.append(
            RiskFlag(
                flag="autocomplete_enabled_sensitive",
                severity="high",
                page_no=page_no,
                field_nos=[f.field_no for f, _ in auto],
                entity_types=_unique(c.entity_type or "" for _, c in auto),
            )
        )
    passwords = [
        f
        for f, _ in fields
        if f.input_type == "password"
        and ((f.autocomplete or "").strip().lower().split() or [""])[-1] not in _PASSWORD_TOKENS
    ]
    if passwords:
        flags.append(
            RiskFlag(
                flag="password_without_autocomplete_token",
                severity="medium",
                page_no=page_no,
                field_nos=[f.field_no for f in passwords],
                entity_types=["PASSWORD"],
            )
        )
    get_forms = {
        (form.frame_index, form.form_index)
        for form in forms
        if form.form_index > 0 and form.method.strip().lower() == "get"
    }
    in_get = [
        (f, c)
        for f, c in fields
        if (f.frame_index, f.form_index) in get_forms
        and f.form_index > 0
        and _at_least(c.entity_type, Sensitivity.MEDIUM)
    ]
    if in_get:
        flags.append(
            RiskFlag(
                flag="sensitive_form_uses_get",
                severity="high",
                page_no=page_no,
                field_nos=[f.field_no for f, _ in in_get],
                entity_types=_unique(c.entity_type or "" for _, c in in_get),
                where="form",
            )
        )
    return flags


def _query_pairs(url: str) -> list[tuple[str, str]]:
    """Query pairs of ``url`` and of its hash route's query (``#/x?k=v``), decoded."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return []
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    fragment = parts.fragment or ""
    if "?" in fragment:
        pairs += parse_qsl(fragment.partition("?")[2], keep_blank_values=True)
    return pairs


def _query_hits(
    url: str, classify_value: Callable[[str], str | None]
) -> list[tuple[str | None, str]]:
    """``(stored key or None, entity)`` for every MEDIUM+ value the query of ``url`` carries."""
    hits: list[tuple[str | None, str]] = []
    for key, value in _query_pairs(url):
        entity: str | None = None
        if value.strip():
            entity = classify_value(value)
            if not _at_least(entity, Sensitivity.MEDIUM):
                cands = key_candidates(key)
                entity = (
                    cands[0].entity_type
                    if cands
                    and cands[0].score >= QUERY_KEY_FLOOR
                    and _at_least(cands[0].entity_type, Sensitivity.MEDIUM)
                    else None
                )
        else:  # a bare key can be the value itself (``?50100234567812``)
            entity = classify_value(key) if key.strip() else None
        if entity is not None and _at_least(entity, Sensitivity.MEDIUM):
            stored = safe_key(key, classify_value)
            hits.append((None if stored == "<k>" else stored, entity))
    return hits


def url_risks(
    page_no: int,
    page_url: str,
    external_links: list[str],
    *,
    classify_value: Callable[[str], str | None],
) -> list[RiskFlag]:
    """``pii_in_query_string`` for the visited URL (``where="url"``) and links to other origins
    (``where="link"``); one flag per page, ``where="url"`` when the page URL is involved."""
    on_page = _query_hits(page_url, classify_value)
    on_links = [hit for link in external_links for hit in _query_hits(link, classify_value)]
    if not on_page and not on_links:
        return []
    hits = on_page + on_links
    return [
        RiskFlag(
            flag="pii_in_query_string",
            severity="high",
            page_no=page_no,
            query_keys=_unique(k for k, _ in hits if k is not None),
            entity_types=_unique(e for _, e in hits),
            where="url" if on_page else "link",
        )
    ]


def rendered_risks(page_no: int, rendered: list[RenderedFinding]) -> list[RiskFlag]:
    """``pii_rendered_unmasked``: HIGH/CRITICAL values rendered as text (masked renderings are
    not findings, so they never flag)."""
    serious = [r for r in rendered if _at_least(r.entity_type, Sensitivity.HIGH)]
    if not serious:
        return []
    critical = any(_at_least(r.entity_type, Sensitivity.CRITICAL) for r in serious)
    return [
        RiskFlag(
            flag="pii_rendered_unmasked",
            severity="high" if critical else "medium",
            page_no=page_no,
            entity_types=_unique(r.entity_type for r in serious),
            where="table" if any(r.in_table for r in serious) else "text",
        )
    ]


def number_flags(pages: list[tuple[int, str, list[RiskFlag]]]) -> list[NumberedRiskFlag]:
    """Number every flag ``A1``… in ``(page_no, kind order)``; ``path`` is the page's redacted
    path, shown next to the code by the console and the evidence pack."""
    order = {kind: n for n, kind in enumerate(KIND_ORDER)}
    rows = [
        (page_no, order[flag.flag], seq, path, flag)
        for page_no, path, flags in pages
        for seq, flag in enumerate(flags)
    ]
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return [
        NumberedRiskFlag(**flag.model_dump(), code=f"A{n}", path=path)
        for n, (_, _, _, path, flag) in enumerate(rows, start=1)
    ]


def risk_codes_by_field(flags: list[RiskFlag]) -> dict[int, list[str]]:
    """field_no -> the kinds of the flags that name it (in flag order, once each)."""
    out: dict[int, list[str]] = {}
    for flag in flags:
        for field_no in flag.field_nos:
            kinds = out.setdefault(field_no, [])
            if flag.flag not in kinds:
                kinds.append(flag.flag)
    return out


# --- field records (what dps_scan_field holds) ---------------------------------------------------
_MODES: tuple[Literal["synthesize", "obfuscate"], ...] = ("synthesize", "obfuscate")


def field_records(
    page_no: int,
    fields: list[FieldFacts],
    classes: dict[str, FieldClassification],
    plans: dict[str, FieldPlan],
    fills: dict[str, PersonaFill],
    results: dict[str, dict[str, FillResult]],
    flags: list[RiskFlag],
    *,
    detector: Detector,
) -> list[FieldRecord]:
    """One value-free :class:`FieldRecord` per extracted field.

    Page text (label, selector, name and id attributes, the autocomplete attribute, the pattern)
    goes through :func:`redact_text`; options are counted, never stored; a pre-filled value is
    reduced to ``prefilled=True``. ``fill_synthesize`` / ``fill_obfuscate`` are what the two fills
    showed (persona values, tokens of them, neutral samples; ``None`` for credentials), the
    validation messages are both modes' verdicts with their text detector-redacted, and
    ``fill_status`` is the plan's status, or ``failed`` when the synthesize fill of the field
    failed. ``fills`` / ``results`` are keyed by mode (a mode that was not filled is absent).
    """
    risk = risk_codes_by_field(flags)
    messages: dict[str, list[ValidationMessage]] = {}
    for mode in _MODES:
        if mode in results:
            for key, recs in validation_records(
                results[mode], mode=mode, detector=detector
            ).items():
                messages.setdefault(key, []).extend(recs)
    syn, obf = fills.get("synthesize"), fills.get("obfuscate")
    out: list[FieldRecord] = []
    for f in fields:
        cls = classes.get(f.key)
        plan = plans.get(f.key)
        status = plan.fill_status if plan is not None else "not_filled"
        synth_result = results.get("synthesize", {}).get(f.key)
        if synth_result is not None and synth_result.status == "failed":
            status = "failed"
        out.append(
            FieldRecord(
                page_no=page_no,
                form_index=f.form_index,
                field_no=f.field_no,
                selector=redact_text(f.selector, detector, limit=200) or "",
                tag=f.tag,
                input_type=f.input_type or None,
                name_attr=redact_text(f.name, detector),
                id_attr=redact_text(f.id, detector),
                label=redact_text(f.label, detector) if f.label else None,
                label_source=f.label_source,
                autocomplete=redact_text(f.autocomplete, detector, limit=64),
                pattern=redact_text(f.pattern, detector, limit=200),
                maxlength=f.maxlength,
                required=f.required,
                options_count=len(f.options) if f.tag == "select" else None,
                entity_type=cls.entity_type if cls is not None else None,
                sensitivity=cls.sensitivity if cls is not None else None,
                confidence=cls.confidence if cls is not None else None,
                detectors=list(cls.detectors) if cls is not None else [],
                evidence=list(cls.evidence) if cls is not None else [],
                fill_synthesize=syn.shown.get(f.key) if syn is not None else None,
                fill_obfuscate=obf.shown.get(f.key) if obf is not None else None,
                validation_messages=messages.get(f.key, []),
                frame_index=f.frame_index,
                in_shadow=f.in_shadow,
                placeholder_shape=placeholder_shape(f.placeholder),
                prefilled=bool(f.value),
                persona_attr=plan.persona_attr if plan is not None else None,
                obfuscate_method=obf.methods.get(f.key) if obf is not None else None,
                fill_format=syn.formats.get(f.key) if syn is not None else None,
                fill_status=status,  # type: ignore[arg-type]
                risk_codes=risk.get(f.field_no, []),
            )
        )
    return out


# --- inventory (value-free) ----------------------------------------------------------------------
def inventory_rows(
    pages: Sequence[ScanPageOut], fields: Sequence[ScanFieldOut]
) -> list[InventoryRow]:
    """One row per field, joined with its page's redacted URL (no fill value in it)."""
    path = {p.page_no: p.url_redacted for p in pages}
    return [
        InventoryRow(
            page_no=f.page_no,
            path=path.get(f.page_no, ""),
            form_index=f.form_index,
            field_no=f.field_no,
            label=f.label,
            tag=f.tag,
            input_type=f.input_type,
            name_attr=f.name_attr,
            autocomplete=f.autocomplete,
            pattern=f.pattern,
            maxlength=f.maxlength,
            required=f.required,
            entity_type=f.entity_type,
            sensitivity=f.sensitivity,
            confidence=f.confidence,
            detectors=list(f.detectors),
            risk_codes=list(f.risk_codes),
            fill_status=f.fill_status,
            persona_attr=f.persona_attr,
            obfuscate_method=f.obfuscate_method,
        )
        for f in sorted(fields, key=lambda x: (x.page_no, x.form_index, x.field_no))
    ]


INVENTORY_HEADER = list(InventoryRow.model_fields)


def inventory_csv(rows: Sequence[InventoryRow]) -> bytes:
    """The inventory as CSV (lists joined with ``;``)."""
    out: list[list[Any]] = []
    for r in rows:
        data = r.model_dump(mode="json")
        out.append(
            [";".join(v) if isinstance(v, list) else v for v in (data[k] for k in INVENTORY_HEADER)]
        )
    return write_csv(INVENTORY_HEADER, out)


def _guard_cell(value: Any) -> str:
    """A CSV cell that a spreadsheet cannot read as a formula; ``+91 98451 20377`` stays as is."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return text
    first = text[0]
    if first in ("=", "@", "\t", "\r"):
        return "'" + text
    if first in ("+", "-") and (len(text) == 1 or not text[1].isdigit()):
        return "'" + text
    return text


def write_csv(header: list[str], rows: Iterable[list[Any]]) -> bytes:
    """UTF-8 CSV with a formula-injection guard on every cell (JSON is the lossless format)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([_guard_cell(h) for h in header])
    for row in rows:
        writer.writerow([_guard_cell(v) for v in row])
    return buf.getvalue().encode("utf-8")


# --- exports (synthetic values and tokens of them only) -------------------------------------------
EXPORT_FORMAT = "dps.scan-export.v1"
EXPORT_LABEL = (
    "Synthetic personas generated by DPS. Obfuscated values are tokens of these personas, never of "
    "page data."
)
CREDENTIAL_NOTE = "credential fields are filled but never exported"
EXPORT_CSV_HEADER = [
    "page_no",
    "path",
    "form_index",
    "field_no",
    "label",
    "entity_type",
    "persona_attr",
    "persona",
    "synthesize_value",
    "obfuscate_value",
    "obfuscate_method",
]
#: Attributes a persona set never carries (filled into pages, never exported).
_SECRET_ATTRS = frozenset({"credential", "otp"})
#: ``obfuscate_method`` values that are not a token of the persona value.
_NOT_TOKENS = frozenset({"keep", "surrogate", "neutral", "credential"})


def seed_fingerprint(runtime: ScanRuntime) -> str:
    """First 16 hex of sha256(raw seed): names the seed, reveals nothing of it."""
    return hashlib.sha256(seed_bytes(runtime.seed)).hexdigest()[:16]


def _is_token(f: ScanFieldOut) -> bool:
    return f.obfuscate_method is not None and f.obfuscate_method not in _NOT_TOKENS


def _synth_value(f: ScanFieldOut, persona: dict[str, str]) -> str | None:
    attr = f.persona_attr
    if attr is None or attr not in persona:
        return f.fill_synthesize  # a neutral value (the display persona's)
    if f.fill_format is None:
        return persona[attr]  # a select or radio: the persona's value is what QA would pick
    try:
        value = apply_format(f.fill_format, persona[attr])
    except ValueError:
        value = None
    return value if value is not None else persona[attr]


def build_export(
    *,
    job_id: str,
    runtime: ScanRuntime,
    pages: Sequence[ScanPageOut],
    fields: Sequence[ScanFieldOut],
    personas: list[dict[str, str]],
    engine: PrivacyEngine,
) -> tuple[bytes, bytes]:
    """The ``dps.scan-export.v1`` JSON and its CSV (one row per field × persona).

    Every persona is re-rendered with each field's stored ``fill_format``; obfuscate values are
    tokens of those persona values under the scan's key (``tokens_for`` per page and persona, as
    the crawler made them, so the display persona's tokens equal ``fill_obfuscate``). Credential
    fields and skipped fields carry no values.
    """
    path = {p.page_no: p.url_redacted for p in pages}
    ordered = sorted(fields, key=lambda x: (x.page_no, x.form_index, x.field_no))
    by_page: dict[int, list[ScanFieldOut]] = {}
    for f in ordered:
        by_page.setdefault(f.page_no, []).append(f)
    rows: list[dict[str, Any]] = []
    csv_rows: list[list[Any]] = []
    for page_fields in by_page.values():
        synth: list[dict[int, str | None]] = []
        tokens: list[dict[int, str] | None] = []
        for n, persona in enumerate(personas, start=1):
            values = {f.field_no: _synth_value(f, persona) for f in page_fields}
            synth.append(values)
            items = {
                str(f.field_no): (
                    f.entity_type or ATTR_ENTITY.get(f.persona_attr or "", "PERSON_NAME"),
                    values[f.field_no] or "",
                )
                for f in page_fields
                if _is_token(f) and values[f.field_no]
            }
            if not items:
                tokens.append({})
                continue
            try:
                result = tokens_for(
                    engine,
                    items,
                    key=runtime.key,
                    policy=runtime.policy,
                    locale=runtime.locale,
                    persona_no=n,
                )
            except LeakageError:
                tokens.append(None)
                continue
            tokens.append({int(k): v for k, v in result.tokens.items()})
        for f in page_fields:
            row: dict[str, Any] = {
                "page_no": f.page_no,
                "path": path.get(f.page_no, ""),
                "form_index": f.form_index,
                "field_no": f.field_no,
                "label": f.label,
                "selector": f.selector,
                "entity_type": f.entity_type,
                "persona_attr": f.persona_attr,
                "fill_format": f.fill_format,
                "obfuscate_method": f.obfuscate_method,
                "fill_status": f.fill_status,
                "values": [],
            }
            credential = f.input_type == "password" or f.persona_attr in _SECRET_ATTRS
            if credential:
                row["note"] = CREDENTIAL_NOTE
            elif f.fill_status not in ("skipped_file", "skipped_hidden", "skipped_readonly") and (
                f.fill_synthesize is not None
            ):
                for n in range(1, len(personas) + 1):
                    value = synth[n - 1][f.field_no]
                    page_tokens = tokens[n - 1]
                    if _is_token(f):
                        obf = page_tokens.get(f.field_no) if page_tokens is not None else None
                    else:
                        obf = value
                    row["values"].append({"persona": n, "synthesize": value, "obfuscate": obf})
                    csv_rows.append(
                        [
                            f.page_no,
                            row["path"],
                            f.form_index,
                            f.field_no,
                            f.label,
                            f.entity_type,
                            f.persona_attr,
                            n,
                            value,
                            obf,
                            f.obfuscate_method,
                        ]
                    )
            rows.append(row)
    doc = {
        "format": EXPORT_FORMAT,
        "scan_id": job_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": runtime.spec.mode,
        "locale": runtime.locale,
        "personas": len(personas),
        "display_persona": runtime.spec.fill.display_persona,
        "label": EXPORT_LABEL,
        "key": {"alg": runtime.key.alg, "kid": runtime.key.kid},
        "seed_fingerprint": seed_fingerprint(runtime),
        "persona_set": [
            {"persona": n, "attributes": {k: v for k, v in p.items() if k not in _SECRET_ATTRS}}
            for n, p in enumerate(personas, start=1)
        ],
        "fields": rows,
    }
    data = json.dumps(doc, ensure_ascii=False, sort_keys=False, indent=1).encode("utf-8")
    return data, write_csv(EXPORT_CSV_HEADER, csv_rows)


# --- the scan evidence pack (value-free) ----------------------------------------------------------
def _canon(doc: Any) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def validation_totals(pages: Sequence[ScanPageOut]) -> dict[str, dict[str, int]]:
    """``{mode: {checked, accepted, rejected}}`` summed over the pages (a failed fill is not
    counted)."""
    out: dict[str, dict[str, int]] = {}
    for p in pages:
        for mode, counts in p.validation.items():
            agg = out.setdefault(mode, {"checked": 0, "accepted": 0, "rejected": 0})
            for key in ("checked", "accepted", "rejected"):
                agg[key] += int(counts.get(key, 0))
    return out


def build_scan_evidence(
    *,
    evidence_id: str,
    job: Job,
    runtime: ScanRuntime | None,
    pages: Sequence[ScanPageOut],
    fields: Sequence[ScanFieldOut],
    counts: dict[str, Any],
    outcome: RunOutcome | None,
    result: str,
    errors: list[dict[str, str]],
    settings: Settings,
    exports: dict[str, str],
    policy: dict[str, str] | None = None,
) -> EvidencePack:
    """The ``dps.evidence.v1`` pack of a scan (contract §7) with its ``scan`` block.

    Built from the job's value-free spec (redacted start URL, origin, scope), the stored rows and
    the run's counters; never from the runtime's raw start URL, key or seed (only the key id and the
    seed's fingerprint). ``submissions`` is 0 by construction (D4); ``non_get_allowed`` is its
    measured counterpart from the request log.
    """
    now = datetime.now(UTC)
    spec = job.spec or {}
    scope = spec.get("scope") or {}
    auth = spec.get("auth") or {}
    cons = spec.get("consistency") or {}
    fill = spec.get("fill") or {}
    if runtime is not None:
        policy = {"name": runtime.policy.name, "hash": runtime.policy.config_hash()}
    policy = policy or {"name": "", "hash": ""}
    flags = number_flags([(p.page_no, p.url_redacted, p.risk_flags) for p in pages])
    by_entity = Counter(f.entity_type for f in fields if f.entity_type)
    tokens = [f for f in fields if _is_token(f) and f.fill_obfuscate]
    captures = Counter(
        p.capture_status for p in pages if p.capture_status in ("captured", "withheld")
    )
    started = job.started_at or job.created_at
    reveal_map: list[dict[str, Any]] = [
        {
            "path": f"field:{f.page_no}/{f.form_index}/{f.field_no}",
            "entity_type": f.entity_type,
            "method": f.obfuscate_method,
            "params": {},
            "anchor_path": None,
            "reveal": METHODS[f.obfuscate_method].reversible
            if f.obfuscate_method in METHODS
            else False,
        }
        for f in tokens
    ]
    scan = {
        "pages": int(counts.get("pages_visited", 0)),
        "forms": int(counts.get("forms", 0)),
        "fields": int(counts.get("fields", 0)),
        "by_entity": dict(sorted(by_entity.items())),
        "risk_flags": [f.model_dump(mode="json") for f in flags],
        "personas": int(fill.get("personas", 1)),
        "display_persona": int(fill.get("display_persona", 1)),
        "submissions": 0,
        "non_get_allowed": int(counts.get("non_get_allowed", 0)),
        "blocked_requests": dict(counts.get("blocked") or {}),
        "navigations": int(counts.get("navigations", 0)),
        "validation": validation_totals(pages),
        "captures": {"captured": captures["captured"], "withheld": captures["withheld"]},
        "spa_routes": outcome.spa_routes
        if outcome is not None
        else sum(1 for p in pages if p.discovered_via == "spa_click"),
        "truncated": outcome.truncated if outcome is not None else None,
        "submit_attempts_neutralised": outcome.submit_attempts_neutralised if outcome else 0,
        "websockets_closed": outcome.websockets_closed if outcome else 0,
        "dialogs_dismissed": outcome.dialogs_dismissed if outcome else 0,
        "popups_closed": outcome.popups_closed if outcome else 0,
        "events_dropped": outcome.events_dropped if outcome else 0,
        "fills_skipped": outcome.fills_skipped if outcome else 0,
    }
    auth_kind = (
        "storage_state_ref"
        if auth.get("storage_state_ref")
        else ("inline" if auth.get("storage_state") else "none")
    )
    purpose = spec.get("purpose") or job.purpose or None
    return EvidencePack(
        id=evidence_id,
        tenant_id=job.tenant_id,
        job_id=job.id,
        job_kind="scan",
        outcome=result,  # type: ignore[arg-type]
        label=LABEL_SYNTHESIZE if job.mode == "synthesize" else LABEL_OBFUSCATE,
        mode=job.mode,
        created_at=now,
        engine_version=__version__,
        image_digest=settings.image_digest or None,
        purpose=Purpose.model_validate(purpose) if purpose else None,
        requested_by=job.created_by,
        source={
            "kind": "ui",
            "url": spec.get("url"),
            "origin": spec.get("url_origin"),
            "scope": {
                k: scope.get(k)
                for k in (
                    "same_origin",
                    "max_pages",
                    "max_depth",
                    "allow",
                    "deny",
                    "iframes",
                    "shadow_dom",
                    "spa_clicks",
                )
            },
            "auth": auth_kind,
        },
        target={"kind": "artefacts", "store": settings.artefact_store, "exports": dict(exports)},
        policy=policy,
        snapshot={},
        consistency={
            "scope": cons.get("scope"),
            "field_scoped": bool(cons.get("field_scoped")),
            "kid": job.kid,
            "seed_fingerprint": seed_fingerprint(runtime) if runtime is not None else None,
            "key_custody": {
                "returned": True,
                "wrapped": bool(cons.get("key_wrapped") or cons.get("wrapped")),
                "purged_at": now.isoformat(),
            },
        },
        columns=[],
        reveal_map=reveal_map,
        counts={
            "pages": int(counts.get("pages_visited", 0)),
            "forms": int(counts.get("forms", 0)),
            "fields": int(counts.get("fields", 0)),
            "personal_fields": int(counts.get("personal", 0)),
        },
        verification={
            "mode": runtime.policy.verify.leakage_check if runtime is not None else "strict",
            "batches": int(counts.get("filled", 0)),
            "values_checked": len(tokens),
            "hits": 0,
            "passed": result != "failed",
        },
        scan=scan,
        timings={
            "started_at": started.isoformat(),
            "finished_at": now.isoformat(),
            "duration_s": round(max(0.0, (now - started).total_seconds()), 3),
        },
        partitions=[],
        errors=list(errors),
        config_hash=hashlib.sha256(
            policy["hash"].encode("utf-8") + hashlib.sha256(_canon(spec)).hexdigest().encode()
        ).hexdigest(),
    )
