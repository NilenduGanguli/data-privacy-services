from __future__ import annotations

import pytest
from pydantic import ValidationError

from dps.config import Settings
from dps.observability import (
    _SAFE_FIELDS,
    SCAN_FIELDS,
    SCAN_FILLS,
    SCAN_PAGES,
    SCAN_REQUESTS,
)
from dps.posture import posture_problems


def _s(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_scanner_defaults_match_the_contract() -> None:
    s = _s()
    assert (s.browser_concurrency, s.browser_metrics_port, s.browser_headless) == (2, 9401, True)
    assert (s.scan_max_pages, s.scan_max_depth) == (50, 3)
    assert (s.scan_page_timeout_seconds, s.scan_job_timeout_seconds) == (30, 1800)
    assert s.scan_store_unmasked_originals is False
    assert s.scan_registered_hosts_list == ["localhost", "127.0.0.1", "demo-site"]


def test_m4_additions_have_their_contract_defaults() -> None:
    s = _s()
    assert (
        s.scan_max_per_template,
        s.scan_max_spa_clicks_per_page,
        s.scan_max_fields_per_page,
        s.scan_max_skipped_rows,
        s.scan_max_events,
        s.scan_settle_ms,
        s.scan_viewport_width,
        s.scan_viewport_height,
        s.scan_screenshot_max_height,
    ) == (3, 25, 400, 200, 2000, 400, 1280, 900, 12000)


def test_registered_hosts_are_normalised() -> None:
    assert _s(scan_registered_hosts=" Demo-Site , ,LOCALHOST").scan_registered_hosts_list == [
        "demo-site",
        "localhost",
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("browser_concurrency", 0),
        ("browser_concurrency", 17),
        ("scan_max_pages", 0),
        ("scan_max_pages", 501),
        ("scan_max_depth", -1),
        ("scan_max_depth", 6),
        ("scan_page_timeout_seconds", 0),
        ("scan_settle_ms", -1),
    ],
)
def test_bounds_are_validated(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        _s(**{field: value})


def test_posture_refuses_unmasked_originals_and_headed_browser_in_prod() -> None:
    base = dict(
        env="prod", api_keys='[{"name":"k","key_sha256":"' + "0" * 64 + '"}]', llm_provider=""
    )
    problems = posture_problems(
        _s(**base, scan_store_unmasked_originals=True, browser_headless=False)
    )
    assert "DPS_SCAN_STORE_UNMASKED_ORIGINALS must be false" in problems
    assert "DPS_BROWSER_HEADLESS must be true" in problems
    clean = posture_problems(_s(**base))
    assert "DPS_SCAN_STORE_UNMASKED_ORIGINALS must be false" not in clean
    assert "DPS_BROWSER_HEADLESS must be true" not in clean
    assert (
        posture_problems(
            _s(env="local", scan_store_unmasked_originals=True, browser_headless=False)
        )
        == []
    )


def test_log_allow_list_has_scanner_fields_and_no_value_fields() -> None:
    assert {
        "job_id",
        "page_no",
        "pages",
        "fields",
        "depth",
        "decision",
        "resource_type",
        "blocked",
        "step",
        "persona",
        "phase",
        "kind",
        "elapsed_s",
    } <= _SAFE_FIELDS
    assert not ({"url", "value", "label", "text", "storage_state", "href"} & _SAFE_FIELDS)


def test_scan_metrics_are_registered_with_value_free_labels() -> None:
    assert SCAN_PAGES._labelnames == ("outcome",)
    assert SCAN_REQUESTS._labelnames == ("decision", "method", "reason")
    assert SCAN_FIELDS._labelnames == ("entity_type",)
    assert SCAN_FILLS._labelnames == ("mode", "outcome")
