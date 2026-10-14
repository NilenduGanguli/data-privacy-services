"""The method registry (spec §5.5): what each transformation does, and how it is reversed.

``run_method`` applies a method by name with the fallbacks the spec requires (FPE unavailable or
domain too small → ``det_token``, unparseable date → ``det_token``) and returns an
:class:`Outcome` that names the method actually used and the parameters ``reverse_method``
needs later. Methods never log and never raise on bad values — only on missing prerequisites
(no key, no surrogate engine) and invalid method parameters, which are programming or
configuration errors. A :class:`MethodError` message names the method and the rule, never a
value, a key or a token.

Every reversible method reverses *exactly*: ``reverse_method(run_method(v)) == v`` byte for byte.
Where a format cannot be reproduced exactly (a date that is not zero-padded, a shift past year
9999), the method falls back to ``det_token`` and says so in the outcome note.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import phonenumbers
from cryptography.exceptions import InvalidTag

from dps.engine.policy import METHOD_NAMES
from dps.engine.transform import crypto, fpe
from dps.engine.transform.crypto import KeyMaterial


class SurrogateProvider(Protocol):
    """Produces a synthetic stand-in for one value (implemented by the synthesis engine)."""

    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str: ...


@runtime_checkable
class RecordAwareSurrogates(Protocol):
    """A provider that takes the record's detected values first, so no surrogate reuses one of
    them or a part of the record's names."""

    def declare_record(self, anchor: str, originals: Iterable[tuple[str, str]]) -> None: ...


def declare_record(
    provider: SurrogateProvider | None, anchor: str, originals: Iterable[tuple[str, str]]
) -> None:
    """Hand the record's ``(entity_type, original)`` pairs to ``provider`` before any of its
    fields is synthesised. A provider without ``declare_record`` (a test double) is left alone.
    """
    if isinstance(provider, RecordAwareSurrogates):
        provider.declare_record(anchor, originals)


CipherFactory = Callable[[bytes, str], fpe.Cipher]


class MethodError(ValueError):
    """A method cannot run in this context (no key, no engine, bad params, not reversible)."""


class TokenNotBound(MethodError):
    """SEC-4: an external token's reveal hint does not prove that DPS produced it under the
    presented key (no, a malformed or a mismatched ``bind`` tag). Raised before the tokenization
    service is called; value-free."""

    code = "external_token_not_bound"

    def __init__(self) -> None:
        super().__init__(
            "external_token_not_bound: an external token is not bound to this key; "
            "nothing was revealed"
        )


@dataclass
class TransformContext:
    """Everything a method needs besides the value: type, location, params, key, anchor."""

    entity_type: str
    path: str
    mode: str
    params: dict[str, Any] = field(default_factory=dict)
    key: KeyMaterial | None = None
    anchor: str = "record"
    field_scoped: bool = False
    locale: str = "en_US"
    fpe_enabled: bool = False
    synth: SurrogateProvider | None = None
    cipher_factory: CipherFactory | None = None
    #: M5 ``external_tokenize``: ``(entity_type, value) → token`` (or token → value when
    #: revealing), filled by ``apply``'s (de)tokenize pre-pass; ``None`` = no adapter configured
    external: Mapping[tuple[str, str], str] | None = field(default=None, repr=False)
    #: SEC-4: the server-held key binding external tokens to ``key`` (``None`` = no KEK)
    binding: bytes | None = field(default=None, repr=False)  # S2: never in a repr

    def subkey(self, method: str, length: int = crypto.SUBKEY_BYTES) -> bytes:
        """The HKDF subkey for ``method`` and this entity type (and path when field-scoped)."""
        info = f"{method}|{self.entity_type}" + (f"|{self.path}" if self.field_scoped else "")
        return self._derive(method, info, length)

    def record_subkey(self, method: str, length: int = crypto.SUBKEY_BYTES) -> bytes:
        """The HKDF subkey for ``method`` shared by every field of every type in the record.

        For values that must agree across a record whatever the entity type or path - the
        ``date_shift`` offset (spec §5.5: "all dates in the record shift equally"). The info is
        ``<method>|record``; no method derives a per-type subkey under the same method name.
        """
        return self._derive(method, f"{method}|record", length)

    def _derive(self, method: str, info: str, length: int) -> bytes:
        if self.key is None or not self.key.has_dek():
            raise MethodError(f"method {method!r} needs key material with a plaintext dek")
        try:
            return crypto.subkey(self.key, info, length)
        except ValueError:
            raise MethodError("key material dek is malformed") from None

    def cipher(self, method: str) -> fpe.Cipher:
        """The FF1 cipher (or the injected test cipher) for ``method``."""
        tweak = self.entity_type + (f"|{self.path}" if self.field_scoped else "")
        key = self.subkey(method)
        if self.cipher_factory is not None:
            return self.cipher_factory(key, tweak)
        return fpe.Ff1(key, tweak)

    def fpe_ready(self) -> bool:
        """FPE is switched on and a cipher is available."""
        return self.fpe_enabled and (self.cipher_factory is not None or fpe.fpe_available())


