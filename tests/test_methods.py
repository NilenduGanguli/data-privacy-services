from __future__ import annotations

from datetime import date

import pytest

from dps.engine.policy import METHOD_NAMES
from dps.engine.transform import crypto
from dps.engine.transform.crypto import generate_key_material
from dps.engine.transform.methods import (
    METHODS,
    MethodError,
    TransformContext,
    reverse_method,
    run_method,
)


class RotCipher:
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


class FakeSynth:
    def surrogate(self, entity_type: str, original: str, locale: str, anchor: str) -> str:
        return f"<{entity_type}:{anchor}>"


KM = generate_key_material("key")


def ctx(entity: str, method_params: dict | None = None, **kw) -> TransformContext:  # noqa: ANN003
    base = dict(
        entity_type=entity,
        path=f"$.{entity.lower()}",
        mode="obfuscate",
        params=method_params or {},
        key=KM,
        anchor="rec-1",
        fpe_enabled=True,
        cipher_factory=lambda key, tweak: RotCipher(),
    )
    base.update(kw)
    return TransformContext(**base)  # type: ignore[arg-type]


def test_registry_matches_policy_method_names() -> None:
    assert set(METHODS) == set(METHOD_NAMES)


def test_det_token_round_trip_and_determinism() -> None:
    c = ctx("EMAIL")
    out = run_method("det_token", "ann@example.com", c)
    assert (
        out.value
        and out.value != "ann@example.com"
        and out.reversible
        and out.method == "det_token"
    )
    assert out.value == run_method("det_token", "ann@example.com", c).value
    assert out.value != run_method("det_token", "ann@example.com", ctx("PERSON_NAME")).value
    assert reverse_method("det_token", out.value, c, out.reverse_params) == "ann@example.com"


def test_fpe_digits_and_text_with_fallbacks() -> None:
    out = run_method(
        "fpe_digits", "4532 7811 2290 4417", ctx("CARD_PAN", {"keep_prefix": 6, "keep_suffix": 4})
    )
    assert (
        out.method == "fpe_digits"
        and out.value
        and out.value.startswith("4532 78")
        and out.value.endswith("4417")
    )
    assert (
        reverse_method(
            "fpe_digits",
            out.value,
            ctx("CARD_PAN", {"keep_prefix": 6, "keep_suffix": 4}),
            out.reverse_params,
        )
        == "4532 7811 2290 4417"
    )
    small = run_method("fpe_digits", "12-34", ctx("SSN"))
    assert small.method == "det_token" and small.note == "fallback:fpe_domain_too_small"
    disabled = run_method("fpe_text", "Ananya Krishnan", ctx("PERSON_NAME", fpe_enabled=False))
    assert disabled.method == "det_token" and disabled.note == "fallback:fpe_unavailable"
    name = run_method("fpe_text", "Ananya Krishnan", ctx("PERSON_NAME"))
    assert name.value and name.value[0].isupper() and " " in name.value
    assert (
        reverse_method("fpe_text", name.value, ctx("PERSON_NAME"), name.reverse_params)
        == "Ananya Krishnan"
    )


def test_email_and_phone_keep_shape() -> None:
    out = run_method("email", "ananya.k@example-mail.in", ctx("EMAIL", {"domain": "keep"}))
    assert (
        out.value
        and out.value.endswith("@example-mail.in")
        and not out.value.startswith("ananya.k")
    )
    assert (
        reverse_method("email", out.value, ctx("EMAIL", {"domain": "keep"}), out.reverse_params)
        == "ananya.k@example-mail.in"
    )
    gen = run_method("email", "ananya.k@example-mail.in", ctx("EMAIL", {"domain": "generalize"}))
    assert gen.value and gen.value.endswith("@example.com") and gen.reversible is False
    phone = run_method("phone", "+91 98451 20377", ctx("PHONE", {"keep_country_code": True}))
    assert phone.value and phone.value.startswith("+91 ") and phone.value != "+91 98451 20377"
    assert (
        reverse_method(
            "phone", phone.value, ctx("PHONE", {"keep_country_code": True}), phone.reverse_params
        )
        == "+91 98451 20377"
    )


