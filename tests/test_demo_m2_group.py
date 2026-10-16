"""``tools/demo_m2.py`` survives a reset metadata database (A-N1).

The demo keeps its job group's key and seed in ``var/demo/<group>.json`` and reuses the group on the
next run. After ``docker compose down -v`` that group is gone (404 ``group_not_found``) and the demo
used to stop there until a new ``--group`` was passed. Now a saved file whose group the server no
longer has is left untouched (it holds the only copy of that group's key) and the demo moves on to
the next free name. Any other error still stops the demo.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from tools import demo_m2
from tools.m2_api import ApiError


class FakeClient:
    """The two calls ``ensure_group`` makes; groups the server knows by id."""

    def __init__(self, known: dict[str, str], *, get_status: int = 404) -> None:
        self.known = dict(known)  # id -> name
        self.get_status = get_status
        self.calls: list[tuple[str, str]] = []

    def call(
        self, method: str, path: str, what: str, *, ok: tuple[int, ...] = (200,), **kw: Any
    ) -> Any:
        self.calls.append((method, path))
        if method == "GET":
            group_id = path.rsplit("/", 1)[1]
            if group_id in self.known:
                return {"id": group_id, "name": self.known[group_id]}
            raise ApiError(what, self.get_status, "group_not_found")
        name = kw["json"]["name"]
        if name in self.known.values():
            raise ApiError(what, 409, "group_name_taken")
        group_id = f"grp_{len(self.known) + 1:04d}"
        self.known[group_id] = name
        # The create answer carries the key once; these are test literals, not key material.
        return {"group": {"id": group_id, "name": name}, "key_material": "k-test", "seed": "s-test"}


@pytest.fixture
def demo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(demo_m2, "DEMO_DIR", tmp_path)
    return tmp_path


def _save(folder: Path, name: str, group_id: str) -> Path:
    path = folder / f"{name}.json"
    path.write_text(json.dumps({"group": {"id": group_id, "name": name}, "key_material": "old"}))
    return path


def test_a_saved_group_that_still_exists_is_reused(demo_dir: Path) -> None:
    _save(demo_dir, "bank-uat-refresh", "grp_live")
    client = FakeClient({"grp_live": "bank-uat-refresh"})
    group = demo_m2.ensure_group(client, "bank-uat-refresh")  # type: ignore[arg-type]
    assert group["group"]["id"] == "grp_live"
    assert [m for m, _ in client.calls] == ["GET"]


def test_a_saved_group_the_server_lost_moves_on_to_a_new_name(demo_dir: Path) -> None:
    stale = _save(demo_dir, "bank-uat-refresh", "grp_gone")
    before = stale.read_bytes()
    client = FakeClient({})  # a reset metadata database: no group at all
    group = demo_m2.ensure_group(client, "bank-uat-refresh")  # type: ignore[arg-type]
    assert group["group"]["name"] == "bank-uat-refresh-1"
    assert stale.read_bytes() == before  # the old key is never overwritten
    fresh = demo_dir / "bank-uat-refresh-1.json"
    assert json.loads(fresh.read_text())["group"]["id"] == group["group"]["id"]
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600


def test_another_error_on_the_saved_group_still_stops_the_demo(demo_dir: Path) -> None:
    _save(demo_dir, "bank-uat-refresh", "grp_x")
    client = FakeClient({}, get_status=503)
    with pytest.raises(ApiError) as caught:
        demo_m2.ensure_group(client, "bank-uat-refresh")  # type: ignore[arg-type]
    assert caught.value.status == 503
    assert [m for m, _ in client.calls] == ["GET"]
