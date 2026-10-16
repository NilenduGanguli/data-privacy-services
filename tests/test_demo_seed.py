"""``tools/demo_seed.py`` — the ten-minute demo's idempotent seed (M5 Task 26).

Offline: an in-memory stand-in for the admin, connection, policy and job-group routes answers
through ``httpx.MockTransport``; the source seeding subprocess is replaced by a recorder. The last
test runs the compose ``demo-secrets`` one-shot's script against a temporary directory and resolves
every demo connection's secret reference for the demo tenant (SEC-2: refs are tenant-scoped).
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from tools import demo_seed
from tools.demo_client import DpsClient

ADMIN = "dps_" + "a" * 32
TENANT = "retail-banking-apac"
ROOT = Path(__file__).resolve().parents[1]


class FakeDps:
    """Just enough of the API for the seed: state in dicts, every call recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.tenants: dict[str, dict[str, Any]] = {}
        self.keys: dict[str, dict[str, Any]] = {}  # id -> {name, key, tenant_ids, scopes, status}
        self.connections: dict[str, dict[str, Any]] = {}
        self.policies: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.retention_days: list[int] = []
        self.retention_jobs: list[str] = []
        self._ids = itertools.count(1)

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids):026d}"

    def key_ok(self, raw: str) -> bool:
        return raw == ADMIN or any(
            k["key"] == raw and k["status"] == "active" for k in self.keys.values()
        )

    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: C901, PLR0911, PLR0912
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        if path == "/readyz":
            return httpx.Response(200, json={"ready": True, "components": {}})
        if not self.key_ok(request.headers.get("X-API-KEY", "")):
            return httpx.Response(401, json={"detail": "x", "code": "unauthenticated"})
        body: Any = json.loads(request.content) if request.content else None
        p = path.removeprefix("/api/v1")
        if p == "/admin/tenants" and method == "GET":
            items = [
                {"tenant_id": t, "exists": True, "effective": dict(v)}
                for t, v in self.tenants.items()
            ]
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if p == "/admin/tenants" and method == "POST":
            if body["tenant_id"] in self.tenants:
                return httpx.Response(409, json={"detail": "x", "code": "tenant_exists"})
            self.tenants[body["tenant_id"]] = {k: v for k, v in body.items() if k != "tenant_id"}
            return httpx.Response(201, json={"tenant_id": body["tenant_id"]})
        if p.startswith("/admin/tenants/") and method == "PATCH":
            tenant = p.rsplit("/", 1)[1]
            self.tenants[tenant].update(body)
            if "retention_days" in body:
                self.retention_days.append(body["retention_days"])
            return httpx.Response(200, json={"tenant_id": tenant})
        if p == "/admin/keys" and method == "GET":
            items = [
                {"id": i, "name": k["name"], "key_prefix": k["key"][:8], "status": k["status"],
                 "tenant_ids": k["tenant_ids"], "scopes": k["scopes"], "source": "db"}
                for i, k in self.keys.items()
            ]  # fmt: skip
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if p == "/admin/keys" and method == "POST":
            kid = self._id("key")
            raw = "dps_" + f"{next(self._ids):032d}"
            self.keys[kid] = {**body, "key": raw, "status": "active"}
            rec = {"id": kid, "name": body["name"], "key_prefix": raw[:8], "status": "active"}
            return httpx.Response(201, json={"key": raw, "record": rec})
        if m := re.fullmatch(r"/admin/keys/([^/]+)/disable", p):
            self.keys[m.group(1)]["status"] = "disabled"
            return httpx.Response(200, json={"id": m.group(1), "status": "disabled"})
        if p == "/policies" and method == "GET":
            return httpx.Response(200, json={"presets": [], "tenant": list(self.policies.values())})
        if p == "/connections" and method == "GET":
            items = [{"id": i, "name": c["name"]} for i, c in self.connections.items()]
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if p == "/connections" and method == "POST":
            cid = self._id("conn")
            self.connections[cid] = body
            return httpx.Response(201, json={"id": cid, "name": body["name"]})
        if p == "/policies" and method == "POST":
            if body["name"] in self.policies:
                return httpx.Response(409, json={"detail": "x", "code": "policy_exists"})
            self.policies[body["name"]] = {
                "name": body["name"], "base_preset": body["base_preset"], "active_version": 1,
                "draft": None, "versions": [{"version": 1, "change_ticket": None}],
            }  # fmt: skip
            return httpx.Response(201, json=self.policies[body["name"]])
        if m := re.fullmatch(r"/policies/([^/]+)", p):
            if m.group(1) not in self.policies:
                return httpx.Response(404, json={"detail": "x", "code": "policy_not_found"})
            return httpx.Response(200, json=self.policies[m.group(1)])
        if m := re.fullmatch(r"/policies/([^/]+)/draft", p):
            self.policies[m.group(1)]["draft"] = body["body"]
            return httpx.Response(200, json=self.policies[m.group(1)])
        if m := re.fullmatch(r"/policies/([^/]+)/publish", p):
            pol = self.policies[m.group(1)]
            pol["active_version"] += 1
            pol["versions"].append(
                {"version": pol["active_version"], "change_ticket": body["change_ticket"]}
            )
            pol["draft"] = None
            return httpx.Response(201, json={"version": pol["active_version"]})
        if p == "/job-groups" and method == "GET":
            items = [{"id": i, "name": g["name"]} for i, g in self.groups.items()]
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if p == "/job-groups" and method == "POST":
            if any(g["name"] == body["name"] for g in self.groups.values()):
                return httpx.Response(409, json={"detail": "x", "code": "group_name_taken"})
            gid = self._id("grp")
            self.groups[gid] = {"name": body["name"]}
            return httpx.Response(
                201,
                json={
                    "group": {"id": gid, "name": body["name"]},
                    "key_material": {"kid": "k1", "dek": "SECRET-DEK-" + gid},
                    "synthesis_seed": {"seed": "SECRET-SEED-" + gid, "fp": "f"},
                },
            )
        if m := re.fullmatch(r"/job-groups/([^/]+)", p):
            if m.group(1) not in self.groups:
                return httpx.Response(404, json={"detail": "x", "code": "group_not_found"})
            return httpx.Response(200, json={"group": {"id": m.group(1)}, "jobs": []})
        if p == "/jobs" and method == "GET":
            state = request.url.params.get("state")
            items = [j for j in self.jobs.values() if not state or j["state"] == state]
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if m := re.fullmatch(r"/jobs/([^/]+)/cancel", p):
            self.jobs[m.group(1)]["state"] = "canceled"
            return httpx.Response(200, json=self.jobs[m.group(1)])
        if p == "/admin/retention/run":
            jid = self._id("job")
            self.retention_jobs.append(jid)  # owned by the system tenant: not in /jobs
            return httpx.Response(202, json={"job": {"id": jid, "state": "queued"}})
        if p == "/admin/audit":
            rows = [
                {"job_id": j, "action": "retention.run", "outcome": "ok"}
                for j in self.retention_jobs
            ]
            return httpx.Response(200, json={"items": rows, "next_cursor": None})
        if m := re.fullmatch(r"/jobs/([^/]+)", p):
            return httpx.Response(200, json=self.jobs[m.group(1)])
        return httpx.Response(404, json={"detail": "x", "code": "not_found"})