def test_date_shift_preserves_format_and_intervals() -> None:
    c = ctx("DATE_OF_BIRTH", {"window_days": 365})
    a = run_method("date_shift", "1988-03-14", c)
    b = run_method("date_shift", "1988-03-20", c)
    assert a.value != "1988-03-14" and len(a.value or "") == 10
    assert (date.fromisoformat(b.value) - date.fromisoformat(a.value)).days == 6  # type: ignore[arg-type]
    assert reverse_method("date_shift", a.value, c, a.reverse_params) == "1988-03-14"  # type: ignore[arg-type]
    slash = run_method("date_shift", "03/14/1988", ctx("DATE_OF_BIRTH", locale="en_US"))
    assert slash.value and slash.value.count("/") == 2
    assert run_method("date_shift", "not a date", c).method == "det_token"


def test_irreversible_methods() -> None:
    assert (
        run_method("hmac_pseudonym", "ann", ctx("PERSON_NAME")).value
        == run_method("hmac_pseudonym", "ann", ctx("PERSON_NAME")).value
    )
    assert (
        len(run_method("hmac_pseudonym", "ann", ctx("PERSON_NAME", {"length": 12})).value or "")
        == 12
    )
    assert (
        run_method("mask_partial", "4532-7811-2290-4417", ctx("CARD_PAN", {"keep_last": 4})).value
        == "****-****-****-4417"
    )
    assert run_method("redact", "x", ctx("SSN")).value == "[REDACTED]"
    assert run_method("null", "x", ctx("SSN")).value is None
    assert run_method("bucket", "33", ctx("AGE", {"ranges": [0, 18, 25, 35, 45]})).value == "25-34"
    assert (
        run_method("generalize", "1988-03-14", ctx("DATE_OF_BIRTH", {"granularity": "year"})).value
        == "1988"
    )
    assert (
        run_method("generalize", "560001", ctx("POSTAL_CODE", {"keep_chars": 3})).value == "560xxx"
    )
    assert run_method(
        "dictionary", "ann", ctx("PERSON_NAME", {"values": ["Alex", "Sam"]})
    ).value in {
        "Alex",
        "Sam",
    }
    noisy = run_method("noise", "1000", ctx("AMOUNT", {"percent": 5}))
    assert noisy.value and 950 <= int(noisy.value) <= 1050
    assert run_method("keep", "x", ctx("DATE")).value == "x"
    assert (
        run_method(
            "surrogate", "Ann", ctx("PERSON_NAME", synth=FakeSynth(), mode="synthesize")
        ).value
        == "<PERSON_NAME:rec-1>"
    )


def test_errors_are_explicit() -> None:
    with pytest.raises(MethodError):
        run_method("det_token", "x", ctx("SSN", key=None))
    with pytest.raises(MethodError):
        run_method("surrogate", "x", ctx("SSN", synth=None))
    with pytest.raises(MethodError):
        run_method("external_tokenize", "x", ctx("SSN"))
    with pytest.raises(MethodError):
        reverse_method("hmac_pseudonym", "abc", ctx("SSN"), {})


# --- additions beyond the plan (see docs/superpowers/build/M0-DEVIATIONS.md, Task 15) ----------
def test_det_token_is_aes256_siv_under_a_64_byte_subkey() -> None:
    out = run_method("det_token", "ann@example.com", ctx("EMAIL"))
    key = crypto.subkey(KM, "det_token|EMAIL", length=crypto.SIV_KEY_BYTES)
    assert crypto.det_decrypt(key, crypto.unb32(out.value or ""), "EMAIL") == b"ann@example.com"


