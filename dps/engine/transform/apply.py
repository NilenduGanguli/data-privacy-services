"""Apply a :class:`Plan` to a JSON document or a text, and reverse it with the key (spec §5.5).

* Whole-value entries replace the leaf (numbers stay numbers when the output is numeric).
* Span entries rewrite slices of a string left to right, tracking the shift so the emitted
  :class:`RevealHint` spans describe the *output*; in ``envelope`` style, ``det_token`` spans are
  wrapped as ``[[PII:TYPE:token]]`` so text can be revealed without hints.
* The record's **anchor** (a hash of its business key when one is found, else of the record) seeds
  date shifting and persona choice, so every field of one record moves together. When key material
  is present the anchor is keyed (HMAC under a subkey) before use, because it travels in the reveal
  hints next to the output and a salt-free hash of a 6-digit customer id is no secret at all.
* Before any field is transformed, the record's detected values are handed to the surrogate
  engine (:func:`~dps.engine.transform.methods.declare_record`), so no surrogate reuses one of
  them or a part of the record's names (a persona drawing the customer's own surname, say).

Reveal hints carry locators, methods, types, the keyed anchor, the key id and the key check value
— never a value or anything computed from a value without the key (spec §5.5). Reveal checks the
presented key against the hints' kid and kcv before it decrypts anything: FF1 and the shape-keeping
methods have no integrity check, so a wrong key would otherwise "reveal" plausible false values.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dps.engine.findings import Plan, PlanEntry
from dps.engine.jsonpath import flatten, get_path, set_path
from dps.engine.policy import Policy
from dps.engine.transform import crypto
from dps.engine.transform.crypto import KeyMaterial
from dps.engine.transform.methods import (
    CipherFactory,
    MethodError,
    SurrogateProvider,
    TransformContext,
    declare_record,
    reverse_method,
    run_method,
)
from dps.engine.transform.tokens import ENVELOPE_RE, make_envelope, parse_envelopes

ANCHOR_ENTITIES = (
    "CUSTOMER_ID",
    "ACCOUNT_NUMBER",
    "EMPLOYEE_ID",
    "CASE_ID",
    "USERNAME",
    "EMAIL",
)
_INT = re.compile(r"-?[0-9]+")
_FLOAT = re.compile(r"-?[0-9]+\.[0-9]+")

OriginalType = Literal["str", "int", "float"]


class RevealHint(BaseModel):
    """Where a reversible replacement sits in the output, and how to undo it. No values.

    ``kid`` names the key the hint was produced under and ``kcv`` is that key's check value
    (:func:`~dps.engine.transform.crypto.key_check_value`). Reveal refuses a key whose kid or
    check value differs, which matters for FPE: a wrong key there decrypts to well-formed,
    plausible values without any error. ``field_scoped`` records whether subkeys were bound to
    the path, so reveal never depends on the policy in force at reveal time.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    path: str
    start: int | None = None
    end: int | None = None
    entity_type: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    reverse_params: dict[str, Any] = Field(default_factory=dict)
    original_type: OriginalType = "str"
    anchor: str = "record"
    kid: str | None = None
    kcv: str | None = None
    field_scoped: bool = False


class OutputSegment(BaseModel):
    """Where one replacement landed. Value-free except ``replacement``, which is already in the
    output (the token, surrogate or mask written there; ``""`` for ``redact``/``null``)."""

    path: str
    start: int | None = None  # input span within the leaf; None = whole value
    end: int | None = None
    out_start: int | None = None  # output span within the same leaf; None = whole value
    out_end: int | None = None
    entity_type: str
    method: str
    replacement: str


class ApplyResult(BaseModel):
    """The transformed document plus what reveal and the report need."""

    output: Any
    reveal_hints: list[RevealHint]
    methods_used: dict[str, int]
    notes: dict[str, int]
    anchor: str
    segments: list[OutputSegment] = Field(default_factory=list)


@dataclass
class ApplyOptions:
    """How to apply a plan: policy, key, surrogate engine, FPE switch, token style, locale."""

    policy: Policy
    key: KeyMaterial | None
    surrogates: SurrogateProvider | None
    fpe_enabled: bool
    cipher_factory: CipherFactory | None = None
    token_style: str = "natural"
    locale: str = "en_US"


