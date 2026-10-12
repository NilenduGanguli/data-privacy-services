from __future__ import annotations

import logging

from dps.observability import (
    LLM_RAW_PII,
    READINESS,
    Readiness,
    _drop_value_fields,
    configure_logging,
    metrics_payload,
)


def test_value_fields_never_reach_the_log() -> None:
    event = {
        "event": "obfuscate",
        "entity_type": "SSN",
        "value": "123-45-6789",
        "dek": "x",
        "count": 3,
    }
    out = _drop_value_fields(None, "info", event)
    assert out == {"event": "obfuscate", "entity_type": "SSN", "count": 3}


def test_readiness_snapshot_requires_required_components() -> None:
    r = Readiness(required=("api-core",))
    assert r.snapshot()["ready"] is False
    r.set("api-core", True, "up")
    r.set("llm-gateway", False, "unconfigured", provider="")
    snap = r.snapshot()
    assert snap["ready"] is True
    assert snap["components"]["llm-gateway"] == {
        "ok": False,
        "detail": "unconfigured",
        "provider": "",
    }


def test_metrics_payload_exposes_the_zero_counter() -> None:
    configure_logging("INFO", json_output=True)
    body, content_type = metrics_payload()
    assert b"dps_llm_raw_pii_sent_total" in body
    assert content_type.startswith("text/plain")
    assert LLM_RAW_PII._value.get() == 0.0  # noqa: SLF001 - the whole point of the metric
    assert READINESS.snapshot()["components"] is not None


def test_configured_pipeline_drops_values_end_to_end(capsys) -> None:  # noqa: ANN001
    from dps.observability import get_logger

    configure_logging("INFO", json_output=True)
    get_logger("dps.test").info(
        "obfuscate", entity_type="SSN", value="123-45-6789", token="tok-abc"
    )
    out = capsys.readouterr().out
    assert '"entity_type": "SSN"' in out
    assert "123-45-6789" not in out and "tok-abc" not in out


# Synthetic stand-ins for a customer value or a secret, one per field name a call site might use.
_PROBE = {
    "ssn": "078-05-1120",
    "email": "probe.person@example.test",
    "access_token": "tok-probe-access",
    "client_secret": "probe-client-secret",
    "Value": "probe-capitalised-value",
    "authorization": "Bearer probe-bearer",
    "dek_b64": "cHJvYmUtZGVr",
    "name": "Probe Person",
    "exc_info": True,
}


def test_only_allow_listed_fields_survive() -> None:
    event = {"event": "obfuscate", "entity_type": "SSN", "count": 2, **_PROBE}
    out = _drop_value_fields(None, "info", event)
    assert out == {"event": "obfuscate", "entity_type": "SSN", "count": 2}


def test_configured_pipeline_drops_unknown_fields_end_to_end(capsys) -> None:  # noqa: ANN001
    from dps.observability import get_logger

    configure_logging("INFO", json_output=True)
    get_logger("dps.test").info("probe", route="/v1/detect", **_PROBE)
    out = capsys.readouterr().out
    assert '"route": "/v1/detect"' in out and '"event": "probe"' in out
    for value in _PROBE.values():
        if isinstance(value, str):
            assert value not in out


def test_exceptions_in_allowed_fields_log_their_class_only() -> None:
    exc = KeyError("078-05-1120")
    out = _drop_value_fields(None, "warning", {"event": "x", "error": exc})
    assert out == {"event": "x", "error": "KeyError"}


def test_fields_the_m0_call_sites_log_are_kept() -> None:
    # Keys logged by the startup banner, the NER adapter, verification and the audit sink.
    event = {
        "event": "audit",
        "level": "info",
        "timestamp": "2026-10-11T00:00:00Z",
        "ts": "2026-10-11T00:00:00Z",
        "trace_id": "t1",
        "route": "/v1/obfuscate",
        "tenant_id": "default",
        "key_id": "k1",
        "mode": "obfuscate",
        "policy": "gdpr-pseudonymise",
        "findings_total": 3,
        "findings_by_type": {"SSN": 1},
        "methods_used": {"fpe": 1},
        "verification_passed": True,
        "reversible_values": 1,
        "duration_ms": 1.5,
        "outcome": "ok",
        "hits": 0,
        "env": "local",
        "auth": True,
        "provider": "none",
        "reason": "no dist",
        "error": "ImportError",
    }
    assert _drop_value_fields(None, "info", dict(event)) == event


