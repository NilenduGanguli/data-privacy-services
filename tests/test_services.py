from __future__ import annotations

import pytest

from tests.services import SERVICES, host_port, require_service, service_url


def test_service_url_defaults_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DPS_TEST_OCR_URL", raising=False)
    monkeypatch.delenv("DPS_OCR_PORT", raising=False)
    assert service_url("ocr") == "http://127.0.0.1:5016"  # the contract port
    monkeypatch.setenv("DPS_OCR_PORT", "5017")  # a lane moved compose's host port ...
    assert service_url("ocr") == "http://127.0.0.1:5017"  # ... and the default follows it
    monkeypatch.setenv("DPS_TEST_OCR_URL", "http://ocr.test:5000")  # an explicit endpoint wins
    assert service_url("ocr") == "http://ocr.test:5000"


def test_every_default_follows_its_port_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, svc in SERVICES.items():
        monkeypatch.delenv(svc.env, raising=False)
        monkeypatch.delenv(svc.port_env, raising=False)
        assert host_port(service_url(name))[1] == svc.port, name
        monkeypatch.setenv(svc.port_env, str(svc.port + 10))
        assert host_port(service_url(name))[1] == svc.port + 10, name


def test_host_port_parses_urls_and_dsns() -> None:
    assert host_port("http://127.0.0.1:5016") == ("127.0.0.1", 5016)
    assert host_port("postgresql://u:p@db:5441/x") == ("db", 5441)
    assert host_port("127.0.0.1:1522/FREEPDB1") == ("127.0.0.1", 1522)
    assert host_port("https://gw.bank") == ("gw.bank", 443)


def test_require_service_skips_or_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DPS_TEST_OCR_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("DPS_TEST_REQUIRE_SERVICES", raising=False)
    with pytest.raises(pytest.skip.Exception, match="--profile docs up -d azure-ocr-mock"):
        require_service("ocr")
    monkeypatch.setenv("DPS_TEST_REQUIRE_SERVICES", "1")
    with pytest.raises(pytest.fail.Exception):
        require_service("ocr")