@dataclass(frozen=True)
class Outcome:
    """The result of one method run: the new value and what reveal needs to undo it."""

    value: str | None
    method: str
    reversible: bool
    note: str | None = None
    reverse_params: dict[str, Any] = field(default_factory=dict)


_TOO_SMALL = object()


# --- parameters (policy- or hint-supplied; never customer values) ------------------------------
def _bad_param(method: str, name: str, rule: str) -> MethodError:
    return MethodError(f"{method}: parameter {name!r} must be {rule}")


def _int_from(raw: Any, method: str, name: str, lo: int, hi: int) -> int:
    rule = f"an integer in [{lo}, {hi}]"
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise _bad_param(method, name, rule)
    try:
        value = int(raw)
    except (ValueError, OverflowError):
        raise _bad_param(method, name, rule) from None
    if (isinstance(raw, float) and raw != value) or not lo <= value <= hi:
        raise _bad_param(method, name, rule)
    return value


def _int_param(
    ctx: TransformContext, method: str, name: str, default: int, lo: int, hi: int
) -> int:
    return _int_from(ctx.params.get(name, default), method, name, lo, hi)


def _float_param(
    ctx: TransformContext, method: str, name: str, default: float, lo: float, hi: float
) -> float:
    raw = ctx.params.get(name, default)
    rule = f"a number in [{lo:g}, {hi:g}]"
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise _bad_param(method, name, rule)
    try:
        value = float(raw)
    except ValueError:
        raise _bad_param(method, name, rule) from None
    if not math.isfinite(value) or not lo <= value <= hi:
        raise _bad_param(method, name, rule)
    return value


def _choice_param(
    ctx: TransformContext, method: str, name: str, default: str, choices: tuple[str, ...]
) -> str:
    raw = ctx.params.get(name, default)
    if raw not in choices:
        raise _bad_param(method, name, "one of " + ", ".join(choices))
    return str(raw)


# --- reversible --------------------------------------------------------------------------------
def _det(value: str, ctx: TransformContext, aad: str) -> str:
    key = ctx.subkey("det_token", crypto.SIV_KEY_BYTES)
    return crypto.b32(crypto.det_encrypt(key, value.encode("utf-8"), aad))


def _det_back(token: str, ctx: TransformContext, aad: str) -> str:
    key = ctx.subkey("det_token", crypto.SIV_KEY_BYTES)
    try:
        return crypto.det_decrypt(key, crypto.unb32(token), aad).decode("utf-8")
    except (InvalidTag, ValueError):  # wrong key, type or path; garbage token: say so, echo nothing
        raise MethodError("det_token could not be reversed with this key material") from None


def _det_token(value: str, ctx: TransformContext) -> str:
    return _det(value, ctx, ctx.entity_type)


def _det_reverse(token: str, ctx: TransformContext) -> str:
    return _det_back(token, ctx, ctx.entity_type)


#: ``fpe_digits`` ``leading_zero``: ``any`` is plain FF1 (the v0.1.0 tokens; the default when a
#: hint or plan entry has no such param); ``preserve`` cycle-walks so the token's first digit is
#: 0 exactly when the value's is (an integer or decimal column then keeps every digit). Every
#: plan entry defaults to ``preserve`` (``findings.PlanEntry``, contract §6.5).
LEADING_ZERO_MODES = ("any", "preserve")