class Recorder:
    """Stands in for ``subprocess.run`` of ``tools/seed_sources.py``."""

    def __init__(self, returncode: int = 0) -> None:
        self.argv: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.argv.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, "", "")


def _client_cls(fake: FakeDps) -> type[DpsClient]:
    class _Mocked(DpsClient):
        _transport = httpx.MockTransport(fake.handler)

    return _Mocked


def _run(fake: FakeDps, out: Path, *extra: str, runner: Callable[..., Any] | None = None) -> int:
    return demo_seed.main(
        ["--base-url", "http://dps.test", "--admin-key", ADMIN, "--out-dir", str(out), *extra],
        client_cls=_client_cls(fake),
        runner=runner or Recorder(),
        sleep=lambda _s: None,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_first_run_creates_everything_and_the_second_reuses_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeDps()
    runner = Recorder()
    assert _run(fake, tmp_path, runner=runner) == 0
    first = capsys.readouterr().out

    tenant = fake.tenants[TENANT]
    assert tenant["display_name"] == "Retail Banking Technology"
    assert tenant["daily_row_quota"] == 50_000_000
    assert tenant["retention_days"] == 30
    assert set(tenant["registered_hosts"]) == {"demo-site", "localhost", "127.0.0.1"}

    names = sorted(k["name"] for k in fake.keys.values())
    assert names == ["crm-uat-provisioner", "demo-console", "qa-onboarding-scanner"]
    scopes = {k["name"]: set(k["scopes"]) for k in fake.keys.values()}
    assert scopes["demo-console"] == {"*"}
    assert scopes["crm-uat-provisioner"] == {"detect", "obfuscate", "jobs"}
    assert scopes["qa-onboarding-scanner"] == {"scan", "synthesize", "obfuscate"}
    assert all(k["tenant_ids"] == [TENANT] for k in fake.keys.values())

    keys_file = tmp_path / "demo-keys.json"
    group_file = tmp_path / "group-crm-uat-refresh.json"
    assert _mode(keys_file) == 0o600 and _mode(group_file) == 0o600
    stored = json.loads(keys_file.read_text())
    assert set(stored) == {"demo-console", "crm-uat-provisioner", "qa-onboarding-scanner"}
    group = json.loads(group_file.read_text())
    assert group["name"] == "crm-uat-refresh" and group["key_material"]["dek"].startswith("SECRET")
    assert group["synthesis_seed"]["seed"].startswith("SECRET")

    conns = {c["name"]: c for c in fake.connections.values()}
    assert set(conns) == set(demo_seed.CONNECTIONS)
    assert {"bank-pg", "crm-oracle", "profiles-mongo", "exports-minio"} <= set(conns)
    assert all(not c["secret_ref"].startswith(("/", "default/")) for c in conns.values())

    retail = fake.policies["retail-kyc"]
    assert "gdpr-pseudonymise" in fake.policies
    assert retail["active_version"] == 2
    assert retail["versions"][-1]["change_ticket"] == "CHG-48213"

    # the three source engines were seeded with the shared demo model's seed
    targets = [a[a.index("--target") + 1] for a in runner.argv]
    assert targets == ["postgres", "oracle", "mongo"]
    assert all(a[a.index("--seed") + 1] == "7" for a in runner.argv)

    # nothing secret on stdout: no raw key, no key material, no seed
    for raw in [*stored.values(), group["key_material"]["dek"], group["synthesis_seed"]["seed"]]:
        assert raw not in first
    assert "demo-console" in first

    before = (len(fake.keys), len(fake.connections), len(fake.groups), retail["active_version"])
    assert _run(fake, tmp_path) == 0
    second = capsys.readouterr().out
    after = (len(fake.keys), len(fake.connections), len(fake.groups), retail["active_version"])
    assert after == before  # idempotent: keys reused, nothing created, v2 not republished
    assert json.loads(keys_file.read_text()) == stored
    assert json.loads(group_file.read_text()) == group
    assert "reused" in second


def test_a_key_that_no_longer_authenticates_is_replaced_and_its_name_retired(
    tmp_path: Path,
) -> None:
    fake = FakeDps()
    assert _run(fake, tmp_path) == 0
    keys_file = tmp_path / "demo-keys.json"
    stored = json.loads(keys_file.read_text())
    old_id = next(i for i, k in fake.keys.items() if k["name"] == "crm-uat-provisioner")
    fake.keys[old_id]["status"] = "expired"  # the stack no longer accepts it

    assert _run(fake, tmp_path) == 0
    again = json.loads(keys_file.read_text())
    assert again["demo-console"] == stored["demo-console"]  # still works: reused
    assert again["crm-uat-provisioner"] != stored["crm-uat-provisioner"]
    active = [k for k in fake.keys.values() if k["name"] == "crm-uat-provisioner"]
    assert [k["status"] for k in active].count("active") == 1


def test_lost_key_file_retires_the_old_keys_of_the_same_names(tmp_path: Path) -> None:
    fake = FakeDps()
    assert _run(fake, tmp_path) == 0
    (tmp_path / "demo-keys.json").unlink()
    assert _run(fake, tmp_path) == 0
    for name in demo_seed.KEYS:
        states = [k["status"] for k in fake.keys.values() if k["name"] == name]
        assert states.count("active") == 1, name


def test_a_group_whose_key_file_is_lost_gets_a_new_name(tmp_path: Path) -> None:
    fake = FakeDps()
    assert _run(fake, tmp_path) == 0
    (tmp_path / "group-crm-uat-refresh.json").unlink()
    assert _run(fake, tmp_path) == 0
    group = json.loads((tmp_path / "group-crm-uat-refresh.json").read_text())
    assert group["name"] != "crm-uat-refresh" and group["name"].startswith("crm-uat-refresh-")
    assert len(fake.groups) == 2


def test_skip_flags(tmp_path: Path) -> None:
    fake = FakeDps()
    runner = Recorder()
    assert _run(fake, tmp_path, "--skip-oracle", runner=runner) == 0
    assert all("oracle" not in a for a in runner.argv)
    assert not any(c["engine"] == "oracle" for c in fake.connections.values())

    fake2 = FakeDps()
    runner2 = Recorder()
    assert _run(fake2, tmp_path / "b", "--skip-sources", runner=runner2) == 0
    assert runner2.argv == []


def test_a_failed_source_seed_fails_the_run_and_names_the_remedy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # exit 3: the schema was seeded with other parameters (the test suite leaves 200 rows)
    assert _run(FakeDps(), tmp_path, runner=Recorder(returncode=3)) != 0
    assert "--reseed" in capsys.readouterr().out


def test_reseed_reloads_the_sources(tmp_path: Path) -> None:
    runner = Recorder()
    assert _run(FakeDps(), tmp_path, "--reseed", runner=runner) == 0
    assert len(runner.argv) == 3 and all("--reset" in a for a in runner.argv)
    plain = Recorder()
    assert _run(FakeDps(), tmp_path / "b", runner=plain) == 0
    assert not any("--reset" in a for a in plain.argv)


def test_reset_jobs_cancels_runs_retention_and_restores_thirty_days(tmp_path: Path) -> None:
    fake = FakeDps()
    assert _run(fake, tmp_path) == 0
    fake.jobs["job_waiting"] = {"id": "job_waiting", "kind": "run", "state": "waiting"}
    assert _run(fake, tmp_path, "--reset-jobs") == 0
    assert fake.jobs["job_waiting"]["state"] == "canceled"
    assert fake.retention_days[-2:] == [1, 30]
    assert ("POST", "/api/v1/admin/retention/run") in fake.calls
    assert fake.tenants[TENANT]["retention_days"] == 30


def test_an_unreachable_stack_fails_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Down(DpsClient):
        _transport = httpx.MockTransport(
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))
        )

    code = demo_seed.main(
        ["--base-url", "http://dps.test", "--admin-key", ADMIN, "--out-dir", str(tmp_path),
         "--ready-seconds", "0"],
        client_cls=Down, runner=Recorder(), sleep=lambda _s: None,
    )  # fmt: skip
    assert code != 0
    assert ADMIN not in capsys.readouterr().out


