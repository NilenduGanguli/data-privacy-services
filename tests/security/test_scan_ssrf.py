"""S5 (security review, the M4 merge into ``fsec``): the scanner's SSRF guard, wired.

* ``POST /scans`` runs ``netguard.check_scan_url`` before any job exists: a registered host that
  is — or resolves to — a link-local/cloud-metadata address (IPv4-mapped IPv6 unwrapped), a numeric
  host encoding, or a name that does not resolve answers 422 ``host_forbidden``; the refusal is
  audited, value-free, and nothing is queued. The browser-worker checks the start URL again before
  any browser starts (DNS may have changed since the scan was accepted).
* At crawl time the scope proxy — every connection of a scan context passes it — resolves each
  hop's host once and dials only an address ``netguard.forbidden_ip_reason`` lets through (no
  link-local/metadata, multicast, unspecified or reserved address; no loopback outside
  ``env=local``), connecting to that address itself so a second lookup cannot answer differently
  (DNS rebinding). A refused hop answers 403 and is counted ``blocked`` / ``host_forbidden``.
* Chromium is launched with ``--host-resolver-rules`` that resolve nothing but the scope proxy's
  own address: the browser never looks a name up itself, so no name reaches an address the guard
  never checked (browser and browser-worker proofs: ``tests/scanner/test_scan_ssrf_browser.py``).

The hosts below (``*.bank.test``) exist only in the fake resolvers: a connection that reaches the
counting server through one of them proves the proxy dialled the address it checked.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from dps.config import Settings
from dps.layers.scanner.guard import UNROUTED, ScanGuard

DNS = {
    "uat.bank.test": ["127.0.0.1"],
    "rebind.bank.test": ["169.254.169.254"],
    "mapped.bank.test": ["::ffff:169.254.169.254"],
    "split.bank.test": ["127.0.0.1", "169.254.169.254"],
    "nat64.bank.test": ["64:ff9b::a9fe:a9fe"],
}


async def _resolve(host: str, _port: int) -> list[str]:
    if host not in DNS:
        raise OSError("no such host")
    return DNS[host]


class _Seen:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.paths: list[str] = []


@pytest.fixture
def counting_server() -> Iterator[tuple[int, _Seen]]:
    seen = _Seen()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            with seen.lock:
                seen.paths.append(self.path)
            body = b"<!doctype html><title>ok</title><p>ok</p>"
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1]), seen
    finally:
        server.shutdown()
        server.server_close()


async def _through_proxy(guard: ScanGuard, head: str) -> int:
    """One raw request to ``guard``'s scope proxy; its status code (0: no HTTP answer)."""
    proxy = await guard.start_proxy()
    host, port = proxy["server"].removeprefix("http://").split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(head.encode("latin-1") + b"\r\n\r\n")  # the head ends after its last header
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    return int(data.split(b" ", 2)[1]) if data.startswith(b"HTTP/1.") else 0


def _guard(*, allow_loopback: bool = True) -> ScanGuard:
    return ScanGuard(
        host_allowed=lambda h: h in DNS or h == "127.0.0.1",
        allow_loopback=allow_loopback,
        resolve=_resolve,
    )


def _refusals(guard: ScanGuard) -> set[tuple[str, str, str, str | None]]:
    return {
        (c.method, c.decision, c.reason, c.resource_type)
        for c in guard.drain_counts()
        if c.decision == "blocked"
    }


async def test_the_proxy_dials_the_address_it_checked(counting_server: tuple[int, _Seen]) -> None:
    """Positive control: a registered name that resolves to an allowed address is reached — at
    that address (the name exists only in the guard's resolver)."""
    port, seen = counting_server
    guard = _guard()
    try:
        status = await _through_proxy(
            guard, f"GET http://uat.bank.test:{port}/landed-uat HTTP/1.1\r\nHost: uat.bank.test"
        )
    finally:
        await guard.stop_proxy()
    assert status == 200 and seen.paths == ["/landed-uat"]
    assert _refusals(guard) == set()


