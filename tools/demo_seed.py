"""Seed the ten-minute demo on a running DPS stack (M5 Task 26). Idempotent.

    python tools/demo_seed.py [--base-url http://127.0.0.1:8400] [--admin-key KEY]
        [--tenant retail-banking-apac] [--rows 20000] [--skip-sources] [--skip-oracle]
        [--reset-jobs] [--out-dir var/demo]

1. Waits for ``/readyz`` (``--ready-seconds``, default 300: Oracle Free needs ~3 min at first start).
2. Tenant ``retail-banking-apac`` (created, else patched): "Retail Banking Technology", daily row
   quota 50 000 000, retention 30 days, registered hosts ``demo-site``, ``localhost``, ``127.0.0.1``.
3. Keys through ``/admin/keys`` for that tenant: ``demo-console`` (every scope), ``crm-uat-provisioner``
   (detect, obfuscate, jobs), ``qa-onboarding-scanner`` (scan, synthesize, obfuscate). Keys in
   ``var/demo/demo-keys.json`` that still authenticate are reused; a key that does not is replaced
   and the other active keys of its name are disabled (nobody holds them any more). The raw keys go
   to that file only (mode 0600, git-ignored); stdout names keys and their stored prefixes.
4. Source data: ``tools/seed_sources.py --target postgres|oracle|mongo --rows N --seed 7`` (the
   shared demo customer space; idempotent per its marker). ``--rows`` defaults to 20 000, the rows the
   compose ``seed-sources`` one-shot loads, because the seed refuses other parameters on a seeded
   schema.
5. Connections by name (created when missing) with the compose ``demo-secrets`` refs, which that
   one-shot writes for the demo tenant too (refs are tenant-scoped, SEC-2): ``bank-pg`` and
   ``bank-uat`` (PostgreSQL ``bank`` read, ``bank_uat`` written), ``crm-oracle`` and ``crm-masked``
   (Oracle ``CRM`` read as ``DPS_RO``, ``CRM_MASKED`` written), ``profiles-mongo`` and
   ``profiles-uat`` (MongoDB ``crm`` read, ``crm_uat`` written), ``exports-minio`` (S3 target).
6. Policies: ``gdpr-pseudonymise`` adopted; ``retail-kyc`` created from it, a draft adding the
   ``employee-id`` recognizer and the declared field ``LOANS.APPLICANTS.EMPLOYER_NAME`` → ``ORG_NAME``,
   published as v2 under change ticket ``CHG-48213`` (once).
7. Job group ``crm-uat-refresh``: its key material and synthesis seed, returned once at create, are
   written to ``var/demo/group-crm-uat-refresh.json`` (0600) — the only place the demo keeps them,
   because ``tools/e2e_demo.py`` runs the three-way join under them. A group whose file is lost cannot
   be used again (DPS keeps no key), so a new group ``crm-uat-refresh-<n>`` is made.
8. A value-free summary (names, ids, counts).

``--reset-jobs`` cancels the tenant's unfinished jobs, sets its retention to 1 day, runs retention
(``POST /admin/retention/run``) and waits for it, then sets retention back to 30 days. Retention
removes jobs older than a day (its floor); for a clean slate use ``docker compose --profile full
down -v``.

Never prints a key, key material, a seed or a customer value; an API failure prints the route
template, the status and the error code only (``tools/demo_client.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `python tools/demo_seed.py`

from tools.demo_client import DemoError, DpsClient, load_keys  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TENANT = "retail-banking-apac"
DEFAULT_OUT = ROOT / "var" / "demo"
KEYS_FILE = "demo-keys.json"
GROUP_NAME = "crm-uat-refresh"
BOOTSTRAP_KEY = "dps_local_dev_key_change_me"  # the compose stack's local demo literal
KEY_DAYS = 90
SEED = 7
DEFAULT_ROWS = 20_000

TENANT = {
    "display_name": "Retail Banking Technology",
    "daily_row_quota": 50_000_000,
    "retention_days": 30,
    "registered_hosts": ["demo-site", "localhost", "127.0.0.1"],
}
#: Key name → scopes, every key granted the demo tenant only.
KEYS: dict[str, list[str]] = {
    "demo-console": ["*"],
    "crm-uat-provisioner": ["detect", "obfuscate", "jobs"],
    "qa-onboarding-scanner": ["scan", "synthesize", "obfuscate"],
}
#: The stored session the scanner logs into the demo site with (written by demo-secrets).
SCAN_SESSION_REF = "dps/ui/demo-onboarding"
_PG = {"host": "source-pg", "port": 5432, "database": "bank"}
_ORACLE = {"host": "oracle", "port": 1521, "service_name": "FREEPDB1"}
#: The demo connections (in-network hosts of the compose stack; demo-secrets refs).
CONNECTIONS: dict[str, dict[str, Any]] = {
    "bank-pg": {
        "engine": "postgres",
        "params": _PG,
        "secret_ref": "dps/src/bank-pg-ro",
        "roles": ["source"],
        "tags": ["production"],
    },
    "bank-uat": {
        "engine": "postgres",
        "params": _PG,
        "secret_ref": "dps/tgt/bank-uat",
        "roles": ["target"],
    },
    "crm-oracle": {
        "engine": "oracle",
        "params": _ORACLE,
        "secret_ref": "dps/src/oracle-crm-ro",
        "roles": ["source"],
        "tags": ["production"],
    },
    "crm-masked": {
        "engine": "oracle",
        "params": _ORACLE,
        "secret_ref": "dps/tgt/oracle-crm-masked",
        "roles": ["target"],
    },
    "profiles-mongo": {
        "engine": "mongo",
        "params": {"database": "crm"},
        "secret_ref": "dps/src/mongo-crm",
        "roles": ["source"],
        "tags": ["production"],
    },
    "profiles-uat": {
        "engine": "mongo",
        "params": {"database": "crm_uat"},
        "secret_ref": "dps/tgt/mongo-crm-uat",
        "roles": ["target"],
    },
    "exports-minio": {
        "engine": "s3",
        "params": {"bucket": "dps-exports", "endpoint_url": "http://minio:9000"},
        "secret_ref": "dps/s3/exports",
        "roles": ["target"],
    },
}
BASE_POLICY = "gdpr-pseudonymise"
POLICY = "retail-kyc"
POLICY_TICKET = "CHG-48213"
POLICY_NOTE = "employee ids and the applicant's employer name"
#: v2 of ``retail-kyc``: the preset plus the employee-id recognizer and one declared field. The
#: allow list holds the seeded fixture id the recognizer must never report (the console's
#: recognizer builder fixture; configuration, not customer data).
POLICY_DRAFT: dict[str, Any] = {
    "preset": BASE_POLICY,
    "detect": {
        "custom_recognizers": [
            {
                "name": "employee-id",
                "supported_entity": "EMPLOYEE_ID",
                "patterns": [{"name": "employee-id", "regex": r"\bEMP-\d{6}\b", "score": 0.9}],
                "context": ["employee", "staff id"],
                "allow_list": ["EMP-000000"],
            }
        ],
        "declared_fields": [{"path": "LOANS.APPLICANTS.EMPLOYER_NAME", "entity_type": "ORG_NAME"}],
    },
}
SOURCE_TARGETS = ("postgres", "oracle", "mongo")
RETENTION_WAIT_SECONDS = 300.0


class SeedError(RuntimeError):
    """A seed step that cannot complete (value-free message)."""


def base_url(env: Mapping[str, str]) -> str:
    """``DPS_BASE_URL`` / ``DPS_TEST_APP_URL``, else ``DPS_APP_PORT`` (contract §12.2)."""
    return (
        env.get("DPS_BASE_URL")
        or env.get("DPS_TEST_APP_URL")
        or f"http://127.0.0.1:{env.get('DPS_APP_PORT') or '8400'}"
    ).rstrip("/")


def write_private(path: Path, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` with mode 0600 from the first byte (atomic rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_private(path: Path) -> dict[str, Any] | None:
    """A 0600 JSON object written by :func:`write_private`, ``None`` when absent or unreadable."""
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _items(c: DpsClient, path: str, **params: Any) -> list[dict[str, Any]]:
    """Every item of a paged list route."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = {"limit": 200, **params, **({"cursor": cursor} if cursor else {})}
        body = c.get(path, **query)
        out.extend(i for i in body.get("items", []) if isinstance(i, dict))
        cursor = body.get("next_cursor")
        if not cursor:
            return out


