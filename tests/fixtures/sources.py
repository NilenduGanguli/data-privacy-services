"""Source-database and object-store fixtures (contract §12.2); source DBs arrive with Task 15."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.fixtures.services import require_service

if TYPE_CHECKING:
    from dps.storage import LocalObjectStore, S3ObjectStore


@pytest.fixture
def object_store(tmp_path: Path) -> LocalObjectStore:
    """A local object store under the test's tmp directory."""
    from dps.storage import LocalObjectStore

    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture
def s3_store() -> S3ObjectStore:
    """The ``dps-artefacts`` bucket on the lane's MinIO (local demo credentials)."""
    pytest.importorskip("boto3")
    from dps.storage import S3ObjectStore

    endpoint = require_service("minio")
    return S3ObjectStore(
        bucket="dps-artefacts",
        endpoint_url=endpoint,
        region="us-east-1",
        access_key_id="minioadmin",
        secret_access_key="minioadmin",
        force_path_style=True,
    )
