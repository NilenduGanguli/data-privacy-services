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
  them or a part of the record's names (a persona drawing the customer's own surname, say). Each
  person name goes with what the layout says about it (a :class:`NameHint`, never read from the
  name): the part of a name its field holds, from the key alone (``first_name``, ``LAST_NAME``,
  ``surname``), so one person's name fields agree; a span's gender, from a title before it.

Reveal hints carry locators, methods, types, the keyed anchor, the key id and the key check value
— never a value or anything computed from a value without the key (spec §5.5). Reveal checks the
presented key against the hints' kid and kcv before it decrypts anything: FF1 and the shape-keeping
methods have no integrity check, so a wrong key would otherwise "reveal" plausible false values.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import hmac
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dps.engine.detect.names import title_gender
from dps.engine.findings import Plan, PlanEntry
from dps.engine.jsonpath import flatten, get_path, set_path
from dps.engine.policy import Policy
from dps.engine.transform import crypto
from dps.engine.transform.crypto import KeyMaterial
from dps.engine.transform.methods import (
    CipherFactory,
    ExternalKey,
    MethodError,
    NameHint,
    SurrogateProvider,
    TokenNotBound,
    TransformContext,
    declare_record,
    external_key,
    external_tag,
    name_field,
    reverse_method,
    run_method,
)
from dps.engine.transform.tokens import ENVELOPE_RE, make_envelope, parse_envelopes
from dps.errors import CodedError

if TYPE_CHECKING:  # the adapter module imports settings and httpx; apply needs only the protocol
    from dps.engine.transform.external import ExternalTokenizer

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
_NAME = "PERSON_NAME"

OriginalType = Literal["str", "int", "float"]


_ENTITY_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


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

    @model_validator(mode="after")
    def _external_entity_type(self) -> RevealHint:
        # F-22: an external token's entity type is an identifier — a hint cannot claim "ACCT|p"
        # to re-split what a v1 bind tag binds, and the detokenize audit names a real type. The
        # message never quotes the value.
        if self.method == "external_tokenize" and not _ENTITY_TYPE.fullmatch(self.entity_type):
            raise ValueError("entity types must match ^[A-Z][A-Z0-9_]{0,63}$")
        return self


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
    #: The record's raw anchor when the caller computes it (L2: one per row, contract §6.2);
    #: ``None`` → :func:`anchor_for`. Either way it is keyed before use.
    anchor: str | None = None
    #: M5 ``external_tokenize``: the bank tokenization adapter (``None`` = not configured) and
    #: tokens the caller already fetched for this batch (``(entity_type, value) → token``).
    tokenizer: ExternalTokenizer | None = None
    external_cache: dict[ExternalKey, str] | None = field(default=None, repr=False)
    #: SEC-4: the server-held binding keys (current first; :func:`~dps.engine.transform.
    #: external.binding_keys`) and the audit hook called once per detokenization batch,
    #: ``(entity_type, profile, count)``, **before** the service is called (it may raise).
    binding_keys: tuple[bytes, ...] = field(default=(), repr=False)  # S2: never in a repr
    on_detokenize: Callable[[str, str | None, int], None] | None = None
    #: F-11: may this batch be tokenized — ``(entity_type, profile) -> bool`` (``None`` = no
    #: allow-list) — and the audit hook called once per tokenization batch, ``(entity_type,
    #: profile, count)``, **before** the service is called (it may raise).
    tokenize_allowed: Callable[[str, str | None], bool] | None = None
    on_tokenize: Callable[[str, str | None, int], None] | None = None


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


def _ctx(
    entry: PlanEntry,
    options: ApplyOptions,
    anchor: str,
    mode: str,
    external: Mapping[ExternalKey, str] | None = None,
) -> TransformContext:
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
        external=external,
        binding=options.binding_keys[0] if options.binding_keys else None,
        name_hint=_name_hint(entry),
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


def _name_hint(entry: PlanEntry, value: str | None = None) -> NameHint | None:
    """What the layout says about a person name: the part of a name a whole value's field holds
    (from its key alone), or the gender of a title right before a span of ``value``."""
    if entry.finding.entity_type != _NAME:
        return None
    loc = entry.finding.locator
    if loc.whole:
        return name_field(loc.path)
    gender = title_gender(value, loc.start or 0) if value is not None else None
    return NameHint(gender=gender) if gender is not None else None


def _record_originals(
    document: Any, plan: Plan
) -> tuple[list[tuple[str, str]], list[NameHint | None]]:
    """The record's detected values as ``(entity_type, original)``, read from the input (they
    are handed to the surrogate engine only, never logged or returned), and each one's
    :func:`_name_hint`."""
    originals: list[tuple[str, str]] = []
    hints: list[NameHint | None] = []
    for entry in plan.entries:
        loc = entry.finding.locator
        try:
            value = str(_read(document, loc.path))
        except MethodError:
            continue  # the transform loop below reports a plan that does not fit
        original = value if loc.whole else value[loc.start or 0 : loc.end]
        originals.append((entry.finding.entity_type, original))
        hints.append(_name_hint(entry, value))
    return originals, hints


