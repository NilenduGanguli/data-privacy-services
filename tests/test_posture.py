from __future__ import annotations

import pytest

from dps.config import Settings
from dps.posture import PostureError, assert_production_posture


def _prod(**kw):  # noqa: ANN003 - test helper
    base = dict(
        _env_file=None,
        env="prod",
        auth_enabled=True,
        api_keys='[{"name":"k","key_sha256":"ab"}]',
        llm_provider="",
        audit_enabled=True,
    )
    base.update(kw)
    return Settings(**base)


def test_local_is_never_checked() -> None:
    assert_production_posture(Settings(_env_file=None, env="local", auth_enabled=False))


def test_good_production_passes() -> None:
    assert_production_posture(_prod())


@pytest.mark.parametrize(
    "bad",
    [
        dict(auth_enabled=False),
        dict(bootstrap_api_key="x"),
        dict(api_keys=""),
        dict(llm_provider="stub"),
        dict(
            llm_provider="stellar",
            stellar_base_url="https://s",
            coin_url="https://c",
            ssl_cert_file="",
        ),
        dict(audit_enabled=False),
    ],
)
def test_bad_production_is_refused(bad: dict) -> None:
    with pytest.raises(PostureError):
        assert_production_posture(_prod(**bad))
