"""The connector conformance suite bound to MongoDB (the lane's replica set), one database per test.

docker compose --profile sources up -d mongo
DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m mongo tests/connectors/test_conformance_mongo.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from tests.connectors.conformance import ConnectorConformance
from tests.fixtures.m3_services import mongo_uri

pytest.importorskip("pymongo")

from tests.connectors.harness_mongo import MongoEnv  # noqa: E402
from tests.connectors.mongo_fixtures import connector  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.mongo]


class TestMongoConformance(ConnectorConformance):
    """Contract §12.3 on MongoDB."""

    @pytest_asyncio.fixture
    async def env(self, mongo_client: Any, mongo_db: str) -> AsyncIterator[MongoEnv]:
        source = connector(mongo_uri(), mongo_db)
        rw = connector(mongo_uri(), mongo_db)
        await source.open()
        await rw.open()
        try:
            yield MongoEnv(mongo_client, mongo_db, source, rw)
        finally:
            await rw.close()
            await source.close()
