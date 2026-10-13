"""Risk flags of a scanned page (spec §8 "Risk flags"), the value-free field records the crawler
stores; the inventory, the exports and the scan evidence pack.

Every flag is value-free: field numbers, entity types and query key names that are plain names
(:func:`dps.layers.scanner.urls.safe_key`) — never a value, a label or a URL. Each rule yields at
most one flag per kind per page.

Nothing here imports Playwright or Pillow (the API imports this module).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from dps.engine.detect.ensemble import Detector
from dps.engine.taxonomy import Sensitivity, entity_info
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
    NumberedRiskFlag,
    RenderedFinding,
    RiskFlag,
    RiskFlagKind,
    ValidationMessage,
)
from dps.layers.scanner.personas_fill import (
    FieldPlan,
    FillResult,
    PersonaFill,
    validation_records,
)
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
