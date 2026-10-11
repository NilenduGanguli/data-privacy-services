from __future__ import annotations

import json

import pytest

from dps.audit import AuditEvent, LogAuditSink, MemoryAuditSink, NullAuditSink
from dps.auth import (
    AuthError,
    KeyRecord,
    StaticKeyStore,
    authenticate,
    hash_key,
    new_api_key,
    resolve_tenant,
)
from dps.config import Settings
from dps.ratelimit import RateLimiter


def _store(**kw) -> StaticKeyStore:  # noqa: ANN003
    keys = [
        {
            "name": "ops",
            "key": "dps_plain",
            "tenant_ids": ["retail", "cards"],
            "scopes": ["detect", "obfuscate"],
        },
        {
            "name": "hashed",
            "key_sha256": hash_key("dps_hashed"),
            "tenant_ids": ["*"],
            "scopes": ["*"],
        },
    ]
    return StaticKeyStore.from_settings(Settings(_env_file=None, api_keys=json.dumps(keys), **kw))


def test_store_resolves_plain_and_hashed_keys_and_bootstrap() -> None:
    store = _store(bootstrap_api_key="dps_boot")
    assert store.lookup("dps_plain").name == "ops" and store.lookup("dps_hashed").scopes == ["*"]  # type: ignore[union-attr]
    assert store.lookup("dps_boot").name == "bootstrap" and store.lookup("nope") is None  # type: ignore[union-attr]
    assert new_api_key().startswith("dps_") and len(new_api_key()) > 30


def test_authenticate_and_scopes() -> None:
    store = _store()
    p = authenticate(store, enabled=True, raw_key="dps_plain")
    assert (
        p.has_scope("detect")
        and not p.has_scope("reveal")
        and p.can_access("retail")
        and not p.can_access("x")
    )
    with pytest.raises(AuthError) as missing:
        authenticate(store, enabled=True, raw_key=None)
    assert missing.value.status == 401
    with pytest.raises(AuthError) as bad:
        authenticate(store, enabled=True, raw_key="wrong")
    assert bad.value.status == 401
    local = authenticate(store, enabled=False, raw_key=None)
    assert local.key_id == "local-dev" and local.has_scope("admin")


def test_resolve_tenant_rules() -> None:
    store = _store()
    ops = authenticate(store, enabled=True, raw_key="dps_plain")
    assert resolve_tenant(ops, "cards") == "cards"
    with pytest.raises(AuthError) as denied:
        resolve_tenant(ops, "wealth")
    assert denied.value.status == 403
    with pytest.raises(AuthError):
        resolve_tenant(ops, None)  # two tenants granted, none chosen
    wild = authenticate(store, enabled=True, raw_key="dps_hashed")
    assert (
        resolve_tenant(wild, None) == "default" and resolve_tenant(wild, "anything") == "anything"
    )


def test_rate_limiter_burst_and_refill() -> None:
    now = [0.0]
    limiter = RateLimiter(rate=10.0, burst=2, enabled=True, clock=lambda: now[0])
    assert limiter.check("k") and limiter.check("k") and not limiter.check("k")
    now[0] += 0.1
    assert limiter.check("k")
    assert RateLimiter(rate=1.0, burst=1, enabled=False).check("k") and RateLimiter(
        1.0, 1, enabled=False
    ).check("k")


def test_audit_event_is_value_free_and_sink_records() -> None:
    sink = MemoryAuditSink()
    event = AuditEvent(
        trace_id="t1",
        route="/api/v1/obfuscate",
        tenant_id="retail",
        key_id="k1",
        mode="obfuscate",
        policy="gdpr-pseudonymise",
        findings_total=3,
        findings_by_type={"SSN": 1},
        methods_used={"fpe_digits": 1},
        verification_passed=True,
        reversible_values=1,
        duration_ms=4.2,
        outcome="ok",
    )
    sink.write(event)
    assert sink.events[-1].route == "/api/v1/obfuscate"
    assert not ({"value", "data", "text", "result", "dek"} & set(AuditEvent.model_fields))


# --- hardening beyond the plan --------------------------------------------------------------------
def _rec(name: str, **kw: object) -> KeyRecord:
    return KeyRecord.model_validate({"name": name, "tenant_ids": ["*"], "scopes": ["*"], **kw})


