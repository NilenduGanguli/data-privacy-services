"""Snapshot repository: create, list, override, accept, supersede (contract §5.4)."""

from __future__ import annotations

import pytest

from dps import snapshots
from dps.engine.policy import MethodSpec
from dps.jobs_models import SnapshotOverride
from tests.connectors.factories import make_snapshot

pytestmark = pytest.mark.db


async def test_create_list_override_accept_supersede(migrated_db) -> None:  # noqa: ANN001
    first = make_snapshot("t1", connection_id="c_1", object_ref="bank.customers")
    await snapshots.create_snapshot(first)
    patched = await snapshots.set_overrides(
        "t1",
        first.id,
        [
            SnapshotOverride(path="$.email", method=MethodSpec(method="det_token")),
            SnapshotOverride(path="$.full_name", anchor=True),
        ],
        fpe_enabled=False,
    )
    email = next(col for col in patched.columns if col.name == "email")
    assert email.method.method == "det_token" and email.override
    assert patched.content_hash != first.content_hash
    assert [col.name for col in patched.columns if col.anchor] == ["full_name"]
    reread = await snapshots.get_snapshot("t1", first.id)
    assert reread is not None and reread.content_hash == patched.content_hash
    assert reread.columns == patched.columns and reread.overrides == patched.overrides
    accepted = await snapshots.accept_snapshot("t1", first.id, accepted_by="k1")
    assert accepted.status == "accepted" and accepted.accepted_by == "k1"
    assert accepted.accepted_at is not None
    with pytest.raises(snapshots.SnapshotStateError) as err:
        await snapshots.set_overrides("t1", first.id, [], fpe_enabled=False)
    assert (err.value.code, err.value.status) == ("snapshot_not_proposed", 409)
    second = make_snapshot("t1", connection_id="c_1", object_ref="bank.customers")
    await snapshots.create_snapshot(second)
    await snapshots.accept_snapshot("t1", second.id, accepted_by="k1")
    old = await snapshots.get_snapshot("t1", first.id)
    assert old is not None and old.status == "superseded"
    items, _ = await snapshots.list_snapshots("t1", connection_id="c_1", status="accepted")
    assert [s.id for s in items] == [second.id] and await snapshots.get_snapshot(
        "t2", second.id
    ) is None
    with pytest.raises(snapshots.SnapshotStateError):
        await snapshots.set_overrides(
            "t1", second.id, [SnapshotOverride(path="$.nope")], fpe_enabled=False
        )


async def test_overrides_accumulate_per_path(migrated_db) -> None:  # noqa: ANN001
    snap = make_snapshot("t1", connection_id="c_2", object_ref="bank.customers")
    await snapshots.create_snapshot(snap)
    await snapshots.set_overrides(
        "t1",
        snap.id,
        [SnapshotOverride(path="$.email", method=MethodSpec(method="det_token"))],
        fpe_enabled=False,
    )
    later = await snapshots.set_overrides(
        "t1",
        snap.id,
        [
            SnapshotOverride(path="$.email", anchor=True),
            SnapshotOverride(path="$.ssn", entity_type=None),
        ],
        fpe_enabled=False,
    )
    cols = {c.name: c for c in later.columns}
    # the earlier method override for $.email survives a later override of another field
    assert cols["email"].method.method == "det_token" and cols["email"].anchor
    assert cols["ssn"].entity_type is None and cols["ssn"].override
    assert [o.path for o in later.overrides] == ["$.email", "$.ssn"]
    with pytest.raises(snapshots.SnapshotStateError) as err:
        await snapshots.set_overrides(
            "t1", snap.id, [SnapshotOverride(path="$.nope")], fpe_enabled=False
        )
    assert err.value.code == "override_unknown_path"
    unchanged = await snapshots.get_snapshot("t1", snap.id)
    assert unchanged is not None and unchanged.content_hash == later.content_hash


async def test_missing_snapshots_and_paging(migrated_db) -> None:  # noqa: ANN001
    with pytest.raises(snapshots.SnapshotStateError) as err:
        await snapshots.accept_snapshot("t1", "snap_missing", accepted_by="k1")
    assert (err.value.code, err.value.status) == ("snapshot_not_found", 404)
    made = []
    for i in range(3):
        s = make_snapshot("t1", connection_id="c_3", object_ref=f"bank.t{i}")
        await snapshots.create_snapshot(s)
        made.append(s.id)
    page, cursor = await snapshots.list_snapshots("t1", connection_id="c_3", limit=2)
    rest, cursor2 = await snapshots.list_snapshots(
        "t1", connection_id="c_3", limit=2, cursor=cursor
    )
    assert cursor is not None and cursor2 is None
    assert sorted(s.id for s in page + rest) == sorted(made)
    assert [s.id for s in (await snapshots.list_snapshots("t1", object_ref="bank.t1"))[0]] == [
        made[1]
    ]
    assert (await snapshots.list_snapshots("t2"))[0] == []
