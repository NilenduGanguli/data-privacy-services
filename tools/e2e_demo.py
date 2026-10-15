"""The ten-minute demo, run end to end against a stack, every promise asserted (M5 Task 26).

    python tools/demo_seed.py                       # once: tenant, keys, connections, group
    python tools/e2e_demo.py [--console] [--skip-scanner] [--skip-oracle]
        → reports/e2e-demo-report.md and reports/e2e-demo-report.json (exit 0, or 1 on a failure)

Runs the demo story of spec §9 as the seed's ``demo-console`` key (``var/demo/demo-keys.json``) in
tenant ``retail-banking-apac``, with the job group key the seed wrote to
``var/demo/group-crm-uat-refresh.json``; the cross-tenant retention run uses ``--admin-key``.

======  =============================================================================================
S1      Overview: ``/readyz`` ready; ``/reports/summary`` tile ``raw_pii_sent_to_llm`` is 0
S2      Playground: the console's KYC sample → ≥ 8 findings incl. AADHAAR, PAN_IN, EMAIL; obfuscate
        under ``@retail-kyc``; reveal gives the input back; synthesize carries no detected input
        value (every string leaf of every detected field, the nested card number too); the LLM
        counter of each answer is 0
S3      Bank tokenization (when ``/readyz`` reports the tokenizer, else SKIP): SSN → ``tk_`` token
        → reveal round trip
S4      Scanned KYC PDF (the console's demo scan, synthetic) → OCR, redacted PDF: OCR read ≥ 1 page,
        ``%PDF``, 0 characters a text extractor finds, 0 originals an OCR re-read finds
S5      Discover PostgreSQL ``bank.customers``, MongoDB ``crm.profiles``, Oracle ``CRM.CUSTOMERS``
        (``--skip-oracle``: two systems); each snapshot classifies ≥ 5 sensitive columns, has
        heat-map data (``value_hit_rate``) and a customer-id column to join on; accepted
S6      ``table`` runs under the group's key into the UAT targets; every job succeeded, every
        partition done, the leakage sweep passed, the pack is labelled pseudonymised
S7      The group's consistency check of this run's S6 jobs (one member per job, on the customer-id
        path of its snapshot; the last submitted anchors the sample): its members are exactly
        those jobs, passed, every pair consistent with overlap > 0
S8      Synthetic companion: copula synthesize of ``bank.customers`` → ``bank_uat``; fidelity
        ≥ 0.85, exact-row matches 0, NNDR measured
S9      UI scanner (``--skip-scanner``: SKIP): a scan of the demo onboarding site (3 personas);
        ≥ 2 pages, a page with both filled screens, the CSV export has a header, and the smoke's
        D4 rule: the demo site's ``/__received`` counters of non-GET requests, GET-form
        submissions and denied paths did not move and ``non_get_allowed`` is 0 — with both
        positive controls (the site served ≥ 1 GET per visited page; the guard blocked ≥ 1 send)
S10     Reports: the S6/S8/S9 packs listed; an evidence PDF is ``%PDF``; audit rows for the demo
        jobs; no input value in any of these bodies
S11     Admin: the eleven posture checks; a key rotated with grace 0 is refused (401); a dry-run
        retention job finishes (its ``retention.run`` audit row)
S12     ``--console``: Playwright walks nine console pages in light and dark (heading shown, no
        console error) and writes ``reports/demo-screens/<page>-<theme>.png``
======  =============================================================================================

A failed assertion is ``FAIL``, an unexpected answer ``ERROR``; the run stops there and later steps
are ``NOT RUN``. The reports hold ids, counts, durations, entity types and states only — never a
value, a key or a token (the JSON follows the ``dps.e2e-demo.v1`` contract of
``tools/build_deck.py``). The Markdown's last line is the wall time of the whole run ("demo flow
completed in m:ss"); the rehearsal target is under ten minutes.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `python tools/e2e_demo.py`

from tools.demo_client import DemoError, DpsClient, load_keys  # noqa: E402

SCHEMA = "dps.e2e-demo.v1"
DEFAULT_TENANT = "retail-banking-apac"
DEFAULT_KEYS = ROOT / "var" / "demo" / "demo-keys.json"
DEFAULT_GROUP = ROOT / "var" / "demo" / "group-crm-uat-refresh.json"
DEFAULT_MD = ROOT / "reports" / "e2e-demo-report.md"
DEFAULT_JSON = ROOT / "reports" / "e2e-demo-report.json"
DEFAULT_SCREENS = ROOT / "reports" / "demo-screens"
BOOTSTRAP_KEY = "dps_local_dev_key_change_me"  # the compose stack's local demo literal
KEY_NAME = "demo-console"
SCAN_URL = "http://demo-site:8410/apply"  # the demo site as the browser-worker sees it
DEMO_DOCS = ROOT / "demo" / "documents"
SAMPLES = json.loads((ROOT / "frontend" / "src" / "lib" / "samples.json").read_text("utf-8"))
#: A fictional SSN (the well-known sample number) for the tokenizer round trip.
SSN = "123-45-6789"
#: The demo tenant's policy (``tools/demo_seed.py`` publishes v2), named by reference.
POLICY_REF = "@retail-kyc"
PURPOSE = {"ticket": "CHG-48213", "legal_basis": "legitimate interest - UAT refresh demo"}
JOB_TIMEOUT = float(os.environ.get("DPS_DEMO_JOB_TIMEOUT") or "600")
SCAN_TIMEOUT = float(os.environ.get("DPS_DEMO_SCAN_TIMEOUT") or "600")
#: The demo site's counters that must not move during a scan, and the one that must (smoke D4).
SITE_COUNTERS = ("non_get", "form_get", "logout_hits", "admin_hits")
SITE_SERVED = "get"
#: The demo site's made-up customer values (no export or report may carry them).
SITE_VALUES: tuple[str, ...] = tuple(
    str(v)
    for k, v in json.loads((ROOT / "demo/onboarding/data.json").read_text("utf-8")).items()
    if k != "dob"
)
STEPS: tuple[tuple[str, str], ...] = (
    ("S1", "Overview: readiness and the LLM exposure tile"),
    ("S2", "Playground: KYC record, findings, obfuscate, reveal, synthetic"),
    ("S3", "Bank tokenization round trip"),
    ("S4", "Scanned PDF to a redacted PDF"),
    ("S5", "Discover Oracle CRM, MongoDB profiles, PostgreSQL customers; accept"),
    ("S6", "Runs under the group key; partitions; evidence"),
    ("S7", "Cross-system consistency join"),
    ("S8", "Synthetic companion (copula)"),
    ("S9", "UI scanner: scan, parallel view, export, nothing submitted"),
    ("S10", "Reports: evidence PDF and audit trail"),
    ("S11", "Admin: posture, key rotation, retention"),
    ("S12", "Console walk, light and dark"),
)


@dataclass(frozen=True)
class Member:
    """One system of the three-way demo: source connection/object → target connection/object."""

    engine: str
    source: str
    obj: dict[str, str | None]
    target: str
    target_obj: dict[str, str | None]


#: Submitted in this order; the last (Oracle) anchors the consistency check's sample.
MEMBERS: tuple[Member, ...] = (
    Member(
        "postgres",
        "bank-pg",
        {"owner": "bank", "name": "customers"},
        "bank-uat",
        {"owner": "bank_uat", "name": "customers_demo"},
    ),
    Member(
        "mongo",
        "profiles-mongo",
        {"owner": None, "name": "profiles"},
        "profiles-uat",
        {"owner": None, "name": "profiles_demo"},
    ),
    Member(
        "oracle",
        "crm-oracle",
        {"owner": "CRM", "name": "CUSTOMERS"},
        "crm-masked",
        {"owner": "CRM_MASKED", "name": "CUSTOMERS_DEMO"},
    ),
)
SYNTH_TARGET = {"owner": "bank_uat", "name": "customers_synthetic"}
#: S7 joins the S6 runs on this entity (the first of the server's join preference).
JOIN_ENTITY = "CUSTOMER_ID"
#: Distinct customer ids S7 samples from the anchor's target.
CHECK_SAMPLE = 500
#: Shortest detected value S2 probes the synthetic record for (shorter ones occur by chance).
SYNTH_PROBE_MIN = 4
#: Rows per partition of the S6 runs: several partitions per job to watch commit (2 at a time).
PARTITION_ROWS = 5_000
#: The categorical column the synthetic companion's utility test predicts (train on synthetic,
#: test on real; spec §5.7).
UTILITY_LABEL = "$.segment"
#: Console pages of S12: (screenshot name, route, CSS selector, text it must show) — the page's
#: own heading, never the sidebar (which names every section). Routes with ``{job}``, ``{scan}``,
#: ``{page}`` and ``{evidence}`` take the S6 run, the S9 scan and its identity page, the S8 pack.
PAGES: tuple[tuple[str, str, str, str], ...] = (
    ("overview", "/", "main h1", "Protection posture"),
    ("playground", "/playground", "main h1", "Try the engine"),
    ("policies", "/policies/retail-kyc", "main h1", "Policies"),
    ("data-sources", "/data-sources", "main h1", "Jobs"),
    ("job", "/data-sources/jobs/{job}", "main h1", "→"),  # "<engine> · <object> → <target>"
    ("scan-parallel", "/scanner/scans/{scan}?tab=parallel&page={page}", "main h1", "Scan"),
    ("reports", "/reports", "main h1", "Evidence and audit"),
    ("evidence", "/reports/evidence/{evidence}", "article .eyebrow", "evidence pack"),
    ("admin", "/admin", "main h1", "Admin"),
)
THEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
#: What covers the data key on the Playground's key card in a screenshot (the ink token).
MASK_COLOR = "#0E141B"


class StepFailed(Exception):
    """An assertion of the demo did not hold (value-free message)."""


class StepSkipped(Exception):
    """The step cannot run on this stack or was skipped by a flag (value-free reason)."""


def check(ok: bool, detail: str) -> None:
    """Raise :class:`StepFailed` with ``detail`` unless ``ok``."""
    if not ok:
        raise StepFailed(detail)


@dataclass
class StepResult:
    """One step's verdict: status, wall seconds, value-free facts and a one-line detail."""

    id: str
    title: str
    status: str
    seconds: float = 0.0
    facts: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "seconds": round(self.seconds, 2),
            "facts": self.facts,
            "detail": self.detail,
        }


