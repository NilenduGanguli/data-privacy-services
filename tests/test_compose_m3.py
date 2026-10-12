"""Static checks of the M3 compose services, the Oracle init script and the demo secret refs.

No Docker needed: reads ``docker-compose.yml`` and ``docker/oracle/initdb/01_crm.sql``, and runs the
``demo-secrets`` one-shot's shell command against a temporary directory, so the refs it writes are
read back through ``dps.connections.load_credentials`` exactly as the app reads them.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from dps.config import Settings
from dps.connections import Connection, load_credentials
from dps.jobs_models import ConnectionParams

ROOT = Path(__file__).resolve().parents[1]
SECRET_ROOT = "/var/run/secrets/dps"
#: The Oracle and MongoDB demo refs (contract §5.1 ``secret_ref``; local demo literals only).
ORACLE_REFS = {
    "dps/src/oracle-crm-ro": "dps_ro",
    "dps/src/oracle-crm-owner": "crm",
    "dps/tgt/oracle-crm-masked": "crm_masked",
    "dps/src/oracle-loans-owner": "loans",
}
MONGO_REFS = {
    "dps/src/mongo-crm": "mongodb://mongo:27017/crm?directConnection=true",
    "dps/tgt/mongo-crm-uat": "mongodb://mongo:27017/crm_uat?directConnection=true",
}


def _services() -> dict[str, Any]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services: dict[str, Any] = compose["services"]
    return services


def test_oracle_service_matches_contract() -> None:
    ora = _services()["oracle"]
    assert ora["image"] == "gvenzl/oracle-free:23-slim-faststart"
    assert "oracle" in ora["profiles"] and "full" in ora["profiles"]
    assert ora["ports"] == ["127.0.0.1:${DPS_ORACLE_PORT:-1522}:1521"]
    assert ora["mem_limit"] == "2700m"
    assert ora["healthcheck"]["test"] == ["CMD", "healthcheck.sh"]
    assert ora["healthcheck"]["start_period"] == "180s"
    assert "./docker/oracle/initdb:/container-entrypoint-initdb.d:ro" in ora["volumes"]
    assert ora["environment"]["ORACLE_PASSWORD"] == "dps_local_oracle"


def test_mongo_service_is_a_single_node_replica_set() -> None:
    mongo = _services()["mongo"]
    assert mongo["image"] == "mongo:7" and mongo["command"] == ["--replSet", "rs0", "--bind_ip_all"]
    assert "sources" in mongo["profiles"] and "full" in mongo["profiles"]
    assert mongo["ports"] == ["127.0.0.1:${DPS_MONGO_PORT:-27018}:27017"]
    probe = " ".join(mongo["healthcheck"]["test"])
    assert "rs.initiate" in probe and "isWritablePrimary" in probe


def test_app_and_worker_read_the_demo_refs_read_only() -> None:
    services = _services()
    for name in ("app", "worker"):
        assert f"dps-secrets:{SECRET_ROOT}:ro" in services[name]["volumes"], name
        assert services[name]["environment"]["DPS_SECRET_DIR"] == SECRET_ROOT, name
    # Oracle and Mongo live in optional profiles: the connectors report connection_failed when
    # they are absent, so nothing depends on them.
    for name in ("app", "worker"):
        assert not {"oracle", "mongo"} & set(services[name].get("depends_on", {})), name


def test_init_sql_creates_contract_users_and_tables() -> None:
    sql = (ROOT / "docker/oracle/initdb/01_crm.sql").read_text(encoding="utf-8").upper()
    assert "ALTER SESSION SET CONTAINER = FREEPDB1" in sql
    for user in ("CRM", "CRM_MASKED", "LOANS", "DPS_RO"):
        assert f'CREATE USER {user} IDENTIFIED BY "{user.lower()}"'.upper() in sql
    assert re.search(r"\bTAX_ID\s+CHAR\(10\)", sql) and re.search(r"\bNOTES\s+CLOB\b", sql)
    assert re.search(r"\bLAST_CONTACT_AT\s+TIMESTAMP\(9\)", sql)
    assert "CREATE TABLE LOANS.APPLICANTS" in sql
    assert "FOR UPDATE" not in sql
    # SQL*Plus ends a statement only at a ';' that ends its line; a trailing comment makes it
    # buffer the next statement too, which Oracle 23ai refuses (ORA-03405) and the init aborts.
    assert not re.search(r";[ \t]*--", sql)
    # DPS_RO is a read-only login: CREATE SESSION and per-table SELECT grants, nothing else.
    dps_ro_grants = re.findall(r"GRANT ([A-Z ,]+?) (?:ON [A-Z_.]+ )?TO DPS_RO", sql)
    assert set(dps_ro_grants) == {"CREATE SESSION", "SELECT"}


def _demo_secrets_command() -> str:
    command = _services()["demo-secrets"]["command"]
    assert isinstance(command, list) and len(command) == 1
    text: str = command[0]
    return text.replace("$$", "$")  # compose's escape for a literal $


def test_demo_secrets_write_the_oracle_and_mongo_refs_as_local_literals() -> None:
    script = _demo_secrets_command()
    assert script.count("oracle:1521/FREEPDB1") == 1  # one dsn for every Oracle ref
    hosts = set(re.findall(r"mongodb://([^/:]+)", script)) | set(
        re.findall(r"([a-z0-9.-]+):1521/", script)
    )
    assert hosts == {"mongo", "oracle"}  # in-network demo names only, never a real host


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_demo_secret_refs_resolve_to_credentials(tmp_path: Path) -> None:
    script = _demo_secrets_command().replace(SECRET_ROOT, str(tmp_path))
    subprocess.run(["sh", "-c", script], check=True, capture_output=True, timeout=30)
    settings = Settings(_env_file=None, secret_dir=str(tmp_path))
    now = datetime.now(UTC)

    def conn(engine: str, ref: str) -> Connection:
        return Connection(
            id="c_" + "0" * 26,
            tenant_id="t1",
            name="demo",
            engine=engine,  # type: ignore[arg-type]
            params=ConnectionParams(),
            secret_ref=ref,
            has_secret=False,
            secret_expires_at=None,
            roles=["source"],
            tags=[],
            capabilities={},
            last_test=None,
            created_by=None,
            created_at=now,
            updated_at=now,
        )

    for ref, login in ORACLE_REFS.items():
        creds = asyncio.run(load_credentials(conn("oracle", ref), settings=settings, kek=None))
        assert creds.username == login and creds.password.get_secret_value() == login, ref
        assert creds.extra["dsn"].get_secret_value() == "oracle:1521/FREEPDB1", ref
    for ref, uri in MONGO_REFS.items():
        creds = asyncio.run(load_credentials(conn("mongo", ref), settings=settings, kek=None))
        assert creds.username == "" and set(creds.extra) == {"uri"}, ref
        assert creds.extra["uri"].get_secret_value() == uri, ref
    # M2's refs are still written beside them.
    assert (tmp_path / "dps/src/bank-pg-ro/username").read_text() == "dps_reader"
