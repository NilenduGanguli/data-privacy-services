"""URLs for the UI scanner: canonical form, crawl templates, scope verdicts and redaction.

Raw URLs (they can carry personal data in paths and query strings) live only in worker memory.
Anything stored or shown is produced by :func:`redact_url` (query values become ``<ENTITY>`` or
``<v>``, identifier-like path segments ``{id}`` or ``<ENTITY>``, query keys that are not plain
names ``<k>``, userinfo and fragment dropped) or :func:`redact_origin`. Errors carry a code only,
never the URL.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import SplitResult, parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

ValueClassifier = Callable[[str], str | None]
NavVerdict = Literal["ok", "scheme", "other_origin", "unregistered_host", "deny", "not_allowed"]

_DEFAULT_PORT = {"http": 80, "https": 443}
_ID_FORMS = (
    r"\d{4}-\d{2}(?:-\d{2})?|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|[0-9a-f]{16,}|[A-Za-z0-9_-]{24,}"
)
#: Redaction: identifier-like segments (4+ digits, dates, UUIDs, long hex/tokens) never reach storage.
_ID_SEGMENT = re.compile(rf"^(?:\d{{4,}}|{_ID_FORMS})$", re.IGNORECASE)
#: Templates: any all-digit segment is an id too, so /help/faq/1 … /help/faq/8 share one template.
_TEMPLATE_SEGMENT = re.compile(rf"^(?:\d+|{_ID_FORMS})$", re.IGNORECASE)
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,40}$")
_GLOB = re.compile(r"^/[A-Za-z0-9._~\-/*{}]*$")
_HASH_ROUTE = re.compile(r"^!?/")
_MAX_GLOB = 200


class UrlError(ValueError):
    """A URL or path glob the scanner refuses. ``str()`` is the code, never the input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _port(parts: SplitResult) -> int | None:
    """The explicit port, or ``None`` when absent or malformed (never raises, never echoes)."""
    try:
        return parts.port
    except ValueError:
        return None


def _netloc(scheme: str, host: str, port: int | None) -> str:
    return host + (f":{port}" if port and port != _DEFAULT_PORT.get(scheme) else "")


@dataclass(frozen=True)
class Origin:
    """Scheme, lower-case host and effective port of an http(s) URL."""

    scheme: str
    host: str
    port: int

    @classmethod
    def of(cls, url: str) -> Origin:
        """The origin of ``url``; :class:`UrlError` ``url_invalid`` for anything but http(s)."""
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        if scheme not in _DEFAULT_PORT or not parts.hostname:
            raise UrlError("url_invalid")
        try:
            port = parts.port or _DEFAULT_PORT[scheme]
        except ValueError:
            raise UrlError("url_invalid") from None
        return cls(scheme, parts.hostname.lower(), port)

    def same(self, url: str) -> bool:
        """True when ``url`` has exactly this origin."""
        try:
            return Origin.of(url) == self
        except UrlError:
            return False

    def text(self) -> str:
        """``scheme://host[:port]`` with the default port left out."""
        return f"{self.scheme}://{_netloc(self.scheme, self.host, self.port)}"


def canonical(url: str, base: str | None = None) -> str | None:
    """Absolute http(s) URL in canonical form, or ``None`` for any other scheme.

    Lower-case scheme/host, default port and userinfo removed, query keys sorted (values kept —
    this form stays in memory), fragment dropped unless it is a hash route (``#/x`` or ``#!/x``).
    """
    raw = urljoin(base, url) if base else url
    parts = urlsplit(raw.strip())
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORT or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = _netloc(scheme, parts.hostname.lower(), port)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    fragment = parts.fragment if _HASH_ROUTE.match(parts.fragment or "") else ""
    return urlunsplit((scheme, netloc, parts.path or "/", query, fragment))


def template(url: str) -> str:
    """Dedupe key for the crawl: ``host[:port]/path`` with identifier-like segments as ``{id}``
    and the sorted query KEYS only (values never matter for page identity)."""
    parts = urlsplit(url)
    port = _port(parts)
    host = (parts.hostname or "").lower() + (f":{port}" if port else "")
    path = "/".join(
        "{id}" if s and _TEMPLATE_SEGMENT.match(unquote(s)) else s
        for s in (parts.path or "/").split("/")
    )
    keys = sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
    out = host + path + (("?" + "&".join(keys)) if keys else "")
    return out + (f"#{parts.fragment}" if _HASH_ROUTE.match(parts.fragment or "") else "")


def validate_glob(glob: str) -> None:
    """Refuse a path glob that is not ``/``-rooted path characters (``UrlError`` ``glob_invalid``)."""
    if len(glob) > _MAX_GLOB or not _GLOB.match(glob):
        raise UrlError("glob_invalid")


def path_matches(path: str, globs: Sequence[str]) -> bool:
    """``fnmatch`` on the path; a glob without ``*`` also covers its sub-paths."""
    for g in globs:
        if fnmatch.fnmatchcase(path, g):
            return True
        stem = g.rstrip("/")
        if "*" not in g and (path == stem or path.startswith(stem + "/")):
            return True
    return False


def redact_url(url: str, classify: ValueClassifier) -> str:
    """Value-free form of ``url`` for storage and display.

    Path segments the classifier recognises become ``<ENTITY>``, identifier-like ones ``{id}``;
    query values become ``<ENTITY>`` or ``<v>`` and keys that are not plain names (or that the
    classifier recognises) ``<k>``; userinfo and the fragment are dropped.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = _netloc(scheme, (parts.hostname or "").lower(), _port(parts))
    segs: list[str] = []
    for s in (parts.path or "/").split("/"):
        text = unquote(s)
        entity = classify(text) if text else None
        segs.append(
            f"<{entity}>" if entity else ("{id}" if text and _ID_SEGMENT.match(text) else s)
        )
    pairs: list[str] = []
    for key, value in sorted(parse_qsl(parts.query, keep_blank_values=True)):
        safe_key = key if _SAFE_KEY.match(key) and not classify(key) else "<k>"
        entity = classify(value) if value else None
        pairs.append(f"{safe_key}=" + (f"<{entity}>" if entity else ("<v>" if value else "")))
    return urlunsplit((scheme, netloc, "/".join(segs), "&".join(pairs), ""))


def redact_origin(url: str) -> str:
    """``scheme://host[:port]`` of ``url`` (for rows about other origins: no path, no query)."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return f"{scheme}://" + _netloc(scheme, (parts.hostname or "").lower(), _port(parts))


@dataclass(frozen=True)
class CrawlScope:
    """Where a scan may navigate and which hosts its requests may reach."""

    start: Origin
    same_origin: bool
    registered_hosts: frozenset[str]
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    def network_host_allowed(self, host: str) -> bool:
        """Every request (pages, scripts, images, XHR) must go to the start host or a host the
        tenant registered; anything else is aborted by the guard."""
        h = host.lower()
        return h == self.start.host or h in self.registered_hosts

    def navigation_verdict(self, url: str) -> NavVerdict:
        """``ok`` or the reason a navigation to ``url`` is out of scope.

        With ``same_origin`` the origin is decided before registration (any other origin is
        ``other_origin``); without it the host must be the start host or registered.
        """
        try:
            origin = Origin.of(url)
        except UrlError:
            return "scheme"
        if self.same_origin and origin != self.start:
            return "other_origin"
        if not self.same_origin and not self.network_host_allowed(origin.host):
            return "unregistered_host"
        path = urlsplit(url).path or "/"
        if path_matches(path, self.deny):
            return "deny"
        if self.allow and not path_matches(path, self.allow):
            return "not_allowed"
        return "ok"
