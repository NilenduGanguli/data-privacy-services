"""``tools/demo_client.py`` — the HTTP client the smoke test, flow report and demo tools share.

Offline: an ``httpx.MockTransport`` stands in for the stack. A request or response body, a value or
a key never reaches an error message, a ``repr`` or the console (contract §0 rule 1).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from tools import demo_client
from tools.demo_client import DemoError, DpsClient, load_keys, route_template

SECRET_VALUE = "123-45-6789"
KEY = "dps_" + "k" * 32
JOB = "job_01HZYX4V9R7Q2W3E4T5Y6U7I8O"


def _client(handler: Any, *, tenant: str | None = None) -> DpsClient:
    class _Mocked(DpsClient):
        _transport = httpx.MockTransport(handler)

    return _Mocked("http://dps.test", KEY, tenant=tenant)


def test_the_signature_is_the_plans() -> None:
    import inspect

    init = inspect.signature(DpsClient.__init__)
    assert list(init.parameters) == ["self", "base_url", "api_key", "tenant", "timeout"]
    assert init.parameters["tenant"].kind is inspect.Parameter.KEYWORD_ONLY
    assert init.parameters["timeout"].default == 60.0
    post = inspect.signature(DpsClient.post)
    assert post.parameters["expect"].default == (200, 201, 202)
    wait = inspect.signature(DpsClient.wait_job)
    assert wait.parameters["timeout"].default == 900
    assert wait.parameters["kind"].default == "jobs"
    for name in ("get", "post", "patch", "delete", "raw", "download", "wait_job", "as_tenant"):
        assert callable(getattr(DpsClient, name)), name
    assert issubclass(DemoError, RuntimeError)


def test_requests_carry_the_key_and_the_tenant_and_paths_default_to_the_api() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    c = _client(handler, tenant="retail-banking-apac")
    assert c.get("/reports/summary", days=14) == {"ok": True}
    assert c.get("/api/v1/jobs/stats") == {"ok": True}
    assert c.get("/readyz") == {"ok": True}
    paths = [r.url.path for r in seen]
    assert paths == ["/api/v1/reports/summary", "/api/v1/jobs/stats", "/readyz"]
    assert seen[0].url.params["days"] == "14"
    assert all(r.headers["X-API-KEY"] == KEY for r in seen)
    assert all(r.headers["X-DPS-Tenant"] == "retail-banking-apac" for r in seen)
    other = c.as_tenant("default")
    assert isinstance(other, type(c)) and other is not c
    other.get("/jobs")
    assert seen[-1].headers["X-DPS-Tenant"] == "default" and seen[-1].headers["X-API-KEY"] == KEY
    plain = _client(handler)
    plain.get("/jobs")
    assert "X-DPS-Tenant" not in seen[-1].headers


def test_post_patch_delete_and_download() -> None:
    calls: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/pdf"):
            return httpx.Response(
                200, content=b"%PDF-1.4 x", headers={"content-type": "application/pdf"}
            )
        if request.url.path.endswith("/run"):
            return httpx.Response(202, json={"job": {"id": JOB}})
        return httpx.Response(200, json={"echo": body})

    c = _client(handler)
    assert c.post("/jobs/run", {"mode": "obfuscate"}) == {"job": {"id": JOB}}
    assert c.post("/jobs/run", {"mode": "obfuscate"}, expect=202)["job"]["id"] == JOB
    assert c.patch("/connections/c1", {"tags": ["x"]}) == {"echo": {"tags": ["x"]}}
    assert c.delete("/connections/c1") is None
    assert c.download("/evidence/ev_1/pdf").startswith(b"%PDF")
    assert c.post("/policies/validate") == {"echo": None}  # no body at all
    assert [m for m, _, _ in calls] == ["POST", "POST", "PATCH", "DELETE", "GET", "POST"]
    raw = c.raw("GET", "/jobs/x")
    assert isinstance(raw, httpx.Response) and raw.status_code == 200


def test_an_unexpected_status_names_the_route_template_and_never_the_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": f"value {SECRET_VALUE} refused", "code": "selection_invalid"},
        )

    c = _client(handler)
    with pytest.raises(DemoError) as err:
        c.post(f"/jobs/{JOB}/retry", {"consistency": {"key_material": {"dek": SECRET_VALUE}}})
    e = err.value
    assert (e.status, e.code) == (422, "selection_invalid")
    message = str(e)
    assert message == "POST /api/v1/jobs/{id}/retry -> HTTP 422 (selection_invalid)"
    assert SECRET_VALUE not in message and JOB not in message and KEY not in message
    with pytest.raises(DemoError) as err2:
        c.post("/jobs/run", {"x": 1}, expect=201)
    assert err2.value.status == 422


def test_a_code_that_is_not_an_error_code_is_dropped() -> None:
    """``code`` comes from the response; only a value-free identifier is kept."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"code": f"bad {SECRET_VALUE}"})

    with pytest.raises(DemoError) as err:
        _client(handler).get("/jobs")
    assert err.value.code is None and SECRET_VALUE not in str(err.value)


