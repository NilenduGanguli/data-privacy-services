"""URLs for the UI scanner: canonical form, crawl templates, scope verdicts and redaction.

Raw URLs (they can carry personal data in paths and query strings) live only in worker memory.
Anything stored or shown is produced by :func:`redact_url` (query and matrix-parameter values
become ``<ENTITY>`` or ``<v>``, value-like path segments ``{id}`` or ``<ENTITY>``, keys that are not
plain names ``<k>``, userinfo and fragment dropped), :func:`template` (the same rules; the persisted
crawl template) or :func:`redact_origin`. A segment is value-like when the classifier recognises
it or, whatever the classifier says, when it holds a run of 4+ digits, 6+ digits in all (digit
groups joined by ``-``, ``.``, ``/``, ``+`` or a space are one number: ``501-002-345-678``,
``+91-987-654-321``), a date shape (``1-5-90``), an alphanumeric run of 8+ characters with a digit,
an ``@``, an ``=`` or whitespace, a date with a month name (``12May90``, ``May-12-90``,
``12-de-mayo-de-90``, ``the-12th-of-may-90``; English, Spanish and Hindi names, the connector
words ``de``/``del``/``of``/``the`` allowed between the parts), or is an id form (date, UUID, long
hex, token). Consecutive all-numeric path segments holding 6+ digits between them
(``/501/002/345/678``) are one value too, and so are 2 to 5 consecutive segments that read as a date
once joined with ``-`` (``/1/5/90``, ``/12/may/90``, ``/12/de/mayo/de/90``): every segment of such a
window is ``{id}``. Errors carry a code only, never the URL.

Scope globs are matched against :func:`normalise_path` (matrix parameters stripped, unreserved
escapes decoded, ``%2F``/``%5C``/backslash as separators, dot segments resolved, ``//`` collapsed)
and the other ways a server may read the same path (matrix parameters stripped before encoded
separators are split, or encoded separators kept, or a lenient server's reading: every escape decoded, a NUL or ``;`` ending the
name, trailing dots and spaces trimmed as IIS/Windows do). A deny glob matching ANY reading denies,
ignoring case; an allow list must match EVERY reading. So ``/Logout``, ``/logout;jsessionid=1``,
``/%6Cogout``, ``//logout``, ``/logout.`` and ``/logout%00`` are all denied by ``/logout``. A hash
route (``#/logout``, ``#!/admin``) is a navigation as well: its path is checked against deny too.
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
#: A value inside a segment or key: 4+ digits, or an alphanumeric run of 8+ with a digit
#: (``SB-50100234567812``, ``CUST0012345``, ``A1B2C3D4…``), an ``@``, an ``=`` (``q=ananya``) or
#: whitespace.
_VALUE_RUN = re.compile(r"\d{4,}|(?<![A-Za-z0-9])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{8,}|[@=]|\s")
#: Digit groups shorter than four still make one number: 6+ digits in all is a value
#: (``501-002-345-678``, ``501.002.345.678``, ``+91-987-654-321``, ``12-05-90``).
_MIN_DIGITS = 6
#: A date with short groups (``1-5-90``, ``01/05/1990`` once decoded) is a value whatever its count.
_DATE_SHAPE = re.compile(r"(?<!\d)\d{1,2}[-./ ]\d{1,2}[-./ ]\d{2,4}(?!\d)")
#: A date with a month name (``12May90``, ``12-may-90``, ``May-12-90``, ``12th Sept 1990``): a day
#: and a year around an English, Spanish or Hindi month name, in any order, with or without
#: separators. A month name with a day or a year only (``may-24``, ``12-may``) is a route.
_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r"|ene(?:ro)?|febrero|marzo|abr(?:il)?|mayo|junio|julio|ago(?:sto)?|se(?:p|pt)?tiembre"
    r"|octubre|noviembre|dic(?:iembre)?"
    "|\u091c\u0928\u0935\u0930\u0940"  # जनवरी
    "|(?:\u095e|\u092b\u093c?)\u0930\u0935\u0930\u0940"  # फ़रवरी (precomposed or not)
    "|\u092e\u093e\u0930\u094d\u091a"  # मार्च
    "|\u0905\u092a\u094d\u0930\u0948\u0932"  # अप्रैल
    "|\u092e\u0908"  # मई
    "|\u091c\u0942\u0928"  # जून
    "|\u091c\u0941\u0932\u093e\u0908"  # जुलाई
    "|\u0905\u0917\u0938\u094d\u0924"  # अगस्त
    "|\u0938\u093f\u0924(?:\u0902\u092c|\u092e\u094d\u092c)\u0930"  # सितंबर / सितम्बर
    "|\u0905\u0915\u094d(?:\u091f|\u0924)\u0942\u092c\u0930"  # अक्टूबर / अक्तूबर
    "|\u0928\u0935(?:\u0902\u092c|\u092e\u094d\u092c)\u0930"  # नवंबर / नवम्बर
    "|\u0926\u093f\u0938(?:\u0902\u092c|\u092e\u094d\u092c)\u0930"  # दिसंबर / दिसम्बर
)
_LETTER = "A-Za-z\u0900-\u097f"
_MONTH = rf"(?:(?<![{_LETTER}])|(?<=\d(?:st|nd|rd|th)))(?:{_MONTHS})(?![{_LETTER}])"
_DAY = r"(?<!\d)\d{1,2}(?:st|nd|rd|th)?(?![\d])"
_YEAR = r"(?<!\d)(?:\d{4}|\d{2})(?!\d)"
_PUNCT = r"[\s\-./_,']{0,3}"
#: Between two date parts: up to three separators, optionally around up to two whole connector
#: words (``12 de mayo de 90``, ``12th of the May 1990``).
_SEP = rf"{_PUNCT}(?:(?<![A-Za-z])(?:de|del|of|the)(?![A-Za-z]){_PUNCT}){{0,2}}"
_MONTH_DATE = re.compile(
    "|".join(
        _SEP.join(order)
        for order in (
            (_DAY, _MONTH, _YEAR),
            (_MONTH, _DAY, _YEAR),
            (_YEAR, _MONTH, _DAY),
            (_YEAR, _DAY, _MONTH),
            (_DAY, _YEAR, _MONTH),
            (_MONTH, _YEAR, _DAY),
        )
    ),
    re.IGNORECASE,
)
#: A date split over path segments spans at most this many of them (``/12/de/mayo/de/90``).
_DATE_WINDOW = 5
#: A path segment made of digits and number separators only (one part of a split number).
_NUMERIC_SEGMENT = re.compile(r"^[-.+ \d]*\d[-.+ \d]*$")
_ENTITY_TOKEN = re.compile(r"^<[A-Z][A-Z0-9_]{0,40}>$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,40}$")
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_ENCODED_SEPARATOR = re.compile(r"%2[Ff]|%5[Cc]")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
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


def _digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


def _value_like(text: str) -> bool:
    """True when decoded ``text`` looks like a value whatever the classifier says (see module)."""
    return bool(
        _VALUE_RUN.search(text)
        or _ID_SEGMENT.match(text)
        or _digits(text) >= _MIN_DIGITS
        or _DATE_SHAPE.search(text)
        or _MONTH_DATE.search(text)
    )


def _split_number(bases: Sequence[str]) -> set[int]:
    """Indexes of the path segments that are parts of one number split over segments: a run of
    consecutive numeric segments (digits and ``-``/``.``/``+``/space only) holding 6+ digits in
    all (``/acct/501/002/345/678``, ``/dob/12/05/90``)."""
    out: set[int] = set()
    run: list[int] = []
    for i, base in enumerate([*bases, ""]):
        if _NUMERIC_SEGMENT.match(unquote(base)):
            run.append(i)
            continue
        if sum(_digits(unquote(bases[j])) for j in run) >= _MIN_DIGITS:
            out.update(run)
        run = []
    return out


def _split_date(texts: Sequence[str]) -> set[int]:
    """Indexes of the (decoded) path segments that are parts of a date split over 2 to
    ``_DATE_WINDOW`` consecutive non-empty segments: the window joined with ``-`` holds a date
    shape or a month-name date that spans two of them or more (``/dob/1/5/90``, ``/12/may/90``,
    ``/may/12/90``, ``/12/de/mayo/de/90``). Every segment the date touches is in the result."""
    idx = [i for i, text in enumerate(texts) if text]
    out: set[int] = set()
    for a in range(len(idx) - 1):
        window = idx[a : a + _DATE_WINDOW]
        bounds: list[tuple[int, int, int]] = []
        pos = 0
        for i in window:
            bounds.append((pos, pos + len(texts[i]), i))
            pos += len(texts[i]) + 1
        joined = "-".join(texts[i] for i in window)
        for rx in (_DATE_SHAPE, _MONTH_DATE):
            for m in rx.finditer(joined):
                hit = {i for start, end, i in bounds if start < m.end() and m.start() < end}
                if len(hit) >= 2:
                    out |= hit
    return out


def _safe_key(key: str, classify: ValueClassifier) -> str:
    """``key`` itself when it is a plain name, else ``<k>`` (a key can be a value too)."""
    return key if _SAFE_KEY.match(key) and not _value_like(key) and not classify(key) else "<k>"


def _segment(raw: str, classify: ValueClassifier, ids: re.Pattern[str]) -> str:
    """One path segment (matrix parameters already split off) as ``<ENTITY>``, ``{id}`` or
    itself."""
    text = unquote(raw)
    if not text:
        return raw
    entity = classify(text)
    if entity:
        return f"<{entity}>"
    return "{id}" if _value_like(text) or ids.match(text) else raw


def _matrix(params: str, classify: ValueClassifier) -> str:
    """``;k=v;…`` of a segment as ``;k=<v>`` (``<k>`` for a key that is not a plain name); a bare
    item is a value (``;<v>``)."""
    out: list[str] = []
    for item in params.split(";"):
        key, eq, value = item.partition("=")
        if not eq:
            out.append(";<v>" if item else ";")
            continue
        text = unquote(value)
        entity = classify(text) if text else None
        safe = _safe_key(unquote(key), classify)
        out.append(f";{safe}=" + (f"<{entity}>" if entity else ("<v>" if text else "")))
    return "".join(out)


def _path(path: str, classify: ValueClassifier, ids: re.Pattern[str], *, matrix: bool) -> str:
    parts = [s.partition(";") for s in (path or "/").split("/")]
    bases = [base for base, _, _ in parts]
    split = _split_number(bases) | _split_date([unquote(base) for base in bases])
    segs: list[str] = []
    for i, (base, semi, params) in enumerate(parts):
        out = "{id}" if i in split else _segment(base, classify, ids)
        if semi and matrix:
            out += _matrix(params, classify)
        segs.append(out)
    return "/".join(segs)


def _keys(query: str, classify: ValueClassifier) -> str:
    keys = sorted({_safe_key(k, classify) for k, _ in parse_qsl(query, keep_blank_values=True)})
    return ("?" + "&".join(keys)) if keys else ""


def template(url: str, classify: ValueClassifier) -> str:
    """Crawl template: ``host[:port]/path[?keys][#route]``, value-free so it can be persisted
    (``dps_scan_page.template``) and used as the per-template visit budget's key.

    Path segments and hash-route segments follow :func:`redact_url`'s rules (any all-digit
    segment is an id too, so ``/help/faq/1`` … ``/8`` share one template); matrix parameters are
    dropped; only the sorted query KEYS are kept (``<k>`` for one that is itself a value). The raw
    URL never leaves worker memory (the crawl's seen-set holds :func:`canonical` forms).
    """
    parts = urlsplit(url)
    port = _port(parts)
    host = (parts.hostname or "").lower() + (f":{port}" if port else "")
    out = host + _path(parts.path, classify, _TEMPLATE_SEGMENT, matrix=False)
    out += _keys(parts.query, classify)
    fragment = parts.fragment or ""
    if _HASH_ROUTE.match(fragment):
        bang = "!" if fragment.startswith("!") else ""
        route, _, query = fragment[len(bang) :].partition("?")
        out += "#" + bang + _path(route, classify, _TEMPLATE_SEGMENT, matrix=False)
        out += _keys(query, classify)
    return out


def template_is_value_free(tpl: str) -> bool:
    """Structural check of a template before it is persisted: no path, route or key part of it is
    value-like and no matrix parameter is left (what :func:`template` guarantees). The host is
    the scan's registered host and is not checked."""
    slash = tpl.find("/")
    if slash < 0:
        return False
    rest = tpl[slash:]
    path, _, route = rest.partition("#")
    for part in (path, route.removeprefix("!")):
        segs, _, keys = part.partition("?")
        path_segs = segs.split("/")
        # template() makes every all-digit segment, every split number and every split date {id}
        if _split_number(path_segs) or any(_TEMPLATE_SEGMENT.match(unquote(s)) for s in path_segs):
            return False
        # a date split over segments, a placeholder standing in for a number (``{id}/may/{id}``):
        # template() would have made the readable parts of it {id} as well
        placeholders = {i for i, s in enumerate(path_segs) if s == "{id}" or _ENTITY_TOKEN.match(s)}
        texts = ["11" if i in placeholders else unquote(s) for i, s in enumerate(path_segs)]
        if _split_date(texts) - placeholders:
            return False
        for seg in [*path_segs, *(keys.split("&") if keys else [])]:
            if seg in ("{id}", "<k>") or _ENTITY_TOKEN.match(seg):
                continue
            if ";" in seg or "=" in seg or _value_like(unquote(seg)):
                return False
    return True


def validate_glob(glob: str) -> None:
    """Refuse a path glob that is not ``/``-rooted path characters (``UrlError`` ``glob_invalid``)."""
    if len(glob) > _MAX_GLOB or not _GLOB.match(glob):
        raise UrlError("glob_invalid")


def _decode_unreserved(segment: str) -> str:
    def one(m: re.Match[str]) -> str:
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else "%" + m.group(1).upper()

    return _ESCAPE.sub(one, segment)


def _normalise(path: str, *, params_first: bool, split_encoded: bool, lenient: bool = False) -> str:
    """One reading of ``path`` (see :func:`normalise_path`); ``params_first`` strips matrix
    parameters before encoded separators are split (Tomcat), ``split_encoded`` treats ``%2F`` and
    ``%5C`` as separators (servers that decode them); ``lenient`` decodes every escape first, ends
    the path at a NUL and trims trailing dots and spaces from each name (IIS/Windows)."""
    if lenient:
        path = unquote(path).split("\x00", 1)[0]
    raw = [s.partition(";")[0] if params_first else s for s in path.replace("\\", "/").split("/")]
    if split_encoded:
        raw = [p for s in raw for p in _ENCODED_SEPARATOR.sub("/", s).split("/")]
    segs = [_decode_unreserved(s.partition(";")[0]) for s in raw]
    if lenient:
        segs = [s if s in (".", "..") else s.rstrip(". ") for s in segs]
    out: list[str] = []
    for seg in segs:
        if seg == "..":
            if out:
                out.pop()
        elif seg not in ("", "."):
            out.append(seg)
    trailing = len(segs) > 1 and segs[-1] in ("", ".", "..")
    return "/" + "/".join(out) + ("/" if out and trailing else "")


def normalise_path(path: str) -> str:
    """The path a server routes for ``path``, for scope matching (never stored).

    A backslash, ``%2F`` and ``%5C`` count as separators, each segment loses its ``;`` matrix
    parameters (so ``..;`` is ``..``), escapes of unreserved characters are decoded (``%6C`` is
    ``l``, ``%2E`` is ``.``), ``.`` and ``..`` are resolved and empty segments (``//``) dropped; a
    trailing ``/`` is kept.
    """
    return _normalise(path, params_first=False, split_encoded=True)


def _readings(path: str) -> set[str]:
    """Every way a server may plausibly read ``path`` (scope matching fails closed over them)."""
    return {
        normalise_path(path),
        _normalise(path, params_first=True, split_encoded=True),
        _normalise(path, params_first=True, split_encoded=False),
        _normalise(path, params_first=True, split_encoded=True, lenient=True),
    }


def _glob_hit(path: str, globs: Sequence[str], fold_case: bool) -> bool:
    p = path.lower() if fold_case else path
    for raw in globs:
        g = normalise_path(raw).lower() if fold_case else normalise_path(raw)
        if fnmatch.fnmatchcase(p, g):
            return True
        stem = g.rstrip("/")
        if "*" not in g and (p == stem or p.startswith(stem + "/")):
            return True
    return False


def path_matches(
    path: str, globs: Sequence[str], *, fold_case: bool = False, every_reading: bool = False
) -> bool:
    """``fnmatch`` on the normalised readings of ``path`` (globs are normalised too); a glob
    without ``*`` also covers its sub-paths. True when any reading matches (a deny list), or with
    ``every_reading`` only when all do (an allow list); ``fold_case`` ignores case."""
    hits = [_glob_hit(r, globs, fold_case) for r in _readings(path)]
    return all(hits) if every_reading else any(hits)


def path_verdict(
    path: str, allow: Sequence[str], deny: Sequence[str]
) -> Literal["ok", "deny", "not_allowed"]:
    """The scope's path rail, failing closed: ``deny`` wins when any reading matches ignoring case
    (case-insensitive servers), then a non-empty ``allow`` must match every reading, case
    included."""
    if path_matches(path, deny, fold_case=True):
        return "deny"
    if allow and not path_matches(path, allow, every_reading=True):
        return "not_allowed"
    return "ok"


def route_path(url: str) -> str | None:
    """The path of ``url``'s hash route (``#/x`` or ``#!/x``, query dropped), else ``None``."""
    fragment = urlsplit(url.strip()).fragment or ""
    if not _HASH_ROUTE.match(fragment):
        return None
    return fragment.removeprefix("!").partition("?")[0]


def url_path_verdict(
    url: str, allow: Sequence[str], deny: Sequence[str]
) -> Literal["ok", "deny", "not_allowed"]:
    """:func:`path_verdict` of ``url``'s path; a hash route is a navigation too, so its path is
    checked against ``deny`` as well (``/app#/logout``). ``allow`` applies to the URL path only."""
    route = route_path(url)
    if route is not None and path_matches(route, deny, fold_case=True):
        return "deny"
    return path_verdict(urlsplit(url.strip()).path or "/", allow, deny)


def redact_url(url: str, classify: ValueClassifier) -> str:
    """Value-free form of ``url`` for storage and display.

    Path segments the classifier recognises become ``<ENTITY>``, value-like ones ``{id}``; query
    and matrix-parameter values become ``<ENTITY>`` or ``<v>`` and keys that are not plain names
    (or that are values themselves) ``<k>``; userinfo and the fragment are dropped.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = _netloc(scheme, (parts.hostname or "").lower(), _port(parts))
    path = _path(parts.path, classify, _ID_SEGMENT, matrix=True)
    pairs: list[str] = []
    for key, value in sorted(parse_qsl(parts.query, keep_blank_values=True)):
        entity = classify(value) if value else None
        pairs.append(
            f"{_safe_key(key, classify)}=" + (f"<{entity}>" if entity else ("<v>" if value else ""))
        )
    return urlunsplit((scheme, netloc, path, "&".join(pairs), ""))


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
        netloc = urlsplit(url.strip()).netloc
        if "@" in netloc or "\\" in netloc:  # a browser may read the authority differently
            return "other_origin"
        if self.same_origin and origin != self.start:
            return "other_origin"
        if not self.same_origin and not self.network_host_allowed(origin.host):
            return "unregistered_host"
        return url_path_verdict(url, self.allow, self.deny)