def _keeps(ctx: TransformContext) -> tuple[int, int, bool]:
    return (
        _int_param(ctx, "fpe_digits", "keep_prefix", 0, 0, 64),
        _int_param(ctx, "fpe_digits", "keep_suffix", 0, 0, 64),
        _choice_param(ctx, "fpe_digits", "leading_zero", "any", LEADING_ZERO_MODES) == "preserve",
    )


_NOT_A_PERMUTATION = "fpe_digits: the cipher is not a permutation (cycle walk did not converge)"


def _fpe_digits(value: str, ctx: TransformContext) -> str | object:
    keep_prefix, keep_suffix, preserve = _keeps(ctx)
    cipher = ctx.cipher("fpe_digits")
    try:
        out = fpe.fpe_digits_encrypt(
            cipher,
            value,
            keep_prefix=keep_prefix,
            keep_suffix=keep_suffix,
            preserve_leading_zero=preserve,
        )
    except fpe.CycleWalkError:
        raise MethodError(_NOT_A_PERMUTATION) from None
    return _TOO_SMALL if out is None else out


def _fpe_digits_reverse(token: str, ctx: TransformContext) -> str:
    keep_prefix, keep_suffix, preserve = _keeps(ctx)
    cipher = ctx.cipher("fpe_digits")
    try:
        return fpe.fpe_digits_decrypt(
            cipher,
            token,
            keep_prefix=keep_prefix,
            keep_suffix=keep_suffix,
            preserve_leading_zero=preserve,
        )
    except fpe.CycleWalkError:
        raise MethodError(_NOT_A_PERMUTATION) from None
    except ValueError:
        raise MethodError("fpe_digits token cannot be reversed (domain too small)") from None


def _fpe_text(value: str, ctx: TransformContext) -> str | object:
    out = fpe.fpe_text_encrypt(ctx.cipher("fpe_text"), value)
    return _TOO_SMALL if out is None else out


def _fpe_text_reverse(token: str, ctx: TransformContext) -> str:
    try:
        return fpe.fpe_text_decrypt(ctx.cipher("fpe_text"), token)
    except ValueError:
        raise MethodError("fpe_text token cannot be reversed (domain too small)") from None


_EMAIL_DOMAIN_MODES = ("keep", "generalize", "encrypt")
_RESERVED_TLD = ".invalid"  # RFC 2606: never resolves, so an encrypted domain is never mailable


def _email(value: str, ctx: TransformContext) -> Outcome:
    mode = _choice_param(ctx, "email", "domain", "keep", _EMAIL_DOMAIN_MODES)
    local, sep, domain = value.rpartition("@")
    if not sep:
        return _as_outcome("det_token", _det_token(value, ctx), note="email:no_at_sign")
    sub, coded = "det_token", None
    if ctx.fpe_ready():
        coded = fpe.fpe_text_encrypt(ctx.cipher("fpe_text"), local)
        if coded is not None:
            sub = "fpe_text"
    if coded is None:
        coded = _det_token(local, ctx)
    rp = {"sub": sub, "domain": mode}
    if mode == "generalize":
        return Outcome(f"{coded}@example.com", "email", False, None, rp)
    if mode == "encrypt":
        hidden = _det(domain, ctx, f"{ctx.entity_type}|domain")
        return Outcome(f"{coded}@{hidden}{_RESERVED_TLD}", "email", True, None, rp)
    return Outcome(f"{coded}@{domain}", "email", True, None, rp)


def _email_reverse(token: str, ctx: TransformContext, rp: dict[str, Any]) -> str:
    mode = rp.get("domain", "keep")
    if mode == "generalize":
        raise MethodError("email with domain=generalize is not reversible")
    if mode not in _EMAIL_DOMAIN_MODES:
        raise MethodError("email reveal hint has an unknown domain mode")
    local, sep, domain = token.rpartition("@")
    if not sep:
        raise MethodError("email token has no @")
    if mode == "encrypt":
        if not domain.endswith(_RESERVED_TLD):
            raise MethodError("email token domain is not an encrypted domain")
        domain = _det_back(domain[: -len(_RESERVED_TLD)], ctx, f"{ctx.entity_type}|domain")
    if rp.get("sub") == "fpe_text":
        if not ctx.fpe_ready():
            raise MethodError("email: reversing an fpe_text local part needs FPE enabled")
        return f"{_fpe_text_reverse(local, ctx)}@{domain}"
    return f"{_det_reverse(local, ctx)}@{domain}"


