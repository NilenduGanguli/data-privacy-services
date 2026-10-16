"""D3 bound to the credentials, not the connection row (S3 finding, auth-tenancy #1).

A ``secret_ref`` held by a ``production``-tagged connection is a production credential: a key
without ``admin`` cannot name it in another connection (create or patch), cannot change or delete a
connection that already holds it (review round 1 B2), and any write (``in_place``, ``append``,
``table``, ``files``) through *any* connection that holds it needs ``admin`` — so a ``jobs`` key can
no longer clone the production connection without its tag and rewrite production, nor re-point a
holder at its own server. A retry of a run writing a production connection re-checks the same
rule."""

from __future__ import annotations

import socket
import threading
from typing import Any

import pytest

from dps.auth import Principal
from dps.jobs_models import SpecError
from dps.layers.connectors.targets import check_retry_scope
from tests.api.test_api_connections import FULL, JOBS, PG
from tests.api.test_api_jobs import PURPOSE, seed_snapshot
from tests.connectors.factories import make_connection

PROD_REF = "dps/src/test-pg-rw"


def _create(c: Any, name: str, headers: dict[str, str], **kw: Any) -> Any:  # noqa: ANN401
    body = {"name": name, "engine": "postgres", "params": PG, "roles": ["source", "target"], **kw}
    return c.post("/api/v1/connections", json=body, headers=headers)


def _in_place(conn_id: str, snap: str) -> dict[str, Any]:
    return {
        "mode": "obfuscate",
        "snapshot_id": snap,
        "source": {"connection_id": conn_id, "object": {"owner": "bank", "name": "customers"}},
        "target": {"write_mode": "in_place", "confirm_write": True},
        "purpose": PURPOSE,
    }


@pytest.mark.db
def test_a_jobs_key_cannot_clone_a_production_connection(api_client, db_settings) -> None:  # noqa: ANN001
    c, sink = api_client
    prod = _create(c, "bank-prod", FULL, secret_ref=PROD_REF, tags=["production"])
    assert prod.status_code == 201, prod.text
    seen = c.get(f"/api/v1/connections/{prod.json()['id']}", headers=JOBS).json()
    assert seen["secret_ref"] == PROD_REF  # a name, not a secret: visible to the jobs key
    # the exploit: the same params and secret_ref, no production tag
    clone = _create(c, "prod-clone", JOBS, secret_ref=seen["secret_ref"])
    assert clone.status_code == 403 and clone.json()["code"] == "admin_scope_required"
    # nor by moving another connection onto the production credential
    dev = _create(c, "bank-dev", JOBS, secret_ref="dps/src/test-pg")
    assert dev.status_code == 201, dev.text
    moved = c.patch(
        f"/api/v1/connections/{dev.json()['id']}", json={"secret_ref": PROD_REF}, headers=JOBS
    )
    assert moved.status_code == 403 and moved.json()["code"] == "admin_scope_required"
    assert c.get(f"/api/v1/connections/{dev.json()['id']}", headers=JOBS).json()["secret_ref"] == (
        "dps/src/test-pg"
    )
    refused = [e for e in sink.events if e.outcome == "rejected"]
    assert {e.action for e in refused} >= {"connection.create", "connection.update"}


@pytest.mark.db
def test_in_place_through_any_holder_of_a_production_credential_needs_admin(
    api_client, db_settings
) -> None:  # noqa: ANN001
    c, _ = api_client
    assert (
        _create(c, "bank-prod", FULL, secret_ref=PROD_REF, tags=["production"]).status_code == 201
    )
    # an untagged holder of the production credential (an admin made it, or it predates the rule)
    clone = _create(c, "prod-clone", FULL, secret_ref=PROD_REF)
    assert clone.status_code == 201, clone.text
    clone_id = clone.json()["id"]
    snap = seed_snapshot(db_settings, "t1", clone_id, "bank.customers")
    assert c.post(f"/api/v1/snapshots/{snap}/accept", headers=JOBS).status_code == 200
    for route in ("/api/v1/jobs/run/validate", "/api/v1/jobs/run"):
        refused = c.post(route, json=_in_place(clone_id, snap), headers=JOBS)
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "admin_scope_required"
    # the admin passes the gate (the run's later checks still apply)
    admin = c.post("/api/v1/jobs/run/validate", json=_in_place(clone_id, snap), headers=FULL)
    assert admin.json().get("code") != "admin_scope_required"
    # a holder of a non-production credential keeps the jobs-scope rules
    dev = _create(c, "bank-dev", JOBS, secret_ref="dps/src/test-pg")
    dev_snap = seed_snapshot(db_settings, "t1", dev.json()["id"], "bank.customers")
    assert c.post(f"/api/v1/snapshots/{dev_snap}/accept", headers=JOBS).status_code == 200
    plain = c.post(
        "/api/v1/jobs/run/validate", json=_in_place(dev.json()["id"], dev_snap), headers=JOBS
    )
    assert plain.json().get("code") != "admin_scope_required"


