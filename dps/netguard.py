"""Network guards: host normalisation (M5 Task 10); SSRF checks and TLS rules (Tasks 16, 17).

Nothing here quotes the value it rejects: a host or URL may carry credentials or an internal name.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

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