def _country_code_len(value: str, ctx: TransformContext) -> int:
    keep = ctx.params.get("keep_country_code", True)
    if not isinstance(keep, bool):
        raise _bad_param("phone", "keep_country_code", "true or false")
    if not keep or not value.strip().startswith("+"):
        return 0
    try:
        parsed = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        return 0
    return len(str(parsed.country_code))


def _phone(value: str, ctx: TransformContext) -> Outcome:
    keep = _country_code_len(value, ctx)
    if ctx.fpe_ready():
        out = fpe.fpe_digits_encrypt(ctx.cipher("phone"), value, keep_prefix=keep)
        if out is not None:
            return Outcome(out, "phone", True, None, {"sub": "fpe_digits", "keep_prefix": keep})
        note = "fallback:fpe_domain_too_small"
    else:
        note = "fallback:fpe_unavailable"
    return _as_outcome("det_token", _det_token(value, ctx), note=note)


def _phone_reverse(token: str, ctx: TransformContext, rp: dict[str, Any]) -> str:
    if not ctx.fpe_ready():
        raise MethodError("phone: reversing needs FPE enabled")
    keep = _int_from(rp.get("keep_prefix", 0), "phone", "keep_prefix", 0, 4)
    try:
        return fpe.fpe_digits_decrypt(ctx.cipher("phone"), token, keep_prefix=keep)
    except ValueError:
        raise MethodError("phone token cannot be reversed (domain too small)") from None


# --- dates -------------------------------------------------------------------------------------
ISO_DATE = "%Y-%m-%d"
ISO_DATETIME = "iso-datetime"
_TEXT_FORMATS = ("%d %b %Y", "%d %B %Y", "%d %b, %Y")
_KNOWN_FORMATS = frozenset({ISO_DATE, ISO_DATETIME, "%m/%d/%Y", "%d/%m/%Y", *_TEXT_FORMATS})
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ISO_DATETIME_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})([T ][0-9]{2}:[0-9]{2}.*)")
MAX_WINDOW_DAYS = 36_500


@dataclass(frozen=True)
class _ParsedDate:
    day: date
    fmt: str
    lead: str = ""
    rest: str = ""  # the verbatim time/zone tail of an ISO datetime, plus trailing whitespace


def _slash_format(locale: str) -> str:
    return "%m/%d/%Y" if locale.endswith("US") else "%d/%m/%Y"


def _render(parsed: _ParsedDate, day: date) -> str:
    core = day.isoformat() if parsed.fmt in (ISO_DATE, ISO_DATETIME) else day.strftime(parsed.fmt)
    return parsed.lead + core + parsed.rest


def _parse_as(value: str, fmt: str) -> _ParsedDate | None:
    """Parse ``value`` in ``fmt`` only if rendering it back reproduces ``value`` exactly."""
    core = value.strip()
    lead, trail = value[: len(value) - len(value.lstrip())], value[len(value.rstrip()) :]
    day: date
    rest = trail
    try:
        if fmt == ISO_DATETIME:
            match = _ISO_DATETIME_RE.fullmatch(core)
            if match is None:
                return None
            datetime.fromisoformat(core)  # the tail must be a real time (and zone)
            day, rest = date.fromisoformat(match.group(1)), match.group(2) + trail
        elif fmt == ISO_DATE:
            if _ISO_DATE_RE.fullmatch(core) is None:
                return None
            day = date.fromisoformat(core)
        else:
            day = datetime.strptime(core, fmt).date()
    except ValueError:
        return None
    parsed = _ParsedDate(day, fmt, lead, rest)
    return parsed if _render(parsed, day) == value else None


def _parse_date(value: str, locale: str) -> _ParsedDate | None:
    for fmt in (ISO_DATE, ISO_DATETIME, _slash_format(locale), *_TEXT_FORMATS):
        parsed = _parse_as(value, fmt)
        if parsed is not None:
            return parsed
    return None


def _lenient_date(value: str, locale: str) -> date | None:
    """Any recognisable date (for irreversible generalisation; exact rendering not needed)."""
    parsed = _parse_date(value, locale)
    if parsed is not None:
        return parsed.day
    core = value.strip()
    if _ISO_DATE_RE.match(core):
        try:
            return datetime.fromisoformat(core).date()
        except ValueError:
            pass
    for fmt in (_slash_format(locale), *_TEXT_FORMATS):
        try:
            return datetime.strptime(core, fmt).date()
        except ValueError:
            continue
    return None