class _Listener:
    """A TCP listener that only counts the connections it receives (S3 review r1 B2's fake
    server: no login may ever reach an endpoint the caller chose)."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            conn.close()

    def close(self) -> None:
        self.sock.close()


@pytest.mark.db
@pytest.mark.parametrize("holder_made_by", ["jobs-key-before-the-tag", "admin"])
def test_a_jobs_key_cannot_change_or_delete_a_production_credential_holder(
    api_client, holder_made_by: str
) -> None:  # noqa: ANN001
    """S3 review r1 B2: an untagged connection that already holds the production credential was
    re-pointed by a ``jobs`` key that restated the same ``secret_ref`` (200), and the next test sent
    the production login and password to the caller's server; the same key deleted it (204)."""
    c, _ = api_client
    if holder_made_by == "admin":
        prod = _create(c, "bank-prod", FULL, secret_ref=PROD_REF, tags=["production"])
        holder = _create(c, "prod-holder", FULL, secret_ref=PROD_REF)
    else:  # a jobs key made it before the production connection was tagged
        holder = _create(c, "early-holder", JOBS, secret_ref=PROD_REF)
        prod = _create(c, "bank-prod", FULL, secret_ref=PROD_REF, tags=["production"])
    assert prod.status_code == 201 and holder.status_code == 201, (prod.text, holder.text)
    holder_id = holder.json()["id"]
    url = f"/api/v1/connections/{holder_id}"
    before = c.get(url, headers=JOBS).json()
    fake = _Listener()
    try:
        moved = {**PG, "host": "127.0.0.1", "port": fake.port}
        for body in (
            {"params": moved, "secret_ref": PROD_REF},  # the exploit: the same ref restated
            {"params": moved},
            {"secret_ref": "dps/src/test-pg"},
            {"credentials": {"username": "u", "password": "pw-holder-distinct"}},
            {"roles": ["source"]},
            {"name": "holder-renamed"},
        ):
            refused = c.patch(url, json=body, headers=JOBS)
            assert refused.status_code == 403, (body, refused.text)
            assert refused.json()["code"] == "admin_scope_required"
        after = c.get(url, headers=JOBS).json()
        for field in ("name", "params", "secret_ref", "roles", "tags", "has_secret"):
            assert after[field] == before[field], field
        c.post(f"{url}/test", json={}, headers=JOBS)  # tests the stored endpoint only
        assert fake.connections == 0  # no login ever reached the caller's server
        gone = c.delete(url, headers=JOBS)
        assert gone.status_code == 403 and gone.json()["code"] == "admin_scope_required"
        assert c.get(url, headers=JOBS).status_code == 200
    finally:
        fake.close()
    # the admin scope still may
    renamed = c.patch(url, json={"name": "holder-renamed"}, headers=FULL)
    assert renamed.status_code == 200, renamed.text
    assert c.delete(url, headers=FULL).status_code == 204
    # a holder of a non-production credential keeps the jobs-scope rules
    dev = _create(c, "bank-dev", JOBS, secret_ref="dps/src/test-pg")
    dev_url = f"/api/v1/connections/{dev.json()['id']}"
    assert c.patch(dev_url, json={"name": "bank-dev-2"}, headers=JOBS).status_code == 200
    assert c.delete(dev_url, headers=JOBS).status_code == 204


def _job(write_mode: str, *, target: str | None = None) -> Any:  # noqa: ANN401
    from datetime import UTC, datetime

    from dps.jobs import Job, JobKind, JobPhase, JobState

    now = datetime.now(UTC)
    return Job(
        id="j_1",
        tenant_id="t1",
        kind=JobKind.run,
        state=JobState.failed,
        phase=JobPhase.prepare,
        mode="obfuscate",
        connection_id="c_src",
        target_connection_id=target,
        spec={"target": {"write_mode": write_mode}},
        created_at=now,
        updated_at=now,
    )


def test_a_retry_of_a_production_write_rechecks_admin() -> None:
    jobs_key = Principal(key_id="k", name="jobs", tenant_ids=["t1"], scopes=["jobs"])
    admin_key = Principal(key_id="a", name="admin", tenant_ids=["t1"], scopes=["jobs", "admin"])
    prod = make_connection("c_src", roles=["source", "target"], tags=["production"])
    clone = make_connection("c_src", roles=["source", "target"])  # untagged, same credential
    refs = frozenset({clone.secret_ref or ""})
    for conn, production_refs in ((prod, frozenset()), (clone, refs)):
        for mode in ("in_place", "append"):
            with pytest.raises(SpecError) as refused:
                check_retry_scope(
                    _job(mode), conn, principal=jobs_key, production_refs=production_refs
                )
            assert refused.value.code == "admin_scope_required" and refused.value.status == 403
            check_retry_scope(
                _job(mode), conn, principal=admin_key, production_refs=production_refs
            )
    # every write mode: a table/files retry into a production connection (tag or credentials)
    # needs admin as well (S1-M3 r1 security B1 on main, kept at the fsec merge)
    for conn, production_refs in ((prod, frozenset()), (clone, refs)):
        for mode in ("table", "files"):
            with pytest.raises(SpecError) as refused:
                check_retry_scope(
                    _job(mode, target="c_src"),
                    conn,
                    principal=jobs_key,
                    production_refs=production_refs,
                )
            assert refused.value.code == "admin_scope_required"
            check_retry_scope(
                _job(mode, target="c_src"),
                conn,
                principal=admin_key,
                production_refs=production_refs,
            )
    # a non-production connection keeps the jobs-scope retry, whatever the write mode
    uat = make_connection("c_uat", roles=["source", "target"])
    for mode in ("in_place", "append", "table", "files"):
        check_retry_scope(_job(mode), uat, principal=jobs_key, production_refs=frozenset())
    check_retry_scope(_job("in_place"), clone, principal=jobs_key, production_refs=frozenset())