class Seeder:
    """The seed steps over one admin client (``X-DPS-Tenant`` = the demo tenant)."""

    def __init__(
        self,
        admin: DpsClient,
        args: argparse.Namespace,
        *,
        client_cls: type[DpsClient],
        runner: Callable[..., subprocess.CompletedProcess[str]],
        sleep: Callable[[float], None],
    ) -> None:
        self.c = admin
        self.args = args
        self.client_cls = client_cls
        self.runner = runner
        self.sleep = sleep
        self.out = Path(args.out_dir)
        self.summary: list[str] = []

    def note(self, line: str) -> None:
        self.summary.append(line)
        print(line, flush=True)

    # -- 1. readiness -----------------------------------------------------------------------------
    def wait_ready(self) -> None:
        deadline = time.monotonic() + float(self.args.ready_seconds)
        while True:
            try:
                ready = self.c.get("/readyz")
                if isinstance(ready, dict) and ready.get("ready") is True:
                    self.note("readyz: ready")
                    return
            except DemoError:
                pass
            if time.monotonic() >= deadline:
                raise SeedError(f"{self.c.base_url}/readyz did not report ready in time")
            self.sleep(5.0)

    # -- 2. tenant -------------------------------------------------------------------------------
    def tenant(self) -> None:
        tenant = self.args.tenant
        known = {t.get("tenant_id") for t in _items(self.c, "/admin/tenants")}
        if tenant in known:
            self.c.patch(f"/admin/tenants/{tenant}", TENANT)
            self.note(f"tenant {tenant}: patched")
            return
        response = self.c.raw("POST", "/admin/tenants", json={"tenant_id": tenant, **TENANT})
        if response.status_code == 409:
            self.c.patch(f"/admin/tenants/{tenant}", TENANT)
            self.note(f"tenant {tenant}: patched")
        elif response.status_code == 201:
            self.note(f"tenant {tenant}: created")
        else:
            raise DemoError("POST", "/api/v1/admin/tenants", response.status_code)

    # -- 3. keys ----------------------------------------------------------------------------------
    def _authenticates(self, raw: str) -> bool:
        probe = self.client_cls(self.c.base_url, raw, tenant=self.args.tenant)
        try:
            status = probe.raw("GET", "/policies").status_code
        finally:
            probe.close()
        if status in (200, 403):
            return True
        if status == 401:
            return False
        raise SeedError(f"checking a demo key: GET /api/v1/policies -> HTTP {status}")

    def keys(self) -> dict[str, str]:
        path = self.out / KEYS_FILE
        held: dict[str, str] = {}
        if path.is_file():
            try:
                held = load_keys(path)
            except (PermissionError, ValueError) as exc:
                raise SeedError(str(exc)) from None
        listed = _items(self.c, "/admin/keys")
        keys: dict[str, str] = {}
        for name, scopes in KEYS.items():
            raw = held.get(name)
            if raw and self._authenticates(raw):
                keys[name] = raw
                prefix = next(
                    (k.get("key_prefix") for k in listed if k.get("name") == name), "dps_…"
                )
                self.note(f"key {name} ({prefix}): reused")
                continue
            made = self.c.post(
                "/admin/keys",
                {
                    "name": name,
                    "tenant_ids": [self.args.tenant],
                    "scopes": scopes,
                    "expires_in_days": KEY_DAYS,
                },
            )
            keys[name] = str(made["key"])
            record = made.get("record") or {}
            new_id = record.get("id")
            retired = 0
            for old in listed:
                if (
                    old.get("name") == name
                    and old.get("status") == "active"
                    and old.get("source", "db") == "db"
                    and old.get("id") != new_id
                    and self.args.tenant in (old.get("tenant_ids") or [self.args.tenant])
                ):
                    self.c.post(f"/admin/keys/{old['id']}/disable")
                    retired += 1
            extra = f", {retired} older key(s) of that name disabled" if retired else ""
            self.note(f"key {name} ({record.get('key_prefix', 'dps_…')}): created{extra}")
            del made
        write_private(path, keys)
        self.note(f"keys file: {path} (mode 600, {len(keys)} keys)")
        return keys

    # -- 4. sources -------------------------------------------------------------------------------
    def sources(self) -> None:
        if self.args.skip_sources:
            self.note("sources: skipped (--skip-sources)")
            return
        targets = [t for t in SOURCE_TARGETS if not (self.args.skip_oracle and t == "oracle")]
        for target in targets:
            argv = [
                sys.executable,
                str(ROOT / "tools" / "seed_sources.py"),
                "--target",
                target,
                "--rows",
                str(self.args.rows),
                "--seed",
                str(SEED),
            ]
            done = self.runner(argv, capture_output=True, text=True, check=False, timeout=1800)
            if done.returncode != 0:
                raise SeedError(
                    f"tools/seed_sources.py --target {target} exited {done.returncode} "
                    "(another --rows or --seed on a seeded schema exits 3: see its docstring)"
                )
            lines = [ln for ln in (done.stdout or "").splitlines() if ":" in ln]
            unchanged = bool(lines) and all(
                "unchanged" in ln or "already seeded" in ln for ln in lines
            )
            state = "already seeded" if unchanged else "seeded"
            self.note(f"source {target}: {state} ({self.args.rows} customers, seed {SEED})")

    # -- 5. connections ---------------------------------------------------------------------------
    def connections(self) -> dict[str, str]:
        found = {str(i["name"]): str(i["id"]) for i in _items(self.c, "/connections")}
        created = 0
        for name, spec in CONNECTIONS.items():
            if self.args.skip_oracle and spec["engine"] == "oracle":
                continue
            if name not in found:
                found[name] = str(self.c.post("/connections", {"name": name, **spec})["id"])
                created += 1
        wanted = [
            n
            for n, s in CONNECTIONS.items()
            if not (self.args.skip_oracle and s["engine"] == "oracle")
        ]
        self.note(f"connections: {created} created, {len(wanted) - created} reused")
        return {n: found[n] for n in wanted}

    # -- 6. policies ------------------------------------------------------------------------------
    def _adopt(self, name: str, preset: str) -> None:
        response = self.c.raw("POST", "/policies", json={"name": name, "base_preset": preset})
        if response.status_code not in (201, 409):
            raise DemoError("POST", "/api/v1/policies", response.status_code)

    def policies(self) -> None:
        self._adopt(BASE_POLICY, BASE_POLICY)
        self._adopt(POLICY, BASE_POLICY)
        detail = self.c.get(f"/policies/{POLICY}")
        if int(detail.get("active_version") or 0) >= 2:
            self.note(f"policy {POLICY}: v{detail.get('active_version')} (reused)")
            return
        drafted = self.c.raw("PUT", f"/policies/{POLICY}/draft", json={"body": POLICY_DRAFT})
        if drafted.status_code != 200:
            raise DemoError("PUT", f"/api/v1/policies/{POLICY}/draft", drafted.status_code)
        published = self.c.post(
            f"/policies/{POLICY}/publish", {"change_ticket": POLICY_TICKET, "note": POLICY_NOTE}
        )
        self.note(f"policy {POLICY}: v{published.get('version')} published ({POLICY_TICKET})")

    # -- 7. job group -----------------------------------------------------------------------------
    def group(self) -> dict[str, Any]:
        path = self.out / f"group-{GROUP_NAME}.json"
        held = read_private(path)
        if held and held.get("group_id"):
            check = self.c.raw("GET", f"/job-groups/{held['group_id']}")
            if check.status_code == 200:
                self.note(f"job group {held.get('name')}: reused ({held['group_id']})")
                return held
        taken = {str(g.get("name")) for g in _items(self.c, "/job-groups")}
        name = GROUP_NAME
        n = 2
        while name in taken:
            name = f"{GROUP_NAME}-{n}"
            n += 1
        made = self.c.post("/job-groups", {"name": name})
        group = made.get("group") or {}
        doc = {
            "group_id": group.get("id"),
            "name": name,
            "tenant": self.args.tenant,
            "key_material": made.get("key_material"),
            "synthesis_seed": made.get("synthesis_seed"),
        }
        del made
        write_private(path, doc)
        self.note(f"job group {name}: created ({group.get('id')}); key file {path} (mode 600)")
        return doc

    # -- --reset-jobs -----------------------------------------------------------------------------
    def _retention_state(self, job_id: str) -> str:
        """``succeeded`` once the job's ``retention.run`` audit row exists: the job belongs to the
        system tenant, which no tenant's ``/jobs`` route reads."""
        deadline = time.monotonic() + RETENTION_WAIT_SECONDS
        while True:
            rows = self.c.get("/admin/audit", tenants="*", action="retention.run", limit=50)
            mine = [r for r in rows.get("items") or [] if r.get("job_id") == job_id]
            if mine:
                return "succeeded" if mine[0].get("outcome") in ("ok", "dry_run") else "failed"
            if time.monotonic() >= deadline:
                return "timeout"
            self.sleep(2.0)

    def reset_jobs(self) -> None:
        canceled = 0
        for state in ("queued", "waiting", "running", "paused"):
            for job in _items(self.c, "/jobs", state=state):
                if job.get("kind") in ("run", "discover", "scan") and job.get("id"):
                    response = self.c.raw("POST", f"/jobs/{job['id']}/cancel")
                    canceled += int(response.status_code == 200)
        self.note(f"reset: {canceled} unfinished demo jobs canceled")
        tenant = self.args.tenant
        self.c.patch(f"/admin/tenants/{tenant}", {"retention_days": 1})
        try:
            job = self.c.post("/admin/retention/run", {"dry_run": False})["job"]
            state = self._retention_state(str(job["id"]))
            self.note(f"reset: retention {state} ({job['id']})")
            if state != "succeeded":
                raise SeedError(f"the retention job {job['id']} did not finish: {state}")
        finally:
            self.c.patch(f"/admin/tenants/{tenant}", {"retention_days": TENANT["retention_days"]})
            self.note(f"reset: tenant retention back to {TENANT['retention_days']} days")


