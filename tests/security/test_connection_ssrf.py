"""S3 review F-08: a tenant-controlled connection endpoint (DB ``host``, S3 ``endpoint_url``, a
MongoDB ``uri`` or Oracle ``dsn`` in its credentials) is refused when it is a numeric host encoding
or is — or resolves to — a link-local/cloud-metadata, multicast, unspecified, reserved or (outside
local) loopback address. Checked at create/patch and again at use; RFC 1918 stays allowed."""

from __future__ import annotations

from typing import Any

import pytest

from dps.config import Settings
from dps.jobs_models import ConnectionParams, ConnectionSpec, SpecError
from dps.netguard import check_connection_endpoint

DNS = {"metadata.google.internal": ["169.254.169.254"], "db.bank.internal": ["10.4.5.6"]}


def _resolve(host: str) -> list[str]:
    if host not in DNS:
        raise OSError("no such host")
    return DNS[host]


def _spec(engine: str, **params: Any) -> ConnectionSpec:  # noqa: ANN401
    ref = None if engine == "pvc" else "dps/src/x"
    roles = {"target"} if engine in ("s3", "pvc") else {"source"}
    return ConnectionSpec.model_validate(
        {
            "name": "c-1",
            "engine": engine,
            "params": ConnectionParams(**params),
            "secret_ref": ref,
            "roles": roles,
        }
    )


def _settings(env: str = "local") -> Settings:
    return Settings(_env_file=None, env=env, llm_provider="stub")


@pytest.mark.parametrize(
    "spec,secret,env",
    [
        (_spec("postgres", host="169.254.169.254", port=5432, database="bank"), None, "local"),
        (_spec("postgres", host="0x7f000001", database="bank"), None, "local"),
        (_spec("postgres", host="2130706433", database="bank"), None, "local"),
        (_spec("postgres", host="127.0.0.1", database="bank"), None, "prod"),
        (_spec("postgres", host="metadata.google.internal", database="bank"), None, "local"),
        (_spec("oracle", host="fe80::1", service_name="FREEPDB1"), None, "local"),
        (_spec("mongo", host="224.0.0.1", database="crm"), None, "local"),
        (_spec("s3", bucket="uat", endpoint_url="https://169.254.169.254/"), None, "local"),
        (
            _spec("s3", bucket="uat", endpoint_url="https://[::ffff:169.254.169.254]/"),
            None,
            "local",
        ),
        (_spec("s3", bucket="uat", endpoint_url="https://100.100.100.200/"), None, "local"),
        (
            _spec("mongo", database="crm"),
            {"uri": "mongodb://u:p@169.254.169.254:27017/crm"},
            "local",
        ),
        (
            _spec("mongo", database="crm"),
            {"uri": "mongodb://u:p@db.bank.internal,169.254.169.254:27017/crm"},
            "local",
        ),
        (
            _spec("oracle", host="db.bank.internal"),
            {"dsn": "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCPS)(HOST=169.254.169.254)(PORT=2484)))"},
            "local",
        ),
        (
            _spec("oracle", host="db.bank.internal"),
            {"dsn": "tcps://0x7f000001:2484/FREEPDB1"},
            "local",
        ),
    ],
)
def test_forbidden_endpoints_are_refused(
    spec: ConnectionSpec, secret: dict[str, str] | None, env: str
) -> None:
    with pytest.raises(SpecError) as refused:
        check_connection_endpoint(spec, _settings(env), secret=secret, resolver=_resolve)
    assert refused.value.code == "host_forbidden" and refused.value.status == 422
    for value in ("169.254", "0x7f", "2130706433", "google.internal", "100.100"):
        assert value not in refused.value.detail


@pytest.mark.parametrize(
    "spec,secret,env",
    [
        (_spec("postgres", host="127.0.0.1", port=5432, database="bank"), None, "local"),
        (_spec("postgres", host="source-pg", database="bank"), None, "prod"),  # does not resolve
        (_spec("postgres", host="db.bank.internal", database="bank"), None, "prod"),  # RFC 1918
        (_spec("postgres", host="10.20.30.40", database="bank"), None, "prod"),
        (_spec("oracle", host="source_oracle", service_name="FREEPDB1"), None, "prod"),
        (
            _spec("s3", bucket="uat", endpoint_url="https://s3.eu-west-1.amazonaws.com"),
            None,
            "prod",
        ),
        (_spec("s3", bucket="uat"), None, "prod"),  # AWS default endpoint
        (_spec("pvc", base_path="exports"), None, "prod"),
        (
            _spec("mongo", database="crm"),
            {"uri": "mongodb+srv://u:p@cluster.bank.internal/crm"},
            "prod",
        ),
        (
            _spec("oracle", host="db.bank.internal"),
            {"dsn": "db.bank.internal:1521/FREEPDB1"},
            "prod",
        ),
    ],
)
def test_ordinary_endpoints_pass(
    spec: ConnectionSpec, secret: dict[str, str] | None, env: str
) -> None:
    check_connection_endpoint(spec, _settings(env), secret=secret, resolver=_resolve)


def test_a_host_carrying_credentials_is_refused() -> None:
    with pytest.raises(SpecError) as refused:
        check_connection_endpoint(
            _spec("postgres", host="user:pw@db.bank.internal", database="bank"),
            _settings(),
            resolver=_resolve,
        )
    assert refused.value.code == "host_forbidden" and "pw" not in refused.value.detail
