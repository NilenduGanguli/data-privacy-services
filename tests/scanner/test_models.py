from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from dps.config import Settings
from dps.jobs_models import (
    CaptureSpec,
    ConsistencySpec,
    FillSpec,
    JobSecret,
    ScanAuth,
    ScanScope,
    ScanSpec,
)
from dps.layers.scanner.models import (
    FieldFacts,
    LinkFacts,
    PageSnapshot,
    PersonaPreviewRequest,
    RequestCount,
    ScanSpecError,
    TextFacts,
    redact_scan_spec,
    spec_refusal_code,
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


def _nested_models(model: type[BaseModel]) -> dict[str, type[BaseModel]]:
    out: dict[str, type[BaseModel]] = {}
    for name, info in model.model_fields.items():
        for cand in getattr(info.annotation, "__args__", (info.annotation,)):
            if isinstance(cand, type) and issubclass(cand, BaseModel):
                out[name] = cand
    return out


_NESTED_MINIMUM: dict[str, dict[str, Any]] = {
    "purpose": {"ticket": "CHG-1234", "legal_basis": "test data for UAT"},
}


def test_every_level_of_the_scan_spec_refuses_extra_keys() -> None:
    nested = _nested_models(ScanSpec)
    assert {"scope", "auth", "fill", "capture", "consistency", "purpose"} <= set(nested)
    assert ScanSpec.model_config.get("extra") == "forbid"
    for name, model in nested.items():
        assert model.model_config.get("extra") == "forbid", name
        assert model.model_config.get("hide_input_in_errors"), name
        assert not _nested_models(model), name  # no deeper model level left unchecked
        body = {**_NESTED_MINIMUM.get(name, {})}
        ScanSpec.model_validate({"url": "http://demo-site:8410/apply", name: body})
        with pytest.raises(ValidationError):
            ScanSpec.model_validate(
                {"url": "http://demo-site:8410/apply", name: {**body, "submit": True}}
            )


def test_the_run_spec_models_are_unchanged_by_the_scan_subclasses() -> None:
    assert ConsistencySpec.model_validate({"submit": True}) == ConsistencySpec()
    spec = ScanSpec.model_validate(
        {"url": "http://demo-site:8410/apply", "consistency": {"scope": "entity"}}
    )
    assert isinstance(spec.consistency, ConsistencySpec) and spec.consistency.scope == "entity"


def test_a_request_count_is_at_least_one() -> None:
    for bad in (0, -3):
        with pytest.raises(ValidationError):
            RequestCount(
                page_no=1,
                method="POST",
                resource_type="xhr",
                decision="allowed",
                reason="same_origin",
                host="demo-site",
                count=bad,
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
        ({"url": "http://demo-site:8410/ADMIN", "scope": {"deny": ["/admin"]}}, "start_url_denied"),
        (
            {"url": "http://demo-site:8410/admin.", "scope": {"deny": ["/admin"]}},
            "start_url_denied",
        ),
        (
            {"url": "http://demo-site:8410/admin%00", "scope": {"deny": ["/admin"]}},
            "start_url_denied",
        ),
        (
            {"url": "http://demo-site:8410/#/admin", "scope": {"deny": ["/admin"]}},
            "start_url_denied",
        ),
        (
            {"url": "http://demo-site:8410/app#!/Admin/x", "scope": {"deny": ["/admin"]}},
            "start_url_denied",
        ),
        (
            {"url": "http://demo-site:8410/admin;x", "scope": {"deny": ["/admin"]}},
            "start_url_denied",
        ),
        (
            {"url": "http://demo-site:8410/%61dmin", "scope": {"deny": ["/admin"]}},
            "start_url_denied",
        ),
        (
            {"url": "http://demo-site:8410/apply/../admin/x", "scope": {"deny": ["/admin"]}},
            "start_url_denied",
        ),
        (
            {"url": "http://demo-site:8410/apply/%2E%2E/help", "scope": {"allow": ["/apply/*"]}},
            "start_url_denied",
        ),
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
    assert stored["consistency"]["wrapped"] is True  # the name M2's redact_spec and custody read
    assert stored["scope"]["max_pages"] == 50 and stored["scope"]["max_depth"] == 3


def test_scope_limits_resolve_against_the_settings() -> None:
    spec = ScanSpec.model_validate(
        {"url": "http://127.0.0.1:8410/", "scope": {"max_pages": 7, "max_depth": 0}}
    )
    v = validate_scan(spec, settings=S, hosts=HOSTS)
    assert (v.max_pages, v.max_depth) == (7, 0)
    unwrapped = redact_scan_spec(v, "http://127.0.0.1:8410/")["consistency"]
    assert unwrapped["key_wrapped"] is False and unwrapped["wrapped"] is False


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


def _errors(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The errors FastAPI reports for ``body`` (``loc`` prefixed with ``body``)."""
    try:
        ScanSpec.model_validate(body)
    except ValidationError as exc:
        return [{**e, "loc": ("body", *e["loc"])} for e in exc.errors()]
    raise AssertionError("the body was accepted")


URL0 = "http://127.0.0.1:8410/apply"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"url": "http:/"}, "url_invalid"),
        ({"url": 7}, "url_invalid"),
        ({"url": URL0, "scope": {"max_depth": 6}}, "scope_invalid"),
        ({"url": URL0, "scope": {"max_pages": 0}}, "scope_invalid"),
        ({"url": URL0, "scope": {"deny": ["no-slash"]}}, "scope_invalid"),
        ({"url": URL0, "scope": "x"}, "scope_invalid"),
        ({"url": URL0, "auth": {"storage_state_ref": "a/../b"}}, "auth_invalid"),
        ({"url": URL0, "auth": {"storage_state_ref": "a", "storage_state": {}}}, "auth_invalid"),
        ({"url": URL0, "fill": {"personas": 51}}, "fill_invalid"),
        ({"url": URL0, "fill": {"personas": 1, "display_persona": 2}}, "fill_invalid"),
        ({"url": URL0, "fill": {"locale": "fr_FR"}}, "fill_invalid"),
        (
            {"url": URL0, "fill": {"personas": 51}, "auth": {"storage_state_ref": "a/../b"}},
            "auth_invalid",
        ),
        ({"url": "x", "fill": {"personas": 51}}, "url_invalid"),
    ],
)
def test_spec_refusal_code_maps_model_refusals_by_location(body: dict[str, Any], code: str) -> None:
    assert spec_refusal_code(_errors(body)) == code


@pytest.mark.parametrize(
    "body",
    [
        {"url": URL0, "submit": True},
        {"url": URL0, "scope": {"submit": True}},
        {"url": URL0, "fill": {"submit": True}},
        {"url": URL0, "auth": {"cookie": "x"}},
        {"url": URL0, "mode": "publish"},
        {"url": URL0, "capture": {"har": "maybe"}},
        {"url": URL0, "parallel_view": "x"},
        {"url": URL0, "fill": {"personas": 51}, "mode": "publish"},
        {"url": URL0, "scope": {"max_depth": 9, "submit": True}},
    ],
)
def test_spec_refusal_code_leaves_uncoded_refusals_alone(body: dict[str, Any]) -> None:
    assert spec_refusal_code(_errors(body)) is None


def test_spec_refusal_code_needs_body_errors() -> None:
    assert spec_refusal_code([]) is None
    assert spec_refusal_code([{"type": "missing", "loc": ("body",), "msg": "x"}]) is None
    assert spec_refusal_code([{"type": "json_invalid", "loc": ("body", 0), "msg": "x"}]) is None
    assert spec_refusal_code([{"type": "missing", "loc": ("header", "x"), "msg": "x"}]) is None
