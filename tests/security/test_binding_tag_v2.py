"""S3 review F-22: the SEC-4 bind tag binds exactly one (entity type, profile).

v1 derived the tag's subkey from ``external_tokenize|<entity>|<profile or ''>``: a tag issued for
(``ACCT``, profile ``p|q``) verified for a hint claiming (``ACCT|p``, ``q``), and ``None``/``""``
profiles bound identically. v2 tags (``reverse_params.bind_v = 2``) encode ``[entity, profile]`` as
JSON; reveal hints must name a real entity type; v1 tags still verify for hints issued before."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dps.engine.policy import load_preset
from dps.engine.transform.apply import ApplyOptions, RevealHint, _is_bound
from dps.engine.transform.crypto import external_binding_tag, generate_key_material, key_check_value

BINDING = b"\x05" * 32
TOKEN = "tk_ABCDEF"


def _hint(entity: str, profile: str | None, reverse: dict[str, Any], km: Any) -> RevealHint:  # noqa: ANN401
    params = {} if profile is None else {"profile": profile}
    return RevealHint(
        path="$.acct",
        entity_type=entity,
        method="external_tokenize",
        params=params,
        reverse_params=reverse,
        kid=km.kid,
        kcv=key_check_value(km),
    )


def _options(km: Any) -> ApplyOptions:  # noqa: ANN401
    return ApplyOptions(
        policy=load_preset("gdpr-pseudonymise"),
        key=km,
        surrogates=None,
        fpe_enabled=False,
        binding_keys=(BINDING,),
    )


def test_v2_tags_are_injective_over_entity_and_profile() -> None:
    km = generate_key_material("key")
    tag = external_binding_tag(BINDING, km, "ACCT", "p|q", TOKEN, version=2)
    assert _is_bound(_hint("ACCT", "p|q", {"bind": tag, "bind_v": 2}, km), TOKEN, _options(km))
    assert not _is_bound(_hint("ACCT", "q", {"bind": tag, "bind_v": 2}, km), TOKEN, _options(km))
    none_tag = external_binding_tag(BINDING, km, "ACCT", None, TOKEN, version=2)
    empty_tag = external_binding_tag(BINDING, km, "ACCT", "", TOKEN, version=2)
    assert none_tag != empty_tag  # v1 bound them identically
    assert not _is_bound(
        _hint("ACCT", "", {"bind": none_tag, "bind_v": 2}, km), TOKEN, _options(km)
    )
    assert not _is_bound(_hint("ACCT", None, {"bind": tag, "bind_v": 3}, km), TOKEN, _options(km))


def test_a_hint_cannot_claim_an_entity_type_that_is_not_one() -> None:
    km = generate_key_material("key")
    with pytest.raises(ValidationError):
        _hint("ACCT|p", "q", {"bind": "x" * 26}, km)


def test_v1_tags_issued_before_still_verify() -> None:
    km = generate_key_material("key")
    v1 = external_binding_tag(BINDING, km, "ACCT", "ssn", TOKEN, version=1)
    assert _is_bound(_hint("ACCT", "ssn", {"bind": v1}, km), TOKEN, _options(km))


def test_new_obfuscations_issue_v2_tags() -> None:
    from dps.config import Settings
    from dps.engine.service import Payload, PrivacyEngine, RequestOptions

    class _Tok:
        name = "t"

        def tokenize(self, entity_type: str, values: Any, *, profile: str | None) -> list[str]:  # noqa: ANN401
            return [f"tk_{v[::-1]}" for v in values]

        def detokenize(self, entity_type: str, tokens: Any, *, profile: str | None) -> list[str]:  # noqa: ANN401
            return [t[3:][::-1] for t in tokens]

        def health(self) -> bool:
            return True

        def close(self) -> None:
            return None

    settings = Settings(
        _env_file=None,
        llm_provider="stub",
        ner_enabled=False,
        fpe_enabled=False,
        job_kek="ERERERERERERERERERERERERERERERERERERERERERE=",
    )
    engine = PrivacyEngine(settings, tokenizer=_Tok())  # type: ignore[arg-type]
    policy = {
        "preset": "gdpr-pseudonymise",
        "treat": {"SSN": {"method": "external_tokenize", "profile": "ssn"}},
    }
    out = engine.obfuscate(Payload(data={"ssn": "123-45-6789"}), policy, RequestOptions())
    hint = next(h for h in out.reveal_hints if h.method == "external_tokenize")
    assert hint.reverse_params.get("bind_v") == 2
    back = engine.reveal(
        Payload(data=out.result),
        out.key_material.model_dump(mode="json"),  # type: ignore[union-attr]
        [h.model_dump(mode="json") for h in out.reveal_hints],
    )
    assert back.result == {"ssn": "123-45-6789"}
