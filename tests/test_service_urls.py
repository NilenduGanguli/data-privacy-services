"""``service_url`` follows the lane's port variables (contract §0 amendment 2026-10-12, §12.2)."""

from __future__ import annotations

import pytest

from tests.fixtures.services import SERVICES, service_url


def test_explicit_test_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPS_TEST_PG_DSN", "postgresql://u:p@db.example:6000/postgres")
    monkeypatch.setenv("DPS_DB_PORT", "5999")
    assert service_url("dps-db") == "postgresql://u:p@db.example:6000/postgres"


def test_default_follows_the_port_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DPS_TEST_PG_DSN", raising=False)
    monkeypatch.delenv("DPS_TEST_SOURCE_PG_DSN", raising=False)
    monkeypatch.setenv("DPS_DB_PORT", "5451")
    monkeypatch.setenv("DPS_SOURCE_PG_PORT", "5452")
    assert service_url("dps-db") == "postgresql://postgres:postgres@127.0.0.1:5451/postgres"
    assert service_url("source-pg") == "postgresql://postgres:postgres@127.0.0.1:5452/bank"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_contract_port_only_when_both_variables_are_unset(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    env, _, (port_var, contract_port), _ = SERVICES[name]
    monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv(port_var, raising=False)
    assert f"127.0.0.1:{contract_port}" in service_url(name)