# -- pure helpers (unit-tested) ------------------------------------------------------------------


def mmss(seconds: float) -> str:
    """``431.6`` → ``7:12`` (rounded to the second, as slide 1 prints it)."""
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def sends_blocked(blocked: Any) -> int:
    """Non-GET requests the request guard stopped: ``blocked_requests`` less GET and HEAD."""
    if not isinstance(blocked, dict):
        return 0
    return sum(int(n) for m, n in blocked.items() if str(m).upper() not in ("GET", "HEAD"))


def d4_verdict(
    before: Mapping[str, int], after: Mapping[str, int], scan: Mapping[str, Any]
) -> tuple[bool, dict[str, Any], str]:
    """The smoke's D4 rule with its two positive controls → ``(passed, facts, detail)``.

    Passed only when none of :data:`SITE_COUNTERS` moved, ``non_get_allowed`` is 0, the site
    served at least one GET per visited page (so the counters are those of the scanned site) and
    the guard stopped at least one send (the pages tried to send). ``scan`` is the evidence pack's
    scan block.
    """
    received = {name: int(after[name]) - int(before[name]) for name in SITE_COUNTERS}
    served = (
        int(after[SITE_SERVED]) - int(before[SITE_SERVED])
        if SITE_SERVED in after and SITE_SERVED in before
        else None
    )
    if served is not None:
        received[SITE_SERVED] = served
    pages = int(scan.get("pages") or 0)
    blocked = sends_blocked(scan.get("blocked_requests"))
    allowed = scan.get("non_get_allowed")
    passed = (
        not any(received[n] for n in SITE_COUNTERS)
        and allowed == 0
        and served is not None
        and served >= max(1, pages)
        and blocked >= 1
    )
    facts = {
        "scan": {
            k: scan.get(k)
            for k in ("pages", "fields", "personas", "submissions", "non_get_allowed")
        },
        "received": received,
        "sends_blocked": blocked,
    }
    detail = (
        ", ".join(f"{n} +{received[n]}" for n in SITE_COUNTERS)
        + f"; served +{served if served is not None else '?'} ({pages} pages)"
        + f"; sends blocked {blocked}; non_get_allowed {allowed}"
    )
    return passed, facts, detail


def leaves(value: Any) -> list[str]:
    """Every string leaf of a JSON value (for "no input value in this body" checks)."""
    if isinstance(value, dict):
        return [s for v in value.values() for s in leaves(v)]
    if isinstance(value, list):
        return [s for v in value for s in leaves(v)]
    return [value] if isinstance(value, str) else []


def found_in(texts: Sequence[str], values: Sequence[str], *, min_len: int = 6) -> int:
    """How many of ``values`` (at least ``min_len`` characters) occur in any of ``texts``."""
    probes = [v.lower() for v in values if len(v) >= min_len]
    hay = [t.lower() for t in texts]
    return sum(1 for v in probes if any(v in t for t in hay))