def anchor_for(document: Any, plan: Plan) -> str:
    """The record's raw anchor: its business key's value hash, else a hash of the whole record.

    The result is derived from values without a key. Callers use it inside the engine only (the
    surrogate engine HMACs it with the synthesis seed); :func:`apply_plan` keys it before it can
    reach a reveal hint.
    """
    for entity in ANCHOR_ENTITIES:
        for entry in plan.entries:
            finding = entry.finding
            if finding.entity_type == entity and finding.locator.whole and finding.value_hash:
                return f"{entity}:{finding.value_hash}"
    canon = (
        document if isinstance(document, str) else json.dumps(document, sort_keys=True, default=str)
    )
    return "record:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _keyed_anchor(raw: str, key: KeyMaterial | None) -> str:
    if key is None or not key.has_dek():
        return raw  # synthesize mode: no hints are emitted, the anchor never leaves the engine
    return "a_" + crypto.b32(crypto.hmac_bytes(crypto.subkey(key, "anchor"), raw))[:26]


def _ctx(entry: PlanEntry, options: ApplyOptions, anchor: str, mode: str) -> TransformContext:
    return TransformContext(
        entity_type=entry.finding.entity_type,
        path=entry.finding.locator.path,
        mode=mode,
        params=entry.params,
        key=options.key,
        anchor=anchor,
        field_scoped=options.policy.consistency.field_scoped,
        locale=options.locale,
        fpe_enabled=options.fpe_enabled,
        synth=options.surrogates,
        cipher_factory=options.cipher_factory,
    )


def _type_name(value: Any) -> OriginalType:
    if isinstance(value, bool):
        return "str"
    return "int" if isinstance(value, int) else "float" if isinstance(value, float) else "str"


def _typed(value: str | None, original_type: str, *, exact: bool) -> Any:
    """Turn a method's string output back into the leaf's JSON type when it fits.

    With ``exact`` (reversible output) a number is produced only when ``str(number)`` gives the
    same string back, so reveal reads the very token that was emitted: ``"0735"`` stays a string
    rather than becoming ``735`` and losing a digit.
    """
    if value is None:
        return None
    number: int | float
    if original_type == "int" and _INT.fullmatch(value):
        number = int(value)
    elif original_type == "float" and (_FLOAT.fullmatch(value) or _INT.fullmatch(value)):
        number = float(value)
    else:
        return value
    return number if not exact or str(number) == value else value


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _read(document: Any, path: str) -> Any:
    if path == "$":
        return document
    try:
        return get_path(document, path)
    except (KeyError, IndexError, TypeError, ValueError):
        raise MethodError("path is not in the document") from None


def _record_originals(document: Any, plan: Plan) -> list[tuple[str, str]]:
    """The record's detected values as ``(entity_type, original)``, read from the input (they
    are handed to the surrogate engine only, never logged or returned)."""
    originals: list[tuple[str, str]] = []
    for entry in plan.entries:
        loc = entry.finding.locator
        try:
            value = str(_read(document, loc.path))
        except MethodError:
            continue  # the transform loop below reports a plan that does not fit
        original = value if loc.whole else value[loc.start or 0 : loc.end]
        originals.append((entry.finding.entity_type, original))
    return originals


def _write(document: Any, path: str, value: Any) -> Any:
    if path == "$":
        return value
    set_path(document, path, value)
    return document


