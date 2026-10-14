"""Value-free flow report: every route group of contract §9 once, against a running stack (M5 Task 24).

    docker compose --profile full up --build -d       # poll /readyz until "ready": true
    python tools/flow_report.py                       # → reports/flow-report.md, exit 0 or 1
    python tools/flow_report.py --demo-keys           # as the demo-console key of var/demo/demo-keys.json

The flow calls each route of :data:`EXPECT` in a dependency order and reuses what it creates: two
connections (the demo source PostgreSQL, read with the ``dps_reader`` login, and its UAT schema
written as ``dps_writer`` — the compose stack's local demo literals, contract §11), a discover job
and its snapshot, a job group with two ``table`` runs of 500 rows under its key (two targets, so the
group's consistency check has a pair) and the first run's evidence, PDF and artefact, a tenant policy with a published version, a tenant, an API key (rotated, disabled,
re-enabled and disabled again) and, when ``/readyz`` reports the browser worker (M4), a scan of the
demo site. It ends by deleting its two connections. Names carry a UTC timestamp (``flow-…``), so
reruns never collide; tenant policies and tenant records cannot be deleted and stay behind.

For each call the report lists the method, the **route template**, the expected and the actual
status, the duration and the response's top-level key names — never a value, an id, a key, a
token or a body. The run exits 1 when any call's status differs from :data:`EXPECT`, or a call
could not run because an earlier one failed; scanner calls without a browser worker are reported
as skipped and do not fail the run.

Keys: ``DPS_API_KEY`` (else ``DPS_BOOTSTRAP_API_KEY``, else the compose stack's local bootstrap
literal) is taken to be an operator key — every tenant, every scope. ``--demo-keys [PATH]`` runs as
``--key-name`` (default ``demo-console``) from the demo seed's key file instead; ``--tenant-key``
declares any key tenant-scoped. A tenant-scoped key cannot start the cross-tenant retention job or
create another tenant, so those two calls expect the documented 403 (:data:`TENANT_KEY_EXPECT`).
``--tenant`` sends ``X-DPS-Tenant``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `python tools/flow_report.py`

from tools.demo_client import DemoError, DpsClient, load_keys  # noqa: E402

#: (method, route template, expected status) — one row per route of contract §9 as built, in the
#: order the flow calls them. Controls on a finished job are refused (409) by design.
EXPECT: list[tuple[str, str, int]] = [
    # ops (M0)
    ("GET", "/health", 200),
    ("GET", "/readyz", 200),
    ("GET", "/metrics", 200),
    # Layer 1 (M0)
    ("POST", "/api/v1/detect", 200),
    ("POST", "/api/v1/obfuscate", 200),
    ("POST", "/api/v1/reveal", 200),
    ("POST", "/api/v1/synthesize", 200),
    ("GET", "/api/v1/policies/presets", 200),
    ("POST", "/api/v1/policies/validate", 200),
    ("POST", "/api/v1/policies/plan", 200),
    # documents, schema synthesis, taxonomy (M1)
    ("POST", "/api/v1/documents/detect", 200),
    ("POST", "/api/v1/documents/obfuscate", 200),
    ("POST", "/api/v1/documents/synthesize", 200),
    ("POST", "/api/v1/schema/generator-spec", 200),
    ("POST", "/api/v1/schema/synthesize", 200),
    ("GET", "/api/v1/entities", 200),
    ("POST", "/api/v1/policies/test-recognizer", 200),
    # connections (M2)
    ("POST", "/api/v1/connections", 201),
    ("GET", "/api/v1/connections", 200),
    ("GET", "/api/v1/connections/{connection_id}", 200),
    ("PATCH", "/api/v1/connections/{connection_id}", 200),
    ("POST", "/api/v1/connections/{connection_id}/test", 200),
    ("GET", "/api/v1/connections/{connection_id}/objects", 200),
    ("GET", "/api/v1/connections/{connection_id}/describe", 200),
    ("POST", "/api/v1/connections/{connection_id}/count", 200),
    # discovery, snapshots, job groups, runs and controls (M2; consistency check M3)
    ("POST", "/api/v1/jobs/discover", 202),
    ("GET", "/api/v1/jobs/{job_id}", 200),
    ("GET", "/api/v1/snapshots", 200),
    ("GET", "/api/v1/snapshots/{snapshot_id}", 200),
    ("PATCH", "/api/v1/snapshots/{snapshot_id}", 200),
    ("POST", "/api/v1/snapshots/{snapshot_id}/accept", 200),
    ("POST", "/api/v1/job-groups", 201),
    ("GET", "/api/v1/job-groups", 200),
    ("GET", "/api/v1/job-groups/{group_id}", 200),
    ("POST", "/api/v1/jobs/run/validate", 200),
    ("POST", "/api/v1/jobs/run", 202),
    ("GET", "/api/v1/jobs", 200),
    ("GET", "/api/v1/jobs/stats", 200),
    ("GET", "/api/v1/jobs/{job_id}/partitions", 200),
    ("POST", "/api/v1/jobs/{job_id}/pause", 409),
    ("POST", "/api/v1/jobs/{job_id}/resume", 409),
    ("POST", "/api/v1/jobs/{job_id}/cancel", 409),
    ("POST", "/api/v1/jobs/{job_id}/retry", 409),
    ("POST", "/api/v1/job-groups/{group_id}/consistency-check", 200),
    # evidence and artefacts (M2; PDF M5)
    ("GET", "/api/v1/evidence", 200),
    ("GET", "/api/v1/evidence/{evidence_id}", 200),
    ("GET", "/api/v1/evidence/{evidence_id}/html", 200),
    ("GET", "/api/v1/evidence/{evidence_id}/pdf", 200),
    ("GET", "/api/v1/artefacts/{artefact_id}", 200),
    # reports (M5)
    ("GET", "/api/v1/reports/summary", 200),
    ("GET", "/api/v1/reports/jobs", 200),
    ("GET", "/api/v1/reports/evidence", 200),
    ("GET", "/api/v1/reports/api-rollups", 200),
    ("GET", "/api/v1/reports/api-rollups/{day}", 200),
    # policy store (M5)
    ("GET", "/api/v1/policies", 200),
    ("POST", "/api/v1/policies", 201),
    ("GET", "/api/v1/policies/{name}", 200),
    ("PUT", "/api/v1/policies/{name}/draft", 200),
    ("GET", "/api/v1/policies/{name}/diff", 200),
    ("POST", "/api/v1/policies/{name}/publish", 201),
    ("GET", "/api/v1/policies/{name}/versions/{version}", 200),
    ("DELETE", "/api/v1/policies/{name}/draft", 200),
    # admin (M5)
    ("GET", "/api/v1/admin/tenants", 200),
    ("POST", "/api/v1/admin/tenants", 201),
    ("PATCH", "/api/v1/admin/tenants/{tenant_id}", 200),
    ("GET", "/api/v1/admin/keys", 200),
    ("POST", "/api/v1/admin/keys", 201),
    ("POST", "/api/v1/admin/keys/{key_id}/rotate", 201),
    ("POST", "/api/v1/admin/keys/{key_id}/disable", 200),
    ("POST", "/api/v1/admin/keys/{key_id}/enable", 200),
    ("GET", "/api/v1/admin/audit", 200),
    ("GET", "/api/v1/admin/queue", 200),
    ("GET", "/api/v1/admin/posture", 200),
    ("POST", "/api/v1/admin/retention/run", 202),
    # UI scanner (M4) — only when /readyz reports the browser worker; a finished scan cannot be
    # canceled (409 not_cancelable)
    ("POST", "/api/v1/scans", 202),
    ("POST", "/api/v1/scans/personas/preview", 200),
    ("GET", "/api/v1/scans", 200),
    ("GET", "/api/v1/scans/{scan_id}", 200),
    ("GET", "/api/v1/scans/{scan_id}/pages", 200),
    ("GET", "/api/v1/scans/{scan_id}/pages/{page_no}", 200),
    ("GET", "/api/v1/scans/{scan_id}/events", 200),
    ("GET", "/api/v1/scans/{scan_id}/inventory", 200),
    ("GET", "/api/v1/scans/{scan_id}/export", 200),
    ("POST", "/api/v1/scans/{scan_id}/cancel", 409),
    # clean-up (M2): the flow's own connections, once their jobs are terminal
    ("DELETE", "/api/v1/connections/{connection_id}", 204),
]
#: A tenant-scoped key (``--demo-keys``, ``--tenant-key``): the documented refusals.
TENANT_KEY_EXPECT: dict[tuple[str, str], int] = {
    ("POST", "/api/v1/admin/retention/run"): 403,  # sweeps every tenant: wildcard keys only
    ("POST", "/api/v1/admin/tenants"): 403,  # a new tenant is beyond the key's grants
}
#: Route groups of contract §9, by template prefix (first match wins), for the report's summary.
GROUPS: list[tuple[str, str]] = [
    ("/api/v1/documents", "documents (M1)"),
    ("/api/v1/schema", "schema synthesis (M1)"),
    ("/api/v1/entities", "taxonomy (M1)"),
    ("/api/v1/policies/test-recognizer", "taxonomy (M1)"),
    ("/api/v1/policies/presets", "policies (M0)"),
    ("/api/v1/policies/validate", "policies (M0)"),
    ("/api/v1/policies/plan", "policies (M0)"),
    ("/api/v1/policies", "policy store (M5)"),
    ("/api/v1/connections", "connections (M2)"),
    ("/api/v1/jobs", "jobs (M2)"),
    ("/api/v1/snapshots", "snapshots (M2)"),
    ("/api/v1/job-groups", "job groups (M2/M3)"),
    ("/api/v1/evidence", "evidence (M2/M5)"),
    ("/api/v1/artefacts", "artefacts (M2)"),
    ("/api/v1/reports", "reports (M5)"),
    ("/api/v1/admin", "admin (M5)"),
    ("/api/v1/scans", "UI scanner (M4)"),
    ("/api/v1/", "Layer 1 (M0)"),
    ("/", "ops (M0)"),
]
SCAN_PREFIX = "/api/v1/scans"
BOOTSTRAP_KEY = "dps_local_dev_key_change_me"  # the compose stack's local demo literal
DEMO_KEYS = ROOT / "var" / "demo" / "demo-keys.json"
DEFAULT_OUT = ROOT / "reports" / "flow-report.md"
DEMO_DIR = ROOT / "demo" / "documents"
#: The compose network's demo source (contract §11); logins are local demo literals (§2.3).
SOURCE_PG = {"host": "source-pg", "port": 5432, "database": "bank"}
SOURCE_LOGIN = {"username": "dps_reader", "password": "dps_reader"}
TARGET_LOGIN = {"username": "dps_writer", "password": "dps_writer"}
RUN_ROWS = 500
#: Two members of one job group: the consistency check needs two distinct targets (M3).
RUN_TARGETS = ("customers_flow", "customers_flow_b")
SCAN_URL = os.environ.get("DPS_FLOW_SCAN_URL") or "http://demo-site:8410/"
PURPOSE = {"ticket": "FLOW-0001", "legal_basis": "legitimate interest - route coverage report"}
#: A fictional customer (made-up values; identifiers only pass their checksums).
SAMPLE = {
    "customer_id": "CUST-884120",
    "full_name": "Ananya Krishnan",
    "email": "ananya.k@example-mail.in",
    "phone": "+91 98451 20377",
    "dob": "1988-03-14",
}
TERMINAL = ("succeeded", "failed", "dead", "canceled")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


@dataclass
class Row:
    """One call of the flow (or one that could not run). ``status`` is ``None`` when not sent."""

    method: str
    template: str
    expected: int
    status: int | None = None
    ms: float = 0.0
    keys: str = ""
    note: str = ""
    #: A value-free outcome the flow relies on that did not hold (a job that did not succeed, a
    #: connection test that failed, a consistency check that did not pass).
    problem: str = ""

    @property
    def skipped(self) -> bool:
        """Not sent on purpose (a component this stack does not run)."""
        return self.status is None and self.note.startswith("skipped")

    @property
    def ok(self) -> bool:
        """As expected (status and outcome), or skipped on purpose."""
        return self.skipped or (self.status == self.expected and not self.problem)


def top_keys(response: httpx.Response) -> str:
    """The response's top-level JSON key names (identifier-like names only), or its shape."""
    ctype = response.headers.get("content-type", "").split(";")[0].strip()
    if response.status_code == 204 or not response.content:
        return "(empty)"
    if ctype != "application/json":
        return f"({ctype or 'unknown'}, {len(response.content)} bytes)"
    try:
        body = response.json()
    except ValueError:
        return "(invalid json)"
    if isinstance(body, list):
        return f"[{len(body)} items]"
    if not isinstance(body, dict):
        return f"({type(body).__name__})"
    names = sorted(k for k in body if isinstance(k, str) and _IDENT.match(k))
    other = len(body) - len(names)
    return ", ".join(names) + (f" (+{other} other keys)" if other else "")