def git_commit(root: Path = ROOT) -> str:
    """The short commit of ``root`` (``unknown`` outside a git checkout)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def report_json(
    results: Sequence[StepResult],
    *,
    tenant: str,
    version: str,
    commit: str,
    total: float,
    generated_at: datetime,
) -> dict[str, Any]:
    """The ``dps.e2e-demo.v1`` document (``tools/build_deck.py``)."""
    return {
        "schema": SCHEMA,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "dps_version": version,
        "commit": commit,
        "tenant": tenant,
        "total_seconds": round(total, 1),
        "steps": [r.as_json() for r in results],
    }


def _fact_text(facts: Mapping[str, Any]) -> str:
    text = json.dumps(facts, sort_keys=True, separators=(", ", ": "))
    return text.replace("|", "\\|")


def report_markdown(doc: Mapping[str, Any], *, base_url: str) -> str:
    """The human report: one row per step, its facts, and the wall time as the last line."""
    lines = [
        "# DPS ten-minute demo — end-to-end rehearsal",
        "",
        f"- Stack: `{base_url}` · tenant `{doc['tenant']}` · DPS {doc['dps_version']} · "
        f"commit `{doc['commit']}`",
        f"- Generated: {doc['generated_at']}",
        "- Value-free: ids, counts, durations, entity types and states only.",
        "",
        "| Step | Beat | Status | Seconds | Detail |",
        "|---|---|---|---:|---|",
    ]
    api_path = 0.0
    for step in doc["steps"]:
        detail = str(step.get("detail") or "").replace("|", "\\|")
        lines.append(
            f"| {step['id']} | {step['title']} | **{step['status']}** | "
            f"{float(step.get('seconds') or 0):.1f} | {detail} |"
        )
        if step["id"] != "S12":
            api_path += float(step.get("seconds") or 0)
    lines += ["", "## Facts per step", ""]
    for step in doc["steps"]:
        if step.get("facts"):
            lines.append(f"- **{step['id']}** `{_fact_text(step['facts'])}`")
    statuses = [s["status"] for s in doc["steps"]]
    verdict = "PASS" if all(s in ("PASS", "SKIP") for s in statuses) else "FAIL"
    lines += [
        "",
        f"Verdict: **{verdict}** — {statuses.count('PASS')} passed, {statuses.count('SKIP')} "
        f"skipped, {sum(1 for s in statuses if s not in ('PASS', 'SKIP'))} not passed; "
        f"API path (S1–S11) {mmss(api_path)}.",
        "",
        f"demo flow completed in {mmss(float(doc['total_seconds']))}",
    ]
    return "\n".join(lines) + "\n"


# -- the demo ------------------------------------------------------------------------------------


class Demo:
    """The steps, sharing ids between them (``self.ctx``)."""

    def __init__(
        self,
        c: DpsClient,
        admin: DpsClient,
        site: httpx.Client,
        args: argparse.Namespace,
        group: Mapping[str, Any],
    ) -> None:
        self.c = c
        self.admin = admin
        self.site = site
        self.args = args
        self.group = group
        self.ctx: dict[str, Any] = {}
        self.members = tuple(m for m in MEMBERS if not (args.skip_oracle and m.engine == "oracle"))

    # S1 ---------------------------------------------------------------------------------------
    def s1(self) -> tuple[dict[str, Any], str]:
        ready = self.c.get("/readyz")
        components = ready.get("components") or {}
        check(ready.get("ready") is True, "/readyz is not ready")
        self.ctx["components"] = components
        self.ctx["version"] = str((components.get("api-core") or {}).get("version") or "unknown")
        tiles = self.c.get("/reports/summary", days=14).get("tiles") or {}
        sent = tiles.get("raw_pii_sent_to_llm")
        check(sent == 0, f"raw_pii_sent_to_llm is {sent}")
        ok = sum(1 for v in components.values() if isinstance(v, dict) and v.get("ok"))
        facts = {
            "ready": True,
            "raw_pii_sent_to_llm": sent,
            "llm_calls": tiles.get("llm_calls"),
            "components_ok": ok,
            "components": len(components),
        }
        return facts, f"ready; {ok}/{len(components)} components ok; raw PII sent to LLM {sent}"

    # S2 ---------------------------------------------------------------------------------------
    def s2(self) -> tuple[dict[str, Any], str]:
        record = SAMPLES["json"]
        det = self.c.post("/detect", {"data": record})
        findings = det.get("findings") or []
        types = sorted({str(f.get("entity_type")) for f in findings})
        check(len(findings) >= 8, f"{len(findings)} findings (8 expected)")
        missing = {"AADHAAR", "PAN_IN", "EMAIL"} - set(types)
        check(not missing, f"missing entity types {sorted(missing)}")
        obf = self.c.post("/obfuscate", {"data": record, "policy": POLICY_REF})
        report = obf.get("report") or {}
        check(bool((report.get("verification") or {}).get("passed")), "obfuscate not verified")
        policy = report.get("policy")
        rev = self.c.post(
            "/reveal",
            {
                "data": obf.get("result"),
                "key_material": obf.get("key_material"),
                "reveal_hints": obf.get("reveal_hints"),
            },
        )
        identical = rev.get("result") == record
        check(identical, "reveal did not restore the input")
        del obf, rev
        syn = self.c.post("/synthesize", {"data": record, "policy": "synthetic-realistic"})
        detected = [v for v in detected_values(record, findings) if len(v) >= SYNTH_PROBE_MIN]
        answer = syn.get("result")
        hay = [*leaves(answer), json.dumps(answer, ensure_ascii=False)]
        leaked = found_in(hay, detected, min_len=SYNTH_PROBE_MIN)
        check(leaked == 0, f"{leaked} detected input values in the synthetic record")
        sent = int((report.get("llm") or {}).get("raw_pii_sent") or 0) + int(
            ((syn.get("report") or {}).get("llm") or {}).get("raw_pii_sent") or 0
        )
        check(sent == 0, f"raw_pii_sent {sent}")
        facts = {
            "findings": len(findings),
            "entity_types": types,
            "policy": policy if isinstance(policy, str) else None,
            "reveal_identical": identical,
            "synthetic_input_values": leaked,
            "raw_pii_sent_to_llm": sent,
        }
        return facts, (
            f"{len(findings)} findings, {len(types)} entity types; reveal identical; "
            f"synthetic carries 0 of {len(detected)} detected values"
        )

    # S3 ---------------------------------------------------------------------------------------
    def s3(self) -> tuple[dict[str, Any], str]:
        tokenizer = (self.ctx.get("components") or {}).get("tokenizer") or {}
        if not tokenizer.get("ok"):
            raise StepSkipped("the tokenizer is not ready on this stack")
        policy = {
            "preset": "gdpr-pseudonymise",
            "treat": {"SSN": {"method": "external_tokenize", "profile": "ssn"}},
        }
        out = self.c.post("/obfuscate", {"data": {"ssn": SSN}, "policy": policy})
        token = str((out.get("result") or {}).get("ssn") or "")
        issued = token.startswith("tk_")
        back = self.c.post(
            "/reveal",
            {
                "data": out.get("result"),
                "key_material": out.get("key_material") or {},
                "reveal_hints": out.get("reveal_hints") or [],
            },
        )
        identical = back.get("result") == {"ssn": SSN}
        del out, token, back
        check(issued, "no tk_ token issued")
        check(identical, "reveal did not restore the SSN")
        return {
            "token_issued": issued,
            "reveal_identical": identical,
        }, "tk_ token; reveal identical"

    # S4 ---------------------------------------------------------------------------------------
    def s4(self) -> tuple[dict[str, Any], str]:
        name = "kyc-scanned.pdf"
        response = self.c.raw(
            "POST",
            "/documents/obfuscate",
            files={"file": (name, (DEMO_DOCS / name).read_bytes(), "application/pdf")},
            data={"request": json.dumps({"options": {"redacted_pdf": True, "ocr": "always"}})},
            timeout=300.0,
        )
        if response.status_code != 200:
            raise DemoError("POST", "/api/v1/documents/obfuscate", response.status_code)
        body = response.json()
        document = body.get("document") or {}
        redaction = document.get("redaction") or {}
        ocr_pages = len(document.get("ocr_pages") or [])
        findings = int((body.get("report") or {}).get("findings_total") or 0)
        pdf = base64.b64decode(body.get("redacted_pdf_b64") or "")
        del body
        check(ocr_pages >= 1, "OCR read no page")
        check(pdf.startswith(b"%PDF"), "the redacted PDF is not a PDF")
        chars = _pdf_text_chars(pdf)
        hits = redaction.get("ocr_hits")
        check(chars == 0, f"{chars} characters extractable from the redacted PDF")
        check(redaction.get("ocr_checked") is True and hits == 0, f"OCR re-read hits {hits}")
        facts = {
            "ocr_pages": ocr_pages,
            "findings": findings,
            "boxes": redaction.get("boxes"),
            "extractable_chars": chars,
            "originals_left": hits,
            "redaction_passed": redaction.get("passed"),
        }
        return facts, f"{ocr_pages} OCR pages, {findings} findings, 0 characters, 0 originals"

    # S5 ---------------------------------------------------------------------------------------
    def _connections(self) -> dict[str, str]:
        if "conns" not in self.ctx:
            found: dict[str, str] = {}
            cursor: str | None = None
            while True:
                params: dict[str, Any] = {"limit": 200, **({"cursor": cursor} if cursor else {})}
                page = self.c.get("/connections", **params)
                found.update({str(i["name"]): str(i["id"]) for i in page.get("items", [])})
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            self.ctx["conns"] = found
        conns: dict[str, str] = self.ctx["conns"]
        return conns

    def s5(self) -> tuple[dict[str, Any], str]:
        conns = self._connections()
        needed = {m.source for m in self.members} | {m.target for m in self.members}
        missing = sorted(needed - set(conns))
        check(not missing, f"connections missing (run tools/demo_seed.py): {missing}")
        jobs: list[tuple[Member, str]] = []
        for m in self.members:
            made = self.c.post(
                "/jobs/discover",
                {"connection_id": conns[m.source], "objects": [m.obj], "policy": POLICY_REF},
            )
            jobs.append((m, str(made["job"]["id"])))
        snaps: list[dict[str, Any]] = []
        for m, job_id in jobs:
            job = self.c.wait_job(job_id, timeout=JOB_TIMEOUT)
            check(job.get("state") == "succeeded", f"{m.engine} discovery {job.get('state')}")
            snap_id = str(((job.get("progress") or {}).get("snapshot_ids") or [None])[0])
            snap = self.c.get(f"/snapshots/{snap_id}")
            columns = snap.get("columns") or []
            sensitive = [c for c in columns if c.get("entity_type")]
            heat = all(isinstance(c.get("value_hit_rate"), int | float) for c in columns)
            check(len(sensitive) >= 5, f"{m.engine}: {len(sensitive)} sensitive columns")
            check(heat and bool(columns), f"{m.engine}: no heat-map data")
            join_path = customer_id_path(columns)
            check(join_path is not None, f"{m.engine}: no customer id column to join on")
            self.ctx.setdefault("join_paths", {})[m.engine] = join_path
            self.c.post(f"/snapshots/{snap_id}/accept")
            snaps.append(
                {
                    "engine": m.engine,
                    "snapshot_id": snap_id,
                    "job_id": job_id,
                    "columns": len(columns),
                    "sensitive_columns": len(sensitive),
                    "heat_map": heat,
                }
            )
        self.ctx["snapshots"] = {s["engine"]: s["snapshot_id"] for s in snaps}
        detail = ", ".join(f"{s['engine']} {s['sensitive_columns']}/{s['columns']}" for s in snaps)
        return {"snapshots": snaps}, f"accepted; sensitive/columns: {detail}"

    # S6 ---------------------------------------------------------------------------------------
    def s6(self) -> tuple[dict[str, Any], str]:
        conns = self._connections()
        snaps = self.ctx.get("snapshots") or {}
        key = self.group.get("key_material")
        check(isinstance(key, dict) and bool(key), "the group file holds no key material")
        submitted: list[tuple[Member, str]] = []
        for m in self.members:
            spec = {
                "mode": "obfuscate",
                "snapshot_id": snaps[m.engine],
                "group_id": self.group["group_id"],
                "purpose": PURPOSE,
                # the snapshot was discovered under the tenant policy: a run names it again
                # (a run without ``policy`` resolves the snapshot's policy name as a preset)
                "policy": POLICY_REF,
                "source": {
                    "connection_id": conns[m.source],
                    "object": m.obj,
                    "parallelism": 2,
                    "partition_rows": PARTITION_ROWS,
                },
                "target": {
                    "write_mode": "table",
                    "connection_id": conns[m.target],
                    "object": m.target_obj,
                    "create_if_missing": True,
                    "table_load": "truncate_insert",
                },
                "consistency": {"scope": "key", "key_material": key},
            }
            made = self.c.post("/jobs/run", spec)
            submitted.append((m, str(made["job"]["id"])))
            del made
        del key
        jobs: list[dict[str, Any]] = []
        deadline = time.monotonic() + JOB_TIMEOUT
        for m, job_id in submitted:
            job = self.c.wait_job(job_id, timeout=max(1.0, deadline - time.monotonic()))
            check(
                job.get("state") == "succeeded",
                f"{m.engine} run {job.get('state')} {job.get('error_code') or ''}".strip(),
            )
            progress = job.get("progress") or {}
            parts = self.c.get(f"/jobs/{job_id}/partitions").get("items") or []
            done = sum(1 for p in parts if p.get("state") == "done")
            check(
                progress.get("partitions_done") == progress.get("partitions_total") == len(parts)
                and done == len(parts),
                f"{m.engine}: {done}/{len(parts)} partitions done",
            )
            pack = self._pack(job)
            verification = pack.get("verification") or {}
            label = str(pack.get("label") or "")
            check(verification.get("passed") is True, f"{m.engine}: leakage sweep not passed")
            check(label.startswith("pseudonymised"), f"{m.engine}: pack label {label!r}")
            counts, timings = pack.get("counts") or {}, pack.get("timings") or {}
            jobs.append(
                {
                    "engine": m.engine,
                    "job_id": job_id,
                    "evidence_id": pack.get("id"),
                    "state": "succeeded",
                    "counts": {
                        "rows_written": counts.get("rows_written"),
                        "partitions": counts.get("partitions"),
                    },
                    "timings": {
                        "rows_per_s": timings.get("rows_per_s"),
                        "duration_s": timings.get("duration_s"),
                    },
                    "verification": {
                        "passed": True,
                        "hits": verification.get("hits"),
                        "review_required": verification.get("review_required"),
                    },
                    "kid_matches_group": (pack.get("consistency") or {}).get("kid")
                    == (self.group.get("key_material") or {}).get("kid"),
                }
            )
        check(all(j["kid_matches_group"] for j in jobs), "a pack is not under the group's key id")
        self.ctx["runs"] = jobs
        detail = ", ".join(f"{j['engine']} {j['counts']['rows_written']} rows" for j in jobs)
        return {"jobs": jobs, "group_id": self.group["group_id"]}, f"verified; {detail}"

    def _pack(self, job: Mapping[str, Any]) -> dict[str, Any]:
        evidence_id = (job.get("progress") or {}).get("evidence_id")
        check(bool(evidence_id), f"job {job.get('id')} has no evidence pack")
        pack: dict[str, Any] = self.c.get(f"/evidence/{evidence_id}")["pack"]
        return pack

    # S7 ---------------------------------------------------------------------------------------
    def s7(self) -> tuple[dict[str, Any], str]:
        runs = self.ctx.get("runs") or []
        wanted = consistency_pairs(runs, self.ctx.get("join_paths") or {})
        result = self.c.post(
            f"/job-groups/{self.group['group_id']}/consistency-check",
            {"pairs": wanted, "sample": CHECK_SAMPLE},
        )
        members = [
            {k: m.get(k) for k in ("engine", "job_id", "path", "form")}
            for m in result.get("members") or []
        ]
        pairs = [
            {k: p.get(k) for k in ("left", "right", "checked", "matched", "coverage", "consistent")}
            for p in result.get("pairs") or []
        ]
        joined = sorted(str(m["job_id"]) for m in members)
        check(
            joined == sorted(str(r["job_id"]) for r in runs),
            f"the check joined {len(members)} members, not this run's S6 jobs",
        )
        n = len(self.members)
        check(len(members) == n, f"{len(members)} members ({n} expected)")
        check(len(pairs) == n * (n - 1) // 2, f"{len(pairs)} pairs")
        check(
            all(p["consistent"] is True and int(p["matched"] or 0) > 0 for p in pairs),
            "a pair is not consistent",
        )
        check(result.get("passed") is True, "the consistency check did not pass")
        facts = {
            "members": members,
            "pairs": pairs,
            "sampled": result.get("sampled"),
            "all_members_matched": result.get("all_members_matched"),
            "consistent_pairs": result.get("consistent_pairs"),
            "total_pairs": result.get("total_pairs"),
            "passed": True,
        }
        return facts, (
            f"{result.get('consistent_pairs')}/{result.get('total_pairs')} pairs consistent, "
            f"{result.get('sampled')} sampled, {result.get('all_members_matched')} in every system"
        )

    # S8 ---------------------------------------------------------------------------------------
    def s8(self) -> tuple[dict[str, Any], str]:
        conns = self._connections()
        pg = MEMBERS[0]
        spec = {
            "mode": "synthesize",
            "snapshot_id": self.ctx["snapshots"]["postgres"],
            "purpose": PURPOSE,
            "policy": {
                "preset": "synthetic-realistic",
                "synthesize": {"utility_label": UTILITY_LABEL},
            },
            "source": {"connection_id": conns[pg.source], "object": pg.obj, "parallelism": 2},
            "target": {
                "write_mode": "table",
                "connection_id": conns[pg.target],
                "object": SYNTH_TARGET,
                "create_if_missing": True,
                "table_load": "truncate_insert",
            },
            "options": {"tabular": "copula"},
        }
        made = self.c.post("/jobs/run", spec)
        job_id = str(made["job"]["id"])
        del made  # its key and seed are never kept
        job = self.c.wait_job(job_id, timeout=JOB_TIMEOUT)
        check(
            job.get("state") == "succeeded",
            f"synthesize {job.get('state')} {job.get('error_code') or ''}".strip(),
        )
        pack = self._pack(job)
        fidelity, privacy = pack.get("fidelity") or {}, pack.get("privacy") or {}
        utility, counts = pack.get("utility") or {}, pack.get("counts") or {}
        nndr = privacy.get("nndr") or {}
        mia = privacy.get("membership_inference") or {}
        score = fidelity.get("score")
        check(isinstance(score, int | float) and score >= 0.85, f"fidelity {score}")
        check(privacy.get("exact_row_matches") == 0, "exact-row matches")
        check(bool(nndr.get("measured")), "NNDR not measured")
        self.ctx["synthetic"] = {"job_id": job_id, "evidence_id": pack.get("id")}
        facts = {
            "job_id": job_id,
            "evidence_id": pack.get("id"),
            "label": pack.get("label"),
            "counts": {"rows_written": counts.get("rows_written")},
            "fidelity": {"score": score},
            "privacy": {
                "exact_row_matches": privacy.get("exact_row_matches"),
                "nndr": {k: nndr.get(k) for k in ("ratio", "target", "measured")},
                "membership_inference": {k: mia.get(k) for k in ("run", "auc", "target")},
            },
            "utility": {k: utility.get(k) for k in ("run", "ratio", "metric")},
        }
        return facts, (
            f"fidelity {score}, exact-row matches 0, NNDR {nndr.get('ratio')} "
            f"(target {nndr.get('target')}), {counts.get('rows_written')} rows"
        )

    # S9 ---------------------------------------------------------------------------------------
    def _received(self) -> dict[str, int]:
        body = self.site.get("/__received").json()
        out = {name: int(body[name]) for name in SITE_COUNTERS}
        if SITE_SERVED in body:
            out[SITE_SERVED] = int(body[SITE_SERVED])
        return out

    def s9(self) -> tuple[dict[str, Any], str]:
        if self.args.skip_scanner:
            raise StepSkipped("--skip-scanner")
        browser = (self.c.get("/readyz").get("components") or {}).get("browser") or {}
        deadline = time.monotonic() + 60
        while not browser.get("ok") and time.monotonic() < deadline:
            time.sleep(3)
            browser = (self.c.get("/readyz").get("components") or {}).get("browser") or {}
        check(bool(browser.get("ok")), f"browser worker {browser.get('detail')}")
        before = self._received()
        made = self.c.post(
            "/scans",
            {
                "url": self.args.scan_url,
                "scope": {"max_pages": 25, "max_depth": 3, "deny": ["/logout", "/admin"]},
                "auth": {"storage_state_ref": "dps/ui/demo-onboarding"},
                "mode": "synthesize",
                "fill": {"personas": 3, "locale": "en_IN"},
                "purpose": {"ticket": "QA-ONB-7", "legal_basis": "legitimate interest - QA"},
            },
        )
        scan_id = str(made["scan"]["id"])
        del made  # key and seed: never kept
        scan = self.c.wait_job(scan_id, timeout=SCAN_TIMEOUT, kind="scans")
        check(scan.get("state") == "succeeded", f"scan {scan.get('state')}")
        after = self._received()
        pages = self.c.get(f"/scans/{scan_id}/pages", limit=200).get("items") or []
        both = [
            p
            for p in pages
            if {"filled_synthesize", "filled_obfuscate"} <= set(p.get("artefacts") or {})
        ]
        check(len(pages) >= 2, f"{len(pages)} pages")
        check(bool(both), "no page has both filled screens")
        shown = next(
            (p for p in both if str(p.get("url_redacted", "")).endswith("/apply/identity")), both[0]
        )
        csv = self.c.download(f"/scans/{scan_id}/export", format="csv").decode("utf-8", "replace")
        header = csv.splitlines()[0] if csv else ""
        check("," in header, "the CSV export has no header")
        export_hits = found_in([csv], SITE_VALUES)
        check(export_hits == 0, f"{export_hits} demo-site values in the export")
        evidence = self.c.get("/evidence", job_id=scan_id).get("items") or []
        check(bool(evidence), "the scan has no evidence pack")
        pack = self.c.get(f"/evidence/{evidence[0]['id']}")["pack"]
        block = pack.get("scan") or {}
        check(block.get("submissions") == 0, f"submissions {block.get('submissions')}")
        passed, d4, detail = d4_verdict(before, after, block)
        check(passed, f"D4: {detail}")
        self.ctx["scan"] = {
            "scan_id": scan_id,
            "page_no": shown.get("page_no"),
            "evidence_id": evidence[0]["id"],
        }
        facts = {
            "scan_id": scan_id,
            "evidence_id": evidence[0]["id"],
            "pages_listed": len(pages),
            "pages_with_both_fills": len(both),
            "export_csv_rows": max(len(csv.splitlines()) - 1, 0),
            "export_site_values": export_hits,
            **d4,
        }
        return facts, f"{len(pages)} pages, {len(both)} with both fills; D4 {detail}"

    # S10 --------------------------------------------------------------------------------------
    def _demo_job_ids(self) -> list[str]:
        ids = [j["job_id"] for j in self.ctx.get("runs") or []]
        if self.ctx.get("synthetic"):
            ids.append(self.ctx["synthetic"]["job_id"])
        if self.ctx.get("scan"):
            ids.append(self.ctx["scan"]["scan_id"])
        return ids

    def s10(self) -> tuple[dict[str, Any], str]:
        bodies: list[str] = []
        jobs = self.c.raw("GET", "/reports/jobs", params={"days": 1})
        check(jobs.status_code == 200, f"/reports/jobs HTTP {jobs.status_code}")
        bodies.append(jobs.text)
        listed = self.c.raw("GET", "/reports/evidence", params={"days": 1, "limit": 200})
        check(listed.status_code == 200, f"/reports/evidence HTTP {listed.status_code}")
        bodies.append(listed.text)
        wanted = self._demo_job_ids()
        packs = {
            str(i.get("job_id")): i
            for i in (listed.json().get("items") or [])
            if isinstance(i, dict)
        }
        shown = [j for j in wanted if j in packs]
        check(len(shown) == len(wanted), f"{len(shown)}/{len(wanted)} demo packs listed")
        pdf_of = (self.ctx.get("synthetic") or {}).get("evidence_id") or (
            (self.ctx.get("runs") or [{}])[0].get("evidence_id")
        )
        pdf = self.c.download(f"/evidence/{pdf_of}/pdf")
        pdf_ok = pdf.startswith(b"%PDF")
        check(pdf_ok, "the evidence PDF is not a PDF")
        audit_rows = 0
        for job_id in wanted:
            rows = self.c.raw("GET", "/admin/audit", params={"q": job_id, "limit": 200})
            check(rows.status_code == 200, f"/admin/audit HTTP {rows.status_code}")
            bodies.append(rows.text)
            got = len(rows.json().get("items") or [])
            check(got >= 1, f"no audit row for job {job_id}")
            audit_rows += got
        values = [*leaves(SAMPLES["json"]), SSN, *SITE_VALUES]
        hits = found_in(bodies + [_pdf_text(pdf)], values)
        check(hits == 0, f"{hits} input values in report, audit or PDF bodies")
        facts = {
            "packs_listed": len(shown),
            "audit_rows": audit_rows,
            "pdf_ok": pdf_ok,
            "pdf_bytes": len(pdf),
            "input_values_found": hits,
        }
        return facts, f"{len(shown)} packs listed, PDF ok, {audit_rows} audit rows, 0 values"

    # S11 --------------------------------------------------------------------------------------
    def s11(self) -> tuple[dict[str, Any], str]:
        posture = self.c.get("/admin/posture")
        total = posture.get("total")
        check(total == 11, f"{total} posture checks (11 expected)")
        made = self.c.post(
            "/admin/keys",
            {
                "name": "demo-rotation-check",
                "tenant_ids": [self.args.tenant],
                "scopes": ["detect"],
                "expires_in_days": 1,
            },
        )
        old_key, old_id = str(made["key"]), str(made["record"]["id"])
        del made
        _works(self.c, old_key, self.args.tenant)
        rotated = self.c.post(f"/admin/keys/{old_id}/rotate", {"grace_hours": 0})
        new_id = str(((rotated.get("new") or {}).get("record") or {}).get("id"))
        del rotated
        status = _status_with(self.c, old_key, self.args.tenant)
        del old_key
        self.c.post(f"/admin/keys/{new_id}/disable")  # nothing of this check stays usable
        check(status == 401, f"the rotated key answered HTTP {status}")
        job = self.admin.post("/admin/retention/run", {"dry_run": True})["job"]
        state = _retention_state(self.admin, str(job["id"]))
        check(state == "succeeded", f"retention {state}")
        facts = {
            "posture": {
                "passed": posture.get("passed"),
                "total": total,
                "enforced": posture.get("enforced"),
                "env": posture.get("env"),
            },
            "rotated_key_status": status,
            "retention_state": state,
            "retention_job_id": job["id"],
        }
        return facts, (
            f"posture {posture.get('passed')}/{total} (enforced {posture.get('enforced')}, "
            f"env {posture.get('env')}); rotated key HTTP {status}; retention {state}"
        )


def detected_values(record: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> list[str]:
    """S2's synthetic-leak probes: every string leaf of every top-level field a finding points
    into — a nested object such as the sample's ``card`` gives its ``pan`` and ``expiry``, never
    its repr (review S6b r1 B2); a span finding gives its whole field."""
    keys = {
        key
        for f in findings
        if isinstance(key := _top_key(f.get("locator")), str) and key in record
    }
    return sorted({value for key in keys for value in leaves(record[key])})


def customer_id_path(columns: Sequence[Mapping[str, Any]]) -> str | None:
    """The column S7 joins a run on: the snapshot's customer-id column the server would join (a
    whole-value mapping, not ``keep``, not free text), the key first, then the anchor, then the
    shortest path — ``consistency._mapped``'s order. ``None`` when the snapshot has none."""
    usable = [
        c
        for c in columns
        if c.get("entity_type") == JOIN_ENTITY
        and not c.get("text_column")
        and (c.get("method") or {}).get("method") != "keep"
    ]
    usable.sort(
        key=lambda c: (not c.get("is_key"), not c.get("anchor"), len(str(c["path"])), c["path"])
    )
    return str(usable[0]["path"]) if usable else None


