"""The connector conformance suite bound to Oracle (the lane's Oracle Free): reads as the
read-only login, writes as the owner, in the session's test owner (tables dropped per test).

docker compose --profile oracle up -d oracle
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m oracle tests/connectors/test_conformance_oracle.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from tests.connectors.conformance import ConnectorConformance
from tests.connectors.oracle_fixtures import drop_all
from tests.fixtures.m3_services import OracleTestSchema

pytest.importorskip("oracledb")

from tests.connectors.harness_oracle import OracleEnv, oracle_connector  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.oracle]


class TestOracleConformance(ConnectorConformance):
    """Contract §12.3 on Oracle."""

    @pytest_asyncio.fixture
    async def env(
        self, oracle_admin: Any, oracle_schema: OracleTestSchema
    ) -> AsyncIterator[OracleEnv]:
        drop_all(oracle_admin, oracle_schema.owner)
        s = oracle_schema
        source = oracle_connector(s.ro_user, s.ro_password, s.dsn)
        rw = oracle_connector(s.owner, s.owner_password, s.dsn)
        await source.open()
        await rw.open()
        try:
            yield OracleEnv(s, oracle_admin, source, rw)
        finally:
            await rw.close()
            await source.close()
            drop_all(oracle_admin, oracle_schema.owner)
