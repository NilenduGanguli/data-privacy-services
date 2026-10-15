"""Task 16 (M2 touch point): the evidence HTML prints the ``scan`` block when a pack has one, and
renders every other pack exactly as before."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dps.layers.connectors.evidence import EvidencePack, render_html

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def _pack(**kw: Any) -> EvidencePack:
    base: dict[str, Any] = dict(
        id="ev_1",
        tenant_id="t1",
        job_id="s_1",
        job_kind="scan",
        outcome="succeeded",
        label="synthetic — measured, not claimed anonymous",
        mode="synthesize",
        created_at=NOW,
        engine_version="0.0.0",
        source={"kind": "ui", "url": "http://demo-site:8410/apply"},
        target={"kind": "artefacts"},
        policy={"name": "default", "hash": "abc"},
        snapshot={},
        consistency={"kid": "k_1"},
        columns=[],
        reveal_map=[],
        counts={"pages": 2},
        verification={"passed": True, "hits": 0},
        timings={"duration_s": 1.0},
        partitions=[],
        errors=[],
        config_hash="h",
    )
    return EvidencePack.model_validate(base | kw)


def test_scan_block_rendered() -> None:
    scan = {
        "pages": 2,
        "forms": 1,
        "fields": 3,
        "by_entity": {"AADHAAR": 1, "<b>X</b>": 1},
        "risk_flags": [
            {
                "code": "A1",
                "flag": "pii_rendered_unmasked",
                "severity": "high",
                "path": "http://demo-site:8410/profile",
                "entity_types": ["AADHAAR"],
            }
        ],
        "personas": 3,
        "display_persona": 1,
        "submissions": 0,
        "non_get_allowed": 0,
        "blocked_requests": {"POST": 3},
        "navigations": 2,
        "validation": {"synthesize": {"checked": 3, "accepted": 3, "rejected": 0}},
        "captures": {"captured": 1, "withheld": 0},
        "spa_routes": 1,
        "scope_routes_reached": 2,
    }
    page = render_html(_pack(scan=scan), sha="s" * 64)
    assert "UI scan" in page and "Submissions: 0" in page and "POST 3" in page
    assert "own click handler ran (no request to refuse): 2" in page  # M4 verify r1 R3-B1
    assert (
        "Non-GET requests allowed: 0" in page and "A1" in page and "pii_rendered_unmasked" in page
    )
    assert "&lt;b&gt;X&lt;/b&gt;" in page and "<b>X</b>" not in page  # escaped like the rest
    assert "<script" not in page


def test_a_pack_without_scan_renders_as_before() -> None:
    page = render_html(_pack(job_kind="run"), sha="s" * 64)
    assert "UI scan" not in page and "Submissions" not in page
    assert "{fidelity}" not in page and "\n<h2>7 Regulatory note</h2>" in page