def test_fields_the_m2_queue_logs_are_kept() -> None:
    # Ids, counts, phases and metadata names the job queue and connectors log (M2).
    event = {
        "event": "partition_done",
        "job_id": "j_01",
        "partition_id": 7,
        "partition_no": 3,
        "phase": "partitions",
        "state": "done",
        "kind": "run",
        "worker_id": "host:1:abcdef",
        "rows": 1000,
        "batch_no": 4,
        "attempts": 1,
        "engine": "postgres",
        "write_mode": "table",
        "connection_id": "c_01",
        "snapshot_id": "snap_01",
        "evidence_id": "ev_01",
        "group_id": "grp_01",
        "partitions": 8,
        "elapsed_s": 1.5,
        "rows_per_s": 6600.0,
        "object_ref": "bank.customers",
        "code": "lease_lost",
        "checks": 3,
        "lease_lost": True,
    }
    assert _drop_value_fields(None, "info", dict(event)) == event
    assert _drop_value_fields(None, "info", {"event": "x", "last_key": "[1]"}) == {"event": "x"}


def test_fields_the_m2_audit_line_logs_are_kept() -> None:
    # DbAuditSink's synchronous line carries the M2 AuditEvent fields (all value-free).
    event = {
        "event": "audit",
        "route": "/api/v1/jobs/{job_id}/cancel",
        "action": "job.cancel",
        "status": 200,
        "job_id": "j_01",
        "counts": {"partitions": 4},
    }
    assert _drop_value_fields(None, "info", dict(event)) == event


def test_free_text_event_messages_are_replaced() -> None:
    # Event names are snake_case identifiers; a message with an interpolated value is not one.
    for event in ("obfuscate", "verification_failed", "llm.call", "s3_upload"):
        assert _drop_value_fields(None, "info", {"event": event}) == {"event": event}
    for event in ("customer 078-05-1120 processed", "Probe Person", "pan_4111111111111111", 42):
        out = _drop_value_fields(None, "info", {"event": event})
        assert out == {"event": "event_redacted"}, event


def test_m1_fields_are_allowed_and_values_still_dropped() -> None:
    out = _drop_value_fields(
        None,
        "info",
        {
            "event": "document_processed",
            "pages": 3,
            "boxes": 9,
            "ocr_pages": 2,
            "text": "Ananya",
            "value": "x",
        },
    )
    assert out == {"event": "document_processed", "pages": 3, "boxes": 9, "ocr_pages": 2}


def test_third_party_loggers_are_capped() -> None:
    configure_logging("DEBUG", json_output=True)
    assert logging.getLogger("pdfminer").level == logging.ERROR
    assert logging.getLogger("httpx").level == logging.WARNING


def test_m5_log_fields_are_allowed() -> None:
    from dps.observability import _SAFE_FIELDS

    assert {
        "job_id",
        "engine",
        "columns",
        "rows",
        "resamples",
        "adapter",
        "deleted",
        "tenants",
        "artefact_id",
        "bytes",
        "gate",
        "version",
        "key_count",
        "grace_hours",
    } <= _SAFE_FIELDS
    # "model" stays allowed: M0's LLM gateway logs the model *name* (never a fitted model)
    forbidden = {"value", "token", "dek", "seed", "tabular_model", "raw_key", "password"}
    assert not (forbidden & _SAFE_FIELDS)


def test_m5_metrics_exist() -> None:
    from dps.observability import EXTERNAL_CALLS, KEYS_LOADED, TABULAR_RESAMPLES, metrics_payload

    TABULAR_RESAMPLES.inc(0)
    EXTERNAL_CALLS.labels("tokenize", "ok").inc(0)
    KEYS_LOADED.set(0)
    body, _ = metrics_payload()
    for name in (
        b"dps_tabular_resamples_total",
        b"dps_external_token_calls_total",
        b"dps_auth_keys_loaded",
    ):
        assert name in body
