"""WP-I (m3 merge): M5's TLS-only egress rule and M3's Oracle connector agree on what is TLS.

M5 accepted ``options.protocol = tcps`` (or a secret ``dsn`` that mentions ``tcps``) as an
encrypted Oracle connection before M3's connector existed; M3's Easy Connect builder ignored the
option, so a "tcps" connection still dialled plain TCP and sent the credentials in clear. Now the
connector dials what the gate approved, and the gate reads the address the connector really uses:
a secret ``dsn`` overrides the params, so the ``dsn`` decides; ``tcps`` must be its protocol, not a
substring of a host name.
"""

from __future__ import annotations

import pytest

from dps.config import Settings
from dps.jobs_models import ConnectionParams, ConnectionSpec, SpecError
from dps.layers.connectors.base import ConnectorError
from dps.layers.connectors.oracle import easy_connect
from dps.netguard import require_tls_connection

STRICT = Settings(_env_file=None, egress_tls_required=True)


def _params(**options: str) -> ConnectionParams:
    return ConnectionParams(host="ora.example", port=2484, service_name="FREEPDB1", options=options)


def test_easy_connect_dials_tcps_when_the_option_says_so() -> None:
    assert easy_connect(_params(protocol="tcps")) == "tcps://ora.example:2484/FREEPDB1"
    assert easy_connect(_params(protocol="TCPS")) == "tcps://ora.example:2484/FREEPDB1"
    assert easy_connect(_params(protocol="tcp")) == "ora.example:2484/FREEPDB1"
    assert easy_connect(_params()) == "ora.example:2484/FREEPDB1"


@pytest.mark.parametrize("protocol", ["udp", "tcps;x", "beq", ""])
def test_easy_connect_refuses_any_other_protocol(protocol: str) -> None:
    with pytest.raises(ConnectorError) as err:
        easy_connect(_params(protocol=protocol))
    assert err.value.code == "connection_invalid"
    assert "ora.example" not in str(err.value)


def _spec(**options: str) -> ConnectionSpec:
    return ConnectionSpec(
        name="ora",
        engine="oracle",
        params=_params(**options),
        secret_ref="dps/src/x",
        roles={"source"},  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "options,dsn,ok",
    [
        ({"protocol": "tcps"}, None, True),
        ({}, "tcps://h.example:2484/S", True),
        (
            {},
            "(DESCRIPTION=(ADDRESS=(PROTOCOL = TCPS)(HOST=h)(PORT=2484))(CONNECT_DATA=(SERVICE_NAME=S)))",
            True,
        ),  # noqa: E501
        ({}, "(description=(address=(protocol=tcps)(host=h)(port=2484)))", True),
        # a host whose name contains "tcps" is not TLS
        ({}, "mytcpshost:1521/S", False),
        ({}, "tcpshost.example:1521/S", False),
        ({}, "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=tcps.example)(PORT=1521)))", False),
        # the secret's dsn overrides the params at connect time, so it decides
        ({"protocol": "tcps"}, "h.example:1521/S", False),
        ({"protocol": "tcps"}, "tcps://h.example:2484/S", True),
    ],
)
def test_the_tls_gate_reads_the_address_the_connector_dials(
    options: dict[str, str], dsn: str | None, ok: bool
) -> None:
    secret = {"dsn": dsn} if dsn is not None else None
    if ok:
        require_tls_connection(_spec(**options), STRICT, secret=secret)
    else:
        with pytest.raises(SpecError) as err:
            require_tls_connection(_spec(**options), STRICT, secret=secret)
        assert err.value.code == "tls_required"
        assert "example" not in err.value.detail
