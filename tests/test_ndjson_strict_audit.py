"""NDJSON streams (M1) under a strict audit sink (M2, ``DPS_AUDIT_STRICT``).

On the JSON routes a strict sink that cannot take the event answers 503 ``audit_unavailable`` and
returns nothing the engine produced. A stream has already sent its header and items when its one
audit event is written, so the event is written before the closing ``report`` line: a refusal
ends the stream with an ``error`` line instead — the NDJSON contract's "failed closed" — and the
replay cache does not record it, so a client never takes an unaudited stream for a complete one.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import AuditEvent, AuditUnavailable, MemoryAuditSink
from dps.config import Settings
from dps.layers.api.ndjson import NDJSON

POLICY = "gdpr-pseudonymise"
ITEMS = [{"email": "a.k@example-mail.in"}, {"email": "b.m@example-mail.in"}]


class _Refusing(MemoryAuditSink):
    """A strict sink whose queue is saturated."""

    def write(self, event: AuditEvent) -> None:
        raise AuditUnavailable()


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stub",
        fpe_enabled=False,
        ner_enabled=False,
        rate_limit_enabled=False,
        audit_strict=True,
    )
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


def _lines(response: Any) -> list[dict[str, Any]]:
    assert response.headers["content-type"].startswith(NDJSON), response.status_code
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_a_stream_whose_audit_is_refused_ends_failed_closed_without_a_report() -> None:
    with TestClient(create_app(_settings(), audit_sink=_Refusing())) as c:
        headers = {"accept": NDJSON, "Idempotency-Key": "strict-stream-1"}
        body = {"data": ITEMS, "policy": POLICY}
        first = c.post("/api/v1/obfuscate", json=body, headers=headers)
        out = _lines(first)
        assert out[0]["type"] == "header"
        assert [line["type"] for line in out[1:-1]] == ["item", "item"]
        assert out[-1] == {
            "type": "error",
            "index": None,
            "code": "audit_unavailable",
            "detail": "audit unavailable",
        }
        assert "example-mail" not in json.dumps(out[-1])
        # not recorded for replay: the retry is a first call
        again = c.post("/api/v1/obfuscate", json=body, headers=headers)
        assert "idempotent-replay" not in again.headers
        assert _lines(again)[-1]["code"] == "audit_unavailable"
        # the JSON route answers 503 with nothing the engine produced, as before
        plain = c.post("/api/v1/obfuscate", json={"data": ITEMS[0], "policy": POLICY})
        assert plain.status_code == 503 and plain.json() == {
            "detail": "audit unavailable",
            "code": "audit_unavailable",
        }


def test_a_stream_with_a_working_sink_still_writes_one_event_and_ends_with_its_report() -> None:
    sink = MemoryAuditSink()
    with TestClient(create_app(_settings(), audit_sink=sink)) as c:
        out = _lines(
            c.post(
                "/api/v1/detect", json={"data": ITEMS, "policy": POLICY}, headers={"accept": NDJSON}
            )
        )
    assert [line["type"] for line in out] == ["header", "item", "item", "report"]
    assert len(sink.events) == 1 and sink.events[0].outcome == "ok"
    assert sink.events[0].trace_id == out[0]["trace_id"]
