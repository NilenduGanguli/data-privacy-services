"""Stateless (vaultless) mock of a bank tokenization service — the HTTP contract DPS speaks.

Local stack and tests only. Tokens are ``tk_`` + base32(AES-SIV(value, ad = entity|profile)), so
the mock is deterministic and reversible without storing anything. It logs request lines only
(method, path, status) and never a body. Run: ``python server.py --port 8420``
(``MOCK_TOKENIZER_TOKEN``, ``MOCK_TOKENIZER_KEY`` from the environment; local demo literals by
default). Standard library plus ``cryptography`` (both in the DPS image).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESSIV

MAX_BODY = 1_000_000
MAX_ITEMS = 1000


def _b32(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").lower().rstrip("=")


def _unb32(text: str) -> bytes:
    return base64.b32decode(text.upper() + "=" * ((-len(text)) % 8))


def make_server(host: str, port: int, *, token: str, key: str) -> ThreadingHTTPServer:
    """A threading HTTP server answering the wire contract (``port`` 0 = any free port)."""
    siv = AESSIV(hashlib.sha512(key.encode("utf-8")).digest())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # request line only, never bodies
            print(f"tokenizer-mock {self.command} {self.path.split('?')[0]}", flush=True)

        def _send(self, status: int, body: dict[str, Any] | None = None) -> None:
            raw = json.dumps(body or {}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802 - http.server's naming
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            else:
                self._send(404)

        def do_POST(self) -> None:  # noqa: N802 - http.server's naming
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                self._send(401)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send(413)
                return
            try:
                body = json.loads(self.rfile.read(length))
                ad = [f"{body['entity_type']}|{body.get('profile') or ''}".encode()]
                if self.path == "/v1/tokenize":
                    items = body["values"]
                    if len(items) > MAX_ITEMS:
                        self._send(413)
                        return
                    tokens = ["tk_" + _b32(siv.encrypt(str(v).encode("utf-8"), ad)) for v in items]
                    self._send(200, {"tokens": tokens})
                    return
                if self.path == "/v1/detokenize":
                    items = body["tokens"]
                    if len(items) > MAX_ITEMS:
                        self._send(413)
                        return
                    values = [siv.decrypt(_unb32(str(t)[3:]), ad).decode("utf-8") for t in items]
                    self._send(200, {"values": values})
                    return
            except Exception:  # noqa: BLE001 - a mock: any malformed input is a 400 without detail
                self._send(400)
                return
            self._send(404)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    """``python server.py [--host H] [--port P]``."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - inside the compose network
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()
    server = make_server(
        args.host,
        args.port,
        token=os.environ.get("MOCK_TOKENIZER_TOKEN", "dps-local-tokenizer-token"),
        key=os.environ.get("MOCK_TOKENIZER_KEY", "dps-local-tokenizer-key"),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