def _window(ctx: TransformContext) -> int:
    return _int_param(ctx, "date_shift", "window_days", 365, 1, MAX_WINDOW_DAYS)


def _offset_days(ctx: TransformContext, anchor: str, window: int) -> int:
    """The record's date offset in ``[-window, window]``, never 0 (spec §5.5).

    Keyed by the record-wide subkey and the record anchor only - not by entity type or path, so
    every date in a record shifts by the same number of days, field-scoped or not, and order,
    intervals and age bands survive. Equal shifts also need an equal ``window_days``: the
    offset is drawn modulo ``2 * window + 1``, so dates treated with different windows shift
    differently. Both apply and reverse go through here; changing the derivation breaks the
    reveal of anything already shifted under a caller-held key.
    """
    offset = crypto.derive_int(ctx.record_subkey("date_shift"), anchor, 2 * window + 1) - window
    return offset if offset != 0 else 1


def _unshift(token: str, ctx: TransformContext, rp: dict[str, Any]) -> str:
    window = _window(ctx)
    fmt = rp.get("fmt")
    if fmt is not None and fmt not in _KNOWN_FORMATS:
        raise MethodError("date_shift reveal hint has an unknown date format")
    parsed = _parse_as(token, fmt) if fmt is not None else _parse_date(token, ctx.locale)
    if parsed is None:
        raise MethodError("date_shift token is not a date in the recorded format")
    anchor = str(rp.get("anchor", ctx.anchor))
    try:
        return _render(parsed, parsed.day - timedelta(days=_offset_days(ctx, anchor, window)))
    except OverflowError:
        raise MethodError("date_shift token is out of the representable date range") from None


def _date_shift(value: str, ctx: TransformContext) -> Outcome:
    window = _window(ctx)
    parsed = _parse_date(value, ctx.locale)
    if parsed is None:
        return _as_outcome("det_token", _det_token(value, ctx), note="fallback:unparseable_date")
    rp = {"anchor": ctx.anchor, "fmt": parsed.fmt}
    try:
        shifted = _render(
            parsed, parsed.day + timedelta(days=_offset_days(ctx, ctx.anchor, window))
        )
        exact = _unshift(shifted, ctx, rp) == value
    except (OverflowError, MethodError):
        shifted, exact = "", False
    if not exact:  # e.g. past 9999-12-31, or a year strftime cannot zero-pad
        return _as_outcome("det_token", _det_token(value, ctx), note="fallback:date_out_of_range")
    return Outcome(shifted, "date_shift", True, None, rp)


def _date_shift_reverse(token: str, ctx: TransformContext, rp: dict[str, Any]) -> str:
    return _unshift(token, ctx, rp)


# --- irreversible ------------------------------------------------------------------------------
def _hmac_pseudonym(value: str, ctx: TransformContext) -> str:
    length = _int_param(ctx, "hmac_pseudonym", "length", 16, 4, 52)
    return crypto.b32(crypto.hmac_bytes(ctx.subkey("hmac_pseudonym"), value))[:length]


def _mask_partial(value: str, ctx: TransformContext) -> str:
    keep_last = _int_param(ctx, "mask_partial", "keep_last", 4, 0, 1024)
    keep_first = _int_param(ctx, "mask_partial", "keep_first", 0, 0, 1024)
    char = str(ctx.params.get("char", "*"))[:1] or "*"
    positions = [i for i, ch in enumerate(value) if ch.isalnum()]
    if len(positions) <= keep_first + keep_last:
        to_mask = set(positions)  # keeping that much would show the whole value: mask it all
    else:
        to_mask = set(positions[keep_first : len(positions) - keep_last])
    return "".join(char if i in to_mask else ch for i, ch in enumerate(value))


def _ranges(ctx: TransformContext) -> list[float]:
    raw = ctx.params.get("ranges", [])
    rule = "an ascending list of finite numbers"
    if not isinstance(raw, list):
        raise _bad_param("bucket", "ranges", rule)
    edges: list[float] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise _bad_param("bucket", "ranges", rule)
        if not math.isfinite(item) or (edges and item <= edges[-1]):
            raise _bad_param("bucket", "ranges", rule)
        edges.append(float(item))
    return edges


