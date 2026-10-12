from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from dps.config import Settings
from dps.jobs_models import CaptureSpec, FillSpec, JobSecret, ScanAuth, ScanScope, ScanSpec
from dps.layers.scanner.models import (
    FieldFacts,
    LinkFacts,
    PageSnapshot,
    PersonaPreviewRequest,
    ScanSpecError,
    TextFacts,
    redact_scan_spec,
    validate_scan,
)

S = Settings(_env_file=None, env="local", inline_secrets_allowed=False)
HOSTS = frozenset({"demo-site", "127.0.0.1"})


def _walk_fields(model: type[BaseModel], seen: set[type] | None = None) -> set[str]:
    seen = seen or set()
    names: set[str] = set()
    for name, info in model.model_fields.items():
        names.add(name)
        ann = info.annotation
        for cand in getattr(ann, "__args__", (ann,)):
            if isinstance(cand, type) and issubclass(cand, BaseModel) and cand not in seen:
                seen.add(cand)
                names |= _walk_fields(cand, seen)
    return names


def test_scan_spec_has_no_submit_field_anywhere() -> None:
    names = _walk_fields(ScanSpec)
    assert {"scope", "auth", "fill", "capture", "consistency", "same_origin", "personas"} <= names
    assert not {n for n in names if "submit" in n or n in {"post", "press", "click_submit"}}
    with pytest.raises(ValidationError):
        ScanSpec.model_validate({"url": "http://demo-site:8410/apply", "submit": True})
    with pytest.raises(ValidationError):
        ScanSpec.model_validate(
            {"url": "http://demo-site:8410/apply", "fill": {"personas": 2, "submit": True}}
        )


def test_defaults_and_bounds() -> None:
    spec = ScanSpec(url="http://demo-site:8410/apply")
    assert spec.mode == "synthesize" and spec.parallel_view and spec.scope.same_origin
    assert spec.scope.iframes and spec.scope.shadow_dom and spec.scope.spa_clicks
    assert (spec.fill.personas, spec.fill.locale, spec.fill.display_persona) == (1, "en_US", 1)
    assert spec.capture.screenshots and spec.policy is None and spec.purpose is None
    with pytest.raises(ValidationError):
        FillSpec(personas=51)
    with pytest.raises(ValidationError):
        FillSpec(personas=2, display_persona=3)
    with pytest.raises(ValidationError):
        FillSpec(locale="fr_FR")
    with pytest.raises(ValidationError):
        ScanScope(deny=["logout"])
    with pytest.raises(ValidationError):
        ScanScope(max_depth=6)
    with pytest.raises(ValidationError):
        ScanAuth(storage_state_ref="../etc", storage_state={"cookies": []})
    with pytest.raises(ValidationError):
        ScanAuth(storage_state_ref="ui/demo", storage_state={"cookies": []})
    with pytest.raises(ValidationError):
        ScanAuth(storage_state_ref="ui/../other")
    assert (
        ScanAuth(storage_state_ref="ui/demo-onboarding").storage_state_ref == "ui/demo-onboarding"
    )


def test_validation_errors_never_echo_the_url() -> None:
    with pytest.raises(ValidationError) as err:
        ScanSpec.model_validate({"url": "http://h/?e=ananya.k@x.in", "mode": "submit"})
    assert "ananya" not in str(err.value)


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"url": "http://user:pw@demo-site:8410/apply"}, "url_invalid"),
        ({"url": "http://:pw@demo-site:8410/apply"}, "url_invalid"),
        ({"url": "ftp://demo-site:8410/apply"}, "url_invalid"),
        ({"url": "http://evil.example/apply"}, "host_not_registered"),
        ({"url": "http://demo-site:8410/apply", "scope": {"max_pages": 51}}, "scope_invalid"),
        ({"url": "http://demo-site:8410/admin", "scope": {"deny": ["/admin"]}}, "start_url_denied"),
        (
            {"url": "http://demo-site:8410/help", "scope": {"allow": ["/apply/*"]}},
            "start_url_denied",
        ),
        (
            {"url": "http://demo-site:8410/apply", "auth": {"storage_state": {"cookies": []}}},
            "inline_auth_forbidden",
        ),
        ({"url": "http://demo-site:8410/apply", "capture": {"har": True}}, "har_not_supported"),
    ],
)
def test_validate_scan_codes(payload: dict[str, Any], code: str) -> None:
    with pytest.raises(ScanSpecError) as err:
        validate_scan(ScanSpec.model_validate(payload), settings=S, hosts=HOSTS)
    assert str(err.value) == code and err.value.code == code and err.value.status == 422