def error_code(response: httpx.Response) -> str:
    """The value-free error ``code`` of a 4xx/5xx JSON answer (contract §8.2), else ``""``."""
    if response.status_code < 400:
        return ""
    try:
        body = response.json()
    except ValueError:
        return ""
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) and _IDENT.match(code) else ""


def group_of(template: str) -> str:
    """The §9 route group a template belongs to."""
    return next(name for prefix, name in GROUPS if template.startswith(prefix))


def verdict(rows: Sequence[Row]) -> int:
    """0 when every call answered its expected status (skips aside), else 1."""
    return 0 if rows and all(r.ok for r in rows) else 1


def render(rows: Sequence[Row], *, meta: Mapping[str, str]) -> str:
    """The Markdown report: a header, one table row per call, a per-group summary."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# DPS flow report",
        "",
        f"Generated {now} by `tools/flow_report.py` (M5 Task 24). Route templates, statuses,",
        "durations and response key names only — never a value, an id, a key or a body.",
        "",
    ]
    lines += [f"- **{k}:** {v}" for k, v in meta.items()]
    lines += [
        "",
        "| # | Group | Method | Route | Expected | Status | ms | Response keys | Note |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        status = "—" if r.status is None else str(r.status)
        mark = "" if r.ok else " ✗"
        lines.append(
            f"| {i} | {group_of(r.template)} | {r.method} | `{r.template}` | {r.expected} | "
            f"{status}{mark} | {r.ms:.0f} | {r.keys} | {'; '.join(filter(None, (r.note, r.problem)))} |"
        )
    groups: dict[str, list[Row]] = {}
    for r in rows:
        groups.setdefault(group_of(r.template), []).append(r)
    lines += [
        "",
        "## Route groups",
        "",
        "| Group | Calls | As expected | Skipped |",
        "|---|---|---|---|",
    ]
    for name, members in groups.items():
        expected = sum(1 for r in members if r.status is not None and r.status == r.expected)
        skipped = sum(1 for r in members if r.skipped)
        lines.append(f"| {name} | {len(members)} | {expected} | {skipped} |")
    bad = [r for r in rows if not r.ok]
    lines += [
        "",
        f"**Verdict: {'PASS' if not bad else 'FAIL'}** — {len(rows)} calls, "
        f"{sum(1 for r in rows if r.status is not None and r.ok)} as expected, "
        f"{sum(1 for r in rows if r.skipped)} skipped, {len(bad)} unexpected or not run.",
        "",
    ]
    return "\n".join(lines)


class Flow:
    """Runs the calls in order and records one :class:`Row` per call (polls are not rows)."""

    def __init__(self, client: DpsClient, *, tenant_key: bool, stamp: str) -> None:
        self.c = client
        self.tenant_key = tenant_key
        self.stamp = stamp
        self.expect = {(m, t): s for m, t, s in EXPECT}
        if tenant_key:
            self.expect.update(TENANT_KEY_EXPECT)
        self.rows: list[Row] = []

    # -- recording ---------------------------------------------------------------------------
    def call(
        self, method: str, template: str, args: Mapping[str, str] | None = None, **kw: Any
    ) -> httpx.Response | None:
        """Send ``method template`` (placeholders filled from ``args``) and record the answer."""
        expected = self.expect[(method, template)]
        path = template.format(**(args or {}))
        start = time.perf_counter()
        try:
            response = self.c.raw(method, path, **kw)
        except DemoError as exc:
            self.rows.append(Row(method, template, expected, note=f"not reached ({exc.code})"))
            return None
        ms = (time.perf_counter() - start) * 1000
        code = error_code(response)
        self.rows.append(
            Row(
                method,
                template,
                expected,
                response.status_code,
                ms,
                top_keys(response),
                note=f"code {code}" if code else "",
            )
        )
        return response

    def expect_true(self, response: httpx.Response | None, field: str, problem: str) -> None:
        """Mark the last row with ``problem`` unless the JSON body's ``field`` is ``true``."""
        if response is not None and self.body(response).get(field) is not True and self.rows:
            self.rows[-1].problem = problem

    def not_run(self, method: str, template: str, why: str) -> None:
        """A call that could not be made because an earlier one failed (fails the run)."""
        self.rows.append(
            Row(method, template, self.expect[(method, template)], note=f"not run: {why}")
        )

    def skip(self, method: str, template: str, why: str) -> None:
        """A call this stack cannot serve on purpose (does not fail the run)."""
        self.rows.append(
            Row(method, template, self.expect[(method, template)], note=f"skipped: {why}")
        )

    @staticmethod
    def body(response: httpx.Response | None) -> dict[str, Any]:
        """A JSON object body, else ``{}`` (never printed)."""
        if response is None or response.status_code >= 400 or not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def wait(self, job_id: str, *, kind: str = "jobs", timeout: float = 600) -> dict[str, Any]:
        """Poll until terminal (unrecorded), then record one ``GET`` of the final state."""
        deadline = time.monotonic() + timeout
        template = f"/api/v1/{kind}/{{{kind[:-1]}_id}}"
        path = template.format(**{f"{kind[:-1]}_id": job_id})
        while time.monotonic() < deadline:
            try:
                state = self._state(self.c.raw("GET", path), kind)
            except DemoError:
                state = None
            if state in TERMINAL:
                break
            time.sleep(1.0)
        final = self.call("GET", template, {f"{kind[:-1]}_id": job_id})
        job = self.body(final)
        state = self._state(final, kind) if final is not None else None
        if final is not None and state != "succeeded" and self.rows:
            self.rows[-1].problem = f"final state {state or 'unknown'}"
        return (
            {**job["scan"], "summary": job.get("summary")}
            if kind == "scans" and "scan" in job
            else job
        )

    @staticmethod
    def _state(response: httpx.Response, kind: str) -> str | None:
        try:
            data = response.json()
        except ValueError:
            return None
        if kind == "scans" and isinstance(data, dict):
            data = data.get("scan") or {}
        return data.get("state") if isinstance(data, dict) else None

    def need(self, value: Any, rows: Sequence[tuple[str, str]], why: str) -> bool:
        """``True`` when ``value`` is set; else every ``(method, template)`` is recorded not run."""
        if value:
            return True
        for method, template in rows:
            self.not_run(method, template, why)
        return False

    # -- the flow ------------------------------------------------------------------------------
    def run(self) -> list[Row]:
        """Every group in dependency order; returns the rows."""
        ready = self.ops()
        self.layer1()
        self.m1()
        conns = self.connections()
        job, group = self.jobs(conns)
        self.evidence(job)
        self.reports()
        self.policy_store()
        self.admin()
        if (ready.get("browser") or {}).get("ok"):
            self.scans()
        else:
            for method, template, _ in EXPECT:
                if template.startswith(SCAN_PREFIX):
                    self.skip(method, template, "/readyz reports no browser worker (M4)")
        self.cleanup(conns)
        del group
        return self.rows

    def ops(self) -> dict[str, Any]:
        self.call("GET", "/health")
        ready = self.body(self.call("GET", "/readyz"))
        self.call("GET", "/metrics")
        components: dict[str, Any] = ready.get("components") or {}
        return components

    def layer1(self) -> None:
        self.call("POST", "/api/v1/detect", json={"data": SAMPLE})
        obf = self.body(
            self.call(
                "POST", "/api/v1/obfuscate", json={"data": SAMPLE, "policy": "gdpr-pseudonymise"}
            )
        )
        if self.need(obf.get("key_material"), [("POST", "/api/v1/reveal")], "obfuscate failed"):
            self.call(
                "POST",
                "/api/v1/reveal",
                json={
                    "data": obf.get("result"),
                    "key_material": obf["key_material"],
                    "reveal_hints": obf.get("reveal_hints") or [],
                },
            )
        del obf
        self.call(
            "POST", "/api/v1/synthesize", json={"data": SAMPLE, "policy": "synthetic-realistic"}
        )
        self.call("GET", "/api/v1/policies/presets")
        self.call("POST", "/api/v1/policies/validate", json={"policy": "gdpr-pseudonymise"})
        self.call(
            "POST",
            "/api/v1/policies/plan",
            json={"data": SAMPLE, "policy": "pci-dss", "mode": "obfuscate"},
        )

    def m1(self) -> None:
        def upload(mode: str, name: str, request: dict[str, Any] | None = None) -> None:
            data = {"request": json.dumps(request)} if request else {}
            files = {"file": (name, (DEMO_DIR / name).read_bytes())}
            self.call("POST", f"/api/v1/documents/{mode}", files=files, data=data, timeout=180.0)

        upload("detect", "kyc.txt")
        upload("obfuscate", "kyc.txt")
        upload("synthesize", "customers.csv", {"policy": "synthetic-realistic"})
        ddl = (
            "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80), "
            "email VARCHAR(120), balance NUMERIC(12,2))"
        )
        spec = self.body(self.call("POST", "/api/v1/schema/generator-spec", json={"ddl": ddl}))
        if self.need(
            spec.get("spec"), [("POST", "/api/v1/schema/synthesize")], "no generator spec"
        ):
            self.call("POST", "/api/v1/schema/synthesize", json={"spec": spec["spec"], "rows": 5})
        self.call("GET", "/api/v1/entities")
        recognizer = {
            "name": "flow-employee-id",
            "supported_entity": "EMPLOYEE_ID",
            "patterns": [{"name": "emp", "regex": r"\bEMP-\d{6}\b", "score": 0.9}],
            "context": ["employee"],
        }
        self.call(
            "POST",
            "/api/v1/policies/test-recognizer",
            json={"recognizer": recognizer, "text": "employee EMP-104233 joined"},
        )

    def connections(self) -> dict[str, str]:
        made: dict[str, str] = {}
        specs = {
            "source": {
                "name": f"flow-{self.stamp}-src",
                "engine": "postgres",
                "params": SOURCE_PG,
                "credentials": SOURCE_LOGIN,
                "roles": ["source"],
            },
            "target": {
                "name": f"flow-{self.stamp}-uat",
                "engine": "postgres",
                "params": SOURCE_PG,
                "credentials": TARGET_LOGIN,
                "roles": ["target"],
            },
        }
        for role, spec in specs.items():
            conn_id = self.body(self.call("POST", "/api/v1/connections", json=spec)).get("id")
            if conn_id:
                made[role] = str(conn_id)
        self.call("GET", "/api/v1/connections", params={"limit": 200})
        src = made.get("source")
        rows = [
            ("GET", "/api/v1/connections/{connection_id}"),
            ("PATCH", "/api/v1/connections/{connection_id}"),
            ("POST", "/api/v1/connections/{connection_id}/test"),
            ("GET", "/api/v1/connections/{connection_id}/objects"),
            ("GET", "/api/v1/connections/{connection_id}/describe"),
            ("POST", "/api/v1/connections/{connection_id}/count"),
        ]
        if not self.need(src, rows, "no source connection"):
            return made
        a = {"connection_id": str(src)}
        self.call("GET", "/api/v1/connections/{connection_id}", a)
        self.call("PATCH", "/api/v1/connections/{connection_id}", a, json={"tags": ["flow-report"]})
        tested = self.call(
            "POST",
            "/api/v1/connections/{connection_id}/test",
            a,
            json={"role": "source", "object": "bank.customers"},
        )
        self.expect_true(tested, "ok", "connection test not ok")
        self.call("GET", "/api/v1/connections/{connection_id}/objects", a, params={"owner": "bank"})
        self.call(
            "GET",
            "/api/v1/connections/{connection_id}/describe",
            a,
            params={"object": "bank.customers"},
        )
        self.call(
            "POST",
            "/api/v1/connections/{connection_id}/count",
            a,
            json={"object": "bank.customers", "selection": {"where": "country_code = 'IN'"}},
        )
        return made

    def jobs(self, conns: Mapping[str, str]) -> tuple[str | None, str | None]:
        src, tgt = conns.get("source"), conns.get("target")
        discover = [("POST", "/api/v1/jobs/discover"), ("GET", "/api/v1/jobs/{job_id}")]
        snapshot_rows = [
            ("GET", "/api/v1/snapshots"),
            ("GET", "/api/v1/snapshots/{snapshot_id}"),
            ("PATCH", "/api/v1/snapshots/{snapshot_id}"),
            ("POST", "/api/v1/snapshots/{snapshot_id}/accept"),
        ]
        snap: str | None = None
        if self.need(src, discover + snapshot_rows, "no source connection"):
            body = {"connection_id": src, "objects": [{"owner": "bank", "name": "customers"}]}
            made = self.body(self.call("POST", "/api/v1/jobs/discover", json=body))
            disc_id = (made.get("job") or {}).get("id")
            if self.need(disc_id, discover[1:] + snapshot_rows, "discover refused"):
                done = self.wait(str(disc_id))
                ids = (done.get("progress") or {}).get("snapshot_ids") or []
                snap = str(ids[0]) if ids else None
                if self.need(snap, snapshot_rows, f"discover {done.get('state')}"):
                    self.call("GET", "/api/v1/snapshots", params={"connection_id": src})
                    s = {"snapshot_id": str(snap)}
                    self.call("GET", "/api/v1/snapshots/{snapshot_id}", s)
                    keep = {"path": "$.country_code", "method": {"method": "keep"}}
                    self.call(
                        "PATCH", "/api/v1/snapshots/{snapshot_id}", s, json={"overrides": [keep]}
                    )
                    self.call("POST", "/api/v1/snapshots/{snapshot_id}/accept", s)
        group = self.body(
            self.call("POST", "/api/v1/job-groups", json={"name": f"flow-{self.stamp}"})
        )
        group_id = (group.get("group") or {}).get("id")
        key = group.get("key_material")
        del group
        self.call("GET", "/api/v1/job-groups")
        if self.need(group_id, [("GET", "/api/v1/job-groups/{group_id}")], "no job group"):
            self.call("GET", "/api/v1/job-groups/{group_id}", {"group_id": str(group_id)})
        run_rows = [
            ("POST", "/api/v1/jobs/run/validate"),
            ("POST", "/api/v1/jobs/run"),
            ("GET", "/api/v1/jobs/{job_id}"),
            ("GET", "/api/v1/jobs/{job_id}/partitions"),
            ("POST", "/api/v1/jobs/{job_id}/pause"),
            ("POST", "/api/v1/jobs/{job_id}/resume"),
            ("POST", "/api/v1/jobs/{job_id}/cancel"),
            ("POST", "/api/v1/jobs/{job_id}/retry"),
        ]
        job_id: str | None = None
        members: list[str] = []
        if self.need(snap and tgt and group_id and key, run_rows, "no snapshot, target or group"):
            # Two members of the group (one table each) so the consistency check has a pair.
            for i, table in enumerate(RUN_TARGETS):
                spec = {
                    "mode": "obfuscate",
                    "snapshot_id": snap,
                    "group_id": group_id,
                    "purpose": PURPOSE,
                    "source": {
                        "connection_id": src,
                        "object": {"owner": "bank", "name": "customers"},
                        "selection": {"max_rows": RUN_ROWS},
                        "parallelism": 2,
                    },
                    "target": {
                        "write_mode": "table",
                        "connection_id": tgt,
                        "object": {"owner": "bank_uat", "name": table},
                        "create_if_missing": True,
                        "table_load": "truncate_insert",
                    },
                    "consistency": {"scope": "key", "key_material": key},
                }
                if i == 0:
                    valid = self.call("POST", "/api/v1/jobs/run/validate", json=spec)
                    self.expect_true(valid, "ok", "run spec not valid")
                made = self.body(self.call("POST", "/api/v1/jobs/run", json=spec))
                del spec
                member = (made.get("job") or {}).get("id")
                del made
                if member:
                    members.append(str(member))
            del key  # the group's key material: never kept beyond the submissions
            job_id = members[0] if members else None
            if self.need(len(members) == len(RUN_TARGETS), run_rows[2:], "run refused"):
                for member in members:
                    self.wait(member)
                j = {"job_id": str(job_id)}
                self.call("GET", "/api/v1/jobs/{job_id}/partitions", j)
                for action in ("pause", "resume", "cancel", "retry"):
                    self.call("POST", f"/api/v1/jobs/{{job_id}}/{action}", j)
        self.call("GET", "/api/v1/jobs", params={"kind": "run", "limit": 20})
        self.call("GET", "/api/v1/jobs/stats")
        check = ("POST", "/api/v1/job-groups/{group_id}/consistency-check")
        if self.need(group_id and len(members) == len(RUN_TARGETS), [check], "no grouped runs"):
            checked = self.call(*check, {"group_id": str(group_id)}, json={"sample": 100})
            self.expect_true(checked, "passed", "consistency check did not pass")
        return job_id, (str(group_id) if group_id else None)

    def evidence(self, job_id: str | None) -> None:
        rows = [
            ("GET", "/api/v1/evidence"),
            ("GET", "/api/v1/evidence/{evidence_id}"),
            ("GET", "/api/v1/evidence/{evidence_id}/html"),
            ("GET", "/api/v1/evidence/{evidence_id}/pdf"),
            ("GET", "/api/v1/artefacts/{artefact_id}"),
        ]
        if not self.need(job_id, rows, "no run job"):
            return
        items = self.body(self.call("GET", "/api/v1/evidence", params={"job_id": job_id})).get(
            "items"
        )
        ev_id = (items or [{}])[0].get("id")
        if not self.need(ev_id, rows[1:], "no evidence pack"):
            return
        e = {"evidence_id": str(ev_id)}
        record = self.body(self.call("GET", "/api/v1/evidence/{evidence_id}", e))
        self.call("GET", "/api/v1/evidence/{evidence_id}/html", e)
        self.call("GET", "/api/v1/evidence/{evidence_id}/pdf", e)
        stored = record.get("artefacts") or []
        artefact = next((a.get("id") for a in stored if a.get("kind") == "evidence_html"), None)
        artefact = artefact or next((a.get("id") for a in stored), None)
        del record
        if self.need(artefact, rows[4:], "no evidence artefact"):
            self.call("GET", "/api/v1/artefacts/{artefact_id}", {"artefact_id": str(artefact)})

    def reports(self) -> None:
        self.call("GET", "/api/v1/reports/summary", params={"days": 14})
        self.call("GET", "/api/v1/reports/jobs", params={"days": 14})
        self.call("GET", "/api/v1/reports/evidence", params={"days": 30})
        listed = self.body(self.call("GET", "/api/v1/reports/api-rollups", params={"days": 14}))
        first = (listed.get("items") or [{}])[0]
        day, action = first.get("day"), first.get("action")
        if self.need(day and action, [("GET", "/api/v1/reports/api-rollups/{day}")], "no roll-up"):
            self.call(
                "GET",
                "/api/v1/reports/api-rollups/{day}",
                {"day": str(day)},
                params={"action": action, "format": "json"},
            )

    def policy_store(self) -> None:
        name = f"flow-{self.stamp}"
        p = {"name": name}
        self.call("GET", "/api/v1/policies")
        made = self.call(
            "POST", "/api/v1/policies", json={"name": name, "base_preset": "gdpr-pseudonymise"}
        )
        rest = [
            ("GET", "/api/v1/policies/{name}"),
            ("PUT", "/api/v1/policies/{name}/draft"),
            ("GET", "/api/v1/policies/{name}/diff"),
            ("POST", "/api/v1/policies/{name}/publish"),
            ("GET", "/api/v1/policies/{name}/versions/{version}"),
            ("DELETE", "/api/v1/policies/{name}/draft"),
        ]
        if not self.need(made is not None and made.status_code == 201, rest, "policy not created"):
            return
        draft = {"body": {"preset": "gdpr-pseudonymise", "treat": {"EMAIL": {"method": "redact"}}}}
        self.call("GET", "/api/v1/policies/{name}", p)
        self.call("PUT", "/api/v1/policies/{name}/draft", p, json=draft)
        self.call("GET", "/api/v1/policies/{name}/diff", p, params={"from": "1", "to": "draft"})
        version = self.body(
            self.call(
                "POST",
                "/api/v1/policies/{name}/publish",
                p,
                json={"change_ticket": "FLOW-0001", "note": "flow report"},
            )
        ).get("version")
        if self.need(version, rest[4:5], "nothing published"):
            self.call(
                "GET",
                "/api/v1/policies/{name}/versions/{version}",
                {"name": name, "version": str(version)},
                params={"format": "yaml"},
            )
        self.call("PUT", "/api/v1/policies/{name}/draft", p, json=draft)
        self.call("DELETE", "/api/v1/policies/{name}/draft", p)

    def admin(self) -> None:
        tenants = self.body(self.call("GET", "/api/v1/admin/tenants")).get("items") or []
        new_tenant = f"flow-{self.stamp}"
        made = self.call(
            "POST",
            "/api/v1/admin/tenants",
            json={"tenant_id": new_tenant, "display_name": "Flow report", "note": "flow report"},
        )
        if made is not None and made.status_code == 201:
            tenant = new_tenant
        elif self.c.tenant:
            tenant = self.c.tenant
        else:  # a tenant-scoped key: its own (first granted) tenant
            tenant = str((tenants[0] if tenants else {}).get("tenant_id") or "default")
        self.call(
            "PATCH",
            "/api/v1/admin/tenants/{tenant_id}",
            {"tenant_id": tenant},
            json={"note": "flow report"},
        )
        self.call("GET", "/api/v1/admin/keys")
        created = self.body(
            self.call(
                "POST",
                "/api/v1/admin/keys",
                json={
                    "name": f"flow-{self.stamp}",
                    "tenant_ids": [tenant],
                    "scopes": ["detect"],
                    "expires_in_days": 1,
                },
            )
        )
        key_id = (created.get("record") or {}).get("id")
        del created  # the raw key is never kept
        rows = [
            ("POST", "/api/v1/admin/keys/{key_id}/rotate"),
            ("POST", "/api/v1/admin/keys/{key_id}/disable"),
            ("POST", "/api/v1/admin/keys/{key_id}/enable"),
        ]
        if self.need(key_id, rows, "no key"):
            rotated = self.body(
                self.call(
                    "POST",
                    "/api/v1/admin/keys/{key_id}/rotate",
                    {"key_id": str(key_id)},
                    json={"grace_hours": 0},
                )
            )
            new_id = ((rotated.get("new") or {}).get("record") or {}).get("id")
            del rotated
            if self.need(new_id, rows[1:], "rotation refused"):
                k = {"key_id": str(new_id)}
                self.call("POST", "/api/v1/admin/keys/{key_id}/disable", k)
                self.call("POST", "/api/v1/admin/keys/{key_id}/enable", k)
                self.call("POST", "/api/v1/admin/keys/{key_id}/disable", k)  # nothing stays usable
        self.call("GET", "/api/v1/admin/audit", params={"limit": 20})
        self.call("GET", "/api/v1/admin/queue")
        self.call("GET", "/api/v1/admin/posture")
        self.call("POST", "/api/v1/admin/retention/run", json={"dry_run": True})

    def scans(self) -> None:
        spec = {
            "url": SCAN_URL,
            "scope": {"max_pages": 3, "max_depth": 1},
            "mode": "synthesize",
            "fill": {"personas": 1},
            "purpose": PURPOSE,
        }
        made = self.body(self.call("POST", "/api/v1/scans", json=spec))
        scan_id = (made.get("scan") or {}).get("id")
        del made
        self.call("POST", "/api/v1/scans/personas/preview", json={"count": 1})
        self.call("GET", "/api/v1/scans", params={"limit": 20})
        rows = [
            ("GET", "/api/v1/scans/{scan_id}"),
            ("GET", "/api/v1/scans/{scan_id}/pages"),
            ("GET", "/api/v1/scans/{scan_id}/pages/{page_no}"),
            ("GET", "/api/v1/scans/{scan_id}/events"),
            ("GET", "/api/v1/scans/{scan_id}/inventory"),
            ("GET", "/api/v1/scans/{scan_id}/export"),
            ("POST", "/api/v1/scans/{scan_id}/cancel"),
        ]
        if not self.need(scan_id, rows, "scan refused"):
            return
        s = {"scan_id": str(scan_id)}
        self.wait(str(scan_id), kind="scans", timeout=900)
        pages = self.body(self.call("GET", "/api/v1/scans/{scan_id}/pages", s)).get("items") or []
        page_no = pages[0].get("page_no") if pages else None
        if self.need(page_no is not None, rows[2:3], "no page visited"):
            self.call(
                "GET", "/api/v1/scans/{scan_id}/pages/{page_no}", {**s, "page_no": str(page_no)}
            )
        self.call("GET", "/api/v1/scans/{scan_id}/events", s)
        self.call("GET", "/api/v1/scans/{scan_id}/inventory", s)
        self.call("GET", "/api/v1/scans/{scan_id}/export", s, params={"format": "csv"})
        self.call("POST", "/api/v1/scans/{scan_id}/cancel", s)

    def cleanup(self, conns: Mapping[str, str]) -> None:
        delete = ("DELETE", "/api/v1/connections/{connection_id}")
        if self.need(conns, [delete], "no connection made"):
            for conn_id in conns.values():
                self.call(*delete, {"connection_id": conn_id})


