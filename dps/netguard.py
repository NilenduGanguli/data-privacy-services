"""Network guards: host normalisation (M5 Task 10); SSRF checks and TLS rules (Tasks 16, 17).

SSRF (security review S5): :func:`check_scan_url` validates a scan target before any browser
starts — registered hosts only, no numeric host encodings (``0x7f000001``, ``2130706433``,
``127.1``), and no address that is link-local or a cloud metadata endpoint, multicast,
unspecified or reserved, or (outside ``env=local``) loopback — checked on the literal and on
every address the name resolves to, IPv4-mapped / NAT64 / 6to4 IPv6 unwrapped first.
:func:`chromium_resolver_rules` makes Chromium resolve only the hosts it names. Private
(RFC 1918) addresses stay allowed: internal UAT sites are the use case.

Wired on the scanner's paths (the M4 merge into ``fsec``): ``POST /scans`` and the browser-worker
(again, before any browser starts) run :func:`check_scan_url`; the scan's scope proxy resolves
every hop's host with :func:`resolve_addresses` and dials only addresses
:func:`forbidden_ip_reason` lets through; Chromium resolves nothing but the proxy's address.

Nothing here quotes the value it rejects: a host or URL may carry credentials or an internal name.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Callable, Mapping, Sequence
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
    try:  # a bare IP literal (an IPv6 one has colons that urlsplit reads as a port)
        return str(ipaddress.ip_address(text.removeprefix("[").removesuffix("]")))
    except ValueError:
        pass
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


# --- SSRF (security review S5; connection endpoints reuse the same core) ------------------------
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
#: Link-local and the cloud metadata endpoints (AWS/GCP/Azure 169.254.169.254, AWS IPv6
#: fd00:ec2::254, Alibaba 100.100.100.200, Oracle 192.0.0.192).
_METADATA = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("192.0.0.192/32"),
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
#: Dotted or bare numbers in decimal, octal or hex: an address in disguise, never a host name.
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$")
Resolver = Callable[[str], list[str]]


class NetGuardError(ValueError):
    """A refused network target: ``code`` (``host_forbidden`` / ``host_not_registered``) and a
    value-free ``detail``."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


def is_ip(host: str) -> bool:
    """True when ``host`` is an IP literal (``ipaddress`` accepts it)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def is_numeric_encoding(host: str) -> bool:
    """True for a host made of numbers (``0x7f000001``, ``2130706433``, ``127.1``) that is not
    a canonical IP literal: resolvers read it as an address, the registry as a name."""
    return bool(_NUMERIC_HOST.match(host.lower())) and not is_ip(host)


def default_resolver(host: str) -> list[str]:
    """Every address ``host`` resolves to (TCP), sorted."""
    return sorted(
        {str(info[4][0]) for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    )


async def resolve_addresses(host: str, port: int = 0) -> list[str]:
    """Every address ``host`` resolves to (TCP), in the resolver's order, duplicates dropped; an
    IP literal is itself. Runs on the event loop's resolver (never blocks the loop).

    Raises:
        OSError: the name does not resolve (``socket.gaierror``).
    """
    if is_ip(host):
        return [host]
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _embedded_ipv4(ip: IPAddress) -> IPAddress:
    """The IPv4 address an IPv4-mapped, NAT64 or 6to4 IPv6 address carries (else ``ip``)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip.sixtofour is not None:
            return ip.sixtofour
    return ip


def forbidden_ip_reason(address: str | IPAddress, *, allow_loopback: bool) -> str | None:
    """Why ``address`` must never be dialled (``None`` when it may be): link-local or a cloud
    metadata endpoint, multicast/unspecified/reserved, or loopback unless ``allow_loopback``."""
    ip = ipaddress.ip_address(address.split("%")[0]) if isinstance(address, str) else address
    for candidate in {ip, _embedded_ipv4(ip)}:
        if any(candidate in net for net in _METADATA if net.version == candidate.version):
            return "link-local or cloud metadata address"
        if candidate.is_link_local:
            return "link-local or cloud metadata address"
        if candidate.is_loopback:  # before "reserved": ::1 lies in the reserved ::/8
            if allow_loopback:
                continue
            return "loopback address"
        if candidate.is_multicast or candidate.is_unspecified or candidate.is_reserved:
            return "non-routable address"
    return None