def _demo_secrets_script(root: Path) -> str:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
    (command,) = compose["services"]["demo-secrets"]["command"]
    return (
        command.replace("$$", "$")
        .replace("/var/run/secrets/dps", str(root))
        .replace("/seed/ui", str(ROOT / "docker/secrets/default/dps/ui"))
    )


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_demo_connection_refs_resolve_for_the_demo_tenant(tmp_path: Path) -> None:
    """The compose demo-secrets one-shot writes every demo connection's ref (and the scanner's
    stored session) under the demo tenant's folder too: refs are tenant-scoped (SEC-2)."""
    from dps.config import Settings
    from dps.secrets import resolve_secret_ref

    subprocess.run(
        ["sh", "-c", _demo_secrets_script(tmp_path)], check=True, capture_output=True, timeout=30
    )
    settings = Settings(_env_file=None, secret_dir=str(tmp_path))  # type: ignore[call-arg]
    for name, spec in demo_seed.CONNECTIONS.items():
        got = resolve_secret_ref(TENANT, spec["secret_ref"], settings)
        assert got, name
    session = resolve_secret_ref(TENANT, demo_seed.SCAN_SESSION_REF, settings)
    assert "storage_state.json" in session
    # the default tenant (the smoke and the bootstrap key) keeps its own copy
    assert resolve_secret_ref("default", "dps/src/bank-pg-ro", settings)
    assert os.path.isdir(tmp_path / TENANT / "dps" / "ui" / "demo-onboarding")