def resolve_key(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[str, str, bool]:
    """``(key, where it came from, tenant-scoped)`` — module docstring, "Keys"."""
    if args.demo_keys is not None:
        keys = load_keys(Path(args.demo_keys))
        if args.key_name not in keys:
            raise SystemExit(f"{args.demo_keys}: no key named {args.key_name!r}")
        return keys[args.key_name], f"demo key {args.key_name}", True
    for name in ("DPS_API_KEY", "DPS_BOOTSTRAP_API_KEY"):
        if env.get(name):
            return env[name], name, bool(args.tenant_key)
    return BOOTSTRAP_KEY, "local bootstrap key", bool(args.tenant_key)


def base_url(env: Mapping[str, str]) -> str:
    """``DPS_BASE_URL`` / ``DPS_TEST_APP_URL``, else the lane's ``DPS_APP_PORT`` (contract §12.2)."""
    return (
        env.get("DPS_BASE_URL")
        or env.get("DPS_TEST_APP_URL")
        or f"http://127.0.0.1:{env.get('DPS_APP_PORT') or '8400'}"
    ).rstrip("/")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Command line (module docstring)."""
    p = argparse.ArgumentParser(
        prog="python tools/flow_report.py", description=__doc__.split("\n")[0]
    )
    p.add_argument(
        "--base-url", default=None, help="the stack (default: DPS_BASE_URL/DPS_TEST_APP_URL)"
    )
    p.add_argument(
        "--out", default=str(DEFAULT_OUT), help="the Markdown report (reports/flow-report.md)"
    )
    p.add_argument(
        "--demo-keys",
        nargs="?",
        const=str(DEMO_KEYS),
        default=None,
        help="run as a key of the demo seed's key file (default var/demo/demo-keys.json)",
    )
    p.add_argument("--key-name", default="demo-console", help="the key to use from --demo-keys")
    p.add_argument("--tenant-key", action="store_true", help="the key is tenant-scoped")
    p.add_argument("--tenant", default=None, help="X-DPS-Tenant for every call")
    return p.parse_args(argv)


def main(
    argv: Sequence[str] | None = None, *, client_factory: Callable[..., DpsClient] = DpsClient
) -> int:
    """Run the flow, write the report, return the exit code (0 when every call was as expected)."""
    args = parse_args(argv)
    env = os.environ
    url = (args.base_url or base_url(env)).rstrip("/")
    key, source, tenant_key = resolve_key(args, env)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    started = time.monotonic()
    with client_factory(url, key, tenant=args.tenant, timeout=120.0) as client:
        del key
        flow = Flow(client, tenant_key=tenant_key, stamp=stamp)
        rows = flow.run()
    meta = {
        "Stack": url,
        "Key": source + (" (tenant-scoped)" if tenant_key else " (operator: every tenant)"),
        "Tenant header": args.tenant or "none (the key's default tenant)",
        "Wall time": f"{time.monotonic() - started:.1f} s",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, meta=meta), encoding="utf-8")
    code = verdict(rows)
    bad = [r for r in rows if not r.ok]
    for r in bad:
        sent = f"HTTP {r.status}" if r.status is not None else ""
        got = "; ".join(part for part in (sent, r.note, r.problem) if part)
        print(f"UNEXPECTED  {r.method} {r.template}: expected {r.expected}, {got}")
    print(
        f"{'PASS' if code == 0 else 'FAIL'}  {len(rows)} calls, {len(bad)} unexpected — "
        f"report written to {out}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