def check_resolved_host(
    host: str, *, allow_loopback: bool, resolver: Resolver = default_resolver, strict_dns: bool
) -> None:
    """Refuse ``host`` when it is a numeric encoding, or when it is (or resolves to) a forbidden
    address. ``strict_dns``: a name that does not resolve is refused too (scans); otherwise it is
    left to the client, which then cannot connect either (connections at accept time).

    Raises:
        NetGuardError: ``host_forbidden`` (value-free detail).
    """
    if is_numeric_encoding(host):
        raise NetGuardError("host_forbidden", "numeric host encodings are not allowed")
    if is_ip(host):
        addresses = [host]
    else:
        try:
            addresses = resolver(host)
        except (OSError, UnicodeError):
            if strict_dns:
                raise NetGuardError("host_forbidden", "the host does not resolve") from None
            return
    for address in addresses:
        try:
            reason = forbidden_ip_reason(address, allow_loopback=allow_loopback)
        except ValueError:
            reason = "non-routable address"
        if reason:
            raise NetGuardError("host_forbidden", f"the host resolves to a {reason}")


def check_scan_url(
    url: str,
    *,
    registered_hosts: Sequence[str],
    settings: Settings,
    resolver: Resolver = default_resolver,
) -> str:
    """Validate a scan target before any browser starts. Returns the normalised host.

    Private (RFC 1918) addresses stay allowed — internal UAT sites are the use case — but
    link-local/metadata, multicast and (outside local) loopback are refused, also after DNS.

    Raises:
        NetGuardError: ``host_forbidden`` or ``host_not_registered`` (value-free detail).
    """
    try:
        parts = urlsplit(url)
        username, password = parts.username, parts.password
    except ValueError:
        raise NetGuardError("host_forbidden", "scan URL is malformed") from None
    allowed_schemes = ("https",) if settings.egress_tls_required else ("http", "https")
    if parts.scheme not in allowed_schemes:
        raise NetGuardError("host_forbidden", f"scan URLs must use {' or '.join(allowed_schemes)}")
    if username is not None or password is not None:
        raise NetGuardError("host_forbidden", "scan URLs must not carry credentials")
    try:
        host = normalize_host(parts.netloc)
    except ValueError:
        raise NetGuardError("host_forbidden", "scan URL has no valid host") from None
    if is_numeric_encoding(host):
        raise NetGuardError("host_forbidden", "numeric host encodings are not allowed")
    registered: set[str] = set()
    for entry in registered_hosts:
        try:
            registered.add(normalize_host(entry))
        except ValueError:
            continue
    if host not in registered:
        raise NetGuardError("host_not_registered", "the host is not registered for this tenant")
    check_resolved_host(
        host, allow_loopback=settings.env == "local", resolver=resolver, strict_dns=True
    )
    return host


def chromium_resolver_rules(hosts: Sequence[str]) -> str:
    """Chromium flag value: resolve ONLY the registered hosts; every other name fails DNS."""
    return "MAP * ~NOTFOUND, " + ", ".join(f"EXCLUDE {h}" for h in sorted(set(hosts)))


# --- connection endpoints (S3 review F-08: the scanner guard's core on the connection surface) ---
#: ``HOST=…`` inside an Oracle connect descriptor (any case, any spacing).
_ORACLE_HOST = re.compile(r"\(\s*host\s*=\s*([^)\s]+)\s*\)", re.IGNORECASE)
#: A database host as written in params: a hostname (underscores tolerated) or an IP literal.
_DB_HOST = re.compile(r"[a-z0-9_.\-]{1,253}")