def apply_plan(
    *, plan: Plan, options: ApplyOptions, data: Any | None = None, text: str | None = None
) -> ApplyResult:
    """Apply ``plan`` to a deep copy of ``data`` (or to ``text``) and return the output with hints.

    Raises:
        ValueError: neither or both of ``data`` and ``text`` given.
        MethodError: a method prerequisite is missing, a method parameter is invalid, or the plan
            does not fit the document (a span outside its value).
    """
    if (data is None) == (text is None):
        raise ValueError("pass exactly one of data or text")
    document: Any = copy.deepcopy(data) if text is None else text
    anchor = _keyed_anchor(anchor_for(document, plan), options.key)
    if options.surrogates is not None:
        declare_record(options.surrogates, anchor, _record_originals(document, plan))
    kid = options.key.kid if options.key is not None else None
    kcv = (
        options.key.kcv or crypto.key_check_value(options.key)
        if options.key is not None and options.key.has_dek()
        else None
    )
    field_scoped = options.policy.consistency.field_scoped
    hints: list[RevealHint] = []
    segments: list[OutputSegment] = []
    used: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    by_path: dict[str, list[PlanEntry]] = {}
    for entry in plan.entries:
        by_path.setdefault(entry.finding.locator.path, []).append(entry)

    def hint(
        entry: PlanEntry,
        outcome_method: str,
        rp: dict[str, Any],
        original_type: OriginalType,
        start: int | None = None,
        end: int | None = None,
    ) -> RevealHint:
        return RevealHint(
            path=entry.finding.locator.path,
            start=start,
            end=end,
            entity_type=entry.finding.entity_type,
            method=outcome_method,
            params=entry.params,
            reverse_params=rp,
            original_type=original_type,
            anchor=anchor,
            kid=kid,
            kcv=kcv,
            field_scoped=field_scoped,
        )

    for path, entries in by_path.items():
        whole = [e for e in entries if e.finding.locator.whole]
        spans = sorted(
            (e for e in entries if not e.finding.locator.whole),
            key=lambda e: e.finding.locator.start or 0,
        )
        current = _read(document, path)
        original_type = _type_name(current)
        if whole:
            if not (isinstance(current, str) or _is_number(current)):
                raise MethodError("plan entry does not point at a scalar value")
            entry = whole[0]
            outcome = run_method(
                entry.method, str(current), _ctx(entry, options, anchor, plan.mode)
            )
            used[outcome.method] += 1
            if outcome.note:
                notes[outcome.note] += 1
            new_value = _typed(outcome.value, original_type, exact=outcome.reversible)
            segments.append(
                OutputSegment(
                    path=path,
                    entity_type=entry.finding.entity_type,
                    method=outcome.method,
                    replacement="" if outcome.value is None else str(outcome.value),
                )
            )
            if outcome.reversible:
                hints.append(hint(entry, outcome.method, outcome.reverse_params, original_type))
        elif spans and (isinstance(current, str) or _is_number(current)):
            source = current if isinstance(current, str) else str(current)
            pieces: list[str] = []
            cursor, delta = 0, 0
            for entry in spans:
                start, end = entry.finding.locator.start or 0, entry.finding.locator.end
                if end is None or not 0 <= start <= end <= len(source):
                    raise MethodError("plan span does not fit the document")
                if start < cursor:
                    continue  # overlapping span already consumed
                ctx = _ctx(entry, options, anchor, plan.mode)
                outcome = run_method(entry.method, source[start:end], ctx)
                used[outcome.method] += 1
                if outcome.note:
                    notes[outcome.note] += 1
                replacement = outcome.value if outcome.value is not None else ""
                if (
                    options.token_style == "envelope"
                    and outcome.reversible
                    and outcome.method == "det_token"
                ):
                    try:
                        replacement = make_envelope(entry.finding.entity_type, replacement)
                    except ValueError:
                        pass  # an entity name the envelope grammar cannot carry: hint only
                pieces.append(source[cursor:start])
                out_start = start + delta
                pieces.append(replacement)
                segments.append(
                    OutputSegment(
                        path=path,
                        start=start,
                        end=end,
                        out_start=out_start,
                        out_end=out_start + len(replacement),
                        entity_type=entry.finding.entity_type,
                        method=outcome.method,
                        replacement=replacement,
                    )
                )
                if outcome.reversible:
                    hints.append(
                        hint(
                            entry,
                            outcome.method,
                            outcome.reverse_params,
                            original_type,
                            out_start,
                            out_start + len(replacement),
                        )
                    )
                delta += len(replacement) - (end - start)
                cursor = end
            pieces.append(source[cursor:])
            new_value = _typed("".join(pieces), original_type, exact=True)
        else:
            continue
        document = _write(document, path, new_value)
    return ApplyResult(
        output=document,
        reveal_hints=hints,
        methods_used=dict(used),
        notes=dict(notes),
        anchor=anchor,
        segments=segments,
    )