def parse_args(argv: Sequence[str] | None, env: Mapping[str, str]) -> argparse.Namespace:
    """Command line (module docstring)."""
    p = argparse.ArgumentParser(
        prog="python tools/demo_seed.py", description=__doc__.split("\n")[0]
    )
    p.add_argument("--base-url", default=base_url(env))
    p.add_argument(
        "--admin-key",
        default=env.get("DPS_API_KEY") or env.get("DPS_BOOTSTRAP_API_KEY") or BOOTSTRAP_KEY,
        help="a key granted every tenant with the admin scope (default DPS_API_KEY, else the "
        "local bootstrap literal)",
    )
    p.add_argument("--tenant", default=DEFAULT_TENANT)
    p.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    p.add_argument("--skip-sources", action="store_true")
    p.add_argument("--skip-oracle", action="store_true")
    p.add_argument("--reset-jobs", action="store_true")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT))
    p.add_argument("--ready-seconds", type=float, default=300.0)
    return p.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    client_cls: type[DpsClient] = DpsClient,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the seed (or ``--reset-jobs``); 0 on success, 1 on a failed step (value-free)."""
    args = parse_args(argv, os.environ)
    started = time.monotonic()
    admin = client_cls(args.base_url, args.admin_key, tenant=args.tenant, timeout=120.0)
    seeder = Seeder(admin, args, client_cls=client_cls, runner=runner, sleep=sleep)
    try:
        seeder.wait_ready()
        if args.reset_jobs:
            seeder.reset_jobs()
        else:
            seeder.tenant()
            seeder.keys()
            seeder.sources()
            seeder.connections()
            seeder.policies()
            seeder.group()
    except (DemoError, SeedError) as exc:
        print(f"FAIL  {exc}", flush=True)
        return 1
    finally:
        admin.close()
    print(f"demo seed done in {time.monotonic() - started:.1f}s (tenant {args.tenant})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