def _bucket(value: str, ctx: TransformContext) -> str:
    edges = _ranges(ctx)
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return "[REDACTED]"
    if not edges or math.isnan(number):
        return "[REDACTED]"
    for lo, hi in zip(edges, edges[1:], strict=False):
        if lo <= number < hi:
            return f"{int(lo)}-{int(hi) - 1}"
    return f"{int(edges[-1])}+" if number >= edges[-1] else f"<{int(edges[0])}"


def _generalize(value: str, ctx: TransformContext) -> str:
    granularity = _choice_param(ctx, "generalize", "granularity", "year", ("year", "month"))
    keep = _int_param(ctx, "generalize", "keep_chars", 3, 0, 1024)
    day = _lenient_date(value, ctx.locale)
    if day is not None:
        return f"{day.year}" if granularity == "year" else f"{day.year}-{day.month:02d}"
    return value[:keep] + "x" * max(0, len(value) - keep)


def _dictionary(value: str, ctx: TransformContext) -> str:
    raw = ctx.params.get("values", [])
    if not isinstance(raw, list) or any(
        isinstance(v, bool) or not isinstance(v, str | int | float) for v in raw
    ):
        raise _bad_param("dictionary", "values", "a list of strings")
    values = [str(v) for v in raw]
    if not values:
        return "[REDACTED]"
    if ctx.key is not None:
        index = crypto.derive_int(ctx.subkey("dictionary"), value, len(values))
    else:  # stable across processes (``hash()`` is salted per process)
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(values)
    return values[index]


_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def _format_number(number: float, decimals: int) -> str:
    return str(int(round(number))) if decimals == 0 else f"{number:.{decimals}f}"


def _noise(value: str, ctx: TransformContext) -> str:
    percent = _float_param(ctx, "noise", "percent", 5.0, 0.0, 100.0)
    text = value.replace(",", "").strip()
    if _NUMBER.fullmatch(text) is None:
        return "[REDACTED]"  # fail closed: an unparseable amount is removed, never passed through
    number = float(text)
    if ctx.key is not None:
        unit = (crypto.derive_int(ctx.subkey("noise"), value, 20001) - 10000) / 10000
    else:
        unit = (secrets.randbelow(20001) - 10000) / 10000
    decimals = len(text.partition(".")[2])
    out = _format_number(number * (1 + unit * percent / 100), decimals)
    if out == text:  # rounding gave the original back: move one unit in the last place
        step = 10.0**-decimals
        out = _format_number(number + (step if unit >= 0 else -step), decimals)
    return out


def _surrogate(value: str, ctx: TransformContext) -> str:
    if ctx.synth is None:
        raise MethodError("surrogate needs a surrogate engine in the context")
    return ctx.synth.surrogate(ctx.entity_type, value, ctx.locale, ctx.anchor)


def _external(value: str, ctx: TransformContext) -> Outcome:
    """The bank service's token for ``value`` (looked up in the pre-pass batch, never called
    per value), with its SEC-4 ``bind`` tag when the output is revealable (obfuscate mode)."""
    if ctx.external is None:
        raise MethodError("external_tokenize: no tokenization adapter is configured")
    token = ctx.external.get((ctx.entity_type, value))
    if token is None:
        raise MethodError("external_tokenize: value was not part of the tokenization batch")
    reverse_params: dict[str, Any] = {}
    if ctx.mode == "obfuscate":
        reverse_params["bind"] = external_tag(ctx, token)
    return Outcome(token, "external_tokenize", True, None, reverse_params)


def external_tag(ctx: TransformContext, token: str, binding: bytes | None = None) -> str:
    """The ``bind`` tag of ``token`` under ``ctx.key`` and ``binding`` (default: the context's
    current binding key); see :func:`~dps.engine.transform.crypto.external_binding_tag`."""
    binding = binding if binding is not None else ctx.binding
    if binding is None:
        raise MethodError(
            "external_tokenize: tokens cannot be bound to the caller's key because DPS_JOB_KEK "
            "is not configured"
        )
    if ctx.key is None or not ctx.key.has_dek():
        raise MethodError("method 'external_tokenize' needs key material with a plaintext dek")
    profile = ctx.params.get("profile")
    try:
        return crypto.external_binding_tag(
            binding,
            ctx.key,
            ctx.entity_type,
            str(profile) if profile is not None else None,
            token,
        )
    except (KeyError, ValueError):
        raise MethodError("key material dek is malformed") from None


