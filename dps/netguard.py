"""Network guards: host normalisation (M5 Task 10); SSRF checks and TLS rules (Tasks 16, 17).

Nothing here quotes the value it rejects: a host or URL may carry credentials or an internal name.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from dps.config import Settings

if TYPE_CHECKING:
    from dps.jobs_models import ConnectionParams

_HOST = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def normalize_host(value: str) -> str:
    """A registered host as a bare lower-case hostname (or IP literal). Accepts a URL or a host.

    ``"https://Host.Example:8443/x"`` and ``"host.example"`` both give ``"host.example"``.

    Raises:
        ValueError: credentials in the URL, a wildcard, or not a hostname/IP (value-free message).
    """
    text = value.strip()
    try:
        parts = urlsplit(text if "://" in text else f"//{text}")
        username, password, hostname = parts.username, parts.password, parts.hostname
        parts.port  # noqa: B018 - a malformed port raises ValueError here
    except ValueError:
        raise ValueError("not a valid host") from None
    if username is not None or password is not None:
        raise ValueError("a registered host must not carry credentials")
    host = (hostname or "").rstrip(".")
    if not host:
        raise ValueError("not a host")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("not a valid hostname") from None
    if not _HOST.match(host):
        raise ValueError("not a valid hostname (wildcards are not allowed)")
    return host


class _Endpoint(Protocol):
    """What a TLS rule needs: an engine and its parameters (a ``ConnectionSpec`` or a stored
    ``Connection``)."""

    @property
    def engine(self) -> str: ...

    @property
    def params(self) -> ConnectionParams: ...


#: ``(PROTOCOL = TCPS)`` inside an Oracle connect descriptor (any case, any spacing).
_ORACLE_PROTOCOL = re.compile(r"\(\s*protocol\s*=\s*([a-z0-9]+)\s*\)", re.IGNORECASE)


def _oracle_tls(options: Mapping[str, str], dsn: str) -> bool:
    """Whether an Oracle connection dials TCPS (WP-I m3). A secret ``dsn`` overrides the params
    at connect time (``OracleConnector.from_secret``), so it decides when present: an Easy
    Connect ``tcps://`` address, or a descriptor whose every ``PROTOCOL`` is ``TCPS`` (never a
    substring of a host name). Without one, ``options.protocol = tcps`` (``easy_connect`` dials
    ``tcps://``)."""
    dsn = dsn.strip()
    if dsn:
        if dsn.startswith("("):
            protocols = [p.lower() for p in _ORACLE_PROTOCOL.findall(dsn)]
            return bool(protocols) and all(p == "tcps" for p in protocols)
        return dsn.lower().startswith("tcps://")
    return options.get("protocol", "").strip().lower() == "tcps"


def require_tls_connection(
    spec: _Endpoint, settings: Settings, *, secret: Mapping[str, str] | None = None
) -> None:
    """With ``DPS_EGRESS_TLS_REQUIRED``, refuse a connection that would travel in clear.

    postgres ``sslmode`` ∈ {verify-full, verify-ca}; mongo ``tls=true``; oracle
    ``protocol=tcps`` or a secret ``dsn`` that uses tcps; s3 ``endpoint_url`` https (or unset =
    the AWS default, https); pvc exempt.

    Raises:
        SpecError: ``tls_required`` (422), naming the engine only.
    """
    from dps.jobs_models import SpecError  # here: netguard stays importable by dps.jobs_models

    if not settings.egress_tls_required:
        return
    opts = spec.params.options
    endpoint = spec.params.endpoint_url
    ok = {
        "postgres": opts.get("sslmode") in ("verify-full", "verify-ca"),
        "mongo": opts.get("tls", "").lower() == "true",
        "oracle": _oracle_tls(opts, (secret or {}).get("dsn", "")),
        "s3": not endpoint or urlsplit(endpoint).scheme == "https",
        "pvc": True,
    }.get(spec.engine, False)
    if not ok:
        raise SpecError(
            "tls_required", 422, f"{spec.engine} connections must use TLS on this deployment"
        )
