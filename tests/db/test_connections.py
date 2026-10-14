"""Connection registry against the metadata database (contract §5.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dps import db, jobs
from dps.connections import (
    ConnectionPatch,
    ConnectionStoreError,
    Credentials,
    create_connection,
    delete_connection,
    get_connection,
    list_connections,
    load_credentials,
    record_test,
    touch_tenant,
    update_connection,
)
from dps.jobs import JobKind, JobPhase
from dps.jobs_models import ConnectionSpec
from dps.layers.connectors.base import ConnectionTestResult
from dps.secrets import SecretUnavailable

pytestmark = pytest.mark.db
PG = {"host": "source-pg", "port": 5432, "database": "bank"}


async def test_secret_ref_and_inline_round_trip(migrated_db, kek, tmp_path) -> None:  # noqa: ANN001
    d = tmp_path / "t1" / "dps" / "src" / "bank-pg-ro"
    d.mkdir(parents=True)
    (d / "username").write_text("dps_reader")
    (d / "password").write_text("dps_reader")
    settings = migrated_db.model_copy(update={"secret_dir": str(tmp_path)})
    ref = await create_connection(
        "t1",
        ConnectionSpec(
            name="bank-pg-ro",
            engine="postgres",
            params=PG,
            secret_ref="dps/src/bank-pg-ro",
            tags=["production"],
        ),
        settings=settings,
        kek=kek,
        created_by="k1",
    )
    assert ref.production and ref.has_secret is False and ref.secret_ref == "dps/src/bank-pg-ro"
    assert (await load_credentials(ref, settings=settings, kek=kek)).username == "dps_reader"
    inline = await create_connection(
        "t1",
        ConnectionSpec(
            name="dev-pg",
            engine="postgres",
            params=PG,
            credentials={"username": "u", "password": "s3cret-pw"},
        ),
        settings=settings,
        kek=kek,
        created_by="k1",
    )
    assert inline.has_secret and inline.secret_expires_at is not None
    assert "s3cret-pw" not in repr(inline) + inline.model_dump_json()
    async with db.acquire("t1") as conn:
        blob = await conn.fetchval(
            "SELECT secret_enc FROM dps.dps_connection WHERE id = $1", inline.id
        )
        params = await conn.fetchval(
            "SELECT params::text FROM dps.dps_connection WHERE id = $1", inline.id
        )
    assert b"s3cret-pw" not in blob and "s3cret" not in params
    creds = await load_credentials(inline, settings=settings, kek=kek)
    assert creds.password.get_secret_value() == "s3cret-pw" and "s3cret" not in repr(creds)
    async with db.acquire("t1") as conn:
        await conn.execute(
            "UPDATE dps.dps_connection SET secret_enc = NULL WHERE id = $1", inline.id
        )
    with pytest.raises(SecretUnavailable):
        await load_credentials(inline, settings=settings, kek=kek)


async def test_expired_inline_secret_and_missing_kek(migrated_db, kek) -> None:  # noqa: ANN001
    c = await create_connection(
        "t1",
        ConnectionSpec(
            name="dev-pg",
            engine="postgres",
            params=PG,
            credentials={"username": "u", "password": "p"},
        ),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    with pytest.raises(ConnectionStoreError) as err:
        await load_credentials(c, settings=migrated_db, kek=None)
    assert (err.value.code, err.value.status) == ("kek_unconfigured", 503)
    async with db.acquire("t1") as conn:
        await conn.execute(
            "UPDATE dps.dps_connection SET secret_expires_at = $2 WHERE id = $1",
            c.id,
            datetime.now(UTC) - timedelta(minutes=1),
        )
    with pytest.raises(SecretUnavailable):
        await load_credentials(c, settings=migrated_db, kek=kek)
    with pytest.raises(SecretUnavailable):  # another tenant never reaches the sealed blob
        await load_credentials(
            c.model_copy(update={"tenant_id": "t2"}), settings=migrated_db, kek=kek
        )


async def test_rules_listing_isolation_and_tests(migrated_db, kek) -> None:  # noqa: ANN001
    spec = ConnectionSpec(
        name="dev-pg", engine="postgres", params=PG, credentials={"username": "u", "password": "p"}
    )
    with pytest.raises(ConnectionStoreError) as err:
        await create_connection(
            "t1",
            spec,
            settings=migrated_db.model_copy(update={"inline_secrets_allowed": False}),
            kek=kek,
            created_by=None,
        )
    assert (err.value.code, err.value.status) == ("inline_secrets_disabled", 422)
    with pytest.raises(ConnectionStoreError) as err2:
        await create_connection("t1", spec, settings=migrated_db, kek=None, created_by=None)
    assert err2.value.code == "kek_unconfigured"
    made = [
        await create_connection(
            "t1",
            spec.model_copy(update={"name": f"pg-{i}"}),
            settings=migrated_db,
            kek=kek,
            created_by=None,
        )
        for i in range(3)
    ]
    with pytest.raises(ConnectionStoreError) as dup:
        await create_connection(
            "t1",
            spec.model_copy(update={"name": "pg-0"}),
            settings=migrated_db,
            kek=kek,
            created_by=None,
        )
    assert (dup.value.code, dup.value.status) == ("connection_name_taken", 409)
    first, cursor = await list_connections("t1", limit=2)
    second, last = await list_connections("t1", limit=2, cursor=cursor)
    assert cursor is not None and last is None
    assert [c.id for c in first + second] == [c.id for c in reversed(made)]
    with pytest.raises(ValueError):
        await list_connections("t1", cursor="not-a-cursor")
    assert await get_connection("t2", made[0].id) is None
    assert await list_connections("t2") == ([], None)
    updated = await update_connection(
        "t1",
        made[0].id,
        ConnectionPatch(tags=["uat"], roles={"source", "target"}),
        settings=migrated_db,
        kek=kek,
    )
    assert updated is not None and updated.tags == ["uat"]
    assert set(updated.roles) == {"source", "target"} and updated.has_secret
    await record_test(
        "t1",
        made[0].id,
        ConnectionTestResult(ok=True, engine="postgres", checks=[], capabilities={"copy": True}),
    )
    tested = await get_connection("t1", made[0].id)
    assert tested is not None and tested.last_test is not None and tested.last_test.ok
    assert tested.capabilities == {"copy": True}
    assert (
        await update_connection(
            "t2", made[0].id, ConnectionPatch(tags=[]), settings=migrated_db, kek=kek
        )
        is None
    )


async def test_patch_rules_secret_switch_and_stale_test(migrated_db, kek, tmp_path) -> None:  # noqa: ANN001
    c = await create_connection(
        "t1",
        ConnectionSpec(
            name="dev-pg",
            engine="postgres",
            params=PG,
            credentials={"username": "u", "password": "p"},
        ),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    await record_test("t1", c.id, ConnectionTestResult(ok=True, engine="postgres"))
    with pytest.raises(ConnectionStoreError) as err:  # tagging production needs admin first
        await update_connection(
            "t1", c.id, ConnectionPatch(tags=["production"]), settings=migrated_db, kek=kek
        )
    assert (err.value.code, err.value.status) == ("admin_scope_required", 403)
    with pytest.raises(ConnectionStoreError) as err:  # production needs secret_ref
        await update_connection(
            "t1",
            c.id,
            ConnectionPatch(tags=["production"]),
            settings=migrated_db,
            kek=kek,
            admin=True,
        )
    assert (err.value.code, err.value.status) == ("connection_invalid", 422)
    renamed = await update_connection(
        "t1", c.id, ConnectionPatch(name="dev-pg-2"), settings=migrated_db, kek=kek
    )
    assert renamed is not None and renamed.name == "dev-pg-2" and renamed.last_test is not None
    switched = await update_connection(
        "t1", c.id, ConnectionPatch(secret_ref="dps/src/bank-pg-ro"), settings=migrated_db, kek=kek
    )
    assert switched is not None and switched.secret_ref == "dps/src/bank-pg-ro"
    assert switched.has_secret is False and switched.secret_expires_at is None
    assert switched.last_test is None  # the old test no longer describes this connection
    back = await update_connection(
        "t1",
        c.id,
        ConnectionPatch(credentials={"username": "u2", "password": "new-pw"}),
        settings=migrated_db,
        kek=kek,
    )
    assert back is not None and back.has_secret and back.secret_ref is None
    creds = await load_credentials(back, settings=migrated_db, kek=kek)
    assert (creds.username, creds.password.get_secret_value()) == ("u2", "new-pw")


async def test_a_pvc_connection_never_takes_a_secret(migrated_db, kek) -> None:  # noqa: ANN001
    c = await create_connection(
        "t1",
        ConnectionSpec(name="exports", engine="pvc", params={"base_path": "x"}, roles={"target"}),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    assert (await load_credentials(c, settings=migrated_db, kek=None)) == Credentials()
    for patch in (
        ConnectionPatch(credentials={"username": "u", "password": "p"}),
        ConnectionPatch(secret_ref="dps/x"),
        ConnectionPatch(roles={"source"}),
    ):
        with pytest.raises(ConnectionStoreError) as err:
            await update_connection("t1", c.id, patch, settings=migrated_db, kek=kek)
        assert err.value.code == "connection_invalid"
    async with db.acquire("t1") as conn:
        assert await conn.fetchval(
            "SELECT secret_enc IS NULL AND secret_ref IS NULL FROM dps.dps_connection WHERE id = $1",
            c.id,
        )


async def test_delete_refused_while_a_job_is_active(migrated_db, kek) -> None:  # noqa: ANN001
    c = await create_connection(
        "t1",
        ConnectionSpec(name="pg", engine="postgres", params=PG, secret_ref="dps/x"),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    job = await jobs.create_job(
        tenant_id="t1", kind=JobKind.discover, phase=JobPhase.discover, spec={}, connection_id=c.id
    )
    with pytest.raises(ConnectionStoreError) as err:
        await delete_connection("t1", c.id)
    assert (err.value.code, err.value.status) == ("connection_in_use", 409)
    assert await jobs.cancel("t1", job.id) is not None
    assert await delete_connection("t2", c.id) is False
    assert await delete_connection("t1", c.id) is True
    assert await get_connection("t1", c.id) is None


async def test_creating_a_connection_registers_the_tenant(migrated_db, kek) -> None:  # noqa: ANN001
    await create_connection(
        "t9",
        ConnectionSpec(name="pg", engine="postgres", params=PG, secret_ref="dps/x"),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    await touch_tenant("t9")  # idempotent
    async with db.acquire(None) as conn:
        assert (
            await conn.fetchval("SELECT count(*) FROM dps.dps_tenant WHERE tenant_id = 't9'") == 1
        )


def _write_secret(root, tenant: str, ref: str) -> None:  # noqa: ANN001
    folder = root / tenant / ref
    folder.mkdir(parents=True)
    (folder / "username").write_text("dps_reader")
    (folder / "password").write_text("t1-only-password")


async def test_a_tenant_cannot_create_update_or_use_another_tenants_secret_ref(
    migrated_db,
    kek,
    tmp_path,  # noqa: ANN001
) -> None:
    _write_secret(tmp_path, "t1", "dps/src/bank-pg-ro")
    (tmp_path / "t2").mkdir()
    settings = migrated_db.model_copy(update={"secret_dir": str(tmp_path)})
    own = await create_connection(
        "t1",
        ConnectionSpec(name="pg", engine="postgres", params=PG, secret_ref="dps/src/bank-pg-ro"),
        settings=settings,
        kek=kek,
        created_by=None,
    )
    assert (await load_credentials(own, settings=settings, kek=kek)).username == "dps_reader"
    foreign = ConnectionSpec(
        name="pg", engine="postgres", params=PG, secret_ref="t1/dps/src/bank-pg-ro"
    )
    with pytest.raises(ConnectionStoreError) as err:  # create: refused by its tenant prefix
        await create_connection("t2", foreign, settings=settings, kek=kek, created_by=None)
    assert (err.value.code, err.value.status) == ("secret_ref_foreign_tenant", 403)
    assert "t1" not in err.value.detail and "bank-pg-ro" not in err.value.detail
    # the same name in t2 is t2's own (missing) secret: stored, but it never opens t1's files
    same_name = await create_connection(
        "t2",
        foreign.model_copy(update={"secret_ref": "dps/src/bank-pg-ro"}),
        settings=settings,
        kek=kek,
        created_by=None,
    )
    with pytest.raises(SecretUnavailable):
        await load_credentials(same_name, settings=settings, kek=kek)
    with pytest.raises(ConnectionStoreError) as err:  # update: refused the same way
        await update_connection(
            "t2",
            same_name.id,
            ConnectionPatch(secret_ref="t1/dps/src/bank-pg-ro"),
            settings=settings,
            kek=kek,
        )
    assert (err.value.code, err.value.status) == ("secret_ref_foreign_tenant", 403)
    for bad in ("_shared/dps/src/bank-pg-ro", "dps/./src"):
        with pytest.raises(ConnectionStoreError) as err:
            await update_connection(
                "t2", same_name.id, ConnectionPatch(secret_ref=bad), settings=settings, kek=kek
            )
        assert err.value.code in ("secret_ref_not_shared", "secret_ref_invalid")
    # a CA bundle reference is a secret reference too
    with pytest.raises(ConnectionStoreError) as err:
        await create_connection(
            "t2",
            ConnectionSpec(
                name="pg-ca",
                engine="postgres",
                params={**PG, "options": {"sslrootcert_ref": "t1/dps/src/bank-pg-ro"}},
                secret_ref="dps/src/x",
            ),
            settings=settings,
            kek=kek,
            created_by=None,
        )
    assert err.value.code == "secret_ref_foreign_tenant"
    # defence in depth: a row that names another tenant's folder (planted past the API) is refused
    async with db.acquire("t2") as conn:
        await conn.execute(
            "UPDATE dps.dps_connection SET secret_ref = $2 WHERE id = $1",
            same_name.id,
            "t1/dps/src/bank-pg-ro",
        )
    planted = await get_connection("t2", same_name.id)
    assert planted is not None
    with pytest.raises(SecretUnavailable):
        await load_credentials(planted, settings=settings, kek=kek)


async def test_a_shared_secret_ref_works_only_for_listed_tenants(
    migrated_db,
    kek,
    tmp_path,  # noqa: ANN001
) -> None:
    _write_secret(tmp_path, "_shared", "s3/exports")
    settings = migrated_db.model_copy(update={"secret_dir": str(tmp_path)})
    spec = ConnectionSpec(
        name="exports",
        engine="s3",
        params={"bucket": "dps-exports"},
        secret_ref="_shared/s3/exports",
        roles={"target"},
    )
    with pytest.raises(ConnectionStoreError) as err:
        await create_connection("t1", spec, settings=settings, kek=kek, created_by=None)
    assert (err.value.code, err.value.status) == ("secret_ref_not_shared", 403)
    (tmp_path / "_shared" / "s3" / "exports" / "tenants").write_text("t1\n")
    made = await create_connection("t1", spec, settings=settings, kek=kek, created_by=None)
    assert (await load_credentials(made, settings=settings, kek=kek)).username == "dps_reader"
    with pytest.raises(ConnectionStoreError) as err:
        await create_connection("t2", spec, settings=settings, kek=kek, created_by=None)
    assert err.value.code == "secret_ref_not_shared"
    # the allow-list is read again when credentials load: a tenant taken off it loses access
    (tmp_path / "_shared" / "s3" / "exports" / "tenants").write_text("t3\n")
    with pytest.raises(SecretUnavailable):
        await load_credentials(made, settings=settings, kek=kek)


async def _prod(settings, kek) -> object:  # noqa: ANN001
    return await create_connection(
        "t1",
        ConnectionSpec(
            name="prod-pg",
            engine="postgres",
            params=PG,
            secret_ref="dps/src/bank-pg-ro",
            tags=["production"],
        ),
        settings=settings,
        kek=kek,
        created_by=None,
    )


async def test_every_mutation_of_a_production_connection_needs_admin(migrated_db, kek) -> None:  # noqa: ANN001
    prod = await _prod(migrated_db, kek)
    refused = [
        ConnectionPatch(name="prod-pg-2"),  # even a rename
        ConnectionPatch(tags=[]),  # un-tagging
        ConnectionPatch(roles={"source", "target"}),
        ConnectionPatch(secret_ref="dps/src/other"),  # credential rotation
        ConnectionPatch(
            params={**PG, "host": "elsewhere.example"}, secret_ref="dps/src/bank-pg-ro"
        ),
    ]
    for patch in refused:
        with pytest.raises(ConnectionStoreError) as err:
            await update_connection("t1", prod.id, patch, settings=migrated_db, kek=kek)
        assert (err.value.code, err.value.status) == ("admin_scope_required", 403)
    unchanged = await get_connection("t1", prod.id)
    assert unchanged is not None and unchanged.model_dump() == prod.model_dump()
    with pytest.raises(ConnectionStoreError) as err:
        await delete_connection("t1", prod.id)
    assert (err.value.code, err.value.status) == ("admin_scope_required", 403)
    # a connection that becomes production needs admin too
    dev = await create_connection(
        "t1",
        ConnectionSpec(name="dev-pg", engine="postgres", params=PG, secret_ref="dps/src/x"),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    with pytest.raises(ConnectionStoreError) as err:
        await update_connection(
            "t1", dev.id, ConnectionPatch(tags=["production"]), settings=migrated_db, kek=kek
        )
    assert err.value.code == "admin_scope_required"
    tagged = await update_connection(
        "t1",
        dev.id,
        ConnectionPatch(tags=["production"]),
        settings=migrated_db,
        kek=kek,
        admin=True,
    )
    assert tagged is not None and tagged.production
    moved = await update_connection(
        "t1",
        prod.id,
        ConnectionPatch(params={**PG, "host": "replica.example"}),
        settings=migrated_db,
        kek=kek,
        admin=True,
    )
    assert moved is not None and moved.params.host == "replica.example"
    assert moved.secret_ref == "dps/src/bank-pg-ro"  # an admin may move it with its secret
    assert await delete_connection("t1", prod.id, admin=True) is True


async def test_an_endpoint_change_needs_the_credentials_again_or_admin(migrated_db, kek) -> None:  # noqa: ANN001
    inline = await create_connection(
        "t1",
        ConnectionSpec(
            name="dev-pg",
            engine="postgres",
            params=PG,
            credentials={"username": "u", "password": "old-pw-distinct"},
        ),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    for params in (
        {**PG, "host": "attacker.example"},
        {**PG, "port": 6543},
        {**PG, "database": "other"},
        {**PG, "options": {"sslmode": "disable"}},
    ):
        with pytest.raises(ConnectionStoreError) as err:
            await update_connection(
                "t1", inline.id, ConnectionPatch(params=params), settings=migrated_db, kek=kek
            )
        assert (err.value.code, err.value.status) == (
            "connection_endpoint_change_requires_credentials",
            403,
        )
        assert "attacker" not in err.value.detail and "old-pw" not in err.value.detail
    kept = await get_connection("t1", inline.id)
    assert kept is not None and kept.params.host == PG["host"] and kept.has_secret
    assert (
        await load_credentials(kept, settings=migrated_db, kek=kek)
    ).password.get_secret_value() == ("old-pw-distinct")
    # a param that does not decide where the credentials go needs nothing
    timeout = await update_connection(
        "t1",
        inline.id,
        ConnectionPatch(params={**PG, "options": {"connect_timeout": "5"}}),
        settings=migrated_db,
        kek=kek,
    )
    assert timeout is not None and timeout.params.options == {"connect_timeout": "5"}
    # new credentials in the same request: allowed, and the old sealed secret is gone
    moved = await update_connection(
        "t1",
        inline.id,
        ConnectionPatch(
            params={**PG, "host": "new-host.example"},
            credentials={"username": "u2", "password": "new-pw-distinct"},
        ),
        settings=migrated_db,
        kek=kek,
    )
    assert moved is not None and moved.params.host == "new-host.example"
    creds = await load_credentials(moved, settings=migrated_db, kek=kek)
    assert (creds.username, creds.password.get_secret_value()) == ("u2", "new-pw-distinct")
    # admin may move it without the credentials
    by_admin = await update_connection(
        "t1",
        inline.id,
        ConnectionPatch(params={**PG, "host": "third.example"}),
        settings=migrated_db,
        kek=kek,
        admin=True,
    )
    assert by_admin is not None and by_admin.params.host == "third.example" and by_admin.has_secret
    # a secret_ref connection: the reference must be restated with the move
    ref = await create_connection(
        "t1",
        ConnectionSpec(name="ref-pg", engine="postgres", params=PG, secret_ref="dps/src/a"),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    with pytest.raises(ConnectionStoreError) as err:
        await update_connection(
            "t1",
            ref.id,
            ConnectionPatch(params={**PG, "host": "attacker.example"}),
            settings=migrated_db,
            kek=kek,
        )
    assert err.value.code == "connection_endpoint_change_requires_credentials"
    restated = await update_connection(
        "t1",
        ref.id,
        ConnectionPatch(params={**PG, "host": "uat.example"}, secret_ref="dps/src/a"),
        settings=migrated_db,
        kek=kek,
    )
    assert restated is not None and restated.params.host == "uat.example"


async def test_object_store_endpoints_and_secretless_connections(migrated_db, kek) -> None:  # noqa: ANN001
    s3 = await create_connection(
        "t1",
        ConnectionSpec(
            name="exports",
            engine="s3",
            params={"bucket": "dps-exports", "endpoint_url": "http://minio:9000"},
            secret_ref="dps/s3/exports",
            roles={"target"},
        ),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    for params in (
        {"bucket": "dps-exports", "endpoint_url": "https://attacker.example"},
        {"bucket": "someone-elses", "endpoint_url": "http://minio:9000"},
        {"bucket": "dps-exports", "endpoint_url": "http://minio:9000", "region": "us-east-1"},
    ):
        with pytest.raises(ConnectionStoreError) as err:
            await update_connection(
                "t1", s3.id, ConnectionPatch(params=params), settings=migrated_db, kek=kek
            )
        assert err.value.code == "connection_endpoint_change_requires_credentials"
    prefixed = await update_connection(
        "t1",
        s3.id,
        ConnectionPatch(
            params={"bucket": "dps-exports", "endpoint_url": "http://minio:9000", "prefix": "loans"}
        ),
        settings=migrated_db,
        kek=kek,
    )
    assert prefixed is not None and prefixed.params.prefix == "loans"
    pvc = await create_connection(
        "t1",
        ConnectionSpec(name="files", engine="pvc", params={"base_path": "a"}, roles={"target"}),
        settings=migrated_db,
        kek=kek,
        created_by=None,
    )
    moved = await update_connection(  # no credentials to redirect
        "t1", pvc.id, ConnectionPatch(params={"base_path": "b"}), settings=migrated_db, kek=kek
    )
    assert moved is not None and moved.params.base_path == "b"