def test_programmatic_specs_are_rechecked() -> None:
    bad_fill = ScanSpec(url="http://demo-site:8410/apply").model_copy(
        update={"fill": FillSpec.model_construct(personas=1, locale="en_US", display_persona=2)}
    )
    with pytest.raises(ScanSpecError, match="fill_invalid"):
        validate_scan(bad_fill, settings=S, hosts=HOSTS)
    bad_glob = ScanSpec(url="http://demo-site:8410/apply").model_copy(
        update={"scope": ScanScope.model_construct(deny=["no-slash"], allow=[], max_pages=None)}
    )
    with pytest.raises(ScanSpecError, match="scope_invalid"):
        validate_scan(bad_glob, settings=S, hosts=HOSTS)


def test_inline_auth_is_accepted_where_inline_secrets_are_allowed() -> None:
    local = Settings(_env_file=None, env="local", inline_secrets_allowed=True)
    spec = ScanSpec.model_validate(
        {"url": "http://demo-site:8410/apply", "auth": {"storage_state": {"cookies": []}}}
    )
    v = validate_scan(spec, settings=local, hosts=HOSTS)
    stored = redact_scan_spec(v, "http://demo-site:8410/apply")
    assert stored["auth"]["storage_state"] == {"inline": True}


def test_validated_scan_defaults_and_redacted_spec_has_no_secrets() -> None:
    spec = ScanSpec.model_validate(
        {
            "url": "http://demo-site:8410/apply?email=ananya.k%40x.in",
            "consistency": {
                "key_material": {"kid": "k_1", "dek": "secret"},
                "synthesis_seed": {"seed": "s"},
                "wrap_key": {"type": "rsa-oaep-256", "public_key_pem": "PEM"},
            },
        }
    )
    v = validate_scan(spec, settings=S, hosts=HOSTS)
    assert (v.max_pages, v.max_depth) == (50, 3) and v.origin.host == "demo-site"
    assert v.start_url == "http://demo-site:8410/apply?email=ananya.k%40x.in"
    assert v.hosts == HOSTS
    stored = redact_scan_spec(v, "http://demo-site:8410/apply?email=<EMAIL>")
    flat = str(stored)
    assert "ananya" not in flat and "secret" not in flat and "PEM" not in flat
    assert stored["url"].endswith("<EMAIL>") and stored["url_origin"] == "http://demo-site:8410"
    assert "key_material" not in stored["consistency"]
    assert "synthesis_seed" not in stored["consistency"]
    assert "wrap_key" not in stored["consistency"]
    assert stored["consistency"]["key_wrapped"] is True
    assert stored["scope"]["max_pages"] == 50 and stored["scope"]["max_depth"] == 3


def test_scope_limits_resolve_against_the_settings() -> None:
    spec = ScanSpec.model_validate(
        {"url": "http://127.0.0.1:8410/", "scope": {"max_pages": 7, "max_depth": 0}}
    )
    v = validate_scan(spec, settings=S, hosts=HOSTS)
    assert (v.max_pages, v.max_depth) == (7, 0)
    assert redact_scan_spec(v, "http://127.0.0.1:8410/")["consistency"]["key_wrapped"] is False


def test_capture_spec_har_is_a_plain_flag() -> None:
    assert CaptureSpec().har is False


def test_job_secret_carries_the_raw_start_url() -> None:
    secret = JobSecret(scan_start_url="http://demo-site:8410/apply?ref=1")
    assert secret.scan_start_url and JobSecret().scan_start_url is None


def test_page_facts_never_show_page_data_in_repr() -> None:
    facts = FieldFacts(
        key="0:1",
        field_no=1,
        form_index=1,
        frame_index=0,
        in_shadow=False,
        tag="input",
        input_type="text",
        name="pan",
        id="pan",
        label="PAN of Ananya",
        label_source="label",
        placeholder="ABCDE1234F",
        title=None,
        aria_label=None,
        autocomplete=None,
        pattern=None,
        maxlength=10,
        minlength=None,
        required=True,
        readonly=False,
        disabled=False,
        visible=True,
        inputmode=None,
        value="AAAPL1234C",
        selector="#pan",
    )
    snap = PageSnapshot(
        url="http://h/?e=ananya.k@x.in",
        title="Ananya's profile",
        forms=[],
        fields=[facts],
        texts=[
            TextFacts(frame_index=0, i=0, text="ananya.k@x.in", in_table=False, label_hint=None)
        ],
        links=[LinkFacts(href="http://h/customers/ananya", in_form=False, download=False)],
        clickables=[],
        frames=[],
    )
    shown = repr(snap) + str(snap)
    for secret in ("AAAPL1234C", "Ananya", "ananya", "ABCDE1234F"):
        assert secret not in shown


def test_persona_preview_request_bounds() -> None:
    assert PersonaPreviewRequest().count == 1
    with pytest.raises(ValidationError):
        PersonaPreviewRequest(count=6)
    with pytest.raises(ValidationError):
        PersonaPreviewRequest(locale="fr_FR")
    with pytest.raises(ValidationError):
        PersonaPreviewRequest.model_validate({"count": 1, "submit": True})
