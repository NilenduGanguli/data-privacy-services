"""DB-backed API keys (M5 Task 9): hashed at rest, expiry, rotation grace, disable/enable, no
privilege escalation, a stale snapshot that fails closed, and the API accepting a DB key."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dps.auth import Principal, StaticKeyStore, hash_key
from dps.auth_db import (
    ChainKeyStore,
    DbKeyStore,
    GrantError,
    create_key,
    disable_key,
    enable_key,
    list_keys,
    rotate_key,
)
from dps.db import acquire

pytestmark = pytest.mark.db
ROOT = Principal(key_id="root", name="root", tenant_ids=["*"], scopes=["*"])


class Clock:
    def __init__(self) -> None:
        self.t = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.t


async def _store(db_settings, clock: Clock | None = None) -> DbKeyStore:  # noqa: ANN001
    store = DbKeyStore(db_settings, now=clock or (lambda: datetime.now(UTC)))
    await store.refresh()
    return store


async def test_create_lookup_and_hash_only_storage(migrated_db, db_settings) -> None:  # noqa: ANN001
    created = await create_key(
        name="kyc-pipeline",
        tenant_ids=["retail"],
        scopes=["detect", "obfuscate"],
        expires_in_days=90,
        actor=ROOT,
        settings=db_settings,
    )
    assert created.key.startswith("dps_") and created.record.key_prefix == created.key[:8]
    store = await _store(db_settings)
    p = store.lookup(created.key)
    assert p is not None and p.tenant_ids == ["retail"]
    assert p.has_scope("obfuscate") and not p.has_scope("reveal")
    async with acquire(None) as conn:
        dump = await conn.fetchval(
            f"SELECT string_agg(t::text, '|') FROM {db_settings.pg_schema}.dps_api_key t"
        )
    assert created.key not in dump and hash_key(created.key) in dump


async def test_expiry_is_enforced_between_refreshes(migrated_db, db_settings) -> None:  # noqa: ANN001
    clock = Clock()
    created = await create_key(
        name="short",
        tenant_ids=["retail"],
        scopes=["detect"],
        expires_in_days=1,
        actor=ROOT,
        settings=db_settings,
    )
    store = await _store(db_settings, clock)
    assert store.lookup(created.key) is not None
    clock.t += timedelta(days=1, seconds=1)
    assert store.lookup(created.key) is None


async def test_rotation_with_grace(migrated_db, db_settings) -> None:  # noqa: ANN001
    clock = Clock()
    old = await create_key(
        name="ledger-batch",
        tenant_ids=["retail"],
        scopes=["jobs"],
        expires_in_days=90,
        actor=ROOT,
        settings=db_settings,
    )
    new, old_view = await rotate_key(
        old.record.id, grace_hours=24, actor=ROOT, settings=db_settings
    )
    assert new.record.name == "ledger-batch" and new.record.rotated_from == old.record.id
    assert old_view.expires_at is not None
    assert old_view.expires_at <= datetime.now(UTC) + timedelta(hours=24, minutes=1)
    store = await _store(db_settings, clock)
    assert store.lookup(old.key) and store.lookup(new.key)
    clock.t += timedelta(hours=25)
    assert store.lookup(old.key) is None and store.lookup(new.key) is not None
    with pytest.raises(GrantError) as again:
        await rotate_key(old.record.id, grace_hours=24, actor=ROOT, settings=db_settings)
    assert again.value.status in (404, 409)


async def test_rotate_now_disables_and_invalidate_is_immediate(migrated_db, db_settings) -> None:  # noqa: ANN001
    old = await create_key(
        name="now-key",
        tenant_ids=["retail"],
        scopes=["detect"],
        expires_in_days=30,
        actor=ROOT,
        settings=db_settings,
    )
    store = await _store(db_settings)
    assert store.lookup(old.key) is not None
    _, old_view = await rotate_key(old.record.id, grace_hours=0, actor=ROOT, settings=db_settings)
    assert old_view.status == "disabled"
    store.invalidate(old.record.id)
    assert store.lookup(old.key) is None


async def test_disable_enable_and_rotated_keys_stay_dead(migrated_db, db_settings) -> None:  # noqa: ANN001
    k = await create_key(
        name="toggle",
        tenant_ids=["retail"],
        scopes=["detect"],
        expires_in_days=30,
        actor=ROOT,
        settings=db_settings,
    )
    assert (await disable_key(k.record.id, actor=ROOT, settings=db_settings)).status == "disabled"
    assert (await enable_key(k.record.id, actor=ROOT, settings=db_settings)).status == "active"
    await rotate_key(k.record.id, grace_hours=0, actor=ROOT, settings=db_settings)
    with pytest.raises(GrantError) as err:
        await enable_key(k.record.id, actor=ROOT, settings=db_settings)
    assert err.value.code == "key_rotated"


@pytest.mark.parametrize(
    ("tenants", "scopes", "code"),
    [
        (["retail"], ["reveal"], "scope_escalation"),
        (["cards"], ["detect"], "tenant_escalation"),
        (["*"], ["detect"], "tenant_escalation"),
        (["retail"], ["*"], "scope_escalation"),
        (["retail"], ["teleport"], "invalid_scope"),
    ],
)
async def test_no_privilege_escalation(migrated_db, db_settings, tenants, scopes, code) -> None:  # noqa: ANN001
    actor = Principal(
        key_id="tenant-admin",
        name="tenant-admin",
        tenant_ids=["retail"],
        scopes=["admin", "detect"],
    )
    with pytest.raises(GrantError) as err:
        await create_key(
            name="x-key",
            tenant_ids=tenants,
            scopes=scopes,
            expires_in_days=30,
            actor=actor,
            settings=db_settings,
        )
    assert err.value.code == code


@pytest.mark.parametrize(
    ("name", "days", "code"),
    [
        ("Bad Name", 30, "invalid_name"),
        ("ok-name", 0, "invalid_expiry"),
        ("ok-name", 366, "invalid_expiry"),
    ],
)
async def test_create_validation(migrated_db, db_settings, name, days, code) -> None:  # noqa: ANN001
    with pytest.raises(GrantError) as err:
        await create_key(
            name=name,
            tenant_ids=["retail"],
            scopes=["detect"],
            expires_in_days=days,
            actor=ROOT,
            settings=db_settings,
        )
    assert err.value.code == code and err.value.status == 422


@pytest.mark.parametrize("tenant", ["_system", "Retail Bank", ""])
async def test_reserved_and_malformed_tenants_are_refused(migrated_db, db_settings, tenant) -> None:  # noqa: ANN001
    with pytest.raises(GrantError) as err:
        await create_key(
            name="bad-tenant",
            tenant_ids=[tenant],
            scopes=["detect"],
            expires_in_days=30,
            actor=ROOT,
            settings=db_settings,
        )
    assert err.value.code == "invalid_tenant" and err.value.status == 422


async def test_a_tenant_admin_cannot_touch_a_wider_key(migrated_db, db_settings) -> None:  # noqa: ANN001
    wide = await create_key(
        name="platform",
        tenant_ids=["*"],
        scopes=["detect"],
        expires_in_days=30,
        actor=ROOT,
        settings=db_settings,
    )
    actor = Principal(
        key_id="tenant-admin",
        name="tenant-admin",
        tenant_ids=["retail"],
        scopes=["admin", "detect"],
    )
    for op in (disable_key, enable_key):
        with pytest.raises(GrantError) as err:
            await op(wide.record.id, actor=actor, settings=db_settings)
        assert err.value.code == "tenant_escalation"
    with pytest.raises(GrantError) as err:
        await rotate_key(wide.record.id, grace_hours=0, actor=actor, settings=db_settings)
    assert err.value.code == "tenant_escalation"
    assert all(
        v.id != wide.record.id
        for v in await list_keys(actor=actor, static=None, settings=db_settings)
    )


async def test_chain_static_first_and_stale_snapshot_fails_closed(migrated_db, db_settings) -> None:  # noqa: ANN001
    import json

    static = StaticKeyStore.from_settings(
        db_settings.model_copy(
            update={
                "api_keys": json.dumps(
                    [
                        {
                            "name": "env",
                            "key": "dps_env_key_for_tests",
                            "tenant_ids": ["*"],
                            "scopes": ["detect"],
                        }
                    ]
                )
            }
        )
    )
    created = await create_key(
        name="db-key",
        tenant_ids=["retail"],
        scopes=["detect"],
        expires_in_days=30,
        actor=ROOT,
        settings=db_settings,
    )
    ticks = [0.0]
    store = DbKeyStore(db_settings, monotonic=lambda: ticks[0])
    await store.refresh()
    chain = ChainKeyStore(static, store)
    env = chain.lookup("dps_env_key_for_tests")
    dbk = chain.lookup(created.key)
    assert env is not None and env.name == "env" and dbk is not None and dbk.name == "db-key"
    ticks[0] += 10 * db_settings.key_cache_seconds + 1
    assert chain.lookup(created.key) is None and chain.lookup("dps_env_key_for_tests") is not None


async def test_last_used_is_flushed_and_list_hides_secrets(migrated_db, db_settings) -> None:  # noqa: ANN001
    created = await create_key(
        name="used-key",
        tenant_ids=["retail"],
        scopes=["detect"],
        expires_in_days=30,
        actor=ROOT,
        settings=db_settings,
    )
    store = await _store(db_settings)
    store.lookup(created.key)
    await store.refresh()
    views = {v.name: v for v in await list_keys(actor=ROOT, static=None, settings=db_settings)}
    assert views["used-key"].last_used_at is not None and views["used-key"].status == "active"
    assert created.key not in str(views) and hash_key(created.key) not in str(views)


def test_api_accepts_a_db_key(db_settings) -> None:  # noqa: ANN001
    import asyncio

    from tests.fixtures.db import _truncate

    try:
        _api_accepts_a_db_key(db_settings)
    finally:
        asyncio.run(_truncate(db_settings))


def _api_accepts_a_db_key(db_settings) -> None:  # noqa: ANN001
    import functools

    from fastapi.testclient import TestClient

    from dps.app import create_app

    settings = db_settings.model_copy(
        update={"auth_enabled": True, "bootstrap_api_key": "", "api_keys": ""}
    )
    with TestClient(create_app(settings)) as client:
        # run on the app's event loop and pool (TestClient's portal), never on the test's loop
        created = client.portal.call(  # type: ignore[union-attr]
            functools.partial(
                create_key,
                name="api-key",
                tenant_ids=["retail"],
                scopes=["detect"],
                expires_in_days=30,
                actor=ROOT,
                settings=settings,
            )
        )
        client.portal.call(client.app.state.db_keys.refresh)  # type: ignore[union-attr,attr-defined]
        ok = client.post(
            "/api/v1/detect",
            json={"text": "mail ann@example.com"},
            headers={"X-API-KEY": created.key},
        )
        assert ok.status_code == 200
        bad = client.post("/api/v1/detect", json={"text": "x"}, headers={"X-API-KEY": "dps_nope"})
        assert bad.status_code == 401