def test_transport_errors_become_demo_errors_without_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(DemoError) as err:
        _client(handler).get(f"/jobs/{JOB}", q=SECRET_VALUE)
    assert err.value.status == 0 and err.value.code == "unreachable"
    assert SECRET_VALUE not in str(err.value) and JOB not in str(err.value)


@pytest.mark.parametrize(
    ("path", "template"),
    [
        (f"/api/v1/jobs/{JOB}/partitions", "/api/v1/jobs/{id}/partitions"),
        ("/api/v1/scans/scan_01HZYX4V9R7Q2W3E4T5Y6U7I8O/pages/12", "/api/v1/scans/{id}/pages/{n}"),
        (
            "/api/v1/reports/api-rollups/2026-10-14?action=detect",
            "/api/v1/reports/api-rollups/{day}",
        ),
        ("/api/v1/artefacts/3f2c1d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f", "/api/v1/artefacts/{id}"),
        ("http://dps.test:8400/api/v1/jobs/stats", "/api/v1/jobs/stats"),
        ("/api/v1/policies/retail-kyc/versions/2", "/api/v1/policies/retail-kyc/versions/{n}"),
    ],
)
def test_route_templates_replace_ids_numbers_and_days(path: str, template: str) -> None:
    assert route_template(path) == template


def test_wait_job_polls_every_two_seconds_until_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    states = iter(["queued", "running", "running", "succeeded"])
    sleeps: list[float] = []
    monkeypatch.setattr(demo_client.time, "sleep", sleeps.append)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/jobs/{JOB}"
        return httpx.Response(200, json={"id": JOB, "state": next(states)})

    job = _client(handler).wait_job(JOB)
    assert job["state"] == "succeeded" and sleeps == [2.0, 2.0, 2.0]


def test_wait_job_reads_a_scan_and_keeps_its_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_client.time, "sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/scans/scan_1"
        return httpx.Response(
            200,
            json={"scan": {"id": "scan_1", "state": "succeeded"}, "summary": {"pages_visited": 3}},
        )

    scan = _client(handler).wait_job("scan_1", kind="scans")
    assert scan["state"] == "succeeded" and scan["summary"] == {"pages_visited": 3}


def test_wait_job_gives_up_after_its_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(range(0, 10_000, 2))
    monkeypatch.setattr(demo_client.time, "monotonic", lambda: float(next(clock)))
    monkeypatch.setattr(demo_client.time, "sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": JOB, "state": "running"})

    with pytest.raises(DemoError) as err:
        _client(handler).wait_job(JOB, timeout=10)
    assert err.value.code == "wait_timeout" and JOB not in str(err.value)
    with pytest.raises(ValueError):
        _client(handler).wait_job(JOB, kind="snapshots")


def test_the_client_never_shows_its_key() -> None:
    c = _client(lambda r: httpx.Response(200, json={}))
    assert KEY not in repr(c) and KEY not in str(c)


def _write(path: Path, content: str, mode: int) -> Path:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_load_keys_reads_a_private_file(tmp_path: Path) -> None:
    keys = {"demo-console": KEY, "crm-uat-provisioner": "dps_" + "p" * 32}
    path = _write(tmp_path / "demo-keys.json", json.dumps(keys), 0o600)
    assert load_keys(path) == keys
    nested = _write(tmp_path / "nested.json", json.dumps({"keys": keys, "tenant": "t"}), 0o600)
    assert load_keys(nested) == keys


@pytest.mark.parametrize("mode", [0o644, 0o604, 0o666, 0o602])
def test_load_keys_refuses_a_file_other_users_can_read_or_write(tmp_path: Path, mode: int) -> None:
    path = _write(tmp_path / "demo-keys.json", json.dumps({"demo-console": KEY}), mode)
    with pytest.raises(PermissionError) as err:
        load_keys(path)
    assert KEY not in str(err.value) and "chmod 600" in str(err.value)
    assert stat.S_IMODE(path.stat().st_mode) == mode  # never "fixed" behind the caller's back


@pytest.mark.parametrize(
    "content", ["[1, 2]", '{"demo-console": 5}', '{"keys": ["x"]}', "not json", '{"": "dps_x"}']
)
def test_load_keys_refuses_other_shapes_without_quoting_them(tmp_path: Path, content: str) -> None:
    path = _write(tmp_path / "demo-keys.json", content.replace("dps_x", KEY), 0o600)
    with pytest.raises(ValueError) as err:
        load_keys(path)
    assert KEY not in str(err.value)
