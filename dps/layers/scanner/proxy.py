"""The scope proxy: every connection of a scan context passes a host check, on every hop.

Playwright routes only the first hop of a request. A redirect hop (an image, a fetch or a
navigation answered with a 3xx), and requests that never reach the route handler at all, would
otherwise go wherever the page sends them, which is a server-side request forgery from inside the
bank's network. So each scan context is created with ``proxy=`` pointing here
(``browser.new_scan_context``), and Chromium opens every connection of that context through this
proxy:

* ``CONNECT host:port`` (HTTPS, and every WebSocket) is tunnelled only when the guard allows
  the host. Otherwise it is answered ``403`` and reported. A tunnel whose first byte is a TLS
  handshake record carries TLS, so the method cannot be seen here. The route handler keeps the
  method rule for HTTPS. A redirect hop of an allowed GET is itself a GET, because 301/302/303
  become GET and 307/308 keep the method. Anything else inside a tunnel is plaintext HTTP, which
  Chromium sends only for a ``ws://`` socket. It gets the plain-HTTP rules below, so the upgrade
  is refused (a dedicated worker's socket, which no route sees, ends here).
* **Plain HTTP** (an absolute-form request) is forwarded only when the guard's policy allows it:
  GET or HEAD, no method override, no body, no upgrade, an allowed host and no fill value in the
  URL. The proxy sends one request per connection, with ``Connection: close`` in both
  directions, so a kept-alive connection can never carry a request that was not checked.

Loopback goes through the proxy as well. Playwright adds ``<-loopback>`` to the bypass list, and
``PROXY_BYPASS`` adds it again so that an environment with
``PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK`` set cannot switch it off. The proxy
listens on 127.0.0.1 on an ephemeral port and logs nothing. Policy and counting belong to the
``check`` callback (``ScanGuard``); the proxy only enforces its answer.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from typing import TypedDict
from urllib.parse import urlsplit

#: the bypass list given to Chromium with the proxy: no host, not even loopback, skips it
PROXY_BYPASS = "<-loopback>"
HEAD_LIMIT = 64 * 1024  # request or response head larger than this: refused
HEAD_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 15.0
IDLE_TIMEOUT_S = 120.0
_CHUNK = 64 * 1024
#: the first byte of a TLS handshake record: a tunnel that starts with anything else is plaintext
TLS_HANDSHAKE = b"\x16"
#: connection-level headers that are never forwarded (the proxy sets its own Connection)
_HOP_BY_HOP = frozenset(
    {"connection", "keep-alive", "proxy-connection", "proxy-authorization", "te", "trailer"}
)


class ProxySettings(TypedDict):
    """The ``proxy=`` option of ``Browser.new_context``."""

    server: str
    bypass: str


#: (method, url, request headers by lower-case name) -> allow. A CONNECT passes ``https://host:port/``.
HopCheck = Callable[[str, str, Mapping[str, str]], bool]


def _status(code: int, text: str) -> bytes:
    return (
        f"HTTP/1.1 {code} {text}\r\ncontent-type: text/plain\r\ncontent-length: 0\r\n"
        "connection: close\r\n\r\n"
    ).encode("ascii")


def _parse_head(head: bytes) -> tuple[str, str, list[tuple[str, str]]] | None:
    """Request line and headers of a head, or ``None`` when it is not a well-formed request."""
    try:
        text = head.decode("latin-1")
    except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes every byte
        return None
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return None
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name or name != name.strip():
            return None
        headers.append((name, value.strip()))
    return parts[0], parts[1], headers


def _authority(target: str) -> tuple[str, int] | None:
    """``host:port`` of a CONNECT target (IPv6 in brackets), or ``None``."""
    try:
        parts = urlsplit(f"//{target}")
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not host or port is None or parts.path or parts.query or parts.username:
        return None
    return host.lower(), port


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy ``reader`` to ``writer`` until EOF or an idle timeout, then half-close ``writer``."""
    try:
        while True:
            data = await asyncio.wait_for(reader.read(_CHUNK), IDLE_TIMEOUT_S)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            if writer.can_write_eof():
                writer.write_eof()