def test_field_scoped_tokens_differ_per_path_and_reverse_only_there() -> None:
    a = ctx("EMAIL", field_scoped=True, path="$.home_email")
    b = ctx("EMAIL", field_scoped=True, path="$.work_email")
    ta, tb = (run_method("det_token", "ann@example.com", c).value or "" for c in (a, b))
    assert ta != tb and reverse_method("det_token", ta, a, {}) == "ann@example.com"
    with pytest.raises(MethodError):
        reverse_method("det_token", ta, b, {})


def test_wrong_key_or_garbage_tokens_are_method_errors_without_values() -> None:
    out = run_method("det_token", "ann@example.com", ctx("EMAIL"))
    other = ctx("EMAIL", key=generate_key_material())
    for token in (out.value or "", "not-base32!", "abc", ""):
        with pytest.raises(MethodError) as info:
            reverse_method("det_token", token, other, {})
        assert "ann" not in str(info.value) and (not token or token not in str(info.value))
    wrapped_only = KM.model_copy(update={"dek": None, "wrapped_dek": "eA"})
    with pytest.raises(MethodError):
        run_method("det_token", "x", ctx("SSN", key=wrapped_only))


def test_unknown_methods_are_method_errors() -> None:
    with pytest.raises(MethodError):
        run_method("teleport", "x", ctx("SSN"))
    with pytest.raises(MethodError):
        reverse_method("teleport", "x", ctx("SSN"), {})


@pytest.mark.parametrize(
    "method,params",
    [
        ("hmac_pseudonym", {"length": "twelve"}),
        ("hmac_pseudonym", {"length": 0}),
        ("fpe_digits", {"keep_prefix": -1}),
        ("fpe_digits", {"keep_suffix": [4]}),
        ("fpe_digits", {"leading_zero": "sideways"}),
        ("date_shift", {"window_days": 10**12}),
        ("date_shift", {"window_days": -5}),
        ("bucket", {"ranges": ["a", "b"]}),
        ("bucket", {"ranges": 7}),
        ("mask_partial", {"keep_last": None}),
        ("noise", {"percent": "lots"}),
        ("generalize", {"keep_chars": -1}),
        ("email", {"domain": "teleport"}),
        ("dictionary", {"values": "Alex"}),
    ],
)
def test_bad_method_params_are_method_errors_that_never_echo_the_value(
    method: str, params: dict
) -> None:
    secret = "1988-03-14 ann@ex.in 4532781122974417"
    with pytest.raises(MethodError) as info:
        run_method(method, secret, ctx("SSN", params))
    assert secret not in str(info.value) and "4532" not in str(info.value)


def test_email_domain_encrypt_hides_the_domain_and_reverses() -> None:
    c = ctx("EMAIL", {"domain": "encrypt"})
    out = run_method("email", "ananya.k@example-mail.in", c)
    assert out.reversible and out.value and "example-mail" not in out.value
    assert out.value.count("@") == 1 and out.value.endswith(".invalid")
    assert reverse_method("email", out.value, c, out.reverse_params) == "ananya.k@example-mail.in"


def test_reversing_an_fpe_sub_method_needs_fpe() -> None:
    phone = run_method("phone", "+91 98451 20377", ctx("PHONE"))
    assert phone.reverse_params["sub"] == "fpe_digits"
    with pytest.raises(MethodError):
        reverse_method(
            "phone", phone.value or "", ctx("PHONE", fpe_enabled=False), phone.reverse_params
        )
    email = run_method("email", "ananya.k@example-mail.in", ctx("EMAIL"))
    with pytest.raises(MethodError):
        reverse_method(
            "email", email.value or "", ctx("EMAIL", fpe_enabled=False), email.reverse_params
        )