def _reveal_ctx(hint: RevealHint, options: ApplyOptions) -> TransformContext:
    return TransformContext(
        entity_type=hint.entity_type,
        path=hint.path,
        mode="reveal",
        params=hint.params,
        key=options.key,
        anchor=hint.anchor,
        field_scoped=hint.field_scoped,
        locale=options.locale,
        fpe_enabled=options.fpe_enabled,
        cipher_factory=options.cipher_factory,
    )


def _reveal_envelopes(text: str, path: str, options: ApplyOptions, *, strict: bool) -> str:
    out: list[str] = []
    cursor = 0
    for start, end, entity, token in parse_envelopes(text):
        ctx = TransformContext(
            entity_type=entity,
            path=path,
            mode="reveal",
            key=options.key,
            field_scoped=options.policy.consistency.field_scoped,
            locale=options.locale,
        )
        try:
            plain = reverse_method("det_token", token, ctx, {})
        except MethodError:
            if strict:
                raise
            continue  # not ours (made under another key): leave it exactly as it is
        out.append(text[cursor:start])
        out.append(plain)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def _walk_envelopes(document: Any, options: ApplyOptions, *, strict: bool) -> Any:
    for path, leaf in flatten(document):
        if isinstance(leaf, str) and "[[PII:" in leaf:
            document = _write(document, path, _reveal_envelopes(leaf, path, options, strict=strict))
    return document


def reveal(*, output: Any, key: KeyMaterial, hints: list[RevealHint], options: ApplyOptions) -> Any:
    """Undo ``apply_plan``: hints first (whole values, then spans right to left), then any
    remaining envelopes anywhere in the document.

    Without hints every envelope must decrypt under ``key`` (a wrong key fails loudly). With
    hints, an envelope that does not authenticate under ``key`` is foreign — for example one
    that was already in the input — and is left as it is.

    Every hint must carry the kid and key check value of the key it was made under, and both
    must match ``key`` (the kcv in constant time) before anything is decrypted.

    Raises:
        MethodError: a hint lacks its kid or kcv, names another key or a key check value that
            ``key`` does not match, points outside the document or at a non-scalar, names an
            irreversible or unknown method, or a token does not decrypt.
    """
    options = dataclasses.replace(options, key=key)
    document = copy.deepcopy(output)
    check_values: set[str] = set()
    for h in hints:
        if h.kid is None or h.kcv is None:
            raise MethodError(
                "reveal hints must carry the kid and kcv of the key they were made under"
            )
        if h.kid != key.kid:
            raise MethodError("reveal hints were produced under a different key (kid mismatch)")
        check_values.add(h.kcv)
    for kcv in check_values:  # one HKDF per distinct value, not per hint
        if not crypto.kcv_matches(key, kcv):
            raise MethodError(
                "the key does not match the key check value in the reveal hints; nothing was revealed"
            )
    whole = [h for h in hints if h.start is None]
    spans = sorted(
        (h for h in hints if h.start is not None), key=lambda h: (h.path, -(h.start or 0))
    )
    for h in whole:
        token = _read(document, h.path)
        if not (isinstance(token, str) or _is_number(token)):
            raise MethodError("reveal hint does not point at a token")
        plain = reverse_method(h.method, str(token), _reveal_ctx(h, options), h.reverse_params)
        document = _write(document, h.path, _typed(plain, h.original_type, exact=True))
    for h in spans:
        value = _read(document, h.path)
        if not (isinstance(value, str) or _is_number(value)):
            raise MethodError("reveal hint does not point at a token")
        text = value if isinstance(value, str) else str(value)
        start = h.start or 0
        if h.end is None or not 0 <= start <= h.end <= len(text):
            raise MethodError("reveal hint span is outside the value")
        segment = text[start : h.end]
        match = ENVELOPE_RE.fullmatch(segment)
        token = match.group(2) if match else segment
        plain = reverse_method(h.method, token, _reveal_ctx(h, options), h.reverse_params)
        new_text = text[:start] + plain + text[h.end :]
        document = _write(document, h.path, _typed(new_text, h.original_type, exact=True))
    return _walk_envelopes(document, options, strict=not hints)