class ScopeProxy:
    """A forward proxy for one scan context. ``check`` decides every hop (``ScanGuard``)."""

    def __init__(self, check: HopCheck) -> None:
        self._check = check
        self._server: asyncio.Server | None = None
        self._port = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self) -> ProxySettings:
        """Listen on 127.0.0.1 (idempotent) and return the context's ``proxy=`` option."""
        if self._server is None:
            self._server = await asyncio.start_server(
                self._accept, host="127.0.0.1", port=0, limit=HEAD_LIMIT
            )
            self._port = int(self._server.sockets[0].getsockname()[1])
        return ProxySettings(server=f"http://127.0.0.1:{self._port}", bypass=PROXY_BYPASS)

    async def close(self) -> None:
        """Stop listening and drop every open connection (idempotent)."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for writer in list(self._writers):
            writer.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if server is not None:
            with contextlib.suppress(OSError):
                await server.wait_closed()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        try:
            await self._serve(reader, writer)
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
            if task is not None:
                self._tasks.discard(task)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
        parsed = _parse_head(head)
        if parsed is None:
            writer.write(_status(400, "Bad Request"))
            return
        method, target, headers = parsed
        if method.upper() == "CONNECT":
            await self._tunnel(reader, writer, target)
        else:
            await self._forward(writer, method, target, headers)
        with contextlib.suppress(OSError):
            await writer.drain()

    async def _tunnel(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: str
    ) -> None:
        authority = _authority(target)
        if authority is None or not self._check("CONNECT", f"https://{target}/", {}):
            writer.write(_status(403, "Forbidden"))
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(*authority, limit=HEAD_LIMIT), CONNECT_TIMEOUT_S
            )
        except (OSError, TimeoutError):
            writer.write(_status(502, "Bad Gateway"))
            return
        self._writers.add(up_writer)
        try:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            first = await asyncio.wait_for(reader.readexactly(1), IDLE_TIMEOUT_S)
            if first == TLS_HANDSHAKE:
                up_writer.write(first)
                await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
                return
            head = first + await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
            parsed = _parse_head(head)
            if parsed is None or not parsed[1].startswith("/"):
                writer.write(_status(400, "Bad Request"))
                return
            method, path, headers = parsed
            url = f"http://{target}{path}"
            if not self._check(method, url, {n.lower(): v for n, v in headers}):
                writer.write(_status(403, "Forbidden"))
                return
            await _exchange(up_reader, up_writer, writer, method, path, headers)
        finally:
            self._writers.discard(up_writer)
            up_writer.close()

    async def _forward(
        self,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
    ) -> None:
        names = {name.lower(): value for name, value in headers}
        try:
            parts = urlsplit(target)
            host, port = parts.hostname, parts.port or 80
        except ValueError:
            host, port = None, 0
        if not host or not self._check(method, target, names):
            writer.write(_status(403, "Forbidden"))
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, limit=HEAD_LIMIT), CONNECT_TIMEOUT_S
            )
        except (OSError, TimeoutError):
            writer.write(_status(502, "Bad Gateway"))
            return
        self._writers.add(up_writer)
        try:
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            await _exchange(up_reader, up_writer, writer, method, path, headers)
        finally:
            self._writers.discard(up_writer)
            up_writer.close()


async def _exchange(
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    headers: list[tuple[str, str]],
) -> None:
    """Send one checked request head (no body: a request with one is never allowed) upstream with
    ``Connection: close`` and copy the one answer back. Nothing more the client sends is read."""
    lines = [f"{method} {path} HTTP/1.1"]
    lines += [f"{n}: {v}" for n, v in headers if n.lower() not in _HOP_BY_HOP]
    lines.append("Connection: close")
    up_writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    await up_writer.drain()
    response = await asyncio.wait_for(up_reader.readuntil(b"\r\n\r\n"), IDLE_TIMEOUT_S)
    writer.write(_close_response(response))
    await writer.drain()
    await _pipe(up_reader, writer)


def _close_response(head: bytes) -> bytes:
    """The upstream response head with its connection headers replaced by ``Connection: close``."""
    lines = head.decode("latin-1").split("\r\n")
    kept = [lines[0]] + [
        line
        for line in lines[1:]
        if line and line.partition(":")[0].strip().lower() not in _HOP_BY_HOP
    ]
    return ("\r\n".join([*kept, "Connection: close"]) + "\r\n\r\n").encode("latin-1")