def _tokenize_prepass(
    plan: Plan, document: Any, options: ApplyOptions
) -> dict[ExternalKey, str] | None:
    """One tokenization call per (entity type, profile) for the values the batch cache lacks;
    ``None`` when no adapter is configured (``external_tokenize`` then fails closed)."""
    if options.tokenizer is None:
        return None
    cache = dict(options.external_cache or {})
    groups: dict[tuple[str, str | None], set[str]] = {}
    for entry in plan.entries:
        if entry.method != "external_tokenize":
            continue
        loc = entry.finding.locator
        try:
            current = str(_read(document, loc.path))
        except MethodError:
            continue  # the transform loop reports a plan that does not fit
        value = current if loc.whole else current[loc.start or 0 : loc.end]
        profile = entry.params.get("profile")
        if external_key(entry.finding.entity_type, profile, value) not in cache:  # F-19
            key = (entry.finding.entity_type, str(profile) if profile is not None else None)
            groups.setdefault(key, set()).add(value)
    for entity, profile in groups:  # every batch is checked before the first call (F-11)
        if options.tokenize_allowed is not None and not options.tokenize_allowed(entity, profile):
            raise CodedError(
                "tokenizer_profile_not_allowed",
                "external_tokenize: this entity type and profile are not allowed for the tenant",
                status=403,
            )
    for (entity, profile), values in groups.items():
        ordered = sorted(values)
        if options.on_tokenize is not None:
            options.on_tokenize(entity, profile, len(ordered))
        tokens = options.tokenizer.tokenize(entity, ordered, profile=profile)
        cache.update(
            {external_key(entity, profile, v): t for v, t in zip(ordered, tokens, strict=True)}
        )
    return cache


def _is_bound(h: RevealHint, token: str, options: ApplyOptions) -> bool:
    """SEC-4: the hint's ``bind`` tag equals the tag of ``token`` under the presented key and one
    of the server's binding keys (constant-time comparison)."""
    tag = h.reverse_params.get("bind")
    if not isinstance(tag, str) or not tag.isascii():
        return False
    version = h.reverse_params.get("bind_v", 1)  # F-22: hints issued before v2 carry no version
    if version not in (1, 2) or isinstance(version, bool):
        return False
    ctx = _reveal_ctx(h, options)
    return any(
        hmac.compare_digest(
            external_tag(ctx, token, binding, version=version).encode(), tag.encode()
        )
        for binding in options.binding_keys
    )


def _detokenize_prepass(
    output: Any, hints: list[RevealHint], options: ApplyOptions
) -> dict[ExternalKey, str] | None:
    """One detokenization call per (entity type, profile) for the hints' external tokens.

    Every external hint's ``bind`` tag is checked first (SEC-4): one token that DPS did not
    produce under this key refuses the whole request before the service is called. Each batch is
    handed to ``options.on_detokenize`` (the audit hook) before its call.

    Raises:
        TokenNotBound: a hint lacks a valid ``bind`` tag for its token (value-free).
    """
    external = [h for h in hints if h.method == "external_tokenize"]
    if not external:
        return None
    groups: dict[tuple[str, str | None], set[str]] = {}
    for h in external:
        try:
            current = str(_read(output, h.path))
        except MethodError:
            continue  # the reveal loop reports a hint that does not fit
        token = current if h.start is None else current[h.start : h.end]
        if not _is_bound(h, token, options):
            raise TokenNotBound()
        profile = h.params.get("profile")
        key = (h.entity_type, str(profile) if profile is not None else None)
        groups.setdefault(key, set()).add(token)
    if options.tokenizer is None:
        return None
    cache: dict[ExternalKey, str] = {}
    for (entity, profile), tokens in groups.items():
        ordered = sorted(tokens)
        if options.on_detokenize is not None:
            options.on_detokenize(entity, profile, len(ordered))
        values = options.tokenizer.detokenize(entity, ordered, profile=profile)
        cache.update(
            {external_key(entity, profile, t): v for t, v in zip(ordered, values, strict=True)}
        )
    return cache


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
    anchor = _keyed_anchor(options.anchor or anchor_for(document, plan), options.key)
    external = _tokenize_prepass(plan, document, options)
    if options.surrogates is not None:
        declare_record(options.surrogates, anchor, *_record_originals(document, plan))
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
                entry.method, str(current), _ctx(entry, options, anchor, plan.mode, external)
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
                ctx = _ctx(entry, options, anchor, plan.mode, external)
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


def _reveal_ctx(
    hint: RevealHint,
    options: ApplyOptions,
    external: Mapping[ExternalKey, str] | None = None,
) -> TransformContext:
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
        external=external,
        binding=options.binding_keys[0] if options.binding_keys else None,
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
    external = _detokenize_prepass(document, hints, options)  # before any hint rewrites it
    whole = [h for h in hints if h.start is None]
    spans = sorted(
        (h for h in hints if h.start is not None), key=lambda h: (h.path, -(h.start or 0))
    )
    for h in whole:
        token = _read(document, h.path)
        if not (isinstance(token, str) or _is_number(token)):
            raise MethodError("reveal hint does not point at a token")
        plain = reverse_method(
            h.method, str(token), _reveal_ctx(h, options, external), h.reverse_params
        )
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
        plain = reverse_method(h.method, token, _reveal_ctx(h, options, external), h.reverse_params)
        new_text = text[:start] + plain + text[h.end :]
        document = _write(document, h.path, _typed(new_text, h.original_type, exact=True))
    return _walk_envelopes(document, options, strict=not hints)