@pytest.mark.parametrize(
    "value",
    [
        "1988-03-14T10:30:00+05:30",
        "1988-03-14 10:30",
        "1988-03-14T10:30:00.123456Z",
        "19880314",  # basic ISO: not a format we can shift exactly -> det_token
        "4 Mar 1988",  # not zero-padded -> det_token
        "14 MAR 1988",  # strptime ignores case, strftime would not round-trip -> det_token
        "14 Mar 1988",
        "14 March 1988",
        "14 Mar, 1988",
        "14/03/1988",
        "9999-12-31",
        "0001-01-01",
        "1988-02-30",
    ],
)
def test_date_shift_reverses_exactly_whatever_the_input(value: str) -> None:
    for anchor in ("rec-1", "rec-2", "rec-3", "rec-4"):
        c = ctx("DATE_OF_BIRTH", {"window_days": 365}, anchor=anchor, locale="en_IN")
        out = run_method("date_shift", value, c)
        assert out.value and out.value != value
        if out.method == "date_shift":
            assert len(out.value) == len(value) or "%B" in str(out.reverse_params.get("fmt"))
        assert reverse_method(out.method, out.value, c, out.reverse_params) == value


def test_date_shift_keeps_the_time_part_verbatim() -> None:
    out = run_method("date_shift", "1988-03-14T10:30:00+05:30", ctx("DATE_OF_BIRTH"))
    assert out.method == "date_shift" and out.value and out.value.endswith("T10:30:00+05:30")


def test_date_shift_reverse_uses_the_recorded_format_not_the_reveal_locale() -> None:
    applied = run_method("date_shift", "03/04/1988", ctx("DATE_OF_BIRTH", locale="en_US"))
    assert applied.method == "date_shift"
    back = reverse_method(
        "date_shift",
        applied.value or "",
        ctx("DATE_OF_BIRTH", locale="en_IN"),
        applied.reverse_params,
    )
    assert back == "03/04/1988"


def _shift_days(entity: str, value: str, window: int = 365, **kw) -> int:  # noqa: ANN003
    out = run_method("date_shift", value, ctx(entity, {"window_days": window}, **kw))
    assert out.method == "date_shift" and out.value
    return (date.fromisoformat(out.value) - date.fromisoformat(value)).days


ANCHORS = ("rec-1", "rec-2", "rec-3", "rec-4")


def test_date_shift_offset_is_one_per_record_whatever_the_entity_type() -> None:
    # spec §5.5: "all dates in the record shift equally" - order, intervals and age bands survive
    for anchor in ANCHORS:
        dob = _shift_days("DATE_OF_BIRTH", "1988-03-14", anchor=anchor)
        assert _shift_days("DATE", "2019-07-01", anchor=anchor) == dob
        assert _shift_days("ACCOUNT_OPENED", "2004-11-30", anchor=anchor) == dob
    # still one offset per record and per key, not one global constant
    assert len({_shift_days("DATE", "2019-07-01", anchor=a) for a in ANCHORS}) > 1
    other_key = generate_key_material("key")
    assert {_shift_days("DATE", "2019-07-01", anchor=a, key=other_key) for a in ANCHORS} != {
        _shift_days("DATE", "2019-07-01", anchor=a) for a in ANCHORS
    }


def test_date_shift_offset_does_not_depend_on_the_path_when_field_scoped() -> None:
    for anchor in ANCHORS:
        scoped = {"anchor": anchor, "field_scoped": True}
        a = _shift_days("DATE_OF_BIRTH", "1988-03-14", path="$.a", **scoped)
        b = _shift_days("DATE_OF_BIRTH", "1988-03-14", path="$.b", **scoped)
        c = _shift_days("DATE", "2019-07-01", path="$.c[0].opened", **scoped)
        assert a == b == c == _shift_days("DATE", "2019-07-01", anchor=anchor)


def test_date_shift_keeps_order_and_intervals_across_entity_types() -> None:
    for anchor in ANCHORS:
        birth = run_method("date_shift", "1988-03-14", ctx("DATE_OF_BIRTH", anchor=anchor))
        opened = run_method("date_shift", "1988-06-22", ctx("DATE", anchor=anchor))
        assert birth.value and opened.value
        gap = date.fromisoformat(opened.value) - date.fromisoformat(birth.value)
        assert gap.days == 100