def _endpoint_host(raw: str) -> str:
    """``raw`` as a bare lower-case host. Raises ``NetGuardError`` for anything that is not a
    host (credentials, a path, a wildcard) — value-free."""
    try:
        return normalize_host(raw)
    except ValueError:
        pass
    text = raw.strip().rstrip(".").lower()
    if _DB_HOST.fullmatch(
        text
    ):  # e.g. ``source_pg``: not DNS-valid, but a host some resolvers know
        return text
    raise NetGuardError("host_forbidden", "the connection host is not a valid host name")


def _uri_hosts(uri: str) -> list[str]:
    """The hosts of a MongoDB URI (``mongodb://[user:pw@]h1[:p],h2/…``, ``mongodb+srv://h/…``)."""
    rest = uri.split("://", 1)[1] if "://" in uri else uri
    netloc = rest.split("/", 1)[0].split("?", 1)[0].rsplit("@", 1)[-1]
    hosts: list[str] = []
    for part in netloc.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("["):  # [v6]:port
            hosts.append(part[1 : part.find("]")] if "]" in part else part)
        else:
            hosts.append(part.rsplit(":", 1)[0] if part.count(":") == 1 else part)
    return hosts


def _dsn_hosts(dsn: str) -> list[str]:
    """The hosts of an Oracle DSN: every ``HOST=`` of a descriptor, or an Easy Connect host."""
    dsn = dsn.strip()
    if dsn.startswith("("):
        return _ORACLE_HOST.findall(dsn)
    rest = dsn.split("://", 1)[1] if "://" in dsn else dsn
    rest = rest.lstrip("/").split("/", 1)[0].split("?", 1)[0]
    if rest.startswith("["):
        return [rest[1 : rest.find("]")]] if "]" in rest else [rest]
    return [rest.rsplit(":", 1)[0] if rest.count(":") == 1 else rest] if rest else []


def connection_hosts(spec: _Endpoint, *, secret: Mapping[str, str] | None = None) -> list[str]:
    """Every host a connection would dial: ``params.host``; an s3 ``endpoint_url``'s host; and,
    from its credentials, a MongoDB ``uri``'s hosts and an Oracle ``dsn``'s hosts."""
    raw: list[str] = []
    if spec.params.host:
        raw.append(spec.params.host)
    if spec.engine == "s3" and spec.params.endpoint_url:
        try:
            hostname = urlsplit(spec.params.endpoint_url).hostname
        except ValueError:
            raise NetGuardError("host_forbidden", "the endpoint URL is malformed") from None
        if not hostname:
            raise NetGuardError("host_forbidden", "the endpoint URL has no host")
        raw.append(hostname)
    secret = secret or {}
    if spec.engine == "mongo" and secret.get("uri"):
        raw += _uri_hosts(secret["uri"])
    if spec.engine == "oracle" and secret.get("dsn"):
        raw += _dsn_hosts(secret["dsn"])
    return [_endpoint_host(h) for h in raw if h.strip()]


def check_connection_endpoint(
    spec: _Endpoint,
    settings: Settings,
    *,
    secret: Mapping[str, str] | None = None,
    resolver: Resolver = default_resolver,
) -> None:
    """Refuse a connection whose endpoint is a numeric host encoding or is (or resolves to) a
    link-local/metadata, multicast, unspecified, reserved or — outside ``env=local`` — loopback
    address (S3 review F-08). RFC 1918 stays allowed (internal databases are the use case). A name
    that does not resolve is left to the driver, which cannot connect to it either. Run at accept
    (create/patch) and again at use (``open_connector``/``open_target_store``).

    Raises:
        SpecError: ``host_forbidden`` (422, value-free).
    """
    from dps.jobs_models import SpecError  # here: netguard stays importable by dps.jobs_models

    try:
        for host in connection_hosts(spec, secret=secret):
            check_resolved_host(
                host, allow_loopback=settings.env == "local", resolver=resolver, strict_dns=False
            )
    except NetGuardError as exc:
        raise SpecError(exc.code, 422, exc.detail) from None