def consistency_pairs(
    runs: Sequence[Mapping[str, Any]], join_paths: Mapping[str, str]
) -> list[dict[str, str]]:
    """S7's members: one per S6 job, its id and the customer-id path of the snapshot S5 accepted
    for its system; the last job submitted (Oracle on a full run) first — the anchor whose target
    the check samples. Never derived from the group's history: an older run, or a target since
    reset, is not this run's (review S6b r1 B3)."""
    pairs: list[dict[str, str]] = []
    for run in reversed(runs):
        engine = str(run["engine"])
        path = join_paths.get(engine)
        check(bool(path), f"{engine}: no customer id column to join on")
        pairs.append({"job_id": str(run["job_id"]), "path": str(path)})
    return pairs


def _top_key(locator: Any) -> str | None:
    """The top-level field of a finding's JSON path (``$.aadhaar`` → ``aadhaar``)."""
    path = locator.get("path") if isinstance(locator, dict) else None
    if not isinstance(path, str) or not path.startswith("$."):
        return None
    return path[2:].split(".")[0].split("[")[0]


def _pdf_text(pdf: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _pdf_text_chars(pdf: bytes) -> int:
    """Characters a text extractor finds in ``pdf`` (needs pypdf; -1 when it is missing)."""
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return -1
    return len(_pdf_text(pdf).strip())


def _status_with(c: DpsClient, key: str, tenant: str) -> int:
    probe = DpsClient(c.base_url, key, tenant=tenant)
    try:
        return probe.raw("POST", "/detect", json={"text": "x"}).status_code
    finally:
        probe.close()


def _works(c: DpsClient, key: str, tenant: str, wait: float = 12.0) -> None:
    deadline = time.monotonic() + wait
    while _status_with(c, key, tenant) != 200:
        check(time.monotonic() < deadline, "a new key did not authenticate")
        time.sleep(0.5)


def _retention_state(admin: DpsClient, job_id: str, wait: float = 300.0) -> str:
    """``succeeded`` once the job's ``retention.run`` audit row exists (the job belongs to the
    system tenant, which no tenant route reads), ``timeout`` otherwise."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        rows = (
            admin.get("/admin/audit", tenants="*", action="retention.run", limit=50).get("items")
            or []
        )
        mine = [r for r in rows if r.get("job_id") == job_id]
        if mine:
            return "succeeded" if mine[0].get("outcome") in ("ok", "dry_run") else "failed"
        time.sleep(2.0)
    return "timeout"


# -- S12: the console walk -----------------------------------------------------------------------


def console_walk(
    base_url: str,
    key: str,
    tenant: str,
    ids: Mapping[str, Any],
    screens: Path,
    *,
    timeout_ms: float = 15_000,
) -> tuple[dict[str, Any], str]:
    """Walk :data:`PAGES` in both themes; screenshots to ``screens``; value-free facts."""
    from playwright.sync_api import sync_playwright

    screens.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    shots = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for theme in THEMES:
                ctx = browser.new_context(
                    viewport={"width": 1440, "height": 900}, color_scheme=theme
                )
                ctx.add_init_script(
                    script="try {"
                    f" sessionStorage.setItem('dps.apiKey', {json.dumps(key)});"
                    f" localStorage.setItem('dps.tenant', {json.dumps(tenant)});"
                    f" localStorage.setItem('dps.theme', {json.dumps(theme)});"
                    "} catch (e) {}"
                )
                page = ctx.new_page()
                _collect_errors(page, theme, errors)
                for name, route, selector, text in PAGES:
                    url = base_url + route.format(**ids)
                    page.goto(url, wait_until="networkidle")
                    page.locator(selector, has_text=text).first.wait_for(timeout=timeout_ms)
                    masks = _playground_result(page, timeout_ms) if name == "playground" else []
                    page.wait_for_load_state("networkidle")
                    page.screenshot(
                        path=str(screens / f"{name}-{theme}.png"),
                        full_page=False,
                        mask=masks,
                        mask_color=MASK_COLOR,
                    )
                    shots += 1
                ctx.close()
        finally:
            browser.close()
    check(not errors, f"{len(errors)} console errors ({', '.join(sorted(set(errors)))})")
    facts = {"pages": len(PAGES), "themes": len(THEMES), "screenshots": shots, "console_errors": 0}
    return facts, f"{len(PAGES)} pages × {len(THEMES)} themes, {shots} screenshots, 0 errors"


def _collect_errors(page: Any, theme: str, errors: list[str]) -> None:
    """Record console errors and uncaught page errors of ``page`` (their kind only)."""

    def on_console(message: Any) -> None:
        if message.type == "error":
            errors.append(f"{theme} console error")

    def on_page_error(_error: Any) -> None:
        errors.append(f"{theme} page error")

    page.on("console", on_console)
    page.on("pageerror", on_page_error)


def _playground_result(page: Any, timeout_ms: float) -> list[Any]:
    """Run **Obfuscate** on the Playground's KYC sample: the findings, the format-kept tokens and
    the key card. The card prints the start and the end of the data key, so that text is masked
    in the screenshot (a screenshot never carries key material); returns the locators to mask."""
    page.get_by_role("button", name="Obfuscate", exact=True).click()
    page.locator(".keycard").first.wait_for(timeout=timeout_ms)
    dek = page.locator(".keycard span", has_text=re.compile(r"^dek "))
    check(dek.count() == 1, "the key card's data key is not where the screenshot mask expects it")
    return [dek]


# -- driver ---------------------------------------------------------------------------------------


def run_steps(
    steps: Sequence[tuple[str, str, Callable[[], tuple[dict[str, Any], str]] | None]],
    *,
    clock: Callable[[], float] = time.monotonic,
    echo: Callable[[str], None] = print,
) -> list[StepResult]:
    """Run ``steps`` in order; ``None`` as a body lists the step ``SKIP`` (not run by choice).
    The first ``FAIL``/``ERROR`` stops the run; later steps are ``NOT RUN``."""
    results: list[StepResult] = []
    stopped = False
    for sid, title, body in steps:
        if stopped:
            results.append(StepResult(sid, title, "NOT RUN"))
            continue
        if body is None:
            results.append(StepResult(sid, title, "SKIP", detail="not requested"))
            echo(f"SKIP  {sid} {title}")
            continue
        start = clock()
        try:
            facts, detail = body()
            result = StepResult(sid, title, "PASS", clock() - start, facts, detail)
        except StepSkipped as exc:
            result = StepResult(sid, title, "SKIP", clock() - start, {}, str(exc))
        except StepFailed as exc:
            result = StepResult(sid, title, "FAIL", clock() - start, {}, str(exc))
            stopped = True
        except DemoError as exc:  # value-free by construction (route template, status, code)
            result = StepResult(sid, title, "ERROR", clock() - start, {}, str(exc))
            stopped = True
        except Exception as exc:  # noqa: BLE001 - e.g. Playwright's TimeoutError in S12
            # an answer of an unexpected shape, a browser that timed out: the class only (a
            # message could quote a body); the run still writes its report (review S6b r1 N1)
            result = StepResult(sid, title, "ERROR", clock() - start, {}, type(exc).__name__)
            stopped = True
        results.append(result)
        echo(f"{result.status:<5} {sid} {title} ({result.seconds:.1f}s) — {result.detail}")
    return results


def parse_args(argv: Sequence[str] | None, env: Mapping[str, str]) -> argparse.Namespace:
    """Command line (module docstring)."""
    p = argparse.ArgumentParser(prog="python tools/e2e_demo.py", description=__doc__.split("\n")[0])
    p.add_argument(
        "--base-url",
        default=(
            env.get("DPS_BASE_URL")
            or env.get("DPS_TEST_APP_URL")
            or f"http://127.0.0.1:{env.get('DPS_APP_PORT') or '8400'}"
        ).rstrip("/"),
    )
    p.add_argument("--tenant", default=DEFAULT_TENANT)
    p.add_argument("--keys", default=str(DEFAULT_KEYS), help="the demo seed's key file")
    p.add_argument("--group-file", default=str(DEFAULT_GROUP))
    p.add_argument(
        "--admin-key",
        default=env.get("DPS_API_KEY") or env.get("DPS_BOOTSTRAP_API_KEY") or BOOTSTRAP_KEY,
        help="a key for every tenant (the cross-tenant retention run of S11)",
    )
    p.add_argument(
        "--demo-site",
        default=(
            env.get("DPS_TEST_DEMO_SITE")
            or f"http://127.0.0.1:{env.get('DPS_DEMO_SITE_PORT') or '8410'}"
        ).rstrip("/"),
        help="the demo site as this script reaches it (its /__received counters)",
    )
    p.add_argument("--scan-url", default=SCAN_URL, help="the scan's start URL (browser-worker)")
    p.add_argument("--console", action="store_true", help="add S12, the console walk")
    p.add_argument("--skip-scanner", action="store_true")
    p.add_argument("--skip-oracle", action="store_true")
    p.add_argument("--out", default=str(DEFAULT_MD))
    p.add_argument("--json", default=str(DEFAULT_JSON))
    p.add_argument("--screens", default=str(DEFAULT_SCREENS))
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the demo; write both reports; 0 when every step passed or was skipped."""
    args = parse_args(argv, os.environ)
    started, generated = time.monotonic(), datetime.now(UTC)
    try:
        key = load_keys(Path(args.keys))[KEY_NAME]
    except (OSError, KeyError, ValueError) as exc:
        print(f"FAIL  {args.keys}: no {KEY_NAME} key ({type(exc).__name__}); run demo_seed first")
        return 1
    group_path = Path(args.group_file)
    try:
        group = json.loads(group_path.read_text("utf-8"))
    except (OSError, ValueError):
        print(f"FAIL  {group_path}: no job group file; run tools/demo_seed.py first")
        return 1
    c = DpsClient(args.base_url, key, tenant=args.tenant, timeout=300.0)
    admin = DpsClient(args.base_url, args.admin_key, timeout=120.0)
    site = httpx.Client(base_url=args.demo_site, timeout=10.0)
    demo = Demo(c, admin, site, args, group)

    def walk() -> tuple[dict[str, Any], str]:
        runs = demo.ctx.get("runs") or []
        ids = {
            "job": runs[0]["job_id"] if runs else "",
            "scan": (demo.ctx.get("scan") or {}).get("scan_id", ""),
            "page": (demo.ctx.get("scan") or {}).get("page_no", 1),
            "evidence": (demo.ctx.get("synthetic") or {}).get("evidence_id", ""),
        }
        return console_walk(args.base_url, key, args.tenant, ids, Path(args.screens))

    bodies: dict[str, Callable[[], tuple[dict[str, Any], str]] | None] = {
        "S1": demo.s1,
        "S2": demo.s2,
        "S3": demo.s3,
        "S4": demo.s4,
        "S5": demo.s5,
        "S6": demo.s6,
        "S7": demo.s7,
        "S8": demo.s8,
        "S9": demo.s9,
        "S10": demo.s10,
        "S11": demo.s11,
        "S12": walk if args.console else None,
    }
    try:
        results = run_steps([(sid, title, bodies[sid]) for sid, title in STEPS])
    finally:
        c.close()
        admin.close()
        site.close()
    if not args.console:
        results = [r for r in results if r.id != "S12"]
    total = time.monotonic() - started
    doc = report_json(
        results,
        tenant=args.tenant,
        version=str(demo.ctx.get("version") or "unknown"),
        commit=git_commit(),
        total=total,
        generated_at=generated,
    )
    out_json, out_md = Path(args.json), Path(args.out)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(report_markdown(doc, base_url=args.base_url), encoding="utf-8")
    ok = all(r.status in ("PASS", "SKIP") for r in results)
    print(f"\n{'PASS' if ok else 'FAIL'}: demo flow completed in {mmss(total)} — {out_md}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
