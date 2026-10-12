from __future__ import annotations

import pytest

from dps.ids import ID_RE, is_id, new_id, ulid


def test_ulid_shape_and_time_order() -> None:
    a, b = ulid(now_ms=1_700_000_000_000), ulid(now_ms=1_700_000_000_001)
    assert len(a) == 26 and a < b and set(a) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_new_id_prefix_and_uniqueness() -> None:
    ids = {new_id("j") for _ in range(2000)}
    assert len(ids) == 2000 and all(ID_RE.fullmatch(i) and i.startswith("j_") for i in ids)
    assert is_id(new_id("snap"), "snap") and not is_id(new_id("j"), "snap") and not is_id("j_x")


@pytest.mark.parametrize("prefix", ["", "J", "toolongprefix", "a-b"])
def test_bad_prefix_is_refused(prefix: str) -> None:
    with pytest.raises(ValueError):
        new_id(prefix)