def test_key_records_never_echo_a_key_in_errors() -> None:
    secret = "dps_s3cret_value_never_echoed"
    cases = [
        {"name": "both", "key": secret, "key_sha256": hash_key(secret)},
        {"name": "neither"},
        {"name": "short-hash", "key_sha256": secret},
        {"name": "bad-scope", "key": secret, "scopes": ["detect", "teleport"]},
        {"name": "no-tenants", "key": secret, "tenant_ids": []},
        {"name": "bad-tenant", "key": secret, "tenant_ids": ["retail bank"]},
        {"name": "", "key": secret},
    ]
    for case in cases:
        with pytest.raises(ValueError) as err:
            KeyRecord.model_validate(case)
        assert secret not in str(err.value), case["name"]


def test_hashed_keys_are_normalised_and_unknown_scopes_listed() -> None:
    upper = _rec("u", key_sha256=hash_key("dps_x").upper())
    assert StaticKeyStore([upper]).lookup("dps_x") is not None
    with pytest.raises(ValueError) as err:
        _rec("s", key="dps_y", scopes=["detect", "teleport"])
    assert "teleport" in str(err.value)


def test_duplicate_keys_are_refused_naming_records_not_keys() -> None:
    secret = "dps_duplicated_secret"
    records = [_rec("a", key=secret), _rec("b", key_sha256=hash_key(secret))]
    with pytest.raises(ValueError) as err:
        StaticKeyStore(records)
    assert "'a'" in str(err.value) and "'b'" in str(err.value) and secret not in str(err.value)


def test_api_keys_setting_errors_are_value_free() -> None:
    secret = "dps_inside_broken_json"
    for raw in (f'[{{"name": "x", "key": "{secret}"', json.dumps({"name": "x", "key": secret})):
        with pytest.raises(ValueError) as err:
            StaticKeyStore.from_settings(Settings(_env_file=None, api_keys=raw))
        assert secret not in str(err.value) and "DPS_API_KEYS" in str(err.value)


def test_key_ids_are_stable_and_do_not_reveal_the_key() -> None:
    a = StaticKeyStore([_rec("ops", key="dps_plain")]).lookup("dps_plain")
    b = StaticKeyStore([_rec("ops", key="dps_plain")]).lookup("dps_plain")
    assert a is not None and b is not None and a.key_id == b.key_id
    assert "dps_plain" not in a.key_id and "dps_plain" not in a.model_dump_json()


def test_tenant_ids_from_headers_are_checked_for_shape() -> None:
    wild = authenticate(_store(), enabled=True, raw_key="dps_hashed")
    for bad in ("retail bank", "a" * 65, "ten\nant", "Ananya Krishnan", "*", ""):
        if not bad:
            assert resolve_tenant(wild, bad) == "default"  # empty header = no header
            continue
        with pytest.raises(AuthError) as err:
            resolve_tenant(wild, bad)
        assert err.value.status == 400 and bad not in err.value.detail
    single = authenticate(
        StaticKeyStore([_rec("one", key="dps_one", tenant_ids=["retail"])]),
        enabled=True,
        raw_key="dps_one",
    )
    assert resolve_tenant(single, None) == "retail"


def test_rate_limiter_isolates_keys_and_never_exceeds_burst() -> None:
    now = [0.0]
    limiter = RateLimiter(rate=1.0, burst=3, clock=lambda: now[0])
    assert [limiter.check("a") for _ in range(4)] == [True, True, True, False]
    assert limiter.check("b")  # another key has its own bucket
    now[0] += 1000.0
    assert [limiter.check("a") for _ in range(4)] == [True, True, True, False]
    with pytest.raises(ValueError):
        RateLimiter(rate=0.0, burst=1)
    with pytest.raises(ValueError):
        RateLimiter(rate=1.0, burst=0)


def test_log_audit_sink_writes_every_field_and_no_value(capsys) -> None:  # noqa: ANN001
    from dps.observability import configure_logging

    configure_logging("INFO", json_output=True)
    event = AuditEvent(
        trace_id="t2",
        route="/api/v1/reveal",
        tenant_id="retail",
        key_id="k1",
        mode="reveal",
        policy="gdpr-pseudonymise",
        reversible_values=2,
        outcome="ok",
    )
    LogAuditSink().write(event)
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "audit"
    assert set(event.model_dump(mode="json")) <= set(line)
    NullAuditSink().write(event)  # accepts and drops
    assert capsys.readouterr().out == ""
