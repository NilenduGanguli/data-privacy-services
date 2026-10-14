"""The smoke test's M5 block (M5 Task 24), offline against a stand-in app.

``python tools/smoke_test.py`` runs it last against ``docker compose --profile full``; here an
``httpx.MockTransport`` answers like the M5 routes do, so the eight checks, their skips and their
verdict lines are pinned — and no value, token or key ever reaches the console.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tools import smoke_test

SSN = "123-45-6789"
NEW_KEY = "dps_" + "n" * 32
TOKEN = "tk_ABCDEFGHIJKLMNOPQRSTUVWXYZ"
NAMES = [
    "policies with hashes",
    "reports summary",
    "posture report",
    "db key lifecycle",
    "external tokenization round trip",
    "422 never echoes input",
    "api responses are not cacheable",
]


class FakeApp:
    """Answers the M5 smoke calls; flags flip one behaviour at a time."""

    def __init__(self, *, db: bool = True, tokenizer: bool = True, no_store: bool = True) -> None:
        self.db, self.tokenizer, self.no_store = db, tokenizer, no_store
        self.disabled = False
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        body = json.loads(request.content) if request.content else None
        headers = {"cache-control": "no-store"} if self.no_store else {}
        if path == "/readyz":
            return httpx.Response(
                200,
                json={
                    "ready": True,
                    "components": {"db": {"ok": self.db}, "tokenizer": {"ok": self.tokenizer}},
                },
            )
        if path == "/api/v1/policies" and method == "GET":
            presets = [{"name": n, "hash": "a" * 64} for n in ("gdpr-pseudonymise", "pci-dss")]
            return httpx.Response(200, json={"presets": presets, "tenant": []}, headers=headers)
        if path == "/api/v1/policies/presets":
            return httpx.Response(200, json=[{"name": "gdpr-pseudonymise"}], headers=headers)
        if path == "/api/v1/reports/summary":
            assert request.url.params["days"] == "14"
            return httpx.Response(200, json={"tiles": {"raw_pii_sent_to_llm": 0}}, headers=headers)
        if path == "/api/v1/admin/posture":
            return httpx.Response(200, json={"total": 11, "passed": 6}, headers=headers)
        if path == "/api/v1/admin/keys" and method == "POST":
            assert body == {
                "name": "smoke-temp",
                "tenant_ids": ["default"],
                "scopes": ["detect"],
                "expires_in_days": 1,
            }
            return httpx.Response(
                201,
                json={"key": NEW_KEY, "record": {"id": "key_1", "key_prefix": NEW_KEY[:8]}},
                headers={"cache-control": "no-store"},
            )
        if path == "/api/v1/admin/keys/key_1/disable":
            self.disabled = True
            return httpx.Response(200, json={"id": "key_1", "status": "disabled"}, headers=headers)
        if path == "/api/v1/detect":
            if request.headers.get("X-API-KEY") == NEW_KEY:
                return httpx.Response(401 if self.disabled else 200, json={}, headers=headers)
            if body and not isinstance(body.get("text", ""), str):
                detail = [{"type": "string_type", "loc": ["body", "text"], "msg": "bad"}]
                return httpx.Response(422, json={"detail": detail}, headers=headers)
            return httpx.Response(200, json={"findings": []}, headers=headers)
        if path == "/api/v1/obfuscate":
            assert body["policy"]["treat"]["SSN"] == {
                "method": "external_tokenize",
                "profile": "ssn",
            }
            return httpx.Response(
                200,
                json={"result": {"ssn": TOKEN}, "key_material": {"kid": "k"}, "reveal_hints": []},
                headers=headers,
            )
        if path == "/api/v1/reveal":
            assert body["data"] == {"ssn": TOKEN}
            return httpx.Response(200, json={"result": {"ssn": SSN}}, headers=headers)
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke_test, "results", [])
    monkeypatch.setattr(smoke_test, "skipped", [])
    monkeypatch.setattr(smoke_test, "REQUIRE_M5", False)
    monkeypatch.setattr(smoke_test.time, "sleep", lambda _s: None)


def _run(app: FakeApp) -> None:
    with httpx.Client(base_url="http://dps.test", transport=httpx.MockTransport(app)) as c:
        smoke_test.m5_checks(c, {"X-API-KEY": "dps_local_dev_key_change_me"})


def test_the_eight_m5_checks_pass_against_a_complete_stack(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = FakeApp()
    _run(app)
    names = [name for name, ok, _ in smoke_test.results if ok]
    # "db key lifecycle" proves a new key works, then that the disabled key is refused.
    assert names == NAMES[:4] + ["db key disabled", *NAMES[4:]]
    assert len(names) == 8 and smoke_test.skipped == []
    assert app.disabled  # the temporary key never outlives the run
    out = capsys.readouterr().out
    assert out.count("PASS") == 8
    for secret in (SSN, NEW_KEY, TOKEN, "123456789"):
        assert secret not in out


def test_db_and_tokenizer_checks_skip_without_failing(capsys: pytest.CaptureFixture[str]) -> None:
    _run(FakeApp(db=False, tokenizer=False))
    passed = [name for name, ok, _ in smoke_test.results if ok]
    assert passed == ["policies with hashes", *NAMES[5:]]
    assert smoke_test.skipped == [
        "reports summary",
        "posture report",
        "db key lifecycle",
        "db key disabled",
        "external tokenization round trip",
    ]
    assert capsys.readouterr().out.count("SKIP") == 5


def test_a_required_m5_block_fails_on_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke_test, "REQUIRE_M5", True)
    with pytest.raises(SystemExit) as stop:
        _run(FakeApp(tokenizer=False))
    assert stop.value.code == 1


def test_a_cacheable_api_response_fails_the_run(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        _run(FakeApp(no_store=False))
    assert stop.value.code == 1
    assert "FAIL  api responses are not cacheable" in capsys.readouterr().out


def test_the_m5_block_runs_after_the_earlier_milestones() -> None:
    import inspect

    source = inspect.getsource(smoke_test.main)
    assert source.index("m2_checks(c)") < source.index("m3_checks(c)") < source.index("m5_checks(")