def test_date_shift_offset_is_bounded_by_the_window() -> None:
    # equal shifts need equal windows: the offset is drawn from [-window, window], never 0
    for anchor in ANCHORS:
        assert _shift_days("DATE", "2019-07-01", window=1, anchor=anchor) in (-1, 1)
        assert 0 < abs(_shift_days("DATE", "2019-07-01", window=30, anchor=anchor)) <= 30


def test_mask_partial_never_shows_a_whole_short_value() -> None:
    assert run_method("mask_partial", "123", ctx("CVV", {"keep_last": 4})).value == "***"
    assert run_method("mask_partial", "4417", ctx("CARD_PAN", {"keep_last": 4})).value == "****"
    assert (
        run_method(
            "mask_partial", "AB-1234", ctx("ACCOUNT_NUMBER", {"keep_first": 1, "keep_last": 2})
        ).value
        == "A*-**34"
    )


def test_noise_never_returns_the_original_and_works_without_a_key() -> None:
    for value in ("1000", "42", "1250.50", "1,000"):
        for anchor in range(20):
            out = run_method("noise", value, ctx("AMOUNT", {"percent": 1}, anchor=str(anchor)))
            assert out.value and out.value != value and out.value != value.replace(",", "")
        keyless = run_method("noise", value, ctx("AMOUNT", {"percent": 5}, key=None))
        assert keyless.value and keyless.value != value
    assert run_method("noise", "n/a", ctx("AMOUNT")).value == "[REDACTED]"


def test_dictionary_without_a_key_is_stable_across_processes() -> None:
    import hashlib

    values = ["Alex", "Sam", "Kim"]
    out = run_method("dictionary", "ann", ctx("PERSON_NAME", {"values": values}, key=None)).value
    expected = values[int.from_bytes(hashlib.sha256(b"ann").digest()[:8], "big") % 3]
    assert out == expected


def test_methods_tolerate_empty_values() -> None:
    for method in ("det_token", "fpe_digits", "fpe_text", "email", "phone", "date_shift"):
        out = run_method(method, "", ctx("SSN"))
        assert out.value is not None
        if out.reversible:
            assert reverse_method(out.method, out.value, ctx("SSN"), out.reverse_params) == ""


def test_fpe_digits_leading_zero_param() -> None:
    # RotCipher maps a leading 7 to 0; "preserve" walks on to a token an integer column can hold.
    legacy = run_method("fpe_digits", "7123456", ctx("CUSTOMER_ID"))
    assert legacy.value == "0456789"  # no param: v0.1.0 behaviour, so old hints still reverse
    assert reverse_method("fpe_digits", "0456789", ctx("CUSTOMER_ID"), {}) == "7123456"
    explicit = ctx("CUSTOMER_ID", {"leading_zero": "any"})
    assert run_method("fpe_digits", "7123456", explicit).value == "0456789"
    keep = ctx("CUSTOMER_ID", {"leading_zero": "preserve"})
    out = run_method("fpe_digits", "7123456", keep)
    assert out.value == "3789012" and out.method == "fpe_digits" and out.reversible
    assert reverse_method("fpe_digits", "3789012", keep, out.reverse_params) == "7123456"


def test_date_shift_keeps_sub_microsecond_digits() -> None:
    c = ctx("DATE_OF_BIRTH", {"window_days": 365}, anchor="CUSTOMER_ID:abc")
    out = run_method("date_shift", "2025-01-01T10:00:00.123456789", c)
    assert out.method == "date_shift"
    assert out.value is not None and out.value.endswith(".123456789")
    assert out.value[:10] != "2025-01-01"
    back = reverse_method("date_shift", out.value, c, out.reverse_params)
    assert back == "2025-01-01T10:00:00.123456789"
    zero = run_method("date_shift", "2025-01-01T10:00:00.000000123", c)
    assert zero.value is not None and zero.value.endswith(".000000123")
    plain = run_method("date_shift", "2025-01-01T10:00:00.123456", c)  # the M0 path, unchanged
    assert plain.value is not None and plain.value.endswith(".123456") and len(plain.value) == 26