@pytest.mark.parametrize("host", ["rebind.bank.test", "mapped.bank.test", "split.bank.test"])
async def test_a_hop_to_a_metadata_address_is_refused_on_both_proxy_paths(
    counting_server: tuple[int, _Seen], host: str
) -> None:
    """A registered (allowed) name that resolves to a link-local/metadata address — directly,
    IPv4-mapped, or among other addresses — is refused, over plain HTTP and as a CONNECT tunnel,
    and counted; nothing is dialled."""
    port, seen = counting_server
    guard = _guard()
    try:
        plain = await _through_proxy(
            guard, f"GET http://{host}:{port}/meta HTTP/1.1\r\nHost: {host}"
        )
        tunnel = await _through_proxy(guard, f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443")
    finally:
        await guard.stop_proxy()
    assert (plain, tunnel) == (403, 403) and seen.paths == []
    assert _refusals(guard) == {
        ("GET", "blocked", "host_forbidden", UNROUTED),
        ("CONNECT", "blocked", "host_forbidden", UNROUTED),
    }
    blocked = guard.drain_blocked()
    assert blocked and {b.reason for b in blocked} == {"host_forbidden"}


async def test_a_nat64_wrapped_metadata_address_is_refused(
    counting_server: tuple[int, _Seen],
) -> None:
    port, seen = counting_server
    guard = _guard()
    try:
        status = await _through_proxy(
            guard, f"GET http://nat64.bank.test:{port}/x HTTP/1.1\r\nHost: nat64.bank.test"
        )
    finally:
        await guard.stop_proxy()
    assert status == 403 and seen.paths == []


async def test_loopback_is_refused_outside_local_even_for_a_registered_host(
    counting_server: tuple[int, _Seen],
) -> None:
    port, seen = counting_server
    guard = _guard(allow_loopback=False)
    try:
        literal = await _through_proxy(
            guard, f"GET http://127.0.0.1:{port}/lit HTTP/1.1\r\nHost: 127.0.0.1"
        )
        named = await _through_proxy(
            guard, f"GET http://uat.bank.test:{port}/named HTTP/1.1\r\nHost: uat.bank.test"
        )
    finally:
        await guard.stop_proxy()
    assert (literal, named) == (403, 403) and seen.paths == []
    assert ("GET", "blocked", "host_forbidden", UNROUTED) in _refusals(guard)


async def test_a_name_that_does_not_resolve_is_never_dialled() -> None:
    """Not a policy refusal (nothing to count): the proxy answers 502 as for any failed dial."""
    guard = ScanGuard(host_allowed=lambda h: True, resolve=_resolve)
    try:
        status = await _through_proxy(
            guard, "GET http://nowhere.bank.test/x HTTP/1.1\r\nHost: nowhere.bank.test"
        )
    finally:
        await guard.stop_proxy()
    assert status == 502 and _refusals(guard) == set()


async def test_a_host_no_resolver_can_encode_is_a_bad_gateway() -> None:
    """A page-chosen name the resolver cannot even encode (a 64-character label) is a failed dial
    (502), never an unhandled error in the proxy."""
    guard = ScanGuard(host_allowed=lambda h: True)
    host = "a" * 64 + ".bank.test"
    try:
        status = await _through_proxy(guard, f"GET http://{host}/x HTTP/1.1\r\nHost: {host}")
    finally:
        await guard.stop_proxy()
    assert status == 502 and _refusals(guard) == set()


async def test_the_default_resolver_checks_real_lookups(counting_server: tuple[int, _Seen]) -> None:
    """Without an injected resolver the guard resolves through the event loop: ``localhost`` is
    loopback — reached in ``env=local``, refused outside it."""
    port, seen = counting_server
    local = ScanGuard(host_allowed=lambda h: h == "localhost")
    strict = ScanGuard(host_allowed=lambda h: h == "localhost", allow_loopback=False)
    try:
        ok = await _through_proxy(
            local, f"GET http://localhost:{port}/local HTTP/1.1\r\nHost: localhost"
        )
        refused = await _through_proxy(
            strict, f"GET http://localhost:{port}/strict HTTP/1.1\r\nHost: localhost"
        )
    finally:
        await local.stop_proxy()
        await strict.stop_proxy()
    assert (ok, refused) == (200, 403) and seen.paths == ["/local"]


# --- Chromium resolves nothing itself ---------------------------------------------------------------
def test_the_scan_browser_resolves_only_the_proxy_address() -> None:
    from dps.layers.scanner.browser import RESOLVER_RULES, launch_args
    from dps.layers.scanner.proxy import PROXY_HOST
    from dps.netguard import chromium_resolver_rules

    assert PROXY_HOST == "127.0.0.1"
    assert RESOLVER_RULES == chromium_resolver_rules([PROXY_HOST])
    assert RESOLVER_RULES == "MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"
    assert f"--host-resolver-rules={RESOLVER_RULES}" in launch_args()


# --- POST /scans and the browser-worker ----------------------------------------------------------
_HOSTS = "127.0.0.1,uat.bank.test,rebind.bank.test,::ffff:169.254.169.254,ghost.bank.test"


def _sync_resolve(host: str) -> list[str]:
    if host == "uat.bank.test":
        return ["10.1.2.3"]
    if host == "rebind.bank.test":
        return ["169.254.169.254"]
    raise OSError("no such host")


@pytest.mark.db
def test_post_scans_runs_the_ssrf_guard_before_any_job_exists(
    db_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from dps.app import create_app
    from dps.audit import MemoryAuditSink
    from tests.api.test_api_connections import FULL
    from tests.fixtures.db import _truncate, api_settings

    monkeypatch.setattr("dps.netguard.default_resolver", _sync_resolve)
    sink = MemoryAuditSink()
    settings = api_settings(db_settings, tmp_path, scan_registered_hosts=_HOSTS)
    try:
        with TestClient(create_app(settings, audit_sink=sink)) as c:
            for url in (
                "https://rebind.bank.test/apply",  # registered, resolves to the metadata address
                "http://[::ffff:169.254.169.254]/latest/meta-data",  # registered, IPv4-mapped
                "http://2130706433/apply",  # a numeric host encoding of 127.0.0.1
                "http://0x7f000001/apply",
                "https://ghost.bank.test/apply",  # registered, does not resolve
            ):
                r = c.post("/api/v1/scans", json={"url": url}, headers=FULL)
                assert (r.status_code, r.json().get("code")) == (422, "host_forbidden"), url
                assert "rebind" not in r.text and "169.254" not in r.text and "ghost" not in r.text
            assert c.get("/api/v1/scans", headers=FULL).json()["items"] == []  # nothing queued
            refused = [e for e in sink.events if e.action == "scan.create"]
            assert [(e.outcome, e.status) for e in refused] == [("rejected", 422)] * 5
            unregistered = c.post(
                "/api/v1/scans", json={"url": "https://evil.bank.test/"}, headers=FULL
            )
            assert unregistered.json()["code"] == "host_not_registered"
            ok = c.post("/api/v1/scans", json={"url": "https://uat.bank.test/apply"}, headers=FULL)
            assert ok.status_code == 202, ok.text  # RFC 1918 stays allowed
    finally:
        asyncio.run(_truncate(db_settings))