def _external_reverse(token: str, ctx: TransformContext, _rp: dict[str, Any]) -> str:
    if ctx.external is None or (ctx.entity_type, token) not in ctx.external:
        raise MethodError("external_tokenize: token could not be detokenized")
    return ctx.external[(ctx.entity_type, token)]


# --- registry ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Method:
    """One registered method: its forward function and, when reversible, its inverse."""

    name: str
    reversible: bool
    apply: Callable[[str, TransformContext], Any]
    reverse: Callable[[str, TransformContext, dict[str, Any]], str] | None = None


METHODS: dict[str, Method] = {
    "det_token": Method("det_token", True, _det_token, lambda t, c, _rp: _det_reverse(t, c)),
    "fpe_digits": Method(
        "fpe_digits", True, _fpe_digits, lambda t, c, _rp: _fpe_digits_reverse(t, c)
    ),
    "fpe_text": Method("fpe_text", True, _fpe_text, lambda t, c, _rp: _fpe_text_reverse(t, c)),
    "email": Method("email", True, _email, _email_reverse),
    "phone": Method("phone", True, _phone, _phone_reverse),
    "date_shift": Method("date_shift", True, _date_shift, _date_shift_reverse),
    "hmac_pseudonym": Method("hmac_pseudonym", False, _hmac_pseudonym),
    "mask_partial": Method("mask_partial", False, _mask_partial),
    "redact": Method("redact", False, lambda v, c: str(c.params.get("label", "[REDACTED]"))),
    "null": Method("null", False, lambda v, c: None),
    "bucket": Method("bucket", False, _bucket),
    "generalize": Method("generalize", False, _generalize),
    "dictionary": Method("dictionary", False, _dictionary),
    "noise": Method("noise", False, _noise),
    "keep": Method("keep", False, lambda v, c: v),
    "surrogate": Method("surrogate", False, _surrogate),
    "external_tokenize": Method("external_tokenize", True, _external, _external_reverse),
}
if set(METHODS) != set(METHOD_NAMES):  # not an assert: must hold under ``python -O`` too
    raise RuntimeError("methods.py and policy.METHOD_NAMES disagree")

_FPE_METHODS = {"fpe_digits", "fpe_text"}


def _as_outcome(method: str, value: str | None, note: str | None = None) -> Outcome:
    return Outcome(value, method, METHODS[method].reversible, note, {})


def _method(name: str) -> Method:
    method = METHODS.get(name)
    if method is None:
        raise MethodError(f"unknown method {name!r}")
    return method


def run_method(name: str, value: str, ctx: TransformContext) -> Outcome:
    """Apply ``name`` to ``value`` with the spec's fallbacks.

    Raises:
        MethodError: unknown method, invalid parameters, or a missing prerequisite (key
            material, surrogate engine, tokenization adapter). Never for a bad value.
    """
    method = _method(name)
    if name in _FPE_METHODS and not ctx.fpe_ready():
        return _as_outcome("det_token", _det_token(value, ctx), note="fallback:fpe_unavailable")
    result = method.apply(value, ctx)
    if isinstance(result, Outcome):
        return result
    if result is _TOO_SMALL:
        return _as_outcome(
            "det_token", _det_token(value, ctx), note="fallback:fpe_domain_too_small"
        )
    if result is not None and not isinstance(result, str):
        raise MethodError(f"method {name!r} returned {type(result).__name__}")
    return Outcome(result, name, method.reversible, None, {})


def reverse_method(
    name: str, token: str, ctx: TransformContext, reverse_params: dict[str, Any]
) -> str:
    """Invert a reversible method; ``MethodError`` for irreversible ones or wrong key material."""
    method = _method(name)
    if not method.reversible or method.reverse is None:
        raise MethodError(f"method {name!r} is not reversible")
    if name in _FPE_METHODS and not ctx.fpe_ready():
        raise MethodError(f"method {name!r} needs FPE enabled to reverse")
    return method.reverse(token, ctx, reverse_params)
