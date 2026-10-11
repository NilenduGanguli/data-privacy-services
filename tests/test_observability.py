from __future__ import annotations

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
