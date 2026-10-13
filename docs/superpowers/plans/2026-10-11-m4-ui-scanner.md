# DPS M4 — UI scanner: browser worker, crawl, classify, parallel view (never submitted), captures, console — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship milestone M4 of Data Privacy Services: a `browser-worker` role (`python -m dps.browser_worker`) that claims `scan` jobs from the durable queue, crawls a registered web front end with Playwright Chromium (same-origin, depth/page/time budgets, deny list, SPA routes found by clicking, open shadow DOM and same-origin iframes), inventories and classifies every form field, flags privacy risks, renders a **parallel view** of every form filled with synthetic personas and with reversible tokens — **never submitting anything** (decision D4: no submit option exists anywhere, and a request guard aborts every non-GET request and every navigation from a filled page, proven by a test against a demo site that counts what it receives) — produces obfuscated captures of pages that render personal data, exports the persona set and the field→entity→value mapping, and shows all of it in the console pages built to mockups 12 and 04.

**Architecture:** One new process role on its own image (`Dockerfile.browser`, `FROM mcr.microsoft.com/playwright:v1.55.0-noble`) running M2's `Consumer` with `kinds=(scan,)`. The scan handler (`dps/layers/scanner/__init__.py::scan_job`) opens the job's sealed secret (key material, synthesis seed, start URL, optional inline storage state), builds one isolated `BrowserContext` with the `ScanGuard` installed before the first page exists, and runs a `ScanRunner` BFS: per page it extracts the DOM (JS in `dom.py`), classifies fields (`classify.py`: `autocomplete` token → structural dictionary → input type/pattern → value detectors), computes risk flags (`inventory.py`), captures the original screenshot (rendered personal data masked with boxes before encoding), fills the page twice in fresh tabs (`personas_fill.py`: persona values from the M0 surrogate engine; tokens from the M0 transform engine via `apply_plan`), captures each filled screen with numbered overlays (`capture.py`, Pillow), makes an obfuscated capture where personal data is rendered, discovers links and click-only SPA routes, and persists value-free rows (`store.py`, migrations 030–031) plus PNG/JSON/CSV artefacts through M2's `ArtefactService`. A finalize step builds the export artefacts and a `dps.evidence.v1` pack with the `scan` block, then purges the key. The API (`routers/scans.py`) creates scans (returns key material and seed once), serves pages, fields, events, inventory and exports; the console adds `ScanNew` (mockup 12) and `ScanDetail` (mockup 04). A dependency-free Node demo site (`demo/onboarding/`) is the scan target for tests, the smoke test and the demo.

**Tech Stack:** Python 3.12, Playwright for Python **1.55.0** (Chromium build 1187, the build baked into the image), Pillow (overlays and masks), asyncpg (M2), FastAPI, pydantic v2; M0 engine (`Detector`, `classify_key`, `apply_plan`, `SurrogateEngine`, `verify_output`); Node 22 (`node:http`, no npm dependencies) for the demo site; React 18 + TypeScript for the console. Tooling: uv, ruff, mypy, pytest (+ pytest-asyncio), Docker Compose.

**Spec:** `docs/specs/2026-10-11-data-privacy-services-design.md` §8 (Layer 3), §9 (UI scanner page), §10 (safety rows), §11 (M4 row), decision D4. **Contract (binding):** `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§1, §2.2/§2.4 migrations 030–031, §3 jobs, §5.1 secret refs, §6 bridge, §7 evidence, §9 routes, §10 settings, §11 compose, §12 tests, §13 artefacts, §14 console). **Builds on:** M0 (engine, auth, observability, app factory, console shell) and M2 (DB, queue, workers, secrets, artefacts, evidence). M1 and M3 are not prerequisites.

**Contract changes made by this plan (each lands in the same commit as its code, as contract §0 requires):** §2.4 — migration 030 adds columns to `dps_scan_page`/`dps_scan_field`, a foreign key on `dps_scan_request.job_id`, the value-free `dps_scan_event` table (navigation log) and CHECK constraints (Task 3); §3.3 — `JobSecret.scan_start_url` (Task 4); §9 — `ScanSpec` additions (`policy`, `scope.iframes|shadow_dom|spa_clicks`, `fill.display_persona`) and two routes, `GET /scans/{id}/events` and `POST /scans/personas/preview` (Tasks 4 and 18); §10 — nine additional `scan_*` settings (Task 1). Nothing in the contract is contradicted; concerns are listed in "Open contract questions" at the end. **M2 names (revision 2026-10-11):** the contract now fixes the M2 interfaces this plan uses (contract §3.2, §3.5, §7.1, §13, §14, §16): `Consumer(..., ctx: HandlerContext, role=..., worker_id=None)` with `ctx` required and the role **passed**, `build_context(settings)`, `create_job(job_id=...)`, `render_html(pack, *, sha)` / `pack_sha256(pack)`, `ArtefactService(store, *, acquire)`, and the console's single `usePoll(load, intervalMs, active, deps)` hook plus `Page<T>`, `JobState`, `request`/`json`/`post`/`qs` from M2 Task 29.

---

## Conventions for every task

- Work in `/Users/neelu/document_intelligence/data-privacy-services` (its own git repo; never run git against the parent `~/document_intelligence`). Commits are authored by the global identity (NilenduGanguli), carry **no trailer of any kind** (no `Co-Authored-By`), use conventional-commit prefixes, and are made with an explicit pathspec: `git add <paths> && git commit -m "<msg>" -- <paths>`. Never `git add -A/.`, `commit -a`, `stash`, `reset`, `checkout --`, `clean`. If `.git/index.lock` exists, wait 3 s and retry (up to 10 times).
- Environment: `source .venv/bin/activate && uv pip install -e ".[dev,fpe,db,connectors,scanner]"`. Browser tests on the host additionally need the Chromium build that Playwright 1.55.0 drives: `python -m playwright install chromium` (one download of chromium-1187 + chromium-headless-shell-1187 into `~/Library/Caches/ms-playwright`; the builds already cached there — 1148, 1234, 1243 — belong to other Playwright versions and are not used).
- Gates per task (contract §0.4): `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q`; when `frontend/` changes: `cd frontend && npm run typecheck && npm run build`. Browser/DB tests skip when their service is unreachable unless `DPS_TEST_REQUIRE_SERVICES=1`; the milestone gate (Definition of Done) runs them with that flag.
- Services for integration tests: metadata Postgres from M2 (`tools/scratch_pg.sh start` on 5441, or `docker compose up -d dps-db`), the demo site (auto-started by `tests/scanner/conftest.py` with the host's `node` when 8410 is free, or `docker compose --profile scanner up -d demo-site`), Chromium (above).
- **Never log, print, store, or put in an exception message a page value, a URL with its query, a label as found on the page, a key, a seed, a token-to-original mapping or a storage state.** Everything persisted is either value-free (types, counts, shapes, redacted URLs, redacted labels) or synthetic (persona values and tokens of persona values). Page data lives only in worker memory for the duration of one page.
- Python: type hints everywhere, Google-style docstrings on public functions, line length 100, `from __future__ import annotations`. JavaScript snippets that run inside pages live as constants in `dps/layers/scanner/dom.py` and `guard.py` and are tested through the browser tests.
- **Import hygiene.** The app image installs `[fpe,docs,db,connectors,tabular]` — no Playwright, no Pillow (contract §11.1). Everything the API imports — `dps/layers/scanner/__init__.py`, `models.py`, `runtime.py`, `urls.py`, `classify.py`, `personas_fill.py`, `inventory.py`, `store.py` — must import neither at module level; Playwright and Pillow are imported only by `guard.py`, `browser.py`, `dom.py`, `capture.py`, `crawler.py`, `dps/browser_worker.py`, and inside function bodies (or under `TYPE_CHECKING`) elsewhere. Task 18's `test_api_imports_without_playwright_or_pillow` enforces it.

## What M4 uses from M0 and M2 (names as fixed by the M0 plan and the contract)

| From | Name | Used for |
|---|---|---|
| M0 `dps/engine/detect/ensemble.py` | `Detector`, `DetectorConfig`, `DetectionResult` | value detectors on pre-filled values, rendered text, query values, labels to redact |
| M0 `dps/engine/detect/structural.py` | `classify_key`, `normalize_key`, `KeyCandidate` | structural dictionary on labels, names, ids, placeholders, rendered row headers |
| M0 `dps/engine/findings.py` | `Finding`, `Locator`, `Plan`, `PlanEntry`, `hash_value` | plans for persona generation, tokens and captures |
| M0 `dps/engine/planner.py` | `build_plan` | obfuscate plans (policy methods + FPE fallbacks) |
| M0 `dps/engine/transform/apply.py` | `apply_plan`, `ApplyOptions` (+ M2's `anchor`), `RevealHint` | the only transformer (contract §0.3) |
| M0 `dps/engine/verify.py` | `verify_output`, `collect_originals`, `normalise`, `Verification` | leakage sweep of tokens and captures; guard needles |
| M0 `dps/engine/synth/surrogates.py` / `personas.py` | `SurrogateEngine`, `SynthesisSeed`, `generate_synthesis_seed`, `seed_bytes`, `seed_from_caller`, `seed_int`, `normalise_locale`, `SUPPORTED_LOCALES` | personas |
| M0 `dps/engine/transform/crypto.py` | `KeyMaterial`, `generate_key_material`, `key_material_from_caller`, `wrap_key_material` | scan key custody (D2) |
| M0 `dps/engine/taxonomy.py` | `entity_info`, `Sensitivity` | sensitivity of classified fields |
| M0 `dps/engine/policy.py` / `service.py` | `Policy`, `PrivacyEngine` (`policy()`, `fpe_enabled`, `cipher_factory`, `ner`) | policy resolution, engine options |
| M0 `dps/auth.py`, `dps/ratelimit.py`, `dps/audit.py` | `require_scope`, `enforce_rate_limit`, `Principal`, `AuditEvent`, `MemoryAuditSink` | route guards, audit |
| M0 `dps/observability.py` | `_SAFE_FIELDS`, `get_logger`, `READINESS`, `metrics_payload` | logging allow-list, readiness, metrics |
| M2 `dps/db.py` | `acquire`, `acquire_queue`, `db_configured`, `init_worker_pool`, `close_pools`, `TENANT_TABLES`, `assert_rls_posture` | metadata DB |
| M2 `dps/jobs.py` | `JobKind`, `JobPhase`, `JobState`, `Job`, `ClaimedJob`, `create_job`, `get_job`, `list_jobs`, `advance_job`, `cancel` | scan jobs are kind `scan`, phase `scan` (+ `finalize` on cancel) |
| M2 `dps/jobs_models.py` | `ConsistencySpec`, `Purpose`, `JobSecret`, `JobProgress` | spec pieces and sealed secrets (contract §3.3, §5.3) |
| M2 `dps/worker.py` | `Consumer` (`ctx` required, `role="browser-worker"`), `HandlerContext`, `build_context` | the browser-worker's claim/heartbeat/reaper loops (contract §3.5) |
| M2 `dps/secrets.py`, `dps/ids.py` | `load_kek`, `seal`, `open_sealed`, `resolve_secret_ref`, `new_id` | key custody, storage-state refs, ids |
| M2 `dps/storage.py` | `ArtefactService(store, *, acquire)` (`save`, `open`, `delete_for_job`) | screenshots, captures, exports, evidence HTML |
| M2 `dps/layers/connectors/evidence.py` | `EvidencePack`, `render_html(pack, *, sha)`, `pack_sha256(pack)` | scan evidence (contract §7) |
| M2 `dps/layers/api/errors.py` | `DpsHttpError` | `{"detail", "code"}` errors |

## M0/M2 touch points (changed together with their tests, contract §0.7)

| Module | Change | Task |
|---|---|---|
| `dps/config.py`, `dps/posture.py` | M4 settings; posture: unmasked originals and headed browsers refused in staging/prod | 1 |
| `dps/observability.py` | allow-list fields `job_id page_no pages fields depth decision resource_type blocked step persona phase kind elapsed_s`; four `dps_scan_*` metrics | 1 |
| `dps/db.py` | `TENANT_TABLES` += the four scan tables | 3 |
| `dps/jobs_models.py` | `ScanSpec` (+ sub-models) replaces any M2 placeholder; `JobSecret.scan_start_url` | 4 |
| `dps/engine/service.py` | public `PrivacyEngine.detector(policy, locale) -> Detector` (wraps the existing private builder) | 9 |
| `dps/engine/synth/surrogates.py` | `IFSC` surrogate becomes rule-shaped (`[A-Z]{4}0[A-Z0-9]{6}`) | 11 |
| `dps/layers/connectors/evidence.py` | HTML renderer prints the `scan` block when present | 16 |
| `dps/app.py` | include the scans router; CSP `img-src 'self' data: blob:` (screenshots are fetched with the API key and shown as blob URLs); `browser` readiness refreshed every 15 s from `dps_worker` heartbeats | 18 |
| `docker-compose.yml`, `.dockerignore`, `tools/smoke_test.py`, `HANDOFF.md`, `README.md` | `browser-worker` and `demo-site` services; scanner smoke checks; docs | 20, 24, 25 |

## File structure (what M4 creates or modifies)

| Path | Responsibility |
|---|---|
| `pyproject.toml` (modify) | `scanner` extra: `playwright==1.55.0`, `pillow>=10.4` |
| `dps/config.py`, `dps/posture.py`, `dps/observability.py` (modify) | settings, posture, allow-list, metrics |
| `dps/migrations/030_scans.sql`, `dps/migrations/031_tenant_hosts.sql` | scan pages/fields/requests/events with RLS; tenant registered hosts |
| `dps/jobs_models.py` (modify) | `ScanScope`, `ScanAuth`, `FillSpec`, `CaptureSpec`, `ScanSpec`; `JobSecret.scan_start_url` |
| `dps/layers/scanner/__init__.py` | `scan_job` and `scan_finalize_job` handlers; `DbScanSink`; `ScanRuntime` |
| `dps/layers/scanner/models.py` | DOM facts (memory only), records (persisted), API models, `validate_scan`, `redact_scan_spec` |
| `dps/layers/scanner/runtime.py` | `ScanSink`, `MemoryScanSink`, `ScanRuntime`, `RunOutcome`, `LeaseLost` — the Playwright-free types the handlers and the API share |
| `dps/layers/scanner/urls.py` | canonical URLs, crawl templates, scope verdicts, URL redaction |
| `dps/layers/scanner/guard.py` | `ScanGuard`, `decide()`, `HARDEN_JS` — the no-submission guarantee |
| `dps/layers/scanner/browser.py` | one Chromium per process; per-scan isolated contexts |
| `dps/layers/scanner/dom.py` | page-side JavaScript (extract, clickables, text rects, text replace, validation) + typed wrappers |
| `dps/layers/scanner/classify.py` | autocomplete map, field classification, rendered-text classification, text redaction |
| `dps/layers/scanner/personas_fill.py` | persona values, field formatting, tokens of persona values, `fill_page` |
| `dps/layers/scanner/capture.py` | screenshots, numbered overlays, mask boxes, obfuscated captures |
| `dps/layers/scanner/crawler.py` | `ScanRunner` (BFS, budgets, SPA clicks, per-page pipeline), `ScanSink`, `MemoryScanSink` |
| `dps/layers/scanner/inventory.py` | risk flags, inventory rows, export JSON/CSV, scan evidence pack |
| `dps/layers/scanner/store.py` | `ScanStore`: SQL for the scan tables |
| `dps/browser_worker.py` | process entry: Chromium, ops server on 9401, `Consumer(kinds=(scan,), ctx=build_context(settings), role="browser-worker")` |
| `dps/layers/api/routers/scans.py` | `/api/v1/scans…` routes |
| `dps/engine/service.py`, `dps/engine/synth/surrogates.py`, `dps/layers/connectors/evidence.py` (`render_html` gains the scan section), `dps/app.py`, `dps/db.py` (modify) | touch points above |
| `demo/onboarding/{server.mjs,site.mjs,data.json}`, `demo/onboarding/static/{app.css,forms.js,spa.js,postal-lookup.js}` | demo onboarding web app (contract §1 path) |
| `docker/secrets/dps/ui/demo-onboarding/storage_state.json` | local-only demo session for the demo site |
| `Dockerfile.browser`, `docker-compose.yml`, `.dockerignore` | browser-worker image; `browser-worker` and `demo-site` services |
| `frontend/src/lib/{types.ts,api.ts}` | scan types, `api.scans.*` (polling reuses M2's `frontend/src/hooks/usePoll.ts`, unchanged) |
| `frontend/src/components/scanner/*.tsx` | `ArtefactImage`, `NoSubmitBanner`, `CrawlMap`, `NavLog`, `CrawlProgress`, `ParallelView`, `FieldInventory`, `RiskFlags`, `PersonaPreview` |
| `frontend/src/pages/{ScanNew,ScanDetail}.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css` | the two scanner pages, routes, styles from mockups 12 and 04 |
| `tools/smoke_test.py` (modify) | scanner checks behind `DPS_SMOKE_SCANNER=1` |
| `tests/scanner/*`, `tests/test_config_m4.py`, `tests/test_no_values_in_logs_m4.py` | unit, DB, browser and end-to-end tests |
| `HANDOFF.md`, `README.md`, `docs/technical/api-m4.md`, contract (modify) | docs as built |

---

### Task 1: Scanner extra, settings, posture, logging allow-list and metrics

**Files:**
- Modify: `pyproject.toml`, `dps/config.py`, `dps/posture.py`, `dps/observability.py`, `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§10 additions)
- Test: `tests/test_config_m4.py`

**Interfaces introduced:**

```python
# dps/config.py — Settings additions (env DPS_<UPPER>; _FILE works for each)
browser_concurrency: int = 2                  # 1..16
browser_metrics_port: int = 9401
browser_headless: bool = True
scan_max_pages: int = 50                       # hard cap per scan, 1..500
scan_max_depth: int = 3                        # default depth when a scan omits it, 0..5
scan_page_timeout_seconds: int = 30
scan_job_timeout_seconds: int = 1800
scan_registered_hosts: str = "localhost,127.0.0.1,demo-site"   # local/dev fallback only
scan_store_unmasked_originals: bool = False
# M4 additions (contract §10 "may add")
scan_max_per_template: int = 3                 # visits per URL template (/help/faq/{id})
scan_max_spa_clicks_per_page: int = 25
scan_max_fields_per_page: int = 400
scan_max_skipped_rows: int = 200               # skipped-page rows kept for the crawl map
scan_max_events: int = 2000                    # navigation-log rows per scan
scan_settle_ms: int = 400                      # wait after load / after fill for client-side rendering
scan_viewport_width: int = 1280
scan_viewport_height: int = 900
scan_screenshot_max_height: int = 12000        # full-page PNGs are cropped to this height

@property
def scan_registered_hosts_list(self) -> list[str]   # lower-cased, stripped, empties dropped
```

`posture_problems` additions (staging/prod only): `"DPS_SCAN_STORE_UNMASKED_ORIGINALS must be false"` when set; `"DPS_BROWSER_HEADLESS must be true"` when false.

`dps/observability.py`: `_SAFE_FIELDS |= {"job_id", "page_no", "pages", "fields", "depth", "decision", "resource_type", "blocked", "step", "persona", "phase", "kind", "elapsed_s"}` and:

```python
SCAN_PAGES = Counter("dps_scan_pages_total", "UI-scanner pages by outcome", ["outcome"])
SCAN_REQUESTS = Counter("dps_scan_requests_total", "Requests seen by the scan guard",
                        ["decision", "method", "reason"])
SCAN_FIELDS = Counter("dps_scan_fields_total", "Form fields inventoried by entity type", ["entity_type"])
SCAN_FILLS = Counter("dps_scan_fills_total", "Field fills by mode and outcome", ["mode", "outcome"])
```

- [ ] **Step 1: Write the failing tests** — `tests/test_config_m4.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from dps.config import Settings
from dps.observability import _SAFE_FIELDS
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


def test_registered_hosts_are_normalised() -> None:
    assert _s(scan_registered_hosts=" Demo-Site , ,LOCALHOST").scan_registered_hosts_list == ["demo-site", "localhost"]


@pytest.mark.parametrize("field,value", [("browser_concurrency", 0), ("scan_max_pages", 501), ("scan_max_depth", 6)])
def test_bounds_are_validated(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        _s(**{field: value})


def test_posture_refuses_unmasked_originals_and_headed_browser_in_prod() -> None:
    base = dict(env="prod", api_keys='[{"name":"k","key_sha256":"' + "0" * 64 + '"}]', llm_provider="")
    problems = posture_problems(_s(**base, scan_store_unmasked_originals=True, browser_headless=False))
    assert "DPS_SCAN_STORE_UNMASKED_ORIGINALS must be false" in problems
    assert "DPS_BROWSER_HEADLESS must be true" in problems
    assert posture_problems(_s(env="local", scan_store_unmasked_originals=True)) == []


def test_log_allow_list_has_scanner_fields_and_no_value_fields() -> None:
    assert {"job_id", "page_no", "decision", "resource_type"} <= _SAFE_FIELDS
    assert not ({"url", "value", "label", "text", "storage_state", "href"} & _SAFE_FIELDS)
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/test_config_m4.py` → FAIL (`AttributeError: 'Settings' object has no attribute 'browser_concurrency'`).

- [ ] **Step 3: Implement.** Add the fields under a `# --- UI scanner (M4) ---` block in `Settings`; bounds via `Field(ge=…, le=…)` (`browser_concurrency` 1..16, `scan_max_pages` 1..500, `scan_max_depth` 0..5, every other int `ge=1` except `scan_settle_ms ge=0`); the `scan_registered_hosts_list` property mirrors `ner_languages_list`. Append the two posture checks after the audit check. Extend `_SAFE_FIELDS` and add the four counters next to `VERIFY_FAIL`. In `pyproject.toml` add `scanner = ["playwright==1.55.0", "pillow>=10.4"]` under `[project.optional-dependencies]`. In the contract §10 table add one row "M4 (additions) | `scan_max_per_template`, `scan_max_spa_clicks_per_page`, `scan_max_fields_per_page`, `scan_max_skipped_rows`, `scan_max_events`, `scan_settle_ms`, `scan_viewport_width`, `scan_viewport_height`, `scan_screenshot_max_height` | 3, 25, 400, 200, 2000, 400, 1280, 900, 12000 | crawl budgets and capture geometry" and one posture sentence "M4 also refuses `browser_headless=false`".

- [ ] **Step 4: Run tests, lint, types** — `pytest -q tests/test_config_m4.py && ruff check dps tests && mypy dps` → `7 passed`, clean; full `pytest -q` stays green (`tests/test_observability.py` still passes because only allow-listed keys were added).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml dps/config.py dps/posture.py dps/observability.py tests/test_config_m4.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(scanner): M4 settings, posture checks, log allow-list and scan metrics" -- pyproject.toml dps/config.py dps/posture.py dps/observability.py tests/test_config_m4.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 2: URL toolkit — canonical form, templates, scope verdicts, redaction

**Files:**
- Create: `dps/layers/scanner/__init__.py` (docstring only for now), `dps/layers/scanner/urls.py`
- Test: `tests/scanner/__init__.py`, `tests/scanner/test_urls.py`

**Interfaces introduced** (`dps/layers/scanner/urls.py`):

```python
ValueClassifier = Callable[[str], str | None]      # text -> entity type, or None

class UrlError(ValueError): code: str               # "url_invalid" | "glob_invalid"

@dataclass(frozen=True)
class Origin:
    scheme: str; host: str; port: int
    @classmethod
    def of(cls, url: str) -> Origin                  # raises UrlError("url_invalid")
    def same(self, url: str) -> bool

def canonical(url: str, base: str | None = None) -> str | None
def template(url: str) -> str
def validate_glob(glob: str) -> None
def path_matches(path: str, globs: Sequence[str]) -> bool
def redact_url(url: str, classify: ValueClassifier) -> str
def redact_origin(url: str) -> str                   # "https://cdn.example.org" — for other-origin rows

NavVerdict = Literal["ok", "scheme", "other_origin", "unregistered_host", "deny", "not_allowed"]

@dataclass(frozen=True)
class CrawlScope:
    start: Origin; same_origin: bool; registered_hosts: frozenset[str]
    allow: tuple[str, ...] = (); deny: tuple[str, ...] = ()
    def network_host_allowed(self, host: str) -> bool
    def navigation_verdict(self, url: str) -> NavVerdict
```

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_urls.py`:

```python
from __future__ import annotations

import pytest

from dps.layers.scanner.urls import (
    CrawlScope, Origin, UrlError, canonical, path_matches, redact_origin, redact_url, template, validate_glob,
)


def _cls(text: str) -> str | None:  # stand-in for the detector: only e-mails are "PII" here
    return "EMAIL" if "@" in text else None


def test_canonical_normalises_and_drops_userinfo_fragment_default_port() -> None:
    assert canonical("HTTP://User:pw@Demo-Site:80/apply?b=2&a=1#top") == "http://demo-site/apply?a=1&b=2"
    assert canonical("/apply/identity", base="http://demo-site:8410/apply") == "http://demo-site:8410/apply/identity"
    assert canonical("https://h/app#/nominee") == "https://h/app#/nominee"   # hash routes are routes
    for bad in ("mailto:a@b.co", "javascript:void(0)", "tel:+1555", "data:text/html,x", "ftp://h/x"):
        assert canonical(bad) is None


def test_template_collapses_identifiers_and_query_values() -> None:
    assert template("http://h:8410/accounts/statements/2026-04?month=4&acct=7") == "h:8410/accounts/statements/{id}?acct&month"
    assert template("http://h/help/faq/7") == "h/help/faq/{id}"
    assert template("http://h/help/faq/7") == template("http://h/help/faq/8")
    assert template("http://h/app#/nominee") == "h/app#/nominee"


def test_globs() -> None:
    assert path_matches("/logout", ["/logout"]) and path_matches("/admin/users", ["/admin"])
    assert path_matches("/help/faq/3", ["/help/faq/*"]) and not path_matches("/apply", ["/admin", "/logout"])
    validate_glob("/payments/*")
    for bad in ("payments", "/a b", "/x?y=1"):
        with pytest.raises(UrlError):
            validate_glob(bad)


def test_redact_url_masks_query_values_ids_and_entities() -> None:
    out = redact_url("http://h/apply/review?ref=APP-2026-0012&email=ananya.k%40x.in&lang=en#x", _cls)
    assert out == "http://h/apply/review?email=<EMAIL>&lang=<v>&ref=<v>"
    assert redact_url("http://h/customers/ananya.k@x.in/profile", _cls) == "http://h/customers/<EMAIL>/profile"
    assert redact_url("http://u:p@h/accounts/12345678", _cls) == "http://h/accounts/{id}"
    assert "ananya" not in out and "2026" not in out
    assert redact_origin("https://cdn.example.org/app.js?v=1") == "https://cdn.example.org"


def test_scope_verdicts() -> None:
    scope = CrawlScope(start=Origin.of("http://demo-site:8410/apply"), same_origin=True,
                       registered_hosts=frozenset({"demo-site", "cdn.bank.internal"}), deny=("/logout", "/admin"))
    assert scope.navigation_verdict("http://demo-site:8410/apply/identity") == "ok"
    assert scope.navigation_verdict("http://demo-site:8410/logout") == "deny"
    assert scope.navigation_verdict("http://cdn.bank.internal/x") == "other_origin"
    assert scope.navigation_verdict("http://tracker.invalid/p.gif") == "other_origin"
    assert scope.network_host_allowed("cdn.bank.internal") and not scope.network_host_allowed("tracker.invalid")
    wide = CrawlScope(start=scope.start, same_origin=False, registered_hosts=scope.registered_hosts, allow=("/apply/*",))
    assert wide.navigation_verdict("http://cdn.bank.internal/apply/x") == "ok"
    assert wide.navigation_verdict("http://tracker.invalid/apply/x") == "unregistered_host"
    assert wide.navigation_verdict("http://demo-site:8410/help") == "not_allowed"
    with pytest.raises(UrlError):
        Origin.of("not a url")
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_urls.py` → FAIL (`ModuleNotFoundError: No module named 'dps.layers.scanner'`).

- [ ] **Step 3: Write `dps/layers/scanner/urls.py`**

```python
"""URLs for the UI scanner: canonical form, crawl templates, scope verdicts and redaction.

Raw URLs (they can carry personal data in paths and query strings) live only in worker memory.
Anything stored or shown is produced by :func:`redact_url` (query values become ``<ENTITY>`` or
``<v>``, identifier-like path segments ``{id}`` or ``<ENTITY>``, userinfo and fragment dropped) or
:func:`redact_origin`.
"""
from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

ValueClassifier = Callable[[str], str | None]
NavVerdict = Literal["ok", "scheme", "other_origin", "unregistered_host", "deny", "not_allowed"]

_DEFAULT_PORT = {"http": 80, "https": 443}
_ID_FORMS = (r"\d{4}-\d{2}(?:-\d{2})?|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
             r"|[0-9a-f]{16,}|[A-Za-z0-9_-]{24,}")
#: Redaction: identifier-like segments (4+ digits, dates, UUIDs, long hex/tokens) never reach storage.
_ID_SEGMENT = re.compile(rf"^(?:\d{{4,}}|{_ID_FORMS})$", re.IGNORECASE)
#: Templates: any all-digit segment is an id too, so /help/faq/1 … /help/faq/8 share one template.
_TEMPLATE_SEGMENT = re.compile(rf"^(?:\d+|{_ID_FORMS})$", re.IGNORECASE)
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,40}$")
_GLOB = re.compile(r"^/[A-Za-z0-9._~\-/*{}]*$")
_HASH_ROUTE = re.compile(r"^!?/")


class UrlError(ValueError):
    """A URL or path glob the scanner refuses. ``str()`` is the code, never the input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Origin:
    scheme: str
    host: str
    port: int

    @classmethod
    def of(cls, url: str) -> Origin:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        if scheme not in _DEFAULT_PORT or not parts.hostname:
            raise UrlError("url_invalid")
        try:
            port = parts.port or _DEFAULT_PORT[scheme]
        except ValueError as exc:
            raise UrlError("url_invalid") from exc
        return cls(scheme, parts.hostname.lower(), port)

    def same(self, url: str) -> bool:
        try:
            return Origin.of(url) == self
        except UrlError:
            return False


def canonical(url: str, base: str | None = None) -> str | None:
    """Absolute http(s) URL, lower-case scheme/host, default port and userinfo removed, query keys
    sorted (values kept — this form stays in memory), fragment dropped unless it is a hash route
    (``#/x`` or ``#!/x``). ``None`` for any other scheme."""
    raw = urljoin(base, url) if base else url
    parts = urlsplit(raw.strip())
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORT or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = parts.hostname.lower() + (f":{port}" if port and port != _DEFAULT_PORT[scheme] else "")
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    fragment = parts.fragment if _HASH_ROUTE.match(parts.fragment or "") else ""
    return urlunsplit((scheme, netloc, parts.path or "/", query, fragment))


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/")]


def template(url: str) -> str:
    """Dedupe key for the crawl: ``host[:port]/path`` with identifier-like segments as ``{id}`` and
    the sorted query KEYS only (values never matter for page identity)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower() + (f":{parts.port}" if parts.port else "")
    path = "/".join("{id}" if s and _TEMPLATE_SEGMENT.match(unquote(s)) else s for s in _segments(parts.path or "/"))
    keys = sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
    out = host + path + (("?" + "&".join(keys)) if keys else "")
    return out + (f"#{parts.fragment}" if _HASH_ROUTE.match(parts.fragment or "") else "")


def validate_glob(glob: str) -> None:
    if not _GLOB.match(glob) or len(glob) > 200:
        raise UrlError("glob_invalid")


def path_matches(path: str, globs: Sequence[str]) -> bool:
    """``fnmatch`` on the path; a glob without ``*`` also covers its sub-paths."""
    for g in globs:
        if fnmatch.fnmatchcase(path, g):
            return True
        if "*" not in g and (path == g.rstrip("/") or path.startswith(g.rstrip("/") + "/")):
            return True
    return False


def redact_url(url: str, classify: ValueClassifier) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host + (f":{port}" if port and port != _DEFAULT_PORT.get(scheme) else "")
    segs = []
    for s in _segments(parts.path or "/"):
        text = unquote(s)
        entity = classify(text) if text else None
        segs.append(f"<{entity}>" if entity else ("{id}" if text and _ID_SEGMENT.match(text) else s))
    pairs = []
    for key, value in sorted(parse_qsl(parts.query, keep_blank_values=True)):
        safe_key = key if _SAFE_KEY.match(key) else "<k>"
        entity = classify(value) if value else None
        pairs.append(f"{safe_key}=" + (f"<{entity}>" if entity else ("<v>" if value else "")))
    return urlunsplit((scheme, netloc, "/".join(segs), "&".join(pairs), ""))


def redact_origin(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    return f"{scheme}://{host}" + (f":{port}" if port and port != _DEFAULT_PORT.get(scheme) else "")


@dataclass(frozen=True)
class CrawlScope:
    start: Origin
    same_origin: bool
    registered_hosts: frozenset[str]
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    def network_host_allowed(self, host: str) -> bool:
        """Every request (pages, scripts, images, XHR) must go to the start host or a host the
        tenant registered; anything else is aborted by the guard."""
        h = host.lower()
        return h == self.start.host or h in self.registered_hosts

    def navigation_verdict(self, url: str) -> NavVerdict:
        try:
            origin = Origin.of(url)
        except UrlError:
            return "scheme"
        if self.same_origin and origin != self.start:
            return "other_origin"
        if not self.same_origin and not self.network_host_allowed(origin.host):
            return "unregistered_host"
        path = urlsplit(url).path or "/"
        if path_matches(path, self.deny):
            return "deny"
        if self.allow and not path_matches(path, self.allow):
            return "not_allowed"
        return "ok"
```

`dps/layers/scanner/__init__.py` for now: `"""Layer 3 — the UI scanner (spec §8, decision D4). Handlers live here from Task 16."""`. `tests/scanner/__init__.py`: empty.

- [ ] **Step 4: Run tests, lint, types** — `pytest -q tests/scanner/test_urls.py && ruff check dps tests && mypy dps` → `5 passed`, clean. (If `test_scope_verdicts` fails on `tracker.invalid` with `same_origin=True`, remember the order: other-origin is decided before registration when `same_origin` is on.)

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/__init__.py dps/layers/scanner/urls.py tests/scanner/__init__.py tests/scanner/test_urls.py
git commit -m "feat(scanner): canonical URLs, crawl templates, scope verdicts and URL redaction" -- dps/layers/scanner/__init__.py dps/layers/scanner/urls.py tests/scanner/__init__.py tests/scanner/test_urls.py
```

---

### Task 3: Migrations 030–031 — scan tables with RLS, registered hosts

**Files:**
- Create: `dps/migrations/030_scans.sql`, `dps/migrations/031_tenant_hosts.sql`
- Modify: `dps/db.py` (`TENANT_TABLES`), `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§2.4 additions)
- Test: `tests/scanner/test_migration_030.py` (marker `db`)

**DDL** (`dps/migrations/030_scans.sql`, complete):

```sql
-- 030 UI scanner (M4). Value-free by design: no page value, raw URL, raw label or storage state is
-- ever written here. fill_* columns hold synthetic persona values or tokens of persona values only.

CREATE TABLE __SCHEMA__.dps_scan_page (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id text NOT NULL,
  job_id text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
  page_no int NOT NULL CHECK (page_no >= 1),
  url_redacted text NOT NULL,                 -- query values -> <ENTITY>|<v>; ids -> {id}; fragment dropped
  title_redacted text,
  depth int NOT NULL CHECK (depth >= 0),
  parent_page_no int,
  status text NOT NULL CHECK (status IN ('queued','visiting','done','redirected','skipped','failed')),
  form_count int NOT NULL DEFAULT 0,
  field_count int NOT NULL DEFAULT 0,
  artefacts jsonb NOT NULL DEFAULT '{}'::jsonb,         -- {original, annotated, filled_synthesize, filled_obfuscate, masked_capture, capture_json}: artefact ids
  rendered_findings jsonb NOT NULL DEFAULT '{}'::jsonb, -- {entity_type: count}
  risk_flags jsonb NOT NULL DEFAULT '[]'::jsonb,        -- list[RiskFlag]
  validation jsonb NOT NULL DEFAULT '{}'::jsonb,        -- {synthesize: {checked, accepted, rejected}, obfuscate: {...}}
  created_at timestamptz NOT NULL DEFAULT now(),
  -- M4 additions (contract §2.4 "may add")
  status_reason text,                         -- deny|other_origin|unregistered_host|not_allowed|depth|template|budget|auth_redirect|redirect|http_4xx|http_5xx|timeout|blocked|host|leakage|error
  discovered_via text NOT NULL DEFAULT 'link' CHECK (discovered_via IN ('start','link','spa_click','redirect')),
  template text NOT NULL,
  http_status int,
  final_url_redacted text,
  forms jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{form_index, method, action_redacted, field_count, frame_index, in_shadow}]
  personal_field_count int NOT NULL DEFAULT 0,
  critical_field_count int NOT NULL DEFAULT 0,
  capture_status text CHECK (capture_status IN ('captured','withheld','not_needed')),
  timings jsonb NOT NULL DEFAULT '{}'::jsonb, -- {load_ms, extract_ms, fill_ms, capture_ms}
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_id, page_no));

CREATE TABLE __SCHEMA__.dps_scan_field (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id text NOT NULL,
  job_id text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
  page_no int NOT NULL,
  form_index int NOT NULL,                    -- 0 = outside any <form>
  field_no int NOT NULL,                      -- overlay number on the page's screenshots
  selector text NOT NULL, tag text NOT NULL, input_type text, name_attr text, id_attr text,
  label text,                                 -- detector-redacted (<ENTITY>), <= 120 chars
  autocomplete text, pattern text, maxlength int, required boolean NOT NULL DEFAULT false, options_count int,
  entity_type text, sensitivity text, confidence real, detectors text[] NOT NULL DEFAULT '{}',
  evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  fill_synthesize text, fill_obfuscate text,  -- display persona's value / token of it (never page data)
  validation_messages jsonb NOT NULL DEFAULT '[]'::jsonb,
  -- M4 additions
  label_source text NOT NULL DEFAULT 'none',
  frame_index int NOT NULL DEFAULT 0, in_shadow boolean NOT NULL DEFAULT false,
  placeholder_shape text,                     -- digits->9, letters->x/X, <= 24 chars (contract §5.4 shapes)
  prefilled boolean NOT NULL DEFAULT false,   -- the page arrived with a value (the value is never stored)
  persona_attr text, obfuscate_method text, fill_format text,
  fill_status text NOT NULL DEFAULT 'not_filled' CHECK (fill_status IN
      ('filled','neutral','not_filled','skipped_file','skipped_hidden','skipped_readonly','failed')),
  risk_codes text[] NOT NULL DEFAULT '{}',
  UNIQUE (job_id, page_no, form_index, field_no));

CREATE TABLE __SCHEMA__.dps_scan_request (      -- interception log: counts per page/method/decision
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id text NOT NULL,
  job_id text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,   -- M4 adds the FK
  page_no int, method text NOT NULL, resource_type text,
  decision text NOT NULL CHECK (decision IN ('allowed','blocked')),
  reason text NOT NULL, host text NOT NULL, count int NOT NULL DEFAULT 1);
CREATE UNIQUE INDEX dps_scan_request_key ON __SCHEMA__.dps_scan_request
  (job_id, (COALESCE(page_no, -1)), method, (COALESCE(resource_type, '')), decision, reason, host);

CREATE TABLE __SCHEMA__.dps_scan_event (        -- navigation log for the console (mockup 12), value-free
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id text NOT NULL,
  job_id text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
  seq int NOT NULL,
  ts timestamptz NOT NULL DEFAULT now(),
  kind text NOT NULL CHECK (kind IN ('navigate','request','fill','capture','skip','spa','popup','dialog','error')),
  method text,
  result text NOT NULL,                       -- '200' | '302' | 'blocked' | 'skipped' | 'filled' | 'captured' | 'withheld' | 'failed' | 'closed' | 'dismissed'
  page_no int,
  target_redacted text NOT NULL,              -- redact_url / redact_origin output, or '' for page-level events
  note_code text NOT NULL,                    -- e.g. 'method', 'filled_navigation', 'deny', 'other_origin', 'persona'
  counts jsonb NOT NULL DEFAULT '{}'::jsonb,  -- e.g. {"fields": 9, "rejected": 0, "persona": 1}
  UNIQUE (job_id, seq));

ALTER TABLE __SCHEMA__.dps_scan_page    SET (fillfactor = 90);
ALTER TABLE __SCHEMA__.dps_scan_page    ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_page    FORCE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_field   ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_field   FORCE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_request FORCE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_event   ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_event   FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_scan_page
  USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_scan_field
  USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_scan_request
  USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_scan_event
  USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_scan_page    TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_scan_field   TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_scan_request TO dps_app_rw;
GRANT SELECT, INSERT, DELETE         ON __SCHEMA__.dps_scan_event   TO dps_app_rw;
GRANT USAGE ON SEQUENCE __SCHEMA__.dps_scan_page_id_seq, __SCHEMA__.dps_scan_field_id_seq,
                        __SCHEMA__.dps_scan_request_id_seq, __SCHEMA__.dps_scan_event_id_seq TO dps_app_rw;
```

`dps/migrations/031_tenant_hosts.sql`:

```sql
-- 031 hosts a tenant may scan (M4 reads, M5 admin edits). Lower-case host names, no scheme/port.
ALTER TABLE __SCHEMA__.dps_tenant ADD COLUMN registered_hosts text[] NOT NULL DEFAULT '{}';
```

`dps/db.py`: append `"dps_scan_page", "dps_scan_field", "dps_scan_request", "dps_scan_event"` to `TENANT_TABLES` (the tuple `assert_rls_posture` walks). Contract §2.4: replace the "030 scans (M4)" block with the DDL above and add a sentence "M4 additions are marked in 030".

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_migration_030.py` (`pytestmark = pytest.mark.db`; uses M2's `migrated_db`, `tenant_conn`, `queue` fixtures):

```python
from __future__ import annotations

import asyncpg
import pytest

from dps import jobs
from dps.db import assert_rls_posture
from dps.jobs import JobKind, JobPhase

pytestmark = pytest.mark.db
TABLES = ("dps_scan_page", "dps_scan_field", "dps_scan_request", "dps_scan_event")


async def _scan(tenant: str) -> str:
    job = await jobs.create_job(tenant_id=tenant, kind=JobKind.scan, phase=JobPhase.scan, spec={"url": "x"},
                                mode="synthesize")
    return job.id


async def test_scan_tables_exist_with_forced_rls(migrated_db, tenant_conn) -> None:
    async with tenant_conn("t-a") as conn:
        rows = await conn.fetch("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                                "WHERE relname = ANY($1::text[])", list(TABLES))
    assert {r["relname"] for r in rows} == set(TABLES)
    assert all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in rows)
    assert await assert_rls_posture() == []


async def test_tenant_isolation_and_with_check(migrated_db, queue, tenant_conn) -> None:
    job_id = await _scan("t-a")
    async with tenant_conn("t-a") as conn:
        await conn.execute("INSERT INTO dps.dps_scan_page (tenant_id, job_id, page_no, url_redacted, depth, status, template)"
                           " VALUES ('t-a', $1, 1, 'http://h/', 0, 'queued', 'h/')", job_id)
    async with tenant_conn("t-b") as conn:
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_scan_page") == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO dps.dps_scan_event (tenant_id, job_id, seq, kind, result, target_redacted, note_code)"
                               " VALUES ('t-a', $1, 1, 'navigate', '200', '', 'start')", job_id)


async def test_request_upsert_accumulates_and_cascade_deletes(migrated_db, queue, tenant_conn) -> None:
    job_id = await _scan("t-a")
    sql = ("INSERT INTO dps.dps_scan_request (tenant_id, job_id, page_no, method, resource_type, decision, reason, host, count)"
           " VALUES ('t-a', $1, NULL, 'POST', 'fetch', 'blocked', 'method', 'demo-site', $2)"
           " ON CONFLICT (job_id, (COALESCE(page_no, -1)), method, (COALESCE(resource_type, '')), decision, reason, host)"
           " DO UPDATE SET count = dps.dps_scan_request.count + EXCLUDED.count")
    async with tenant_conn("t-a") as conn:
        await conn.execute(sql, job_id, 2)
        await conn.execute(sql, job_id, 3)
        assert await conn.fetchval("SELECT count FROM dps.dps_scan_request WHERE job_id = $1", job_id) == 5
        await conn.execute("DELETE FROM dps.dps_job WHERE id = $1", job_id)
        assert await conn.fetchval("SELECT count(*) FROM dps.dps_scan_request WHERE job_id = $1", job_id) == 0


async def test_registered_hosts_column_defaults_empty(migrated_db, tenant_conn) -> None:
    async with tenant_conn("t-a") as conn:
        await conn.execute("INSERT INTO dps.dps_tenant (tenant_id) VALUES ('t-a') ON CONFLICT DO NOTHING")
        assert await conn.fetchval("SELECT registered_hosts FROM dps.dps_tenant WHERE tenant_id = 't-a'") == []


async def test_page_status_check_rejects_unknown_status(migrated_db, queue, tenant_conn) -> None:
    job_id = await _scan("t-a")
    async with tenant_conn("t-a") as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("INSERT INTO dps.dps_scan_page (tenant_id, job_id, page_no, url_redacted, depth, status, template)"
                               " VALUES ('t-a', $1, 1, 'u', 0, 'submitted', 't')", job_id)
```

(The schema literal `dps` matches the test DB's `pg_schema`; if M2's fixtures use a different schema, read it from `db_settings.pg_schema`.)

- [ ] **Step 2: Run to confirm failure** — `tools/scratch_pg.sh start && DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/scanner/test_migration_030.py` → FAIL (`relation "dps.dps_scan_page" does not exist`).

- [ ] **Step 3: Write the two migration files and the `TENANT_TABLES` change** exactly as above.

- [ ] **Step 4: Run** — same command → `5 passed`. Then `python -m dps.migrate --verify` against the scratch DB exits 0, and `pytest -q` (unit) stays green.

- [ ] **Step 5: Commit**

```bash
git add dps/migrations/030_scans.sql dps/migrations/031_tenant_hosts.sql dps/db.py tests/scanner/test_migration_030.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(scanner): migrations 030-031 — scan pages, fields, request log, events with RLS; tenant hosts" -- dps/migrations/030_scans.sql dps/migrations/031_tenant_hosts.sql dps/db.py tests/scanner/test_migration_030.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 4: Scan specification, records and API models

**Files:**
- Modify: `dps/jobs_models.py` (scan spec models; `JobSecret.scan_start_url`), contract §3.3 and §9 `ScanSpec` paragraph
- Create: `dps/layers/scanner/models.py`
- Test: `tests/scanner/test_models.py`

**Interfaces introduced** — in `dps/jobs_models.py` (contract §1 puts `ScanSpec` here; if M2 shipped a placeholder `ScanSpec`, replace it with this one):

```python
PATH_GLOB = r"^/[A-Za-z0-9._~\-/*{}]*$"
SECRET_REF = r"^[a-z0-9][a-z0-9/_.-]{0,200}$"

class ScanScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_origin: bool = True
    max_pages: int | None = Field(None, ge=1)          # None -> settings.scan_max_pages; > cap -> 422 scope_invalid
    max_depth: int | None = Field(None, ge=0, le=5)    # None -> settings.scan_max_depth
    allow: list[Annotated[str, Field(pattern=PATH_GLOB, max_length=200)]] = Field(default_factory=list, max_length=50)
    deny: list[Annotated[str, Field(pattern=PATH_GLOB, max_length=200)]] = Field(default_factory=list, max_length=100)
    iframes: bool = True        # same-origin iframes (mockup 12 switch)
    shadow_dom: bool = True     # open shadow roots (mockup 12 switch)
    spa_clicks: bool = True     # click in-scope links that have no plain href

class ScanAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage_state_ref: str | None = Field(None, pattern=SECRET_REF)   # DPS_SECRET_DIR/<ref>/storage_state.json
    storage_state: dict[str, Any] | None = None                        # dev only; sealed, never in the spec row
    # validator: not both; ref must not contain ".."

class FillSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personas: int = Field(1, ge=1, le=50)
    locale: str = "en_US"                                # one of SUPPORTED_LOCALES
    display_persona: int = Field(1, ge=1)                # screenshots + dps_scan_field.fill_* use this persona
    # validator: display_persona <= personas; locale in SUPPORTED_LOCALES

class CaptureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screenshots: bool = True
    har: bool = False                                    # true -> 422 har_not_supported (a HAR holds response bodies)

class ScanSpec(BaseModel):
    """POST /api/v1/scans. There is no submit field anywhere in this model — extra keys are refused."""
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=2048)
    scope: ScanScope = Field(default_factory=ScanScope)
    auth: ScanAuth = Field(default_factory=ScanAuth)
    mode: Literal["obfuscate", "synthesize"] = "synthesize"
    fill: FillSpec = Field(default_factory=FillSpec)
    parallel_view: bool = True
    capture: CaptureSpec = Field(default_factory=CaptureSpec)
    consistency: ConsistencySpec = Field(default_factory=ConsistencySpec)
    policy: str | dict[str, Any] | None = None           # None -> settings.default_policy
    purpose: Purpose | None = None

# JobSecret gains (contract §3.3):
    scan_start_url: str | None = None                    # the raw start URL (may carry a query); purged with the key
```

`mode` semantics (documented on the model): both filled screens are always produced when `parallel_view` is true, so every scan generates (or accepts) **both** a `KeyMaterial` and a `SynthesisSeed` and returns both once; `mode` selects how obfuscated captures replace rendered data (tokens with a reversible map for `obfuscate`, persona surrogates for `synthesize`), the parallel view's default side, and the evidence label.

In `dps/layers/scanner/models.py`:

```python
class Rect(BaseModel): x: float; y: float; w: float; h: float

# --- DOM facts: page data, worker memory only (never logged, never persisted as such) ---------
class OptionFacts(BaseModel): value: str; text: str; disabled: bool; selected: bool
class FieldFacts(BaseModel):
    key: str                      # data-dps-field marker "<frame_index>:<field_no>"
    field_no: int; form_index: int; frame_index: int; in_shadow: bool
    tag: str; input_type: str; name: str | None; id: str | None
    label: str; label_source: str; placeholder: str | None; title: str | None; aria_label: str | None
    autocomplete: str | None; pattern: str | None; maxlength: int | None; minlength: int | None
    required: bool; readonly: bool; disabled: bool; visible: bool; inputmode: str | None
    options: list[OptionFacts] = []
    value: str | None = None      # pre-filled value (never read for password inputs)
    checked: bool | None = None
    rect: Rect | None = None      # document coordinates in the top-level page
    selector: str                 # human-readable locator, redacted before storage
class FormFacts(BaseModel): form_index: int; method: str; action: str; frame_index: int; in_shadow: bool; field_count: int
class TextFacts(BaseModel): frame_index: int; i: int; text: str; in_table: bool; label_hint: str | None
class LinkFacts(BaseModel): href: str; in_form: bool; download: bool
class ClickableFacts(BaseModel): frame_index: int; ordinal: int; kind: Literal["role_link", "hash_link", "js_link"]
class FrameFacts(BaseModel): frame_index: int; same_origin: bool; offset: Rect; path: str
class PageSnapshot(BaseModel):
    url: str; title: str; forms: list[FormFacts]; fields: list[FieldFacts]; texts: list[TextFacts]
    links: list[LinkFacts]; clickables: list[ClickableFacts]; frames: list[FrameFacts]; truncated: bool = False

# --- classification -----------------------------------------------------------------------
class FieldClassification(BaseModel):
    entity_type: str | None; sensitivity: str | None; confidence: float
    detectors: list[str]          # families that fired: token, label, type, pattern, maxlength, value
    evidence: list[str]           # value-free: "autocomplete:cc-number", "label:aadhaar", "pattern:AADHAAR", "value:AADHAAR"
class RenderedFinding(BaseModel):
    frame_index: int; i: int; start: int; end: int; entity_type: str; score: float
    detectors: list[str]; in_table: bool

RiskFlagKind = Literal["autocomplete_enabled_sensitive", "password_without_autocomplete_token",
                       "pii_in_query_string", "sensitive_form_uses_get", "pii_rendered_unmasked"]
class RiskFlag(BaseModel):
    flag: RiskFlagKind; severity: Literal["high", "medium"]; page_no: int
    field_nos: list[int] = []; entity_types: list[str] = []; query_keys: list[str] = []
    where: Literal["table", "text", "url", "link", "form"] | None = None

class ValidationMessage(BaseModel):
    mode: Literal["synthesize", "obfuscate"]; source: Literal["native", "aria", "aria-invalid"]
    codes: list[str]; text: str   # detector-redacted, <= 200 chars

# --- persisted records (value-free; fill_* are synthetic) -------------------------------------
class FieldRecord(BaseModel):
    page_no: int; form_index: int; field_no: int; selector: str; tag: str; input_type: str | None
    name_attr: str | None; id_attr: str | None; label: str | None; label_source: str
    autocomplete: str | None; pattern: str | None; maxlength: int | None; required: bool; options_count: int | None
    entity_type: str | None; sensitivity: str | None; confidence: float | None
    detectors: list[str] = []; evidence: list[str] = []
    fill_synthesize: str | None = None; fill_obfuscate: str | None = None
    validation_messages: list[ValidationMessage] = []
    frame_index: int = 0; in_shadow: bool = False; placeholder_shape: str | None = None; prefilled: bool = False
    persona_attr: str | None = None; obfuscate_method: str | None = None; fill_format: str | None = None
    fill_status: Literal["filled", "neutral", "not_filled", "skipped_file", "skipped_hidden",
                         "skipped_readonly", "failed"] = "not_filled"
    risk_codes: list[str] = []
PageStatus = Literal["queued", "visiting", "done", "redirected", "skipped", "failed"]
class PageRecord(BaseModel):
    page_no: int; url_redacted: str; title_redacted: str | None = None; depth: int; parent_page_no: int | None
    status: PageStatus; status_reason: str | None = None; discovered_via: Literal["start", "link", "spa_click", "redirect"]
    template: str; http_status: int | None = None; final_url_redacted: str | None = None
    form_count: int = 0; field_count: int = 0; personal_field_count: int = 0; critical_field_count: int = 0
    forms: list[dict[str, Any]] = []; artefacts: dict[str, str] = {}; rendered_findings: dict[str, int] = {}
    risk_flags: list[RiskFlag] = []; validation: dict[str, dict[str, int]] = {}
    capture_status: Literal["captured", "withheld", "not_needed"] | None = None; timings: dict[str, int] = {}
class RequestCount(BaseModel):
    page_no: int | None; method: str; resource_type: str | None
    decision: Literal["allowed", "blocked"]; reason: str; host: str; count: int
EventKind = Literal["navigate", "request", "fill", "capture", "skip", "spa", "popup", "dialog", "error"]
class ScanEvent(BaseModel):
    seq: int; ts: datetime; kind: EventKind; method: str | None = None; result: str
    page_no: int | None = None; target: str = ""; note_code: str; counts: dict[str, int] = {}

# --- API models -------------------------------------------------------------------------
class ScanPageOut(PageRecord): created_at: datetime; updated_at: datetime
class ScanFieldOut(FieldRecord): pass
class NumberedRiskFlag(RiskFlag): code: str; path: str            # "A1".. in (page_no, discovery) order
class ScanSummary(BaseModel):
    pages_by_status: dict[str, int]; pages_visited: int; pages_discovered: int; pages_queued: int
    pages_classified: int; pages_filled: int; forms: int; fields: int; personal_fields: int; critical_fields: int
    risk_flags: list[NumberedRiskFlag]; blocked_requests: dict[str, int]   # by method
    non_get_allowed: int                     # measured from dps_scan_request; must be 0
    navigations: int; captures: int; validation: dict[str, dict[str, int]]
    personas: int; display_persona: int; mode: str; locale: str
    export_artefacts: dict[str, str]         # {"json": id, "csv": id} once finalized
    evidence_id: str | None; elapsed_s: float | None
class ScanCreated(BaseModel): scan: Job; key_material: KeyMaterial | None = None; synthesis_seed: SynthesisSeed | None = None; replay: bool = False
class ScanDetail(BaseModel): scan: Job; summary: ScanSummary
class ScanPageDetail(BaseModel): page: ScanPageOut; fields: list[ScanFieldOut]; requests: list[RequestCount]
class PersonaPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: str = "en_US"; count: int = Field(1, ge=1, le=5); synthesis_seed: dict[str, Any] | None = None
class PersonaPreview(BaseModel): synthesis_seed: SynthesisSeed; personas: list[dict[str, Any]]; notes: list[str]
class InventoryRow(BaseModel):
    page_no: int; path: str; form_index: int; field_no: int; label: str | None; tag: str; input_type: str | None
    name_attr: str | None; autocomplete: str | None; pattern: str | None; maxlength: int | None; required: bool
    entity_type: str | None; sensitivity: str | None; confidence: float | None; detectors: list[str]
    risk_codes: list[str]; fill_status: str; persona_attr: str | None; obfuscate_method: str | None

# --- validation of a ScanSpec at accept time --------------------------------------------
class ScanSpecError(ValueError):
    def __init__(self, code: str, status: int = 422) -> None: ...      # str() == code
@dataclass(frozen=True)
class ValidatedScan:
    spec: ScanSpec; start_url: str; origin: Origin; max_pages: int; max_depth: int; hosts: frozenset[str]
def validate_scan(spec: ScanSpec, *, settings: Settings, hosts: frozenset[str]) -> ValidatedScan
def redact_scan_spec(v: ValidatedScan, url_redacted: str) -> dict[str, Any]
```

`validate_scan` rules → `ScanSpecError(code)` (status 422 unless noted): start URL not canonicalisable or carrying userinfo → `url_invalid`; host not in `hosts` → `host_not_registered`; `scope.max_pages > settings.scan_max_pages` → `scope_invalid`; any glob failing `validate_glob` → `scope_invalid`; start path matches `deny` or misses a non-empty `allow` → `start_url_denied`; `auth.storage_state` while `settings.inline_secrets_allowed` is false → `inline_auth_forbidden`; `capture.har` → `har_not_supported`; `fill.display_persona > fill.personas` or unsupported locale → `fill_invalid` (pydantic already refuses most; this re-check covers programmatic construction). `redact_scan_spec` dumps the model with `mode="json"`, sets `url` to the redacted start URL, adds `url_origin`, replaces `auth.storage_state` by `{"inline": true}` when present, removes `consistency.key_material`, `consistency.synthesis_seed`, `consistency.wrap_key` — the contract §3.3 rule — and records `consistency.key_wrapped: bool` (whether the returned key was wrapped; the evidence pack's key-custody block reads it).

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_models.py`:

```python
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from dps.config import Settings
from dps.jobs_models import CaptureSpec, FillSpec, ScanAuth, ScanScope, ScanSpec
from dps.layers.scanner.models import ScanSpecError, redact_scan_spec, validate_scan

S = Settings(_env_file=None, env="local", inline_secrets_allowed=False)
HOSTS = frozenset({"demo-site", "127.0.0.1"})


def _walk_fields(model: type[BaseModel], seen: set[type] | None = None) -> set[str]:
    seen = seen or set()
    names: set[str] = set()
    for name, info in model.model_fields.items():
        names.add(name)
        ann = info.annotation
        for cand in getattr(ann, "__args__", (ann,)):
            if isinstance(cand, type) and issubclass(cand, BaseModel) and cand not in seen:
                seen.add(cand)
                names |= _walk_fields(cand, seen)
    return names


def test_scan_spec_has_no_submit_field_anywhere() -> None:
    names = _walk_fields(ScanSpec)
    assert not {n for n in names if "submit" in n or n in {"post", "press", "click_submit"}}
    with pytest.raises(ValidationError):
        ScanSpec.model_validate({"url": "http://demo-site:8410/apply", "submit": True})
    with pytest.raises(ValidationError):
        ScanSpec.model_validate({"url": "http://demo-site:8410/apply", "fill": {"personas": 2, "submit": True}})


def test_defaults_and_bounds() -> None:
    spec = ScanSpec(url="http://demo-site:8410/apply")
    assert spec.mode == "synthesize" and spec.parallel_view and spec.scope.same_origin
    with pytest.raises(ValidationError):
        FillSpec(personas=51)
    with pytest.raises(ValidationError):
        FillSpec(personas=2, display_persona=3)
    with pytest.raises(ValidationError):
        FillSpec(locale="fr_FR")
    with pytest.raises(ValidationError):
        ScanScope(deny=["logout"])
    with pytest.raises(ValidationError):
        ScanAuth(storage_state_ref="../etc", storage_state={"cookies": []})


@pytest.mark.parametrize("payload,code", [
    ({"url": "http://user:pw@demo-site:8410/apply"}, "url_invalid"),
    ({"url": "http://evil.example/apply"}, "host_not_registered"),
    ({"url": "http://demo-site:8410/apply", "scope": {"max_pages": 51}}, "scope_invalid"),
    ({"url": "http://demo-site:8410/admin", "scope": {"deny": ["/admin"]}}, "start_url_denied"),
    ({"url": "http://demo-site:8410/apply", "auth": {"storage_state": {"cookies": []}}}, "inline_auth_forbidden"),
    ({"url": "http://demo-site:8410/apply", "capture": {"har": True}}, "har_not_supported"),
])
def test_validate_scan_codes(payload: dict, code: str) -> None:
    with pytest.raises(ScanSpecError) as err:
        validate_scan(ScanSpec.model_validate(payload), settings=S, hosts=HOSTS)
    assert str(err.value) == code and err.value.status == 422


def test_validated_scan_defaults_and_redacted_spec_has_no_secrets() -> None:
    spec = ScanSpec.model_validate({
        "url": "http://demo-site:8410/apply?email=ananya.k%40x.in",
        "consistency": {"key_material": {"kid": "k_1", "dek": "secret"}, "synthesis_seed": {"seed": "s"},
                        "wrap_key": {"type": "rsa-oaep-256", "public_key_pem": "PEM"}},
    })
    v = validate_scan(spec, settings=S, hosts=HOSTS)
    assert (v.max_pages, v.max_depth) == (50, 3) and v.origin.host == "demo-site"
    stored = redact_scan_spec(v, "http://demo-site:8410/apply?email=<EMAIL>")
    flat = str(stored)
    assert "ananya" not in flat and "secret" not in flat and "PEM" not in flat
    assert stored["url"].endswith("<EMAIL>") and stored["url_origin"] == "http://demo-site:8410"
    assert "key_material" not in stored["consistency"] and "synthesis_seed" not in stored["consistency"]


def test_capture_spec_har_is_a_plain_flag() -> None:
    assert CaptureSpec().har is False
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_models.py` → FAIL (`ImportError: cannot import name 'ScanScope'`).

- [ ] **Step 3: Implement** the models in `dps/jobs_models.py` and `dps/layers/scanner/models.py` as specified. `validate_scan` uses `canonical`, `Origin.of`, `path_matches`, `validate_glob` from Task 2; it rejects userinfo by checking `urlsplit(spec.url).username is not None`. In the contract: §3.3 `JobSecret` gains the `scan_start_url` line; §9's `ScanSpec` paragraph gains "M4 additions: `policy`, `scope.iframes|shadow_dom|spa_clicks`, `fill.display_persona`; `extra="forbid"` everywhere; `capture.har=true` → 422 `har_not_supported`".

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_models.py && ruff check dps tests && mypy dps` → `10 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/jobs_models.py dps/layers/scanner/models.py tests/scanner/test_models.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(scanner): ScanSpec without any submit field, scan records and API models, accept-time validation" -- dps/jobs_models.py dps/layers/scanner/models.py tests/scanner/test_models.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 5: Scan store — SQL for pages, fields, request counts, events, summary

**Files:**
- Create: `dps/layers/scanner/store.py`
- Test: `tests/scanner/test_store.py` (marker `db`)

**Interfaces introduced:**

```python
class ScanStore:
    def __init__(self, schema: str) -> None            # validated ^[a-z_][a-z0-9_]{0,62}$, quoted once
    async def reset_scan(self, conn: asyncpg.Connection, job_id: str) -> None
    async def upsert_page(self, conn, tenant_id: str, job_id: str, page: PageRecord) -> None
    async def replace_fields(self, conn, tenant_id: str, job_id: str, page_no: int, fields: list[FieldRecord]) -> None
    async def add_request_counts(self, conn, tenant_id: str, job_id: str, counts: list[RequestCount]) -> None
    async def append_events(self, conn, tenant_id: str, job_id: str, events: list[ScanEvent]) -> None
    async def list_pages(self, conn, job_id: str, *, after_page_no: int = 0, limit: int = 200) -> list[ScanPageOut]
    async def get_page(self, conn, job_id: str, page_no: int) -> ScanPageOut | None
    async def list_fields(self, conn, job_id: str, page_no: int | None = None) -> list[ScanFieldOut]
    async def list_requests(self, conn, job_id: str, page_no: int | None = None) -> list[RequestCount]
    async def list_events(self, conn, job_id: str, *, after_seq: int = 0, limit: int = 200) -> list[ScanEvent]
    async def summary_counts(self, conn, job_id: str) -> dict[str, Any]
    async def export_artefacts(self, conn, job_id: str) -> dict[str, str]        # {"json": id, "csv": id}
    async def registered_hosts(self, conn, tenant_id: str) -> list[str]
    async def browser_workers_online(self, conn, *, heartbeat_seconds: int) -> int
    async def insert_evidence(self, conn, tenant_id: str, job_id: str, pack: EvidencePack, *,
                              pack_sha256: str, html_artefact_id: str | None, expires_at: datetime | None) -> str
```

Every method runs on a connection the caller obtained with `acquire(tenant_id)` (API) or `acquire_queue(tenant_id)` (worker), so RLS scopes rows to the tenant; queries still filter by `job_id`. jsonb parameters are passed as `json.dumps(...)` with `::jsonb` casts (asyncpg has no default jsonb codec unless M2 registered one; if `dps/db.py` registers a jsonb codec in `init`, pass Python objects instead — check `dps/db.py` once and follow it).

Key SQL (complete):

```python
_UPSERT_PAGE = """
INSERT INTO {s}.dps_scan_page (tenant_id, job_id, page_no, url_redacted, title_redacted, depth, parent_page_no,
  status, status_reason, discovered_via, template, http_status, final_url_redacted, form_count, field_count,
  personal_field_count, critical_field_count, forms, artefacts, rendered_findings, risk_flags, validation,
  capture_status, timings)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb,$19::jsonb,$20::jsonb,$21::jsonb,
        $22::jsonb,$23,$24::jsonb)
ON CONFLICT (job_id, page_no) DO UPDATE SET
  url_redacted = EXCLUDED.url_redacted, title_redacted = EXCLUDED.title_redacted, status = EXCLUDED.status,
  status_reason = EXCLUDED.status_reason, http_status = EXCLUDED.http_status,
  final_url_redacted = EXCLUDED.final_url_redacted, form_count = EXCLUDED.form_count,
  field_count = EXCLUDED.field_count, personal_field_count = EXCLUDED.personal_field_count,
  critical_field_count = EXCLUDED.critical_field_count, forms = EXCLUDED.forms, artefacts = EXCLUDED.artefacts,
  rendered_findings = EXCLUDED.rendered_findings, risk_flags = EXCLUDED.risk_flags,
  validation = EXCLUDED.validation, capture_status = EXCLUDED.capture_status, timings = EXCLUDED.timings,
  updated_at = now()
"""

_ADD_REQUESTS = """
INSERT INTO {s}.dps_scan_request (tenant_id, job_id, page_no, method, resource_type, decision, reason, host, count)
SELECT $1, $2, r.page_no, r.method, r.resource_type, r.decision, r.reason, r.host, r.count
FROM jsonb_to_recordset($3::jsonb) AS r(page_no int, method text, resource_type text, decision text,
                                         reason text, host text, count int)
ON CONFLICT (job_id, (COALESCE(page_no, -1)), method, (COALESCE(resource_type, '')), decision, reason, host)
DO UPDATE SET count = {s}.dps_scan_request.count + EXCLUDED.count
"""

_SUMMARY = """
SELECT
  (SELECT jsonb_object_agg(status, n) FROM (SELECT status, count(*) n FROM {s}.dps_scan_page WHERE job_id = $1 GROUP BY status) x) AS by_status,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1 AND status IN ('done','redirected','failed')) AS pages_visited,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1 AND status IN ('done','redirected')) AS classified,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1 AND artefacts ? 'filled_synthesize') AS filled,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1 AND capture_status = 'captured') AS captures,
  (SELECT coalesce(sum(form_count), 0) FROM {s}.dps_scan_page WHERE job_id = $1) AS forms,
  (SELECT coalesce(sum(field_count), 0) FROM {s}.dps_scan_page WHERE job_id = $1) AS fields,
  (SELECT coalesce(sum(personal_field_count), 0) FROM {s}.dps_scan_page WHERE job_id = $1) AS personal,
  (SELECT coalesce(sum(critical_field_count), 0) FROM {s}.dps_scan_page WHERE job_id = $1) AS critical,
  (SELECT jsonb_object_agg(method, n) FROM (SELECT method, sum(count) n FROM {s}.dps_scan_request
     WHERE job_id = $1 AND decision = 'blocked' GROUP BY method) y) AS blocked,
  (SELECT coalesce(sum(count), 0) FROM {s}.dps_scan_request WHERE job_id = $1 AND decision = 'allowed'
     AND method NOT IN ('GET','HEAD')) AS non_get_allowed,
  (SELECT count(*) FROM {s}.dps_scan_event WHERE job_id = $1 AND kind = 'navigate') AS navigations
"""
```

`replace_fields` deletes `WHERE job_id = $1 AND page_no = $2` then `executemany` inserts (all columns; `validation_messages`, `evidence` as jsonb text, `detectors`/`risk_codes` as `text[]`). `append_events` uses `executemany` with `ON CONFLICT (job_id, seq) DO NOTHING`. `reset_scan` deletes from the four tables `WHERE job_id = $1` (a restarted attempt after a reaper requeue starts clean). `export_artefacts` reads `dps_artefact` rows of kinds `export_json`/`export_csv` for the job. `registered_hosts` reads `dps_tenant.registered_hosts` (missing row → `[]`). `browser_workers_online` counts `dps_worker` rows with `role = 'browser-worker' AND last_seen > now() - make_interval(secs => $1)` (`dps_worker` is global, no RLS). `insert_evidence` inserts into `dps_evidence` (`id = pack.id` — the caller mints it with `new_id("ev")` because the pack embeds its own id — `pack` = `pack.model_dump(mode="json")`, `pack_sha256`, `html_artefact_id`, `expires_at`) and returns the id.

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_store.py` (`pytestmark = pytest.mark.db`): `test_page_upsert_then_update` (insert a queued page, upsert it as `done` with `artefacts={"original": "a1"}`; `get_page` returns status `done`, artefact id `a1`, `updated_at >= created_at`); `test_replace_fields_is_idempotent_per_page` (replace twice with 2 then 1 records → `list_fields(job, 1)` has 1 row; page 2's rows untouched); `test_request_counts_accumulate` (two calls with POST blocked counts 2 and 3 → `list_requests` shows one row with `count == 5`); `test_events_page_by_seq` (append seq 1..5; `list_events(after_seq=2, limit=2)` returns seqs `[3, 4]`; re-appending seq 3 is ignored); `test_summary_counts` (pages: 2 done, 1 skipped, 1 queued; fields 3; one blocked POST 4 → `by_status == {"done": 2, "skipped": 1, "queued": 1}`, `pages_visited == 2`, `blocked == {"POST": 4}`, `non_get_allowed == 0`); `test_reset_scan_clears_all_four_tables`; `test_registered_hosts_and_workers_online` (insert `dps_tenant` with `{demo-site}` → `["demo-site"]`; insert a `dps_worker` row role `browser-worker` `last_seen=now()` → `browser_workers_online(heartbeat_seconds=60) == 1`, a stale row (`now() - interval '1 hour'`) is not counted). 7 tests.

- [ ] **Step 2: Run to confirm failure** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/scanner/test_store.py` → FAIL (`ModuleNotFoundError: dps.layers.scanner.store`).

- [ ] **Step 3: Write `dps/layers/scanner/store.py`** with the SQL above (`{s}` formatted once in `__init__` with the quoted schema) and the record ↔ row mapping (`ScanPageOut.model_validate(dict(row) | json-decoded jsonb columns)`).

- [ ] **Step 4: Run** — same command → `7 passed`; `mypy dps` clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/store.py tests/scanner/test_store.py
git commit -m "feat(scanner): ScanStore — page/field/request/event persistence and scan summary SQL" -- dps/layers/scanner/store.py tests/scanner/test_store.py
```

---
### Task 6: The request guard — DPS never submits anything (D4)

**Files:**
- Create: `dps/layers/scanner/guard.py`
- Test: `tests/scanner/test_guard.py` (pure decision tests; the browser proof is Task 15)

**Interfaces introduced:**

```python
ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})
MIN_NEEDLE = 4
HARDEN_JS: str

@dataclass(frozen=True, slots=True)
class Decision:
    allow: bool
    reason: str      # ok | scheme | method | host | filled_navigation | filled_value

def decide(*, method: str, url: str, is_navigation: bool, page_filled: bool, host_allowed: bool,
           needles: Collection[str]) -> Decision

@dataclass(frozen=True)
class BlockedRequest:
    page_no: int | None; method: str; resource_type: str | None; reason: str; url: str   # url: memory only

class ScanGuard:
    def __init__(self, *, host_allowed: Callable[[str], bool]) -> None
    async def install(self, context: BrowserContext, *, harden_js: bool = True) -> None
    def bind(self, page: Page, page_no: int | None) -> None
    def arm(self, page: Page, fill_values: Iterable[str]) -> None
    def release(self, page: Page) -> None
    def drain_counts(self) -> list[RequestCount]
    def drain_blocked(self) -> list[BlockedRequest]
    blocked_total: int; websockets_closed: int
```

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_guard.py`:

```python
from __future__ import annotations

import pytest

pytest.importorskip("playwright")
from dps.layers.scanner.guard import ALLOWED_METHODS, HARDEN_JS, decide  # noqa: E402

BASE = dict(is_navigation=False, page_filled=False, host_allowed=True, needles=frozenset())


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "post", "CONNECT"])
def test_every_non_get_method_is_blocked_filled_or_not(method: str) -> None:
    for filled in (False, True):
        d = decide(method=method, url="http://demo-site:8410/api/autosave", **{**BASE, "page_filled": filled})
        assert d.allow is False and d.reason == "method"


def test_get_and_head_pass_on_unfilled_pages() -> None:
    assert ALLOWED_METHODS == {"GET", "HEAD"}
    for m in ("GET", "HEAD", "get"):
        assert decide(method=m, url="http://demo-site:8410/apply", **BASE).allow


def test_unregistered_hosts_and_other_schemes_are_blocked() -> None:
    assert decide(method="GET", url="http://tracker.invalid/p.gif", **{**BASE, "host_allowed": False}).reason == "host"
    assert decide(method="GET", url="ftp://demo-site/x", **BASE).reason == "scheme"


def test_filled_pages_never_navigate() -> None:
    d = decide(method="GET", url="http://demo-site:8410/help/track/result?email=x", **{**BASE, "page_filled": True,
               "is_navigation": True})
    assert (d.allow, d.reason) == (False, "filled_navigation")


def test_filled_pages_never_send_a_fill_value_in_a_url() -> None:
    needles = frozenset({"917322064418"})
    d = decide(method="GET", url="http://demo-site:8410/api/lookup?aadhaar=9173%202206%204418",
               **{**BASE, "page_filled": True, "needles": needles})
    assert (d.allow, d.reason) == (False, "filled_value")
    ok = decide(method="GET", url="http://demo-site:8410/static/app.css", **{**BASE, "page_filled": True, "needles": needles})
    assert ok.allow


def test_harden_js_neutralises_programmatic_submission() -> None:
    for needle in ("HTMLFormElement.prototype", "'submit'", "'requestSubmit'", "preventDefault", "__dpsGuard"):
        assert needle in HARDEN_JS
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_guard.py` → FAIL (`ModuleNotFoundError: dps.layers.scanner.guard`).

- [ ] **Step 3: Write `dps/layers/scanner/guard.py`**

```python
"""Request interception for the UI scanner — decision D4: DPS never submits anything.

The guard is installed on the scan's BrowserContext before the first page exists, so it sees every
request of every page, popup and iframe of that context:

* **Methods.** Only GET and HEAD leave the browser. Every other method — a form POST, an XHR/fetch
  autosave, a ``navigator.sendBeacon`` ping, PUT/PATCH/DELETE, a CORS preflight — is aborted,
  whatever page issued it and whether or not the page was filled. There is no switch to change this.
* **Hosts.** Requests to hosts outside ``CrawlScope.network_host_allowed`` are aborted.
* **Filled pages.** From ``arm(page)`` until the page is closed, every navigation request (any
  frame — a GET form submission is a navigation) and every GET whose URL carries a fill value is
  aborted. The worker closes a filled page after capturing it; it never reuses one.
* **WebSockets** are closed before they connect (``connect_to_server`` is never called).
* **JavaScript hardening** (``HARDEN_JS``, on by default) turns ``form.submit()`` and
  ``form.requestSubmit()`` into counted no-ops and cancels every submit event in the capture phase,
  so a page whose JS submits on change does not even attempt the request. It is defence in depth:
  ``tests/scanner/test_no_submit.py`` proves zero submissions with it switched off.

Playwright cannot route requests that a service worker intercepts, so scan contexts are created
with ``service_workers="block"`` (``browser.new_scan_context``). Playwright routes only the first
hop of a redirect chain; the crawler re-checks the final host after every navigation, and filled
pages never navigate at all.

Logging and counters carry method, resource type, decision, reason and host — never a URL or value.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from urllib.parse import unquote_plus, urlsplit

from playwright.async_api import BrowserContext, Page, Request, Route, WebSocketRoute
from playwright.async_api import Error as PlaywrightError

from dps.engine.verify import normalise
from dps.layers.scanner.models import RequestCount
from dps.observability import SCAN_REQUESTS

ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})
MIN_NEEDLE = 4

HARDEN_JS = r"""
(() => {
  if (window.__dpsGuard) { return; }
  Object.defineProperty(window, '__dpsGuard', { value: { submitAttempts: 0 }, configurable: false, writable: false });
  const attempt = () => { window.__dpsGuard.submitAttempts += 1; };
  const lock = (proto, name) => Object.defineProperty(proto, name, {
    value: function () { attempt(); }, configurable: false, writable: false });
  lock(HTMLFormElement.prototype, 'submit');
  lock(HTMLFormElement.prototype, 'requestSubmit');
  window.addEventListener('submit', (event) => { attempt(); event.preventDefault(); event.stopImmediatePropagation(); }, true);
})();
"""


@dataclass(frozen=True, slots=True)
class Decision:
    allow: bool
    reason: str


def _carries_value(url: str, needles: Collection[str]) -> bool:
    haystack = normalise(unquote_plus(url))
    return any(needle in haystack for needle in needles)


def decide(*, method: str, url: str, is_navigation: bool, page_filled: bool, host_allowed: bool,
           needles: Collection[str]) -> Decision:
    """The whole policy as a pure function (the route handler only gathers its inputs)."""
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return Decision(False, "scheme")
    if method.upper() not in ALLOWED_METHODS:
        return Decision(False, "method")
    if not host_allowed:
        return Decision(False, "host")
    if page_filled:
        if is_navigation:
            return Decision(False, "filled_navigation")
        if needles and _carries_value(url, needles):
            return Decision(False, "filled_value")
    return Decision(True, "ok")


@dataclass(frozen=True)
class BlockedRequest:
    """One aborted request, for the navigation log. ``url`` stays in memory; the crawler redacts it."""

    page_no: int | None
    method: str
    resource_type: str | None
    reason: str
    url: str


class ScanGuard:
    """Installs the interception on one scan's BrowserContext and keeps value-free counters."""

    def __init__(self, *, host_allowed: Callable[[str], bool]) -> None:
        self._host_allowed = host_allowed
        self._filled: set[Page] = set()
        self._needles: set[str] = set()
        self._page_no: dict[Page, int | None] = {}
        self._counts: Counter[tuple[int | None, str, str | None, str, str, str]] = Counter()
        self._blocked: list[BlockedRequest] = []
        self.blocked_total = 0
        self.websockets_closed = 0

    async def install(self, context: BrowserContext, *, harden_js: bool = True) -> None:
        if harden_js:
            await context.add_init_script(script=HARDEN_JS)
        await context.route("**/*", self._handle)
        await context.route_web_socket(re.compile(r".*"), self._close_websocket)

    def bind(self, page: Page, page_no: int | None) -> None:
        self._page_no[page] = page_no

    def arm(self, page: Page, fill_values: Iterable[str]) -> None:
        """Mark ``page`` filled. Needles stay for the rest of the scan: a later page that would
        carry an earlier persona's value in a URL is caught as well."""
        for value in fill_values:
            needle = normalise(value)
            if len(needle) >= MIN_NEEDLE:
                self._needles.add(needle)
        self._filled.add(page)

    def release(self, page: Page) -> None:
        self._filled.discard(page)
        self._page_no.pop(page, None)

    def drain_counts(self) -> list[RequestCount]:
        out = [RequestCount(page_no=p, method=m, resource_type=r, decision=d, reason=why, host=h, count=n)  # type: ignore[arg-type]
               for (p, m, r, d, why, h), n in self._counts.items()]
        self._counts.clear()
        return out

    def drain_blocked(self) -> list[BlockedRequest]:
        out, self._blocked = self._blocked, []
        return out

    @staticmethod
    def _page_of(request: Request) -> Page | None:
        try:
            return request.frame.page
        except PlaywrightError:  # worker or detached frame: no page to attribute
            return None

    async def _handle(self, route: Route) -> None:
        request = route.request
        page = self._page_of(request)
        filled = (page in self._filled) if page is not None else bool(self._filled)
        host = (urlsplit(request.url).hostname or "").lower()
        method = request.method.upper()
        decision = decide(method=method, url=request.url, is_navigation=request.is_navigation_request(),
                          page_filled=filled, host_allowed=bool(host) and self._host_allowed(host),
                          needles=self._needles if filled else ())
        page_no = self._page_no.get(page) if page is not None else None
        verdict = "allowed" if decision.allow else "blocked"
        self._counts[(page_no, method, request.resource_type, verdict, decision.reason, host or "-")] += 1
        SCAN_REQUESTS.labels(verdict, method, decision.reason).inc()
        try:
            if decision.allow:
                await route.continue_()
                return
            self.blocked_total += 1
            self._blocked.append(BlockedRequest(page_no, method, request.resource_type, decision.reason, request.url))
            await route.abort("blockedbyclient")
        except PlaywrightError:
            return  # the page or context closed while the request was paused

    async def _close_websocket(self, ws: WebSocketRoute) -> None:
        self.websockets_closed += 1
        await ws.close()
```

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_guard.py && ruff check dps tests && mypy dps` → `12 passed` (7 parametrised method cases + 5), clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/guard.py tests/scanner/test_guard.py
git commit -m "feat(scanner): request guard — only GET/HEAD leave the browser; filled pages never navigate" -- dps/layers/scanner/guard.py tests/scanner/test_guard.py
```

---

### Task 7: Demo onboarding web app (`demo/onboarding/`)

**Files:**
- Create: `demo/onboarding/server.mjs`, `demo/onboarding/site.mjs`, `demo/onboarding/data.json`, `demo/onboarding/static/app.css`, `demo/onboarding/static/forms.js`, `demo/onboarding/static/spa.js`, `demo/onboarding/static/postal-lookup.js`, `docker/secrets/dps/ui/demo-onboarding/storage_state.json`
- Create: `tests/scanner/conftest.py` (fixtures `demo_site_url`, `demo_data`, `chromium`, `scan_settings`, `scan_engine`, `det_engine`), `tests/scanner/helpers.py` (plain helpers; starts with `received`, grows in Tasks 12 and 14)
- Test: `tests/scanner/test_demo_site.py` (marker `browser`; needs only `node`)

The demo site is the scan target for every browser test, the smoke test and the ten-minute demo. It is dependency-free (Node 22 built-ins only), so it runs with the host's `node` and in `node:22-slim`. Every value it renders is synthetic demo data from `data.json`; the tests treat those values as if they were customer data and prove they never reach DPS storage. Pages and the traps they contain:

| Path | Content | What it exercises |
|---|---|---|
| `/` | links to every area; `<img src="http://tracker.invalid/pixel.gif">`; external `https://cdn.bank-assets.invalid/terms`; `mailto:`; `/files/brochure.pdf` with `download`; `/logout`, `/admin` | other-origin block, non-http links ignored, download links never followed, deny list |
| `/apply` | product, country (selects), full name (`name`), email, mobile; links to every application step (so all steps sit at depth 1 from `/apply`, as in the mockup-12 crawl map) | start form, the scan's start URL in tests and the demo |
| `/apply/identity` | the nine mockup-04 fields (full name `name`, DOB text `bday` + `DD/MM/YYYY` pattern, Aadhaar `\d{4} \d{4} \d{4}` no autocomplete, PAN `[A-Z]{5}[0-9]{4}[A-Z]` maxlength 10 no autocomplete, mobile `tel`, email, address textarea `street-address`, gender select `sex`, nationality select); autosave POST on input; `sendBeacon` on first focus; link to `/apply/review?email=<data.email>&ref=<ref>` | A1 autocomplete risk, A2 PII in a query string, autosave + beacon interception |
| `/apply/address` | label styles: `label[for]`, wrapping label, `aria-labelledby`, `aria-label` (state select), placeholder-only, nearest text (country select); PIN code inside `<postal-lookup>` open shadow root | label resolution, shadow DOM |
| `/apply/employment` | employer (`organization`), occupation, annual income (number), IFSC (pattern), account number (no autocomplete), employee id `EMP-\d{6}` | structural + pattern classification |
| `/apply/card` | same-origin `<iframe name="card-widget" src="/frames/card-widget">` with name on card, card number (`cc-number`), expiry `MM/YY`, CVV (`cc-csc`); outer select with `data-autosubmit` (`requestSubmit()` on change) | iframe extraction, auto-submit trap |
| `/apply/documents` | `<input type="file">` proof of address | file inputs listed, never filled |
| `/apply/us` | given/family name, SSN `\d{3}-\d{2}-\d{4}` no autocomplete, DOB `type=date` `bday`, phone, email, street, city, state select, ZIP `\d{5}`, driver license number | US locale fields |
| `/apply/review` | `<dl>` rendering the applicant (full name, email, mobile, masked Aadhaar `XXXX XXXX 2346`, PAN number, reference) — the row labels are chosen so the structural dictionary recognises them (`full name`, `pan number`) | rendered PII via `dt` label hints |
| `/login` | username, password (no autocomplete token), OTP `one-time-code` | password risk flag |
| `/profile` (session) | `<table>` with `<th scope=row>` headers rendering full name, DOB, Aadhaar, PAN, **full card number**, email, mobile, address; link to `/profile/contact` | A3 PII rendered unmasked, masked capture, original masking, auth via storage state |
| `/profile/contact` (session) | pre-filled form: "Contact detail" (value = e-mail, ambiguous label), mobile, address | value detectors on pre-filled values |
| `/app`, `/app/*` | SPA shell (History API): `<a href="/app/nominee" data-spa>` and `<span role="link" data-route="/app/preferences">` (no href, click-only); nominee form (name, relationship, DOB `type=date`, Aadhaar); preferences (select, consent checkbox) | SPA routes discovered by clicking |
| `/help`, `/help/contact-us`, `/help/track`, `/help/faq/1…8` | contact form; **GET form** `/help/track` → `/help/track/result` (email + reference); eight FAQ pages | sensitive GET form flag, template budget |
| `/fixtures/labels` | one input per label rule plus hidden, submit and button elements (not linked; extraction tests only) | label sources, non-field controls |
| `/logout` (counts, clears the session), `/admin` (counts, 403) | — | deny list proof |
| any non-GET | counted into `/__received` and answered `204` (API paths) or `303` | the no-submission proof |
| `GET /__received` | `{"non_get", "form_get", "logout_hits", "admin_hits", "by_path": {"POST /api/autosave": n, …}}` | read by tests and the smoke test |
| `GET /health` | `{"status":"ok"}` | compose healthcheck |

`/profile*` require cookie `demo_session=demo-valid`; without it they `302` to `/login?next=/profile`.

- [ ] **Step 1: Write the data, server and site files**

`demo/onboarding/data.json` (synthetic; never a real person):

```json
{
  "full_name": "Ananya Krishnan",
  "dob": "14/03/1988",
  "aadhaar": "2341 2341 2346",
  "pan": "BQPKR4432L",
  "card_number": "4111 1111 1111 1111",
  "email": "ananya.k@example-mail.in",
  "mobile": "+91 98451 20377",
  "address": "14 Lavelle Road, Bengaluru 560001",
  "application_ref": "APP-2026-0012"
}
```

`demo/onboarding/server.mjs`:

```js
// Demo onboarding web app for the DPS UI scanner (M4). Node 22 built-ins only.
// Every value it renders is synthetic (data.json) and stands in for customer data in tests.
// GET /__received reports every non-GET request and every GET form submission it ever received,
// so tests can prove that the scanner never submitted anything.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as site from './site.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const arg = (name) => { const i = process.argv.indexOf(name); return i > -1 ? process.argv[i + 1] : undefined; };
const PORT = Number(arg('--port') ?? process.env.DEMO_PORT ?? 8410);
const HOST = arg('--host') ?? process.env.DEMO_HOST ?? '127.0.0.1';
const SESSION = 'demo-valid';
const TYPES = { '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json' };
const received = { non_get: 0, form_get: 0, logout_hits: 0, admin_hits: 0, by_path: {} };

const hasSession = (req) => (req.headers.cookie ?? '').split(/;\s*/).includes(`demo_session=${SESSION}`);
const send = (res, status, body, type = 'text/html; charset=utf-8', extra = {}) => {
  res.writeHead(status, { 'content-type': type, 'cache-control': 'no-store', ...extra });
  res.end(body);
};
const redirect = (res, to, extra = {}) => { res.writeHead(302, { location: to, ...extra }); res.end(); };

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host ?? 'localhost'}`);
  const path = url.pathname.replace(/\/+$/, '') || '/';
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    if (!path.startsWith('/__')) {
      received.non_get += 1;
      const key = `${req.method} ${path}`;
      received.by_path[key] = (received.by_path[key] ?? 0) + 1;
    }
    req.resume();
    return path.startsWith('/api/') ? send(res, 204, '') : redirect(res, '/thanks');
  }
  if (path === '/health') return send(res, 200, JSON.stringify({ status: 'ok' }), 'application/json');
  if (path === '/__received') return send(res, 200, JSON.stringify(received), 'application/json');
  if (path.startsWith('/static/')) {
    const file = normalize(join(HERE, path));
    if (!file.startsWith(join(HERE, 'static'))) return send(res, 404, 'not found', 'text/plain');
    try { return send(res, 200, await readFile(file), TYPES[extname(file)] ?? 'application/octet-stream'); }
    catch { return send(res, 404, 'not found', 'text/plain'); }
  }
  if (path === '/logout') { received.logout_hits += 1; return redirect(res, '/', { 'set-cookie': 'demo_session=; Path=/; Max-Age=0' }); }
  if (path.startsWith('/admin')) { received.admin_hits += 1; return send(res, 403, site.layout('Forbidden', '<p>Administrators only.</p>')); }
  if (path === '/help/track/result') { received.form_get += 1; return send(res, 200, site.layout('Tracking', '<p>Lookup is disabled in the demo.</p>')); }
  if (path.startsWith('/profile') && !hasSession(req)) return redirect(res, `/login?next=${encodeURIComponent(path)}`);
  const html = site.render(path, url);
  return html === null ? send(res, 404, site.layout('Not found', '<p>No such page.</p>')) : send(res, 200, html);
});

server.listen(PORT, HOST, () => console.log(`demo onboarding site on http://${HOST}:${PORT}`));
```

`demo/onboarding/site.mjs` — a declarative page table rendered by one form renderer (complete):

```js
// Page specifications for the demo onboarding site. render(path, url) returns HTML or null.
import { readFileSync } from 'node:fs';
const DATA = JSON.parse(readFileSync(new URL('./data.json', import.meta.url), 'utf8'));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function layout(title, body) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)} — Demo Bank onboarding</title><link rel="stylesheet" href="/static/app.css"></head>
<body><header class="top"><a href="/" class="brand">Demo Bank</a><nav><a href="/apply">Apply</a><a href="/apply/us">Apply (US)</a><a href="/profile">Profile</a><a href="/app">Nominees</a><a href="/help">Help</a><a href="/login">Sign in</a><a href="/logout">Log out</a></nav></header>
<main><h1>${esc(title)}</h1>${body}</main><script src="/static/forms.js"></script></body></html>`;
}

const attrs = (f) => ['name', 'id', 'type', 'autocomplete', 'pattern', 'maxlength', 'placeholder', 'inputmode', 'title', 'aria-label', 'aria-labelledby', 'value']
  .filter((k) => f[k] !== undefined).map((k) => ` ${k}="${esc(f[k])}"`).join('') + (f.required ? ' required' : '');

function control(f) {
  const described = f.id ? ` aria-describedby="${esc(f.id)}-msg"` : '';
  if (f.tag === 'select') {
    const opts = f.options.map((o) => `<option value="${esc(o)}">${esc(o || 'Choose…')}</option>`).join('');
    return `<select${attrs(f)}${described}${f.autosubmit ? ' data-autosubmit' : ''}>${opts}</select>`;
  }
  if (f.tag === 'textarea') return `<textarea${attrs({ ...f, value: undefined })}${described} rows="2">${esc(f.value ?? '')}</textarea>`;
  if (f.tag === 'postal-lookup') return '<postal-lookup></postal-lookup>';
  return `<input${attrs({ type: 'text', ...f })}${described}>`;
}

function field(f) {
  const msg = f.id ? `<span class="msg" id="${esc(f.id)}-msg" aria-live="polite"></span>` : '';
  const cls = `field${f.full ? ' full' : ''}`;
  switch (f.labelStyle ?? 'for') {
    case 'for': return `<div class="${cls}"><label for="${esc(f.id)}">${esc(f.label)}</label>${control(f)}${msg}</div>`;
    case 'wrap': return `<div class="${cls}"><label>${esc(f.label)} ${control(f)}</label>${msg}</div>`;
    case 'labelledby': return `<div class="${cls}"><span class="lbl" id="${esc(f['aria-labelledby'])}">${esc(f.label)}</span>${control(f)}${msg}</div>`;
    case 'near': return `<div class="${cls}"><div class="lbl">${esc(f.label)}</div>${control(f)}${msg}</div>`;
    default: return `<div class="${cls}">${control(f)}${msg}</div>`;   // aria-label / placeholder / title / none
  }
}

function form(spec) {
  const data = [spec.autosave ? ` data-autosave="/api/autosave"` : '', spec.beacon ? ' data-beacon' : ''].join('');
  return `<form id="${esc(spec.id)}" method="${spec.method ?? 'post'}" action="${esc(spec.action)}"${data} class="grid">
${spec.fields.map(field).join('\n')}
<div class="actions"><button type="submit">${esc(spec.submitLabel ?? 'Continue →')}</button></div></form>`;
}

const links = (ls) => `<ul class="links">${ls.map(([href, text, extra = '']) => `<li><a href="${esc(href)}"${extra}>${esc(text)}</a></li>`).join('')}</ul>`;
const REVIEW_LINK = `/apply/review?email=${encodeURIComponent(DATA.email)}&ref=${encodeURIComponent(DATA.application_ref)}`;
const SEL = (name, id, label, options, extra = {}) => ({ tag: 'select', name, id, label, options: ['', ...options], ...extra });

const PAGES = {
  '/': { title: 'Welcome', body: () => `<p>Open an account in minutes.</p>${links([
    ['/apply', 'Apply for an account'], ['/apply/us', 'Apply from the United States'], ['/profile', 'My profile'],
    ['/app', 'Nominee centre'], ['/help', 'Help'], ['/login', 'Sign in'], ['/logout', 'Log out'], ['/admin', 'Administration'],
    ['https://cdn.bank-assets.invalid/terms', 'Terms (external)'], ['mailto:help@example.org', 'Mail us'],
    ['/files/brochure.pdf', 'Brochure', ' download']])}<img src="http://tracker.invalid/pixel.gif" alt="" width="1" height="1">` },
  '/apply': { title: 'Apply for an account', form: { id: 'start', action: '/apply/start', fields: [
    SEL('product', 'product', 'Product', ['Savings', 'Salary', 'Current']),
    SEL('country', 'country', 'Country of residence', ['India', 'United States']),
    { label: 'Full name', name: 'fullName', id: 'fullName', autocomplete: 'name', required: true },
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email', required: true },
    { label: 'Mobile', name: 'mobile', id: 'mobile', type: 'tel', autocomplete: 'tel', required: true }] },
    after: () => `<p>Application steps:</p>${links([['/apply/identity', 'Identity details'], ['/apply/address', 'Address'],
      ['/apply/employment', 'Employment'], ['/apply/card', 'Debit card'], ['/apply/documents', 'Documents']])}` },
  '/apply/identity': { title: 'Identity details', form: { id: 'identity', action: '/apply/identity/submit', autosave: true, beacon: true, fields: [
    { label: 'Full name', name: 'fullName', id: 'fullName', autocomplete: 'name', required: true },
    { label: 'Date of birth', name: 'dob', id: 'dob', autocomplete: 'bday', placeholder: 'DD/MM/YYYY', pattern: '\\d{2}/\\d{2}/\\d{4}', required: true },
    { label: 'Aadhaar number', name: 'aadhaar', id: 'aadhaar', inputmode: 'numeric', placeholder: 'XXXX XXXX XXXX', pattern: '\\d{4} \\d{4} \\d{4}', required: true },
    { label: 'PAN', name: 'pan', id: 'pan', maxlength: 10, pattern: '[A-Z]{5}[0-9]{4}[A-Z]', required: true },
    { label: 'Mobile', name: 'mobile', id: 'mobile', type: 'tel', autocomplete: 'tel', required: true },
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email', required: true },
    { label: 'Residential address', name: 'address', id: 'address', tag: 'textarea', autocomplete: 'street-address', full: true },
    SEL('gender', 'gender', 'Gender', ['Female', 'Male', 'Other'], { autocomplete: 'sex' }),
    SEL('nationality', 'nationality', 'Nationality', ['Indian', 'American', 'British', 'Canadian', 'Mexican'])] },
    after: () => links([['/apply/address', 'Continue to address'], [REVIEW_LINK, 'Review application']]) },
  '/apply/address': { title: 'Address', form: { id: 'address', action: '/apply/address/submit', autosave: true, fields: [
    { label: 'Address line 1', name: 'line1', id: 'line1', autocomplete: 'address-line1', required: true },
    { label: 'Address line 2', name: 'line2', autocomplete: 'address-line2', labelStyle: 'wrap' },
    { label: 'City', name: 'city', 'aria-labelledby': 'city-lbl', autocomplete: 'address-level2', labelStyle: 'labelledby' },
    SEL('state', undefined, 'State', ['Karnataka', 'Maharashtra', 'Tamil Nadu', 'Delhi'], { 'aria-label': 'State', autocomplete: 'address-level1', labelStyle: 'none' }),
    { tag: 'postal-lookup', labelStyle: 'none' },
    { name: 'landmark', placeholder: 'Landmark (optional)', labelStyle: 'none' },
    SEL('residence', undefined, 'Country of residence', ['India', 'United States', 'United Kingdom'], { labelStyle: 'near' })] },
    after: () => links([['/apply/employment', 'Continue to employment']]) },
  '/apply/employment': { title: 'Employment', form: { id: 'employment', action: '/apply/employment/submit', fields: [
    { label: 'Employer', name: 'employer', id: 'employer', autocomplete: 'organization' },
    { label: 'Occupation', name: 'occupation', id: 'occupation' },
    { label: 'Annual income (INR)', name: 'income', id: 'income', type: 'number' },
    { label: 'IFSC', name: 'ifsc', id: 'ifsc', maxlength: 11, pattern: '[A-Z]{4}0[A-Z0-9]{6}' },
    { label: 'Account number', name: 'account', id: 'account', inputmode: 'numeric', pattern: '\\d{9,18}' },
    { label: 'Employee ID', name: 'employeeId', id: 'employeeId', pattern: 'EMP-\\d{6}' }] },
    after: () => links([['/apply/card', 'Continue to card']]) },
  '/apply/card': { title: 'Debit card', form: { id: 'card', action: '/apply/card/submit', fields: [
    { label: 'Name to emboss', name: 'emboss', id: 'emboss', autocomplete: 'cc-name' },
    SEL('delivery', 'delivery', 'Card delivery', ['Home address', 'Branch pickup'], { autosubmit: true })] },
    after: () => `<iframe name="card-widget" title="Card details" src="/frames/card-widget" style="width:100%;height:330px;border:0"></iframe>${links([['/apply/documents', 'Continue to documents']])}` },
  '/apply/documents': { title: 'Documents', form: { id: 'documents', action: '/apply/documents/submit', fields: [
    { label: 'Proof of address (PDF or JPEG)', name: 'addressProof', id: 'addressProof', type: 'file' }] } },
  '/apply/us': { title: 'Apply from the United States', form: { id: 'us', action: '/apply/us/submit', autosave: true, fields: [
    { label: 'First name', name: 'firstName', id: 'firstName', autocomplete: 'given-name', required: true },
    { label: 'Last name', name: 'lastName', id: 'lastName', autocomplete: 'family-name', required: true },
    { label: 'Social Security number', name: 'ssn', id: 'ssn', pattern: '\\d{3}-\\d{2}-\\d{4}', placeholder: '000-00-0000', required: true },
    { label: 'Date of birth', name: 'dob', id: 'dob', type: 'date', autocomplete: 'bday' },
    { label: 'Phone', name: 'phone', id: 'phone', type: 'tel', autocomplete: 'tel' },
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email' },
    { label: 'Street address', name: 'street', id: 'street', autocomplete: 'address-line1' },
    { label: 'City', name: 'city', id: 'city', autocomplete: 'address-level2' },
    SEL('state', 'state', 'State', ['CA', 'NY', 'TX', 'WA'], { autocomplete: 'address-level1' }),
    { label: 'ZIP code', name: 'zip', id: 'zip', autocomplete: 'postal-code', pattern: '\\d{5}' },
    { label: 'Driver license number', name: 'dl', id: 'dl' }] } },
  '/apply/review': { title: 'Review your application', body: () => `<dl class="review">
<dt>Full name</dt><dd>${esc(DATA.full_name)}</dd><dt>Email</dt><dd>${esc(DATA.email)}</dd><dt>Mobile</dt><dd>${esc(DATA.mobile)}</dd>
<dt>Aadhaar</dt><dd>XXXX XXXX ${esc(DATA.aadhaar.slice(-4))}</dd><dt>PAN number</dt><dd>${esc(DATA.pan)}</dd>
<dt>Application reference</dt><dd>${esc(DATA.application_ref)}</dd></dl>` },
  '/login': { title: 'Sign in', form: { id: 'login', action: '/login', submitLabel: 'Sign in', fields: [
    { label: 'Username', name: 'username', id: 'username', autocomplete: 'username', required: true },
    { label: 'Password', name: 'password', id: 'password', type: 'password', required: true },
    { label: 'One-time code', name: 'otp', id: 'otp', autocomplete: 'one-time-code', inputmode: 'numeric', maxlength: 6, pattern: '\\d{6}' }] } },
  '/profile': { title: 'My profile', body: () => `<table class="profile"><tbody>
${[['Full name', DATA.full_name], ['Date of birth', DATA.dob], ['Aadhaar', DATA.aadhaar], ['PAN number', DATA.pan], ['Card number', DATA.card_number],
   ['Email', DATA.email], ['Mobile', DATA.mobile], ['Address', DATA.address]].map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td>${esc(v)}</td></tr>`).join('\n')}
</tbody></table>${links([['/profile/contact', 'Edit contact details']])}` },
  '/profile/contact': { title: 'Contact details', form: { id: 'contact', action: '/profile/contact/save', autosave: true, fields: [
    { label: 'Contact detail', name: 'contact1', id: 'contact1', value: DATA.email },
    { label: 'Mobile', name: 'mobile', id: 'mobile', type: 'tel', autocomplete: 'tel', value: DATA.mobile },
    { label: 'Address', name: 'address', id: 'address', tag: 'textarea', value: DATA.address, full: true }] } },
  '/help': { title: 'Help', body: () => links([['/help/contact-us', 'Contact us'], ['/help/track', 'Track an application'],
    ...[1, 2, 3, 4, 5, 6, 7, 8].map((n) => [`/help/faq/${n}`, `FAQ ${n}`])]) },
  '/help/contact-us': { title: 'Contact us', form: { id: 'contact-us', action: '/help/contact-us', submitLabel: 'Send', fields: [
    { label: 'Your name', name: 'name', id: 'name', autocomplete: 'name' },
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email' },
    { label: 'Phone', name: 'phone', id: 'phone', type: 'tel', autocomplete: 'tel' },
    { label: 'Message', name: 'message', id: 'message', tag: 'textarea', full: true }] } },
  '/help/track': { title: 'Track an application', form: { id: 'track', method: 'get', action: '/help/track/result', submitLabel: 'Track', fields: [
    { label: 'Email', name: 'email', id: 'email', type: 'email', autocomplete: 'email', required: true },
    { label: 'Application reference', name: 'ref', id: 'ref', pattern: 'APP-\\d{4}-\\d{4}' }] } },
  '/fixtures/labels': { title: 'Label fixture', body: () => `<form id="fixture" action="/fixture" method="post">
<label for="a1">Customer email</label><input id="a1" name="a1">
<label>Mobile number <input name="a2"></label>
<span id="l3">Passport number</span><input name="a3" aria-labelledby="l3">
<input name="a4" aria-label="Date of birth">
<input name="a5" placeholder="PIN code">
<div><span>Aadhaar</span><input name="a6"></div>
<input name="a7" title="Employee ID">
<input name="a8">
<label for="a9">Occupation</label><select id="a9" name="a9"><option value="">Choose</option><option>Salaried</option><option>Self-employed</option><option>Student</option></select>
<input type="hidden" name="csrf" value="tok-123">
<button type="submit">Send</button><input type="submit" value="Go"><input type="button" value="Help"><input type="reset">
</form>` },
};

function faq(n) {
  const next = n < 8 ? links([[`/help/faq/${n + 1}`, 'Next question']]) : '';
  return layout(`FAQ ${n}`, `<p>Answer number ${n}. No forms here.</p>${next}`);
}

export function render(path, url) {
  const m = /^\/help\/faq\/(\d+)$/.exec(path);
  if (m) return Number(m[1]) >= 1 && Number(m[1]) <= 8 ? faq(Number(m[1])) : null;
  if (path === '/app' || path.startsWith('/app/')) return layout('Nominee centre', `<div id="app"><nav class="spa"><a href="/app/nominee" data-spa>Nominee</a> · <span role="link" tabindex="0" data-route="/app/preferences" class="linkish">Preferences</span></nav><section id="view"></section></div><script src="/static/spa.js"></script>`);
  if (path === '/frames/card-widget') return `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/app.css"></head><body class="widget">
${form({ id: 'card-widget', action: '/frames/card-widget', autosave: true, submitLabel: 'Save card', fields: [
    { label: 'Name on card', name: 'ccName', id: 'ccName', autocomplete: 'cc-name' },
    { label: 'Card number', name: 'ccNumber', id: 'ccNumber', autocomplete: 'cc-number', inputmode: 'numeric', maxlength: 19 },
    { label: 'Expiry', name: 'ccExp', id: 'ccExp', autocomplete: 'cc-exp', placeholder: 'MM/YY', pattern: '(0[1-9]|1[0-2])/\\d{2}' },
    { label: 'CVV', name: 'ccCsc', id: 'ccCsc', autocomplete: 'cc-csc', maxlength: 3, inputmode: 'numeric' }] })}
<script src="/static/forms.js"></script></body></html>`;
  const page = PAGES[path];
  if (!page) return null;
  const body = (page.body ? page.body() : '') + (page.form ? form(page.form) : '') + (page.after ? page.after() : '');
  const extra = path === '/apply/address' ? '<script src="/static/postal-lookup.js"></script>' : '';
  return layout(page.title, body + extra);
}
```

`demo/onboarding/static/forms.js` (complete — the behaviours the guard must defeat):

```js
// Demo-site form behaviours: autosave POST, a telemetry beacon, client-side validation messages,
// and an auto-submitting select. A real onboarding app does all of these; DPS must defeat each.
(() => {
  for (const form of document.querySelectorAll('form')) {
    if (form.dataset.autosave) {
      let timer = null;
      form.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          fetch(form.dataset.autosave, { method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify(Object.fromEntries(new FormData(form))) }).catch(() => {});
        }, 150);
      });
    }
    if (form.dataset.beacon !== undefined) {
      form.addEventListener('focusin', () => navigator.sendBeacon('/api/telemetry/form-start', JSON.stringify({ form: form.id })), { once: true });
    }
    for (const el of form.querySelectorAll('input, select, textarea')) {
      el.addEventListener('blur', () => {
        const msg = el.id ? document.getElementById(`${el.id}-msg`) : null;
        if (!msg) return;
        if (el.checkValidity()) { msg.textContent = ''; el.removeAttribute('aria-invalid'); }
        else { msg.textContent = 'Use the format shown'; el.setAttribute('aria-invalid', 'true'); }
      });
    }
    for (const sel of form.querySelectorAll('select[data-autosubmit]')) {
      sel.addEventListener('change', () => form.requestSubmit());
    }
  }
})();
```

`demo/onboarding/static/spa.js` (complete):

```js
// Client-side router for /app (History API). /app/preferences is reachable only by clicking a
// role="link" element that has no href — the scanner must discover it by clicking.
(() => {
  const view = document.getElementById('view');
  const field = (label, attrs) => `<div class="field"><label for="${attrs.id}">${label}</label><input ${Object.entries(attrs).map(([k, v]) => `${k}="${v}"`).join(' ')}></div>`;
  const ROUTES = {
    '/app': () => '<p>Manage nominees and preferences.</p>',
    '/app/nominee': () => `<form id="nominee" method="post" action="/app/nominee" class="grid">
      ${field('Nominee full name', { id: 'nomName', name: 'nomName' })}
      <div class="field"><label for="nomRel">Relationship</label><select id="nomRel" name="nomRel"><option value="">Choose…</option><option>Spouse</option><option>Parent</option><option>Child</option></select></div>
      ${field('Nominee date of birth', { id: 'nomDob', name: 'nomDob', type: 'date' })}
      ${field('Nominee Aadhaar', { id: 'nomAadhaar', name: 'nomAadhaar', pattern: '\\d{4} \\d{4} \\d{4}', placeholder: 'XXXX XXXX XXXX' })}
      <div class="actions"><button type="submit">Save nominee</button></div></form>`,
    '/app/preferences': () => `<form id="prefs" method="post" action="/app/preferences" class="grid">
      <div class="field"><label for="stmt">Statement delivery</label><select id="stmt" name="stmt"><option value="">Choose…</option><option>Email</option><option>Post</option></select></div>
      <div class="field"><label><input type="checkbox" name="marketing"> Send me offers</label></div>
      <div class="actions"><button type="submit">Save</button></div></form>`,
  };
  const render = () => { view.innerHTML = (ROUTES[location.pathname] ?? ROUTES['/app'])(); };
  const go = (path) => { history.pushState({}, '', path); render(); };
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-spa]');
    if (a) { e.preventDefault(); go(a.getAttribute('href')); return; }
    const r = e.target.closest('[role="link"][data-route]');
    if (r) { e.preventDefault(); go(r.dataset.route); }
  });
  window.addEventListener('popstate', render);
  render();
})();
```

`demo/onboarding/static/postal-lookup.js` (complete):

```js
// <postal-lookup>: a web component with an OPEN shadow root holding the PIN code input.
class PostalLookup extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>label{display:block;font-size:12px;color:#4d5875;margin-bottom:4px}
input{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #c5cde0;border-radius:6px;font:inherit}</style>
<label for="pin">PIN code</label><input id="pin" name="pin" inputmode="numeric" pattern="\\d{6}" maxlength="6" autocomplete="postal-code" required>`;
  }
}
customElements.define('postal-lookup', PostalLookup);
```

`demo/onboarding/static/app.css`: a plain, readable bank look — system font, `main { max-width: 860px; margin: 24px auto; padding: 0 16px }`, `.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px }`, `.field.full, .actions { grid-column: 1 / -1 }`, inputs/selects/textarea full width with 8px padding and 1px `#c5cde0` borders, `.msg { color: #be123c; font-size: 12px }`, `header.top` with brand and nav links, `table.profile th { text-align: left; padding: 6px 12px }`, `dl.review { display: grid; grid-template-columns: 200px 1fr }`, `.linkish { color: #0f766e; text-decoration: underline; cursor: pointer }`, `@media (max-width: 640px) { .grid { grid-template-columns: 1fr } }`.

`docker/secrets/dps/ui/demo-onboarding/storage_state.json` (local demo session for the compose host name `demo-site`; a demo literal like the compose passwords):

```json
{"cookies": [{"name": "demo_session", "value": "demo-valid", "domain": "demo-site", "path": "/", "expires": -1,
  "httpOnly": true, "secure": false, "sameSite": "Lax"}], "origins": []}
```

- [ ] **Step 2: Write `tests/scanner/conftest.py`** (complete):

```python
"""Scanner fixtures: the demo site (auto-started with the host's node), Chromium, engines."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from dps.config import Settings
from dps.engine.service import PrivacyEngine

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "onboarding"


def _require() -> bool:
    return os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1"


def skip_or_fail(hint: str) -> None:
    if _require():
        pytest.fail(f"required service unavailable — {hint}")
    pytest.skip(hint)


def _up(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def demo_site_url() -> Iterator[str]:
    url = os.environ.get("DPS_TEST_DEMO_SITE", "http://127.0.0.1:8410").rstrip("/")
    if _up(url):
        yield url
        return
    node = shutil.which("node")
    port = urlsplit(url).port or 8410
    if node is None or socket.socket().connect_ex(("127.0.0.1", port)) == 0:
        skip_or_fail("start: docker compose --profile scanner up -d demo-site (or: node demo/onboarding/server.mjs)")
    proc = subprocess.Popen([node, str(DEMO / "server.mjs"), "--port", str(port), "--host", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if _up(url):
                break
            time.sleep(0.2)
        else:
            skip_or_fail("demo site did not come up")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def demo_data() -> dict[str, str]:
    return json.loads((DEMO / "data.json").read_text(encoding="utf-8"))


@pytest.fixture
async def chromium() -> AsyncIterator[Any]:
    pw = pytest.importorskip("playwright.async_api")
    async with pw.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except pw.Error:
            skip_or_fail("run: python -m playwright install chromium (playwright==1.55.0 drives chromium-1187)")
        try:
            yield browser
        finally:
            await browser.close()


@pytest.fixture
def scan_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"scan_settle_ms": 150, "scan_page_timeout_seconds": 15,
                                       "scan_registered_hosts": "127.0.0.1,localhost,demo-site"})


class RotCipher:  # deterministic FF1 stand-in (the M0 test cipher)
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


@pytest.fixture
def scan_engine(scan_settings: Settings) -> PrivacyEngine:
    return PrivacyEngine(scan_settings.model_copy(update={"fpe_enabled": True}),
                         cipher_factory=lambda key, tweak: RotCipher())


@pytest.fixture
def det_engine(scan_settings: Settings) -> PrivacyEngine:
    """FPE off: every reversible method falls back to det_token (base32)."""
    return PrivacyEngine(scan_settings.model_copy(update={"fpe_enabled": False}))
```

`tests/scanner/helpers.py` (first version):

```python
"""Plain helpers for scanner tests (fixtures live in conftest.py)."""
from __future__ import annotations

from typing import Any

import httpx


def received(url: str) -> dict[str, Any]:
    """The demo site's counters of everything non-GET (and every GET-form submission) it received."""
    return httpx.get(f"{url}/__received", timeout=5.0).json()
```

- [ ] **Step 3: Write the failing tests** — `tests/scanner/test_demo_site.py` (`pytestmark = pytest.mark.browser`):

```python
from __future__ import annotations

import httpx
import pytest

from tests.scanner.helpers import received

pytestmark = pytest.mark.browser


def test_pages_render_and_profile_needs_a_session(demo_site_url: str, demo_data: dict) -> None:
    with httpx.Client(base_url=demo_site_url, follow_redirects=False) as c:
        for path in ("/", "/apply", "/apply/identity", "/apply/address", "/apply/card", "/apply/us", "/login",
                     "/help", "/help/track", "/help/faq/3", "/app", "/app/nominee", "/frames/card-widget", "/fixtures/labels"):
            assert c.get(path).status_code == 200, path
        assert c.get("/profile").status_code == 302 and c.get("/profile").headers["location"].startswith("/login")
        page = c.get("/profile", cookies={"demo_session": "demo-valid"}).text
        assert demo_data["card_number"] in page and demo_data["aadhaar"] in page
        assert 'pattern="\\d{4} \\d{4} \\d{4}"' in c.get("/apply/identity").text


def test_received_counts_non_get_and_form_get_and_logout(demo_site_url: str) -> None:
    before = received(demo_site_url)
    httpx.post(f"{demo_site_url}/api/autosave", json={"x": 1})
    httpx.get(f"{demo_site_url}/help/track/result", params={"email": "x"})
    httpx.get(f"{demo_site_url}/logout")
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] + 1
    assert after["by_path"]["POST /api/autosave"] == before["by_path"].get("POST /api/autosave", 0) + 1
    assert after["form_get"] == before["form_get"] + 1 and after["logout_hits"] == before["logout_hits"] + 1


def test_static_files_and_traversal_guard(demo_site_url: str) -> None:
    assert httpx.get(f"{demo_site_url}/static/forms.js").status_code == 200
    assert httpx.get(f"{demo_site_url}/static/../server.mjs").status_code == 404
```

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_demo_site.py` → `3 passed` (the fixture starts `node demo/onboarding/server.mjs` on 8410 when nothing listens there). Manually: `node demo/onboarding/server.mjs` and open `http://127.0.0.1:8410/apply/identity` once — nine fields, typing fires `POST /api/autosave` (devtools network tab).

- [ ] **Step 5: Commit**

```bash
git add demo/onboarding docker/secrets/dps/ui/demo-onboarding/storage_state.json tests/scanner/conftest.py tests/scanner/helpers.py tests/scanner/test_demo_site.py
git commit -m "feat(scanner): demo onboarding site — KYC forms, SPA, iframe, shadow DOM, rendered PII, submission counters" -- demo/onboarding docker/secrets/dps/ui/demo-onboarding/storage_state.json tests/scanner/conftest.py tests/scanner/helpers.py tests/scanner/test_demo_site.py
```

---

### Task 8: Browser runtime and DOM extraction (fields, labels, shadow DOM, iframes, text, links, clickables)

**Files:**
- Create: `dps/layers/scanner/browser.py`, `dps/layers/scanner/dom.py`
- Test: `tests/scanner/test_dom_browser.py` (marker `browser`)

**Interfaces introduced:**

```python
# browser.py
async def get_browser(settings: Settings) -> Browser          # one Chromium per process; relaunched if disconnected
async def close_browser() -> None
async def new_scan_context(browser: Browser, settings: Settings, *, storage_state: dict[str, Any] | None,
                           locale: str) -> BrowserContext       # service_workers="block", accept_downloads=False, UTC, DPS UA suffix

# dom.py
EXTRACT_JS: str; CLICKABLES_JS: str; TEXT_RECTS_JS: str; REPLACE_TEXT_JS: str; SET_VALUES_JS: str
VALIDATION_JS: str; SUBMIT_ATTEMPTS_JS: str; SCROLL_JS: str
async def extract_page(page: Page, *, iframes: bool, shadow_dom: bool, max_fields: int) -> PageSnapshot
async def frame_map(page: Page, *, iframes: bool) -> dict[int, Frame]       # frame_index -> Frame, same order as extract_page
async def text_rects(page: Page, spans: list[tuple[int, int, int, int]]) -> list[list[Rect]]
        # (frame_index, i, start_cp, end_cp) — code-point offsets converted to UTF-16 inside
def utf16_offset(text: str, index: int) -> int
```

Ordering contract: frames are numbered `0` (main) then every same-origin child frame in `page.frames` order (depth-first by Playwright's order); fields are numbered `1..n` across frames in that order and in **tree order inside each frame, descending into open shadow roots at their host** — so overlay numbers run top to bottom as the mockup shows. The JS stamps each field with `data-dps-field="<frame>:<n>"` and each clickable with `data-dps-click="<frame>:<k>"`; a fresh load of the same page re-runs the same script and gets the same numbers (the fill step re-extracts and matches by key and by signature).

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_dom_browser.py` (`pytestmark = pytest.mark.browser`). Helper `async def _snap(chromium, url, **kw)` opens a context via `new_scan_context`, loads `url`, waits for `networkidle` (bounded 3 s), returns `extract_page(page, iframes=True, shadow_dom=True, max_fields=400)`.

```python
async def test_label_sources_and_non_fields(chromium, demo_site_url, scan_settings) -> None:
    snap = await _snap(chromium, f"{demo_site_url}/fixtures/labels", scan_settings)
    by_name = {f.name: f for f in snap.fields}
    assert {n: (by_name[n].label, by_name[n].label_source) for n in ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8")} == {
        "a1": ("Customer email", "label_for"), "a2": ("Mobile number", "label_wrap"),
        "a3": ("Passport number", "aria_labelledby"), "a4": ("Date of birth", "aria_label"),
        "a5": ("PIN code", "placeholder"), "a6": ("Aadhaar", "nearest_text"),
        "a7": ("Employee ID", "title"), "a8": ("", "none")}
    assert by_name["a9"].tag == "select" and [o.text for o in by_name["a9"].options][1:] == ["Salaried", "Self-employed", "Student"]
    assert by_name["csrf"].input_type == "hidden" and by_name["csrf"].visible is False
    assert {f.input_type for f in snap.fields}.isdisjoint({"submit", "button", "reset", "image"})
    assert [f.field_no for f in snap.fields] == list(range(1, len(snap.fields) + 1))


async def test_shadow_dom_field_found_in_tree_order(chromium, demo_site_url, scan_settings) -> None:
    snap = await _snap(chromium, f"{demo_site_url}/apply/address", scan_settings)
    names = [f.name for f in snap.fields]
    pin = next(f for f in snap.fields if f.name == "pin")
    assert pin.in_shadow and pin.label == "PIN code" and pin.label_source == "label_for" and pin.autocomplete == "postal-code"
    assert names.index("state") < names.index("pin") < names.index("landmark")
    assert pin.form_index == next(f for f in snap.fields if f.name == "line1").form_index   # host's form
    assert next(f for f in snap.fields if f.name == "residence").label_source == "nearest_text"


async def test_iframe_fields_with_frame_offsets(chromium, demo_site_url, scan_settings) -> None:
    snap = await _snap(chromium, f"{demo_site_url}/apply/card", scan_settings)
    card = next(f for f in snap.fields if f.name == "ccNumber")
    frame = next(fr for fr in snap.frames if fr.frame_index == card.frame_index)
    assert card.frame_index == 1 and frame.same_origin and card.autocomplete == "cc-number"
    assert card.rect is not None and card.rect.y >= frame.offset.y      # document coordinates of the top page
    assert card.key.startswith("1:")


async def test_texts_links_clickables_and_values_stay_in_memory(chromium, demo_site_url, scan_settings, demo_data) -> None:
    profile = await _snap(chromium, f"{demo_site_url}/profile", scan_settings, cookies=True)
    cells = {t.text.strip(): t for t in profile.texts}
    assert cells[demo_data["aadhaar"]].in_table and cells[demo_data["aadhaar"]].label_hint == "Aadhaar"
    home = await _snap(chromium, f"{demo_site_url}/", scan_settings)
    hrefs = {l.href for l in home.links}
    assert f"{demo_site_url}/apply" in hrefs and any(l.download for l in home.links)
    app = await _snap(chromium, f"{demo_site_url}/app", scan_settings)
    assert [c.kind for c in app.clickables] == ["role_link"]
    contact = await _snap(chromium, f"{demo_site_url}/profile/contact", scan_settings, cookies=True)
    assert next(f for f in contact.fields if f.name == "contact1").value == demo_data["email"]


async def test_text_rects_convert_code_points_to_utf16() -> None:   # pure helper, no browser
    from dps.layers.scanner.dom import utf16_offset
    assert utf16_offset("ab😀cd", 3) == 4 and utf16_offset("abc", 2) == 2


async def test_new_scan_context_blocks_service_workers_and_downloads(chromium, scan_settings) -> None:
    from dps.layers.scanner.browser import new_scan_context
    ctx = await new_scan_context(chromium, scan_settings, storage_state=None, locale="en_IN")
    page = await ctx.new_page()
    ua = await page.evaluate("navigator.userAgent")
    assert "DPS-Scanner/" in ua and await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "UTC"
    await ctx.close()
```

(`cookies=True` in `_snap` passes `storage_state={"cookies": [{"name": "demo_session", "value": "demo-valid", "domain": "127.0.0.1", "path": "/", "expires": -1, "httpOnly": True, "secure": False, "sameSite": "Lax"}], "origins": []}`.)

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_dom_browser.py` → FAIL (`ModuleNotFoundError: dps.layers.scanner.dom`).

- [ ] **Step 3: Write `dps/layers/scanner/browser.py`**

```python
"""Chromium for the browser-worker: one browser per process, one isolated context per scan."""
from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from dps import __version__
from dps.config import Settings

_lock = asyncio.Lock()
_playwright: Playwright | None = None
_browser: Browser | None = None


async def get_browser(settings: Settings) -> Browser:
    global _playwright, _browser
    async with _lock:
        if _browser is None or not _browser.is_connected():
            if _playwright is None:
                _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=settings.browser_headless,
                args=["--disable-dev-shm-usage", "--disable-background-networking", "--disable-sync",
                      "--disable-component-update", "--no-first-run"])
        return _browser


async def close_browser() -> None:
    global _playwright, _browser
    async with _lock:
        if _browser is not None:
            await _browser.close()
        if _playwright is not None:
            await _playwright.stop()
        _browser, _playwright = None, None


async def new_scan_context(browser: Browser, settings: Settings, *, storage_state: dict[str, Any] | None,
                           locale: str) -> BrowserContext:
    """Isolated context: no service workers (Playwright cannot route their requests), no downloads,
    no permissions, UTC, a user agent that names DPS so the target's logs show who visited."""
    ua = (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
          f"Chrome/{browser.version} Safari/537.36 DPS-Scanner/{__version__}")
    return await browser.new_context(
        viewport={"width": settings.scan_viewport_width, "height": settings.scan_viewport_height},
        device_scale_factor=1, storage_state=storage_state, service_workers="block", accept_downloads=False,
        locale=locale.replace("_", "-"), timezone_id="UTC", user_agent=ua, permissions=[],
        java_script_enabled=True, ignore_https_errors=False)
```

- [ ] **Step 4: Write `dps/layers/scanner/dom.py`** — the page-side JavaScript (complete) and wrappers:

```python
"""Page-side JavaScript for the scanner and typed wrappers around it.

Everything these snippets return is page data and stays in worker memory; callers persist only
what classification and redaction derive from it.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Frame, Page

from dps.layers.scanner.models import (ClickableFacts, FieldFacts, FormFacts, FrameFacts, LinkFacts, PageSnapshot,
                                       Rect, TextFacts)

EXTRACT_JS = r"""
(args) => {
  const { frameIndex, shadow, startField, startForm, maxFields } = args;
  const NON_FIELD = new Set(['submit', 'button', 'reset', 'image']);
  const SKIP_TEXT = 'script,style,noscript,template,select,option,textarea,button';
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const text = (el) => clean(el ? (el.innerText || el.textContent) : '');
  const visible = (el) => {
    if (!(el instanceof Element)) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && el.getClientRects().length > 0;
  };
  const byId = (el, id) => {
    const root = el.getRootNode();
    return (root && root.getElementById ? root.getElementById(id) : null) || document.getElementById(id);
  };
  const withoutControls = (label) => {
    const copy = label.cloneNode(true);
    copy.querySelectorAll('input,select,textarea,button').forEach((n) => n.remove());
    return text(copy);
  };
  // Nearest text = the closest preceding text with NO other control in between (text that sits
  // before another control belongs to that control). Climbs at most three levels.
  const isControl = (s) => s.matches('input,select,textarea,button') || !!s.querySelector('input,select,textarea,button');
  const nearestText = (el) => {
    let n = el;
    for (let depth = 0; depth < 3 && n; depth += 1) {
      let s = n.previousElementSibling;
      while (s) {
        if (isControl(s)) return '';
        const t = text(s);
        if (t && t.length <= 80) return t;
        s = s.previousElementSibling;
      }
      n = n.parentElement;
    }
    return '';
  };
  const resolveLabel = (el) => {
    const id = el.getAttribute('id');
    const root = el.getRootNode();
    if (id && root.querySelector) {
      const l = root.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (l && text(l)) return [text(l), 'label_for'];
    }
    const wrap = el.closest('label');
    if (wrap) { const t = withoutControls(wrap); if (t) return [t, 'label_wrap']; }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = clean(lb.split(/\s+/).map((x) => byId(el, x)).filter(Boolean).map(text).join(' '));
      if (t) return [t, 'aria_labelledby'];
    }
    const al = clean(el.getAttribute('aria-label')); if (al) return [al, 'aria_label'];
    const ph = clean(el.getAttribute('placeholder')); if (ph) return [ph, 'placeholder'];
    const near = nearestText(el); if (near) return [near, 'nearest_text'];
    const ti = clean(el.getAttribute('title')); if (ti) return [ti, 'title'];
    return ['', 'none'];
  };
  const labelHint = (el) => {
    const cell = el.closest('td,[role=cell],[role=gridcell]');
    if (cell) {
      const row = cell.parentElement;
      const th = row ? row.querySelector('th,[role=rowheader]') : null;
      if (th && th !== cell) return text(th);
      const table = cell.closest('table');
      const head = table ? table.querySelector('thead tr') : null;
      if (head && row) { const idx = Array.prototype.indexOf.call(row.children, cell); if (head.children[idx]) return text(head.children[idx]); }
    }
    const dd = el.closest('dd');
    if (dd) { let p = dd.previousElementSibling; while (p && p.tagName !== 'DT') p = p.previousElementSibling; if (p) return text(p); }
    const prev = el.previousElementSibling;
    if (prev && /:\s*$/.test(text(prev)) && text(prev).length <= 40) return text(prev).replace(/:\s*$/, '');
    return null;
  };
  const formOf = (el) => {
    if (el.form) return el.form;
    const direct = el.closest('form');
    if (direct) return direct;
    const host = el.getRootNode() && el.getRootNode().host;
    return host ? host.closest('form') : null;
  };
  const describe = (el, inShadow) => {
    const tag = el.tagName.toLowerCase();
    const id = el.getAttribute('id'); const name = el.getAttribute('name');
    const own = tag + (id ? `#${id}` : '') + (name ? `[name="${name}"]` : '');
    const host = inShadow && el.getRootNode().host ? el.getRootNode().host.tagName.toLowerCase() + ' >>> ' : '';
    return host + own;
  };
  window.__dpsTextNodes = [];
  const fields = [], forms = new Map(), texts = [], links = [], clickables = [];
  const sx = window.scrollX, sy = window.scrollY;
  const onField = (el, inShadow) => {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase()
      : tag === 'select' ? (el.multiple ? 'select-multiple' : 'select-one') : 'textarea';
    if (tag === 'input' && NON_FIELD.has(type)) return;
    if (fields.length >= maxFields) { truncated = true; return; }
    const n = startField + fields.length + 1;
    const key = `${frameIndex}:${n}`;
    el.setAttribute('data-dps-field', key);
    let formIndex = 0;
    const form = formOf(el);
    if (form) {
      if (!forms.has(form)) forms.set(form, { form_index: startForm + forms.size + 1, method: (form.getAttribute('method') || 'get').toLowerCase(),
        action: form.getAttribute('action') || '', frame_index: frameIndex, in_shadow: inShadow, field_count: 0 });
      const f = forms.get(form); f.field_count += 1; formIndex = f.form_index;
    }
    const [label, source] = resolveLabel(el);
    const r = el.getBoundingClientRect();
    const secret = type === 'password' || type === 'file' || type === 'checkbox' || type === 'radio';
    fields.push({ key, field_no: n, form_index: formIndex, frame_index: frameIndex, in_shadow: inShadow, tag, input_type: type,
      name: el.getAttribute('name'), id: el.getAttribute('id'), label: label.slice(0, 200), label_source: source,
      placeholder: el.getAttribute('placeholder'), title: el.getAttribute('title'), aria_label: el.getAttribute('aria-label'),
      autocomplete: el.getAttribute('autocomplete'), pattern: el.getAttribute('pattern'),
      maxlength: el.maxLength > 0 ? el.maxLength : null, minlength: el.minLength > 0 ? el.minLength : null,
      required: el.required === true, readonly: el.readOnly === true, disabled: el.disabled === true,
      visible: type !== 'hidden' && visible(el), inputmode: el.getAttribute('inputmode'),
      options: tag === 'select' ? Array.from(el.options).slice(0, 300).map((o) => ({ value: o.value, text: clean(o.text), disabled: o.disabled, selected: o.selected })) : [],
      value: secret || tag === 'select' ? null : (el.value || null),
      checked: type === 'checkbox' || type === 'radio' ? el.checked : null,
      rect: { x: r.left + sx, y: r.top + sy, w: r.width, h: r.height }, selector: describe(el, inShadow) });
  };
  const onText = (node) => {
    const parent = node.parentElement;
    const raw = node.nodeValue || '';
    if (!parent || clean(raw).length < 2 || parent.closest(SKIP_TEXT) || !visible(parent)) return;
    if (texts.length >= 5000) { truncated = true; return; }
    const i = window.__dpsTextNodes.length;
    window.__dpsTextNodes.push(node);
    texts.push({ frame_index: frameIndex, i, text: raw.slice(0, 2000), in_table: !!parent.closest('table,[role=grid],[role=table]'),
      label_hint: labelHint(parent) });
  };
  let truncated = false, clickOrdinal = 0;
  const walk = (root, inShadow) => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = it.nextNode();
    while (node) {
      if (node.nodeType === Node.TEXT_NODE) onText(node);
      else {
        const el = node;
        if (el.matches('input,select,textarea')) onField(el, inShadow);
        else if (el.matches('a[href]') && !/^(javascript:|#)/i.test(el.getAttribute('href') || '')) {
          if (links.length < 500) links.push({ href: el.href, in_form: !!el.closest('form'), download: el.hasAttribute('download') });
        }
        if (el.matches('[role="link"]:not([href]), a[href^="#"], a[href^="javascript:" i]') && !el.closest('form') && visible(el)) {
          const kind = el.matches('[role="link"]:not([href])') ? 'role_link' : (el.getAttribute('href') || '').startsWith('#') ? 'hash_link' : 'js_link';
          clickOrdinal += 1;
          el.setAttribute('data-dps-click', `${frameIndex}:${clickOrdinal}`);
          clickables.push({ frame_index: frameIndex, ordinal: clickOrdinal, kind });
        }
        if (shadow && el.shadowRoot && el.shadowRoot.mode === 'open') walk(el.shadowRoot, true);
      }
      node = it.nextNode();
    }
  };
  walk(document.body || document.documentElement, false);
  return { title: clean(document.title), fields, forms: Array.from(forms.values()), texts, links, clickables, truncated };
}
"""

CLICKABLES_JS = r"""
(frameIndex) => {   // re-stamp data-dps-click on a fresh load without a full extract (same ordinals)
  let k = 0;
  const visit = (root) => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let el = it.nextNode();
    while (el) {
      if (el.matches('[role="link"]:not([href]), a[href^="#"], a[href^="javascript:" i]') && !el.closest('form') && el.getClientRects().length > 0) {
        k += 1; el.setAttribute('data-dps-click', `${frameIndex}:${k}`);
      }
      if (el.shadowRoot && el.shadowRoot.mode === 'open') visit(el.shadowRoot);
      el = it.nextNode();
    }
  };
  visit(document.body || document.documentElement);
  return k;
}
"""

TEXT_RECTS_JS = r"""
(spans) => spans.map(([i, start, end]) => {
  const node = (window.__dpsTextNodes || [])[i];
  if (!node) return [];
  const range = document.createRange();
  try { range.setStart(node, start); range.setEnd(node, end); } catch (e) { return []; }
  return Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0)
    .map((r) => ({ x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height }));
})
"""

REPLACE_TEXT_JS = r"""
(pairs) => { let n = 0; for (const [i, t] of pairs) { const node = (window.__dpsTextNodes || [])[i]; if (node) { node.nodeValue = t; n += 1; } } return n; }
"""

SET_VALUES_JS = r"""
(pairs) => {   // set input values WITHOUT dispatching events (captures must not trigger autosave)
  let n = 0;
  for (const [key, value] of pairs) {
    const el = document.querySelector(`[data-dps-field="${key}"]`) || (function find(root) {
      for (const host of root.querySelectorAll('*')) { if (host.shadowRoot) { const hit = host.shadowRoot.querySelector(`[data-dps-field="${key}"]`) || find(host.shadowRoot); if (hit) return hit; } }
      return null; })(document);
    if (el) { el.value = value; n += 1; }
  }
  return n;
}
"""

VALIDATION_JS = r"""
(el) => {
  const out = [];
  const CODES = ['valueMissing','typeMismatch','patternMismatch','tooLong','tooShort','rangeUnderflow','rangeOverflow','stepMismatch','badInput','customError'];
  if (el.willValidate && !el.checkValidity()) {
    out.push({ source: 'native', codes: CODES.filter((c) => el.validity[c]), text: (el.validationMessage || '').slice(0, 200) });
  }
  const root = el.getRootNode();
  const ids = `${el.getAttribute('aria-describedby') || ''} ${el.getAttribute('aria-errormessage') || ''}`.split(/\s+/).filter(Boolean);
  for (const id of ids) {
    const n = (root.getElementById ? root.getElementById(id) : null) || document.getElementById(id);
    const t = n ? (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim() : '';
    if (t && n.getClientRects().length > 0) out.push({ source: 'aria', codes: [], text: t.slice(0, 200) });
  }
  if (el.getAttribute('aria-invalid') === 'true') out.push({ source: 'aria-invalid', codes: [], text: '' });
  return out;
}
"""

SUBMIT_ATTEMPTS_JS = "() => (window.__dpsGuard ? window.__dpsGuard.submitAttempts : 0)"
SCROLL_JS = "() => { window.scrollTo(0, 0); return [window.scrollX, window.scrollY, document.documentElement.scrollHeight]; }"


def utf16_offset(text: str, index: int) -> int:
    """Python code-point offset -> JavaScript UTF-16 code-unit offset (astral characters count 2)."""
    return len(text[:index].encode("utf-16-le")) // 2


def _origin(url: str) -> tuple[str, str, int | None]:
    p = urlsplit(url)
    return (p.scheme, (p.hostname or "").lower(), p.port)


async def frame_map(page: Page, *, iframes: bool) -> dict[int, Frame]:
    """frame_index -> Frame: 0 = main; then same-origin (or about:blank/srcdoc) child frames."""
    out: dict[int, Frame] = {0: page.main_frame}
    if not iframes:
        return out
    main = _origin(page.main_frame.url)
    for frame in page.frames:
        if frame is page.main_frame or frame.is_detached():
            continue
        if frame.url.startswith("about:") or _origin(frame.url) == main:
            out[len(out)] = frame
    return out


async def _frame_offset(frame: Frame, scroll: tuple[float, float]) -> Rect:
    if frame.parent_frame is None:
        return Rect(x=0, y=0, w=0, h=0)
    element = await frame.frame_element()
    box = await element.bounding_box()      # relative to the main viewport
    if box is None:
        return Rect(x=0, y=0, w=0, h=0)
    return Rect(x=box["x"] + scroll[0], y=box["y"] + scroll[1], w=box["width"], h=box["height"])


async def extract_page(page: Page, *, iframes: bool, shadow_dom: bool, max_fields: int) -> PageSnapshot:
    frames = await frame_map(page, iframes=iframes)
    sx, sy, _ = await page.evaluate(SCROLL_JS)
    fields: list[FieldFacts] = []
    forms: list[FormFacts] = []
    texts: list[TextFacts] = []
    links: list[LinkFacts] = []
    clicks: list[ClickableFacts] = []
    frame_facts: list[FrameFacts] = []
    truncated, title = False, ""
    for index, frame in frames.items():
        offset = await _frame_offset(frame, (sx, sy))
        frame_facts.append(FrameFacts(frame_index=index, same_origin=True, offset=offset,
                                      path="main" if index == 0 else (frame.name or f"frame{index}")))
        raw: dict[str, Any] = await frame.evaluate(EXTRACT_JS, {
            "frameIndex": index, "shadow": shadow_dom, "startField": len(fields), "startForm": len(forms),
            "maxFields": max(0, max_fields - len(fields))})
        if index == 0:
            title = raw["title"]
        for f in raw["fields"]:
            if index and f.get("rect"):
                f["rect"] = {**f["rect"], "x": f["rect"]["x"] + offset.x, "y": f["rect"]["y"] + offset.y}
            fields.append(FieldFacts.model_validate(f))
        forms += [FormFacts.model_validate(f) for f in raw["forms"]]
        texts += [TextFacts.model_validate(t) for t in raw["texts"]]
        links += [LinkFacts.model_validate(link) for link in raw["links"]]
        clicks += [ClickableFacts.model_validate(c) for c in raw["clickables"]]
        truncated = truncated or bool(raw["truncated"])
    return PageSnapshot(url=page.url, title=title, forms=forms, fields=fields, texts=texts, links=links,
                        clickables=clicks, frames=frame_facts, truncated=truncated)


async def text_rects(page: Page, frames: dict[int, Frame], snapshot: PageSnapshot,
                     spans: list[tuple[int, int, int, int]]) -> list[list[Rect]]:
    """Client rects (document coordinates of the top page) for (frame_index, i, start, end) spans;
    ``start``/``end`` are code-point offsets into the node text the detector saw."""
    by_key = {(t.frame_index, t.i): t.text for t in snapshot.texts}
    offsets = {f.frame_index: f.offset for f in snapshot.frames}
    out: list[list[Rect]] = []
    for frame_index, i, start, end in spans:
        text = by_key.get((frame_index, i), "")
        js_spans = [[i, utf16_offset(text, start), utf16_offset(text, end)]]
        raw = await frames[frame_index].evaluate(TEXT_RECTS_JS, js_spans)
        off = offsets.get(frame_index, Rect(x=0, y=0, w=0, h=0))
        out.append([Rect(x=r["x"] + off.x, y=r["y"] + off.y, w=r["w"], h=r["h"]) for r in raw[0]])
    return out
```

(`frame_map` for the iframe test returns the card widget as index 1; `extract_page` relies on it so both always agree.)

- [ ] **Step 5: Run** — `pytest -q tests/scanner/test_dom_browser.py && ruff check dps tests && mypy dps` → `6 passed`, clean.

- [ ] **Step 6: Commit**

```bash
git add dps/layers/scanner/browser.py dps/layers/scanner/dom.py tests/scanner/test_dom_browser.py
git commit -m "feat(scanner): Chromium runtime and DOM extraction — labels, shadow DOM, iframes, text nodes, clickables" -- dps/layers/scanner/browser.py dps/layers/scanner/dom.py tests/scanner/test_dom_browser.py
```

---
### Task 9: Classification — autocomplete tokens, structural dictionary, type/pattern, value detectors

**Files:**
- Create: `dps/layers/scanner/classify.py`
- Modify: `dps/engine/service.py` (public `detector()`), `tests/test_service.py` (one assertion)
- Test: `tests/scanner/test_classify.py`

**Interfaces introduced:**

```python
# dps/engine/service.py (M0 touch point)
def detector(self, policy: Policy, locale: str) -> Detector      # the builder obfuscate/synthesize already use

# dps/layers/scanner/classify.py
AUTOCOMPLETE_ENTITY: dict[str, str | None]
PATTERN_PROBES: dict[str, tuple[str, ...]]
MAXLENGTH_HINTS: dict[int, frozenset[str]]
AutocompleteState = Literal["absent", "on", "off", "named", "unknown"]
def autocomplete_token(attr: str | None) -> tuple[str | None, AutocompleteState]
def compile_html_pattern(pattern: str | None) -> re.Pattern[str] | None
def pattern_entities(pattern: str | None) -> list[str]
def classify_field(facts: FieldFacts, *, detector: Detector, min_score: float) -> FieldClassification
def classify_rendered(texts: list[TextFacts], *, detector: Detector, structural_floor: float = 0.7) -> list[RenderedFinding]
def redact_text(text: str | None, detector: Detector, *, limit: int = 120) -> str | None
def value_classifier(detector: Detector) -> Callable[[str], str | None]
def placeholder_shape(text: str | None) -> str | None
```

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_classify.py`:

```python
from __future__ import annotations

import pytest

from dps.engine.detect.ensemble import Detector
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.scanner.classify import (autocomplete_token, classify_field, classify_rendered, compile_html_pattern,
                                         pattern_entities, placeholder_shape, redact_text, value_classifier)
from dps.layers.scanner.models import FieldFacts, TextFacts

POLICY = load_preset("gdpr-pseudonymise")


@pytest.fixture
def det(settings) -> Detector:  # noqa: ANN001
    return PrivacyEngine(settings).detector(POLICY, "en_IN")


def F(**kw) -> FieldFacts:  # noqa: ANN003, N802
    base = dict(key="0:1", field_no=1, form_index=1, frame_index=0, in_shadow=False, tag="input", input_type="text",
                name=None, id=None, label="", label_source="none", placeholder=None, title=None, aria_label=None,
                autocomplete=None, pattern=None, maxlength=None, minlength=None, required=False, readonly=False,
                disabled=False, visible=True, inputmode=None, selector="input")
    return FieldFacts.model_validate(base | kw)


@pytest.mark.parametrize("attr,expected", [
    ("section-a shipping street-address", ("street-address", "named")), ("off", (None, "off")),
    (None, (None, "absent")), ("on", (None, "on")), ("bogus-token", (None, "unknown"))])
def test_autocomplete_token(attr, expected) -> None:  # noqa: ANN001
    assert autocomplete_token(attr) == expected


def test_autocomplete_token_wins_with_high_confidence(det) -> None:  # noqa: ANN001
    c = classify_field(F(label="Full name", autocomplete="name"), detector=det, min_score=0.6)
    assert c.entity_type == "PERSON_NAME" and c.confidence == 0.99 and {"token", "label"} <= set(c.detectors)
    assert "autocomplete:name" in c.evidence


def test_label_and_pattern_classify_aadhaar(det) -> None:  # noqa: ANN001
    c = classify_field(F(label="Aadhaar number", pattern=r"\d{4} \d{4} \d{4}"), detector=det, min_score=0.6)
    assert c.entity_type == "AADHAAR" and c.confidence >= 0.98 and c.sensitivity == "CRITICAL"
    assert "pattern:AADHAAR" in c.evidence and any(e.startswith("label:") for e in c.evidence)


def test_label_pattern_and_maxlength_classify_pan(det) -> None:  # noqa: ANN001
    c = classify_field(F(label="PAN", maxlength=10, pattern="[A-Z]{5}[0-9]{4}[A-Z]"), detector=det, min_score=0.6)
    assert c.entity_type == "PAN_IN" and {"label", "pattern", "maxlength"} <= set(c.detectors)


def test_input_type_alone(det) -> None:  # noqa: ANN001
    assert classify_field(F(input_type="email"), detector=det, min_score=0.6).entity_type == "EMAIL"
    assert classify_field(F(input_type="password"), detector=det, min_score=0.6).entity_type == "PASSWORD"


def test_prefilled_value_classifies_an_ambiguous_label_without_leaking(det) -> None:  # noqa: ANN001
    c = classify_field(F(label="Contact detail", value="ananya.k@example-mail.in"), detector=det, min_score=0.6)
    assert c.entity_type == "EMAIL" and "value" in c.detectors
    assert not any("ananya" in e for e in c.evidence)


def test_below_threshold_is_unclassified(det) -> None:  # noqa: ANN001
    c = classify_field(F(label="Nominee name"), detector=det, min_score=0.6)
    assert c.entity_type is None and c.detectors == [] and c.confidence < 0.6


def test_patterns_are_probed_safely() -> None:
    assert compile_html_pattern("(a+)+") is None and compile_html_pattern("[") is None
    assert pattern_entities(r"\d{6}") == ["POSTAL_CODE"]
    assert pattern_entities(r"\d{9,18}") == []            # matches many probes: no signal
    assert pattern_entities("[A-Z]{4}0[A-Z0-9]{6}") == ["IFSC"]


def test_rendered_values_and_row_header_hints(det) -> None:  # noqa: ANN001
    texts = [TextFacts(frame_index=0, i=0, text="Full name", in_table=True, label_hint=None),
             TextFacts(frame_index=0, i=1, text="Ananya Krishnan", in_table=True, label_hint="Full name"),
             TextFacts(frame_index=0, i=2, text="Questions? Call us any time.", in_table=False, label_hint=None),
             TextFacts(frame_index=0, i=3, text=" ananya.k@example-mail.in ", in_table=False, label_hint=None)]
    found = {(r.i, r.entity_type) for r in classify_rendered(texts, detector=det)}
    assert found == {(1, "PERSON_NAME"), (3, "EMAIL")}


def test_redaction_helpers(det) -> None:  # noqa: ANN001
    assert redact_text("Welcome back, ananya.k@example-mail.in", det) == "Welcome back, <EMAIL>"
    assert value_classifier(det)("ananya.k@example-mail.in") == "EMAIL" and value_classifier(det)("en") is None
    assert placeholder_shape("XXXX 1234 abcd") == "XXXX 9999 xxxx"


def test_engine_exposes_its_detector(settings) -> None:  # noqa: ANN001
    assert isinstance(PrivacyEngine(settings).detector(POLICY, "en_US"), Detector)
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_classify.py` → FAIL (`ModuleNotFoundError: dps.layers.scanner.classify`).

- [ ] **Step 3: Add `PrivacyEngine.detector`** — in `dps/engine/service.py`: `def detector(self, policy: Policy, locale: str) -> Detector: """Public form of the detector builder (used by the UI scanner).""" return self._detector(policy, locale)`. Add to `tests/test_service.py::test_detect_and_plan`: `assert isinstance(engine.detector(engine.policy(None), "en_US"), Detector)`.

- [ ] **Step 4: Write `dps/layers/scanner/classify.py`**

```python
"""Field and rendered-text classification for the UI scanner (spec §8 "Classify").

Signal families, strongest first: ``token`` (the HTML autocomplete token, mapped straight to an
entity type), ``label`` / ``name`` (the M0 structural dictionary over label, placeholder,
aria-label, title / name, id), ``type`` (input type), ``pattern`` (the ``pattern`` attribute tested
against synthetic probes), ``maxlength`` (supporting evidence only) and ``value`` (M0 value
detectors over a pre-filled value). Families combine per entity by noisy-OR; the token's entity
wins unless a checksum-validated value disagrees. Evidence strings are value-free.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Literal

from dps.engine.detect.ensemble import Detector
from dps.engine.detect.structural import classify_key
from dps.engine.taxonomy import entity_info
from dps.layers.scanner.models import FieldClassification, FieldFacts, RenderedFinding, TextFacts

AutocompleteState = Literal["absent", "on", "off", "named", "unknown"]

AUTOCOMPLETE_ENTITY: dict[str, str | None] = {
    "name": "PERSON_NAME", "given-name": "PERSON_NAME", "additional-name": "PERSON_NAME",
    "family-name": "PERSON_NAME", "nickname": "PERSON_NAME", "honorific-prefix": None, "honorific-suffix": None,
    "username": "USERNAME", "new-password": "PASSWORD", "current-password": "PASSWORD", "one-time-code": "PASSWORD",
    "organization": "ORG_NAME", "organization-title": None,
    "street-address": "STREET_ADDRESS", "address-line1": "STREET_ADDRESS", "address-line2": "STREET_ADDRESS",
    "address-line3": "STREET_ADDRESS", "address-level4": "CITY", "address-level3": "CITY", "address-level2": "CITY",
    "address-level1": "LOCATION", "country": "LOCATION", "country-name": "LOCATION", "postal-code": "POSTAL_CODE",
    "cc-name": "PERSON_NAME", "cc-given-name": "PERSON_NAME", "cc-additional-name": "PERSON_NAME",
    "cc-family-name": "PERSON_NAME", "cc-number": "CARD_PAN", "cc-exp": "CARD_EXPIRY", "cc-exp-month": "CARD_EXPIRY",
    "cc-exp-year": "CARD_EXPIRY", "cc-csc": "CVV", "cc-type": None, "transaction-currency": None,
    "transaction-amount": "AMOUNT", "language": None, "bday": "DATE_OF_BIRTH", "bday-day": "DATE_OF_BIRTH",
    "bday-month": "DATE_OF_BIRTH", "bday-year": "DATE_OF_BIRTH", "sex": "GENDER", "url": "URL", "photo": "URL",
    "tel": "PHONE", "tel-country-code": "PHONE", "tel-national": "PHONE", "tel-area-code": "PHONE",
    "tel-local": "PHONE", "tel-local-prefix": "PHONE", "tel-local-suffix": "PHONE", "tel-extension": "PHONE",
    "email": "EMAIL", "impp": "USERNAME",
}
_MODIFIERS = frozenset({"shipping", "billing", "home", "work", "mobile", "fax", "pager", "webauthn"})

#: Synthetic probes (never real values) used to read what a ``pattern`` attribute accepts.
PATTERN_PROBES: dict[str, tuple[str, ...]] = {
    "AADHAAR": ("2345 6789 0123", "234567890123"), "PAN_IN": ("ABCPK1234Q",),
    "SSN": ("900-12-3456", "900123456"), "CARD_PAN": ("4111 1111 1111 1111", "4111111111111111"),
    "CARD_EXPIRY": ("04/28", "04/2028"), "CVV": ("123",), "IFSC": ("ABCD0123456",),
    "POSTAL_CODE": ("560001", "94105", "94105-1234", "SW1A 1AA"),
    "PHONE": ("+91 98451 20377", "9845120377", "(415) 555-0132", "+1 415-555-0132"),
    "DATE_OF_BIRTH": ("14/03/1988", "1988-03-14", "03/14/1988"), "EMPLOYEE_ID": ("EMP-123456",),
    "CUSTOMER_ID": ("CUST-884120",), "NINO": ("QQ123456C", "QQ 12 34 56 C"), "SIN": ("046 454 286", "046454286"),
    "CURP": ("GODE561231HDFRRN09",), "RFC": ("GODE561231GR8",), "ACCOUNT_NUMBER": ("123456789012",),
    "ROUTING_ABA": ("021000021",),
}
MAXLENGTH_HINTS: dict[int, frozenset[str]] = {
    3: frozenset({"CVV"}), 4: frozenset({"CVV"}), 5: frozenset({"POSTAL_CODE"}), 6: frozenset({"POSTAL_CODE"}),
    9: frozenset({"SSN", "ROUTING_ABA"}), 10: frozenset({"PAN_IN", "PHONE"}), 11: frozenset({"IFSC", "SSN"}),
    12: frozenset({"AADHAAR"}), 14: frozenset({"AADHAAR"}), 16: frozenset({"CARD_PAN"}), 19: frozenset({"CARD_PAN"}),
}
_TYPE_SIGNAL = {"email": ("EMAIL", 0.9), "tel": ("PHONE", 0.85), "password": ("PASSWORD", 0.95), "url": ("URL", 0.7),
                "date": ("DATE", 0.5), "month": ("DATE", 0.5)}
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")
_SHAPE_UPPER, _SHAPE_LOWER = re.compile(r"[A-Z]"), re.compile(r"[a-z]")


def autocomplete_token(attr: str | None) -> tuple[str | None, AutocompleteState]:
    if attr is None or not attr.strip():
        return None, "absent"
    tokens = attr.strip().lower().split()
    if tokens == ["on"]:
        return None, "on"
    if tokens == ["off"]:
        return None, "off"
    names = [t for t in tokens if not t.startswith("section-") and t not in _MODIFIERS]
    if names and names[0] in AUTOCOMPLETE_ENTITY:
        return names[0], "named"
    return None, "unknown"


@lru_cache(maxsize=512)
def compile_html_pattern(pattern: str | None) -> re.Pattern[str] | None:
    """The ``pattern`` attribute as a Python regex (HTML anchors it as ``^(?:p)$``). Refuses long
    patterns and nested quantifiers (catastrophic backtracking on a hostile page)."""
    if not pattern or len(pattern) > 200 or _NESTED_QUANTIFIER.search(pattern):
        return None
    try:
        return re.compile(f"(?:{pattern})")
    except re.error:
        return None


def pattern_entities(pattern: str | None) -> list[str]:
    compiled = compile_html_pattern(pattern)
    if compiled is None:
        return []
    hits = sorted(e for e, probes in PATTERN_PROBES.items() if any(compiled.fullmatch(p) for p in probes))
    return hits if 1 <= len(hits) <= 2 else []


def _noisy_or(scores: list[float]) -> float:
    acc = 1.0
    for s in scores:
        acc *= 1.0 - s
    return 1.0 - acc


def _whole_value_hits(detector: Detector, value: str) -> list[tuple[str, float, bool]]:
    """(entity, score, checksum_validated) for findings covering the whole (stripped) value."""
    lead, tail = len(value) - len(value.lstrip()), len(value.rstrip())
    out = []
    for f in detector.detect(text=value).findings:
        start = f.locator.start if f.locator.start is not None else 0
        end = f.locator.end if f.locator.end is not None else len(value)
        if start <= lead and end >= tail:
            out.append((f.entity_type, f.score, any(e.startswith("checksum") for e in f.evidence)))
    return out


def classify_field(facts: FieldFacts, *, detector: Detector, min_score: float) -> FieldClassification:
    signals: dict[str, dict[str, tuple[float, str]]] = {}

    def add(entity: str, family: str, score: float, evidence: str) -> None:
        fams = signals.setdefault(entity, {})
        if family not in fams or score > fams[family][0]:
            fams[family] = (score, evidence)

    token, _ = autocomplete_token(facts.autocomplete)
    token_entity = AUTOCOMPLETE_ENTITY.get(token) if token else None
    if token_entity:
        add(token_entity, "token", 0.99, f"autocomplete:{token}")
    for source, text, factor, family in (
        ("label", facts.label, 1.0, "label"), ("aria", facts.aria_label, 1.0, "label"),
        ("placeholder", facts.placeholder, 0.9, "label"), ("title", facts.title, 0.9, "label"),
        ("name", facts.name, 0.95, "name"), ("id", facts.id, 0.95, "name")):
        if text:
            for cand in classify_key(text):
                add(cand.entity_type, family, round(cand.score * factor, 3), f"{source}:{cand.evidence.split(':', 1)[-1]}")
    if facts.input_type in _TYPE_SIGNAL:
        entity, score = _TYPE_SIGNAL[facts.input_type]
        add(entity, "type", score, f"type:{facts.input_type}")
    probed = pattern_entities(facts.pattern)
    for entity in probed:
        add(entity, "pattern", 0.75 if len(probed) == 1 else 0.6, f"pattern:{entity}")
    validated: set[str] = set()
    if facts.value and facts.input_type not in ("password", "hidden"):
        for entity, score, checked in _whole_value_hits(detector, facts.value):
            add(entity, "value", score, f"value:{entity}")
            if checked and score >= 0.95:
                validated.add(entity)
    hinted = MAXLENGTH_HINTS.get(facts.maxlength or 0, frozenset())
    for entity in list(signals):
        if entity in hinted:
            add(entity, "maxlength", 0.3, f"maxlength:{facts.maxlength}")
    if not signals:
        return FieldClassification(entity_type=None, sensitivity=None, confidence=0.0, detectors=[], evidence=[])
    scores = {e: _noisy_or([s for s, _ in fams.values()]) for e, fams in signals.items()}
    if token_entity and not (validated and token_entity not in validated):
        best = token_entity
    elif validated:
        best = max(validated, key=lambda e: scores[e])
    else:
        best = max(scores, key=lambda e: (scores[e], len(signals[e])))
    confidence = min(0.99, math.floor(scores[best] * 100) / 100)
    if scores[best] < min_score:
        return FieldClassification(entity_type=None, sensitivity=None, confidence=confidence, detectors=[], evidence=[])
    fams = signals[best]
    return FieldClassification(entity_type=best, sensitivity=str(entity_info(best).sensitivity), confidence=confidence,
                               detectors=sorted(fams), evidence=[ev for _, ev in fams.values()])


_T_PATH = re.compile(r"^\$\.t\[(\d+)\]$")


def classify_rendered(texts: list[TextFacts], *, detector: Detector, structural_floor: float = 0.7) -> list[RenderedFinding]:
    """Personal data rendered as page text: value detectors over every text node, plus the row/
    column header, ``<dt>`` or ``Label:`` hint next to a value (the structural dictionary for
    rendered data — it catches names that patterns cannot)."""
    if not texts:
        return []
    out: list[RenderedFinding] = []
    whole: set[int] = set()
    for f in detector.detect(data={"t": [t.text for t in texts]}).findings:
        m = _T_PATH.match(f.locator.path)
        if m is None:
            continue
        k = int(m.group(1))
        t = texts[k]
        lead, tail = len(t.text) - len(t.text.lstrip()), len(t.text.rstrip())
        start = f.locator.start if f.locator.start is not None else lead
        end = f.locator.end if f.locator.end is not None else tail
        if start <= lead and end >= tail:
            whole.add(k)
        out.append(RenderedFinding(frame_index=t.frame_index, i=t.i, start=start, end=end, entity_type=f.entity_type,
                                   score=f.score, detectors=f.detectors, in_table=t.in_table))
    for k, t in enumerate(texts):
        if k in whole or not t.label_hint or sum(ch.isalnum() for ch in t.text) < 2:
            continue
        cands = classify_key(t.label_hint)
        if cands and cands[0].score >= structural_floor:
            out = [r for r in out if not (r.frame_index == t.frame_index and r.i == t.i)]
            lead, tail = len(t.text) - len(t.text.lstrip()), len(t.text.rstrip())
            out.append(RenderedFinding(frame_index=t.frame_index, i=t.i, start=lead, end=tail,
                                       entity_type=cands[0].entity_type, score=cands[0].score,
                                       detectors=["structural:label_hint"], in_table=t.in_table))
    return sorted(out, key=lambda r: (r.frame_index, r.i, r.start))


def redact_text(text: str | None, detector: Detector, *, limit: int = 120) -> str | None:
    """Page text (labels, titles, validation messages, selectors) with detected values replaced
    by ``<ENTITY>`` — the only form in which such text is stored."""
    if text is None:
        return None
    clean = " ".join(text.split())[: limit * 2]
    spans = sorted(((f.locator.start or 0, f.locator.end if f.locator.end is not None else len(clean), f.entity_type)
                    for f in detector.detect(text=clean).findings), reverse=True)
    for start, end, entity in spans:
        clean = clean[:start] + f"<{entity}>" + clean[end:]
    return clean[:limit]


def value_classifier(detector: Detector) -> Callable[[str], str | None]:
    def classify(value: str) -> str | None:
        hits = _whole_value_hits(detector, value) if len(value.strip()) >= 3 else []
        return max(hits, key=lambda h: h[1])[0] if hits else None
    return classify


def placeholder_shape(text: str | None) -> str | None:
    if not text:
        return None
    shape = re.sub(r"\d", "9", text)
    shape = _SHAPE_LOWER.sub("x", _SHAPE_UPPER.sub("X", shape))
    return shape[:24]
```

- [ ] **Step 5: Run** — `pytest -q tests/scanner/test_classify.py tests/test_service.py && ruff check dps tests && mypy dps` → `15 passed` for the new module (5 parametrised + 10), `test_service.py` green, clean. If `test_rendered_values_and_row_header_hints` finds an extra `(2, …)` hit, the M0 phone pattern matched "Call us"; that text contains no digits, so a hit means a pattern regression in M0 — fix there, not here.

- [ ] **Step 6: Commit**

```bash
git add dps/layers/scanner/classify.py dps/engine/service.py tests/test_service.py tests/scanner/test_classify.py
git commit -m "feat(scanner): field classification (autocomplete, structural, type, pattern, value) and rendered-PII detection" -- dps/layers/scanner/classify.py dps/engine/service.py tests/test_service.py tests/scanner/test_classify.py
```

---

### Task 10: Risk flags

**Files:**
- Create: `dps/layers/scanner/inventory.py` (risk flags now; inventory/export/evidence in Task 16)
- Test: `tests/scanner/test_risk_flags.py`

**Interfaces introduced:**

```python
def field_risks(page_no: int, fields: list[tuple[FieldFacts, FieldClassification]], forms: list[FormFacts]) -> list[RiskFlag]
def url_risks(page_no: int, page_url: str, external_links: list[str], *, classify_value: Callable[[str], str | None]) -> list[RiskFlag]
def rendered_risks(page_no: int, rendered: list[RenderedFinding]) -> list[RiskFlag]
def number_flags(pages: list[tuple[int, str, list[RiskFlag]]]) -> list[NumberedRiskFlag]   # (page_no, path, flags) -> A1..An
def risk_codes_by_field(flags: list[RiskFlag]) -> dict[int, list[str]]                      # field_no -> flag kinds
```

Rules (value-free output; each yields at most one flag per kind per page):

| Flag | Severity | Fires when |
|---|---|---|
| `autocomplete_enabled_sensitive` | high | a visible text-entry field classified HIGH or CRITICAL (credentials excluded) has no `autocomplete` attribute or `autocomplete="on"` |
| `password_without_autocomplete_token` | medium | an `input[type=password]` whose last autocomplete token is neither `new-password` nor `current-password` |
| `sensitive_form_uses_get` | high | a `<form method="get">` containing at least one field classified MEDIUM or above (submitting it would put the values in a URL) |
| `pii_in_query_string` | high | the visited page URL (`where="url"`) or a link to another origin (`where="link"`) carries a query value that the value detectors classify MEDIUM+, or a non-empty value under a key the structural dictionary scores ≥ 0.7 for a MEDIUM+ entity; `query_keys` lists safe key names only (`^[A-Za-z0-9_.\-\[\]]{1,40}$`) |
| `pii_rendered_unmasked` | high if any CRITICAL entity else medium | rendered findings of HIGH/CRITICAL entities (masked renderings such as `XXXX XXXX 2346` are not findings, so they never flag); `where="table"` when any is inside a table/grid |

`number_flags` orders by `(page_no, kind order above)` and assigns `A1`, `A2`, … (the console and the evidence pack show these codes).

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_risk_flags.py` (builds `FieldFacts` with the `F()` helper from Task 9 copied into a module-level function, and `FieldClassification` literals):

```python
def test_autocomplete_on_sensitive_fields_flags_once_per_page() -> None:
    fields = [(F(field_no=3, label="Aadhaar number"), C("AADHAAR", "CRITICAL")),
              (F(field_no=4, label="PAN", maxlength=10), C("PAN_IN", "CRITICAL")),
              (F(field_no=5, autocomplete="tel"), C("PHONE", "MEDIUM")),
              (F(field_no=6, autocomplete="off"), C("SSN", "CRITICAL"))]
    flags = field_risks(2, fields, [])
    assert [(f.flag, f.field_nos, f.entity_types) for f in flags] == [
        ("autocomplete_enabled_sensitive", [3, 4], ["AADHAAR", "PAN_IN"])]


def test_password_without_token_and_get_form() -> None:
    fields = [(F(field_no=1, input_type="password"), C("PASSWORD", "CRITICAL")),
              (F(field_no=2, input_type="password", autocomplete="current-password"), C("PASSWORD", "CRITICAL")),
              (F(field_no=3, form_index=2, input_type="email"), C("EMAIL", "MEDIUM"))]
    forms = [FormFacts(form_index=2, method="get", action="/help/track/result", frame_index=0, in_shadow=False, field_count=1)]
    kinds = {f.flag: f for f in field_risks(5, fields, forms)}
    assert kinds["password_without_autocomplete_token"].field_nos == [1]
    assert kinds["sensitive_form_uses_get"].field_nos == [3] and kinds["sensitive_form_uses_get"].where == "form"
    assert "autocomplete_enabled_sensitive" not in kinds        # credentials are excluded from that rule


def test_pii_in_query_string_page_url_and_external_link() -> None:
    cls = lambda v: "EMAIL" if "@" in v else None  # noqa: E731
    flags = url_risks(9, "http://h/apply/review?email=ananya.k%40example-mail.in&ref=APP-2026-0012", [], classify_value=cls)
    assert [(f.flag, f.query_keys, f.entity_types, f.where) for f in flags] == [("pii_in_query_string", ["email"], ["EMAIL"], "url")]
    ext = url_risks(1, "http://h/", ["https://ads.example.net/p?ssn=900-12-3456"], classify_value=lambda v: None)
    assert ext[0].where == "link" and ext[0].query_keys == ["ssn"] and ext[0].entity_types == ["SSN"]
    assert url_risks(1, "http://h/help?lang=en", [], classify_value=lambda v: None) == []


def test_rendered_unmasked_severity_and_where() -> None:
    r = [RenderedFinding(frame_index=0, i=4, start=0, end=19, entity_type="CARD_PAN", score=0.99, detectors=["pattern"], in_table=True),
         RenderedFinding(frame_index=0, i=7, start=0, end=24, entity_type="EMAIL", score=0.9, detectors=["pattern"], in_table=True)]
    (flag,) = rendered_risks(11, r)
    assert (flag.flag, flag.severity, flag.where, flag.entity_types) == ("pii_rendered_unmasked", "high", "table", ["CARD_PAN"])
    assert rendered_risks(11, [r[1]]) == []          # MEDIUM only: no flag


def test_numbering_and_field_codes() -> None:
    a = RiskFlag(flag="autocomplete_enabled_sensitive", severity="high", page_no=3, field_nos=[3, 4])
    b = RiskFlag(flag="pii_in_query_string", severity="high", page_no=9, query_keys=["email"])
    c = RiskFlag(flag="password_without_autocomplete_token", severity="medium", page_no=3, field_nos=[2])
    numbered = number_flags([(9, "/apply/review?email=<EMAIL>", [b]), (3, "/apply/identity", [a, c])])
    assert [(n.code, n.flag, n.path) for n in numbered] == [
        ("A1", "autocomplete_enabled_sensitive", "/apply/identity"),
        ("A2", "password_without_autocomplete_token", "/apply/identity"),
        ("A3", "pii_in_query_string", "/apply/review?email=<EMAIL>")]
    assert risk_codes_by_field([a, c]) == {3: ["autocomplete_enabled_sensitive"], 4: ["autocomplete_enabled_sensitive"],
                                           2: ["password_without_autocomplete_token"]}
```

(`C(entity, sensitivity)` builds a `FieldClassification` with `confidence=0.9, detectors=["label"], evidence=[]`. The external-link test relies on the structural rule `ssn → SSN 0.9` because the value classifier returns `None`.)

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_risk_flags.py` → FAIL (`ModuleNotFoundError: dps.layers.scanner.inventory`).

- [ ] **Step 3: Implement** the five functions in `dps/layers/scanner/inventory.py` exactly per the table (use `autocomplete_token` for the autocomplete states; `entity_info(...).sensitivity` for tiers; `parse_qsl(urlsplit(url).query, keep_blank_values=False)` for query pairs; the kind order `("autocomplete_enabled_sensitive", "password_without_autocomplete_token", "sensitive_form_uses_get", "pii_in_query_string", "pii_rendered_unmasked")` for numbering). Credentials = entity types `PASSWORD`, `API_KEY`, `JWT`, `PRIVATE_KEY`.

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_risk_flags.py && ruff check dps tests && mypy dps` → `5 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/inventory.py tests/scanner/test_risk_flags.py
git commit -m "feat(scanner): risk flags — autocomplete on sensitive fields, password tokens, GET forms, PII in URLs, rendered PII" -- dps/layers/scanner/inventory.py tests/scanner/test_risk_flags.py
```

---

### Task 11: Persona values, field formatting and tokens of persona values

**Files:**
- Create: `dps/layers/scanner/personas_fill.py` (pure parts; `fill_page` is added in Task 13)
- Modify: `dps/engine/synth/surrogates.py` (IFSC rule), `tests/test_surrogates.py` (one test)
- Test: `tests/scanner/test_personas_fill.py`

**Design.** A persona is a fixed set of attributes (`name`, `dob`, `email`, `phone`, `address`, `aadhaar`, `pan`, `ssn`, `card_number`, … — table below). Every attribute that names an entity type is produced by the **M0 surrogate engine through `apply_plan`** (method `surrogate`, anchor `persona:<n>`), fed a seeded *exemplar*: a synthetic stand-in drawn from `random.Random(seed_int(seed, "scanner|exemplar|<n>|<attr>"))`. The exemplar only gives the generator its shape and per-persona variety (M0's identifier generators are deterministic in `(seed, entity type, original)`, so a constant exemplar would hand every persona the same Aadhaar). No page value is ever an input. All attributes are computed every time, in sorted order, so persona `n` is identical in the setup preview, on every page, in the export, and in a later scan given the same seed. Derived attributes (`given_name`, `family_name`, `address_line2`, `region`, `country`, `age`, `credential`, `otp`) come from those values or from the seeded RNG; `credential` and `otp` are typed into password/OTP inputs and **never persisted or exported**.

| Attribute | Entity | Exemplar (seeded) | Attribute | Entity | Exemplar (seeded) |
|---|---|---|---|---|---|
| `name` | PERSON_NAME | `"Asha Rao"` (persona decides) | `card_number` | CARD_PAN | 16 digits |
| `dob` | DATE_OF_BIRTH | ISO date 1958–2002 | `card_expiry` | CARD_EXPIRY | `MM/YY` |
| `email` | EMAIL | `user<6d>@example.org` | `card_csc` | CVV | 3 digits |
| `phone` | PHONE | `+<12d>` (international format) | `account_number` | ACCOUNT_NUMBER | 12 digits |
| `address` | STREET_ADDRESS | `<n> Exemplar Road, Exampleton <6d>` | `ifsc` | IFSC | `AAAA0AA9999` |
| `address_line1` | STREET_ADDRESS | `<n> Exemplar Road` | `routing` | ROUTING_ABA | 9 digits |
| `city` | CITY | `Exampleton` | `iban` | IBAN | `GB<2d>EXMP<14d>` |
| `postal_code` | POSTAL_CODE | 6 digits | `passport` / `driver_license` | PASSPORT / DRIVER_LICENSE | `A` + 7 digits |
| `gender` / `nationality` | GENDER / NATIONALITY | constants (persona decides) | `voter_id` | VOTER_ID | `AAA` + 7 digits |
| `username` | USERNAME | `user_<4d>` | `nino` / `sin` | NINO / SIN | `AA<6d>A` / `### ### ###` |
| `organization` | ORG_NAME | `Exemplar Works <3d>` | `curp` / `rfc` | CURP / RFC | letters+digits shapes |
| `aadhaar` | AADHAAR | 12 digits, first 2–9 | `employee_id` / `customer_id` | EMPLOYEE_ID / CUSTOMER_ID | `EMP-<6d>` / `CUST-<6d>` |
| `pan` | PAN_IN | `AAAAA9999A` | `url` / `amount` | URL / AMOUNT | `https://example.org/<6d>` / 5 digits |
| `ssn` / `itin` | SSN / ITIN | `###-##-####` | | | |

**Interfaces introduced:**

```python
PERSONA_ATTRS: dict[str, AttrSpec]            # AttrSpec(entity: str, exemplar: Callable[[random.Random], str])
ENTITY_ATTR: dict[str, str]                   # entity type -> persona attribute
TOKEN_ATTR: dict[str, str]                    # autocomplete token -> persona attribute (given-name -> given_name, ...)
LOCALE_COUNTRY: dict[str, str]; LOCALE_REGIONS: dict[str, tuple[str, ...]]
class LeakageError(RuntimeError): ...         # str() == "leakage"

def persona_values(engine: PrivacyEngine, *, seed: SynthesisSeed, persona_no: int, locale: str,
                   policy: Policy) -> dict[str, str]
def preview_entities(locale: str) -> list[tuple[str, str]]   # (label, attr) for the setup preview, per locale

FillKind = Literal["text", "date", "select", "check", "radio", "credential", "neutral_text", "skip"]
@dataclass(frozen=True)
class FieldPlan:
    kind: FillKind; persona_attr: str | None; fill_status: str; obfuscate: bool = False
def plan_field(facts: FieldFacts, cls: FieldClassification) -> FieldPlan

def choose_format(attr: str, canonical: str, facts: FieldFacts, locale: str) -> str     # a fill_format
def apply_format(fmt: str, canonical: str) -> str | None
def sample_for_pattern(pattern: str | None, rng: random.Random) -> str | None
def choose_option(facts: FieldFacts, wanted: str | None, rng: random.Random) -> OptionFacts | None

@dataclass
class TokenResult:
    tokens: dict[str, str]; methods: dict[str, str]; reveal_hints: list[RevealHint]; document: dict[str, str]
def tokens_for(engine: PrivacyEngine, *, key: KeyMaterial, policy: Policy, locale: str, persona_no: int,
               items: dict[str, tuple[str, str]]) -> TokenResult     # field key -> (entity, value)

@dataclass
class FillAction:
    key: str; frame_index: int; field_no: int; kind: FillKind
    value: str | None = None; option_value: str | None = None; check: bool = False
@dataclass
class PersonaFill:
    actions: list[FillAction]; shown: dict[str, str | None]; methods: dict[str, str]
    formats: dict[str, str | None]; needles: list[str]
def prepare_fill(fields: list[FieldFacts], classes: dict[str, FieldClassification], plans: dict[str, FieldPlan], *,
                 persona: dict[str, str], persona_no: int, seed: bytes, locale: str,
                 mode: Literal["synthesize", "obfuscate"],
                 tokens: Callable[[dict[str, tuple[str, str]]], TokenResult] | None) -> PersonaFill
```

Non-obvious code (complete):

```python
def _rng(seed: bytes, *parts: object) -> random.Random:
    return random.Random(seed_int(seed, "scanner|" + "|".join(str(p) for p in parts)))


def persona_values(engine: PrivacyEngine, *, seed: SynthesisSeed, persona_no: int, locale: str,
                   policy: Policy) -> dict[str, str]:
    raw = seed_bytes(seed)
    loc = normalise_locale(locale)
    attrs = sorted(PERSONA_ATTRS)
    doc = {a: PERSONA_ATTRS[a].exemplar(_rng(raw, "exemplar", persona_no, a)) for a in attrs}
    entries = [PlanEntry(finding=Finding(locator=Locator(path=f"$.{a}"), entity_type=PERSONA_ATTRS[a].entity, score=1.0,
                                         detectors=["scanner:persona"],
                                         sensitivity=entity_info(PERSONA_ATTRS[a].entity).sensitivity,
                                         value_hash=hash_value(doc[a])),
                         method="surrogate", params={}, reason="scanner:persona", reversible=False) for a in attrs]
    plan = Plan(mode="synthesize", record_sensitivity=Sensitivity.CRITICAL, entries=entries)
    options = ApplyOptions(policy=policy, key=None, surrogates=SurrogateEngine(raw, default_locale=loc),
                           fpe_enabled=False, cipher_factory=None, token_style="natural", locale=loc,
                           anchor=f"persona:{persona_no}")
    values: dict[str, str] = dict(apply_plan(plan=plan, options=options, data=doc).output)
    rng = _rng(raw, "derived", persona_no)
    parts = values["name"].split()
    values["given_name"], values["family_name"] = parts[0], parts[-1]
    values["address_line2"] = f"Floor {rng.randint(1, 20)}"
    values["region"] = rng.choice(LOCALE_REGIONS.get(loc, LOCALE_REGIONS["en_US"]))
    values["country"] = LOCALE_COUNTRY.get(loc, "United States")
    born = date.fromisoformat(values["dob"])
    values["age"] = str((date.today() - born).days // 365)
    values["credential"] = "Dps!" + b32(hmac_bytes(raw, f"scanner|credential|{persona_no}"))[:12]
    values["otp"] = f"{rng.randint(0, 999_999):06d}"
    return values


def apply_format(fmt: str, canonical: str) -> str | None:
    if fmt == "as_is":
        return canonical
    if fmt == "digits":
        return re.sub(r"\D", "", canonical) or None
    if fmt == "national":
        try:
            return str(phonenumbers.parse(canonical, None).national_number)
        except phonenumbers.NumberParseException:
            return None
    if fmt.startswith("shape:"):
        shape, chars = fmt[6:], re.sub(r"[^A-Za-z0-9]", "", canonical)
        if len(chars) != shape.count("#"):
            return None
        it = iter(chars)
        return "".join(next(it) if ch == "#" else ch for ch in shape)
    if fmt.startswith("date:"):
        try:
            return date.fromisoformat(canonical[:10]).strftime(fmt[5:])
        except ValueError:
            return None
    if fmt.startswith("expiry:"):
        m = re.fullmatch(r"(\d{2})/(\d{2})", canonical)
        return date(2000 + int(m.group(2)), int(m.group(1)), 1).strftime(fmt[7:]) if m else None
    raise ValueError("unknown fill format")


def choose_format(attr: str, canonical: str, facts: FieldFacts, locale: str) -> str:
    """The first rendering of the persona value that the field's own rules accept (pattern,
    maxlength, input type). The choice is stored, so the export re-renders every persona the same way."""
    if facts.input_type == "date":
        return "date:%Y-%m-%d"
    if facts.input_type == "month":
        return "expiry:%Y-%m" if attr == "card_expiry" else "date:%Y-%m"
    candidates: list[str]
    if attr == "dob":
        ph = (facts.placeholder or "").strip().lower()
        candidates = ([f"date:{DATE_PLACEHOLDERS[ph]}"] if ph in DATE_PLACEHOLDERS else [])
        candidates += ["date:%m/%d/%Y" if locale == "en_US" else "date:%d/%m/%Y", "date:%Y-%m-%d"]
    elif attr == "card_expiry":
        candidates = ["as_is", "expiry:%m/%Y"]
    else:
        candidates = ["as_is", *(f"shape:{s}" for s in SHAPES.get(attr, ())),
                      *(["national"] if attr == "phone" else []), "digits"]
    pattern = compile_html_pattern(facts.pattern)
    for fmt in dict.fromkeys(candidates):
        value = apply_format(fmt, canonical)
        if value is None or (facts.maxlength and len(value) > facts.maxlength):
            continue
        if pattern is not None and not pattern.fullmatch(value):
            continue
        if facts.input_type == "number" and not value.isdigit():
            continue
        return fmt
    return candidates[0]
```

with `SHAPES = {"aadhaar": ("#### #### ####", "############", "####-####-####"), "card_number": ("#### #### #### ####", "################", "####-####-####-####"), "ssn": ("###-##-####", "#########"), "itin": ("###-##-####", "#########"), "sin": ("### ### ###", "#########"), "postal_code": ("#####", "######")}` and `DATE_PLACEHOLDERS = {"dd/mm/yyyy": "%d/%m/%Y", "mm/dd/yyyy": "%m/%d/%Y", "yyyy-mm-dd": "%Y-%m-%d", "dd-mm-yyyy": "%d-%m-%Y", "dd.mm.yyyy": "%d.%m.%Y"}`.

```python
_PAT_TOKEN = re.compile(r"\\d|\\w|\\(.)|\[([^\]]+)\]|([^\\\[\](){}?*+|^$.])|(\{(\d+)(?:,(\d*))?\}|\?|\+|\*)")


def sample_for_pattern(pattern: str | None, rng: random.Random) -> str | None:
    """A neutral value for a non-personal field with a pattern (``APP-\\d{4}-\\d{4}`` → ``APP-4821-0937``).
    Supports literals, escapes, ``\\d``, ``\\w``, character classes with ranges and the quantifiers
    ``{n}``, ``{n,m}``, ``?``, ``+``, ``*``. Anything else (groups, alternation, anchors) → ``None``."""
    if not pattern or compile_html_pattern(pattern) is None or re.search(r"[()|^$]", pattern.replace("\\(", "").replace("\\)", "")):
        return None
    out: list[str] = []
    pos, last_pool = 0, None
    while pos < len(pattern):
        m = _PAT_TOKEN.match(pattern, pos)
        if m is None:
            return None
        tok = m.group(0)
        if m.group(4) is not None:                         # a quantifier applies to the previous pool
            if last_pool is None or not out:
                return None
            reps = 0 if tok in ("?", "*") else (int(m.group(5)) if m.group(5) else 1)
            out.pop()
            out.extend(rng.choice(last_pool) for _ in range(reps))
        else:
            if tok == "\\d":
                pool = "0123456789"
            elif tok == "\\w":
                pool = "abcdefghjkmnpqrstuvwxyz23456789"
            elif m.group(1) is not None:
                pool = m.group(1)
            elif m.group(2) is not None:
                pool = _expand_class(m.group(2))
                if not pool:
                    return None
            else:
                pool = tok
            out.append(rng.choice(pool))
            last_pool = pool
        pos = m.end()
    value = "".join(out)
    compiled = compile_html_pattern(pattern)
    return value if compiled is not None and compiled.fullmatch(value) else None


def _expand_class(body: str) -> str:
    if body.startswith("^"):
        return ""
    chars, i = [], 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            chars.extend(chr(c) for c in range(ord(body[i]), ord(body[i + 2]) + 1))
            i += 3
        elif body[i] == "\\" and i + 1 < len(body):
            chars.extend("0123456789" if body[i + 1] == "d" else body[i + 1])
            i += 2
        else:
            chars.append(body[i])
            i += 1
    return "".join(dict.fromkeys(chars))


def tokens_for(engine: PrivacyEngine, *, key: KeyMaterial, policy: Policy, locale: str, persona_no: int,
               items: dict[str, tuple[str, str]]) -> TokenResult:
    """Tokens of persona values (obfuscate mode) through the engine — policy methods, FPE when
    enabled, det_token fallback — verified so that no persona value survives its own token."""
    keys = list(items)
    doc = {f"f{i}": items[k][1] for i, k in enumerate(keys)}
    findings = [Finding(locator=Locator(path=f"$.f{i}"), entity_type=items[k][0], score=1.0, detectors=["scanner:fill"],
                        sensitivity=entity_info(items[k][0]).sensitivity, value_hash=hash_value(items[k][1]))
                for i, k in enumerate(keys)]
    plan = build_plan(findings, policy, "obfuscate", fpe_enabled=engine.fpe_enabled)
    options = ApplyOptions(policy=policy, key=key, surrogates=None, fpe_enabled=engine.fpe_enabled,
                           cipher_factory=engine.cipher_factory, token_style="natural",
                           locale=normalise_locale(locale), anchor=f"persona:{persona_no}")
    applied = apply_plan(plan=plan, options=options, data=doc)
    kept = {(e.finding.locator.path, None, None) for e in plan.entries if e.method == "keep"}
    if not verify_output(collect_originals(findings, data=doc), applied.output, kept=kept).passed:
        raise LeakageError("leakage")
    hint_method = {h.path: h.method for h in applied.reveal_hints}
    tokens = {k: str(applied.output[f"f{i}"]) for i, k in enumerate(keys)}
    methods = {k: hint_method.get(f"$.f{i}", plan.entries[i].method) for i, k in enumerate(keys)}
    return TokenResult(tokens=tokens, methods=methods, reveal_hints=applied.reveal_hints, document=dict(applied.output))
```

`plan_field` rules (complete): `file` → `skip/skipped_file`; `hidden` or not visible → `skip/skipped_hidden`; readonly or disabled → `skip/skipped_readonly`; attribute = `TOKEN_ATTR[token]` if the autocomplete token maps, else `ENTITY_ATTR[entity]`; password input or attribute `credential`/`otp` → `credential/filled`; `select` → `select`, `filled` when an attribute maps else `neutral`; checkbox → `check`, `neutral` if required else `not_filled`; radio → `radio` (`filled`/`neutral` as for select); no attribute → `neutral_text/neutral`; otherwise `text` (or `date` for `type=date|month`) `filled`, `obfuscate = sensitivity in (MEDIUM, HIGH, CRITICAL)`. There is no branch that targets a button, `type=submit|image|reset|button` (they are never extracted), or a link.

`prepare_fill` (complete behaviour): for each planned field in field order — `text/date`: `value = apply_format(fmt := choose_format(attr, persona[attr], f, locale), persona[attr])`; `credential`: `persona["credential"]` (or `persona["otp"]` for `otp`), `shown=None`; `neutral_text`: `sample_for_pattern(f.pattern, rng)` else `"1"` for `type=number`, `"Sample text"` for textarea, `"Sample"` otherwise, method `neutral`; `select`/`radio`: `choose_option(f, persona.get(attr), _rng(seed, "option", persona_no, hash_value(f"{f.name}|{f.id}|{f.label}")))` (radios grouped by `name`: one checked per group); `check`: `check=True` only for `neutral`. In `obfuscate` mode the PII text fields (`plan.obfuscate`) go through `tokens(...)` in one call and their values/methods are replaced by the tokens; everything else keeps the synthesize value (method `keep`). `needles` = the shown values of `plan.obfuscate` fields (sensitive values only — select labels like "Female" would make harmless static URLs look suspicious). `shown` for credentials is `None`.

M0 touch point — `dps/engine/synth/surrogates.py`, in `_generate` before the `DIAGNOSIS_CODE…` branch:

```python
        if entity == "IFSC":   # rule-shaped: 4 bank letters, a literal 0, 6 branch characters
            letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
            return ("".join(rng.choice(letters) for _ in range(4)) + "0"
                    + "".join(rng.choice(letters + "0123456789") for _ in range(6)))
```

and remove `"IFSC"` from the tuple in the following branch. `tests/test_surrogates.py` gains `test_ifsc_surrogate_is_rule_shaped`: `re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", eng.surrogate("IFSC", "HDFC0001234", "en_IN", "r1"))` and it differs from the original.

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_personas_fill.py` (uses `scan_engine` from the scanner conftest and `F()`/`C()` helpers):

```python
SEED = generate_synthesis_seed()
POLICY = load_preset("gdpr-pseudonymise")


def test_persona_values_are_deterministic_coherent_and_rule_valid(scan_engine) -> None:
    p1 = persona_values(scan_engine, seed=SEED, persona_no=1, locale="en_IN", policy=POLICY)
    assert p1 == persona_values(scan_engine, seed=SEED, persona_no=1, locale="en_IN", policy=POLICY)
    p2 = persona_values(scan_engine, seed=SEED, persona_no=2, locale="en_IN", policy=POLICY)
    assert p1["name"] != p2["name"] and p1["aadhaar"] != p2["aadhaar"] and p1["card_number"] != p2["card_number"]
    assert validate("AADHAAR", p1["aadhaar"]) and luhn_valid(p1["card_number"]) and p1["card_number"].startswith("411111")
    assert re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", p1["pan"]) and p1["ssn"].startswith("9")
    assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", p1["ifsc"])
    assert p1["email"].split("@")[1] in {"example.com", "example.net", "example.org"}
    assert p1["given_name"] == p1["name"].split()[0] and p1["country"] == "India"
    assert date.fromisoformat(p1["dob"]) and p1["credential"].startswith("Dps!")


def test_formats_follow_the_field_rules() -> None:
    aad = F(pattern=r"\d{4} \d{4} \d{4}")
    assert choose_format("aadhaar", "917322064418", aad, "en_IN") == "shape:#### #### ####"
    assert apply_format("shape:#### #### ####", "917322064418") == "9173 2206 4418"
    assert choose_format("dob", "1988-07-29", F(placeholder="DD/MM/YYYY", pattern=r"\d{2}/\d{2}/\d{4}"), "en_IN") == "date:%d/%m/%Y"
    assert choose_format("dob", "1988-07-29", F(input_type="date"), "en_US") == "date:%Y-%m-%d"
    assert choose_format("phone", "+91 81234 56789", F(pattern=r"[6-9]\d{9}"), "en_IN") == "national"
    assert choose_format("card_expiry", "04/29", F(input_type="month"), "en_IN") == "expiry:%Y-%m"
    assert apply_format("expiry:%Y-%m", "04/29") == "2029-04"


def test_sample_for_pattern_and_unsupported_patterns() -> None:
    rng = random.Random(7)
    assert re.fullmatch(r"APP-\d{4}-\d{4}", sample_for_pattern(r"APP-\d{4}-\d{4}", rng) or "")
    assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", sample_for_pattern("[A-Z]{4}0[A-Z0-9]{6}", rng) or "")
    assert sample_for_pattern("(?=x)y|z", rng) is None and sample_for_pattern(None, rng) is None


def test_choose_option_matches_persona_and_skips_placeholders() -> None:
    f = F(tag="select", input_type="select-one", options=[
        OptionFacts(value="", text="Choose…", disabled=False, selected=True),
        OptionFacts(value="Female", text="Female", disabled=False, selected=False),
        OptionFacts(value="Male", text="Male", disabled=False, selected=False)])
    assert choose_option(f, "female", random.Random(1)).value == "Female"
    picks = {choose_option(f, None, random.Random(s)).value for s in range(20)}
    assert picks <= {"Female", "Male"} and choose_option(f, None, random.Random(3)) == choose_option(f, None, random.Random(3))


def test_plan_field_never_targets_files_hidden_readonly() -> None:
    assert plan_field(F(input_type="file"), C(None, None)).fill_status == "skipped_file"
    assert plan_field(F(input_type="hidden", visible=False), C(None, None)).fill_status == "skipped_hidden"
    assert plan_field(F(readonly=True), C("EMAIL", "MEDIUM")).fill_status == "skipped_readonly"
    assert plan_field(F(input_type="password"), C("PASSWORD", "CRITICAL")).kind == "credential"
    assert plan_field(F(input_type="checkbox"), C(None, None)).fill_status == "not_filled"
    p = plan_field(F(autocomplete="given-name"), C("PERSON_NAME", "MEDIUM"))
    assert (p.kind, p.persona_attr, p.obfuscate) == ("text", "given_name", True)


def test_tokens_of_persona_values_keep_shape_and_reveal_to_the_persona(scan_engine) -> None:
    key = generate_key_material("key")
    items = {"0:3": ("AADHAAR", "9173 2206 4418"), "0:6": ("EMAIL", "meera.venkatesan@example.com")}
    res = tokens_for(scan_engine, key=key, policy=POLICY, locale="en_IN", persona_no=1, items=items)
    assert res.tokens["0:3"] != "9173 2206 4418" and re.fullmatch(r"\d{4} \d{4} \d{4}", res.tokens["0:3"])
    assert res.tokens["0:6"].endswith("@example.com") and res.methods["0:3"] == "fpe_digits"
    revealed = scan_engine.reveal(Payload(data=res.document), key.model_dump(mode="json"),
                                  [h.model_dump() for h in res.reveal_hints])
    assert revealed.result == {"f0": "9173 2206 4418", "f1": "meera.venkatesan@example.com"}


def test_det_token_fallback_without_fpe(det_engine) -> None:
    res = tokens_for(det_engine, key=generate_key_material("key"), policy=POLICY, locale="en_IN", persona_no=1,
                     items={"0:3": ("AADHAAR", "9173 2206 4418")})
    assert res.methods["0:3"] == "det_token" and not re.fullmatch(r"\d{4} \d{4} \d{4}", res.tokens["0:3"])


def test_prepare_fill_modes(scan_engine) -> None:
    fields = [F(key="0:1", field_no=1, label="Full name", autocomplete="name"),
              F(key="0:3", field_no=3, label="Aadhaar number", pattern=r"\d{4} \d{4} \d{4}"),
              F(key="0:8", field_no=8, tag="select", input_type="select-one", autocomplete="sex", options=[
                  OptionFacts(value="", text="Choose…", disabled=False, selected=True),
                  OptionFacts(value="Female", text="Female", disabled=False, selected=False),
                  OptionFacts(value="Male", text="Male", disabled=False, selected=False)])]
    classes = {"0:1": C("PERSON_NAME", "MEDIUM"), "0:3": C("AADHAAR", "CRITICAL"), "0:8": C("GENDER", "LOW")}
    plans = {f.key: plan_field(f, classes[f.key]) for f in fields}
    persona = persona_values(scan_engine, seed=SEED, persona_no=1, locale="en_IN", policy=POLICY)
    syn = prepare_fill(fields, classes, plans, persona=persona, persona_no=1, seed=seed_bytes(SEED), locale="en_IN",
                       mode="synthesize", tokens=None)
    assert syn.shown["0:1"] == persona["name"] and syn.shown["0:3"] == apply_format("shape:#### #### ####", persona["aadhaar"])
    assert syn.shown["0:8"] == persona["gender"].title() and set(syn.needles) == {syn.shown["0:1"], syn.shown["0:3"]}
    key = generate_key_material("key")
    obf = prepare_fill(fields, classes, plans, persona=persona, persona_no=1, seed=seed_bytes(SEED), locale="en_IN",
                       mode="obfuscate", tokens=functools.partial(tokens_for, scan_engine, key=key, policy=POLICY,
                                                                  locale="en_IN", persona_no=1))
    assert obf.shown["0:3"] != syn.shown["0:3"] and obf.methods["0:3"] == "fpe_digits"
    assert obf.shown["0:8"] == syn.shown["0:8"] and obf.methods["0:8"] == "keep"
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_personas_fill.py` → FAIL (`ModuleNotFoundError: dps.layers.scanner.personas_fill`).

- [ ] **Step 3: Implement** `personas_fill.py` (code above plus the tables and `plan_field`/`prepare_fill`/`choose_option`/`preview_entities` as specified: `preview_entities("en_IN") == [("Name", "name"), ("Date of birth", "dob"), ("Aadhaar", "aadhaar"), ("PAN", "pan"), ("Mobile", "phone")]`; en_US `name, dob, ssn, phone, email`; en_GB `name, dob, nino, phone, email`; en_CA `name, dob, sin, phone, email`; es_MX `name, dob, curp, rfc, email`) and the IFSC surrogate change with its test.

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_personas_fill.py tests/test_surrogates.py && ruff check dps tests && mypy dps` → `8 passed` (scanner) and the surrogate suite green, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/personas_fill.py dps/engine/synth/surrogates.py tests/test_surrogates.py tests/scanner/test_personas_fill.py
git commit -m "feat(scanner): coherent seeded personas through the surrogate engine, field-rule formatting, tokens of persona values" -- dps/layers/scanner/personas_fill.py dps/engine/synth/surrogates.py tests/test_surrogates.py tests/scanner/test_personas_fill.py
```

---
### Task 12: Captures — screenshots, numbered overlays, mask boxes, obfuscated captures

**Files:**
- Create: `dps/layers/scanner/capture.py`
- Modify: `tests/scanner/helpers.py` (add the browser-test helpers below)
- Test: `tests/scanner/test_capture.py` (two Pillow unit tests + four `browser` tests)

**Interfaces introduced:**

```python
VARIANT_COLOUR: dict[str, tuple[int, int, int]]     # original amber, synthesize violet, obfuscate teal (Vault signal colours)
MASK_FILL: tuple[int, int, int] = (15, 21, 36)
@dataclass(frozen=True)
class OverlayBox: number: int; rect: Rect
@dataclass(frozen=True)
class MaskBox: rect: Rect; label: str
@dataclass
class CaptureResult:
    status: Literal["captured", "withheld"]; png: bytes | None = None; capture_json: bytes | None = None
    reason: str | None = None; methods_used: dict[str, int] = field(default_factory=dict)

def crop_height(png: bytes, max_height: int) -> bytes
def draw_masks(png: bytes, masks: list[MaskBox], *, pad: int = 2) -> bytes
def draw_overlays(png: bytes, boxes: list[OverlayBox], *, variant: str) -> bytes
async def field_boxes(frames: dict[int, Frame], fields: list[FieldFacts]) -> list[OverlayBox]
async def capture_screen(page: Page, frames: dict[int, Frame], snapshot: PageSnapshot, *,
                         rendered: list[RenderedFinding], prefilled: list[tuple[FieldFacts, str]],
                         max_height: int, unmasked: bool = False) -> bytes | None     # None = withheld (fail closed)
async def obfuscated_capture(page: Page, frames: dict[int, Frame], snapshot: PageSnapshot, *,
                             rendered: list[RenderedFinding], prefilled: list[tuple[FieldFacts, str]],
                             engine: PrivacyEngine, policy: Policy, mode: Literal["obfuscate", "synthesize"],
                             key: KeyMaterial | None, seed: bytes, locale: str, guard: ScanGuard,
                             settings: Settings, page_no: int) -> CaptureResult
```

Rules: an unmasked image of a page that renders personal data (or arrived with sensitive values pre-filled) never leaves memory — `capture_screen` paints a solid box (labelled with the entity type) over every rendered finding and every sensitive pre-filled input *before* PNG encoding, and returns `None` when any of them cannot be located (fail closed). `unmasked=True` is used only for the `original` variant when `scan_store_unmasked_originals` is set (local; posture refuses it in staging/prod). The obfuscated capture replaces rendered values and pre-filled input values **in the DOM** (tokens with a reversible map in `obfuscate` mode, persona surrogates in `synthesize` mode), re-reads what the page now renders, boxes any original that survived (values split across nodes), withholds the capture if one cannot be boxed, and arms the guard first (a modified page must not navigate). Frames (M4-G2 fix, D-C): every capture paints `dom.unread_frame_boxes(snapshot)` (child frames never read: cross-origin, or iframes off) and is **withheld** when it returns `None` (a frame failed while it was read — `PageSnapshot.frames_failed` — or an unread frame cannot be located).

`tests/scanner/helpers.py` additions (complete):

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dps.config import Settings
from dps.layers.scanner.browser import close_scan_context, new_scan_context
from dps.layers.scanner.guard import ScanGuard
from dps.layers.scanner.urls import CrawlScope, Origin

DEMO_VALUE_KEYS = ("full_name", "aadhaar", "pan", "card_number", "email", "mobile", "address")


def demo_cookie_state(host: str) -> dict[str, Any]:
    return {"cookies": [{"name": "demo_session", "value": "demo-valid", "domain": host, "path": "/", "expires": -1,
                         "httpOnly": True, "secure": False, "sameSite": "Lax"}], "origins": []}


@asynccontextmanager
async def guarded_context(browser: Any, settings: Settings, base_url: str, *, harden_js: bool = True,
                          cookies: bool = False) -> AsyncIterator[tuple[Any, ScanGuard]]:
    """A scan-like context: same browser options and the real guard, installed before any page."""
    origin = Origin.of(base_url)
    scope = CrawlScope(start=origin, same_origin=True, registered_hosts=frozenset(settings.scan_registered_hosts_list))
    guard = ScanGuard(host_allowed=scope.network_host_allowed)
    ctx = await new_scan_context(browser, settings, storage_state=demo_cookie_state(origin.host) if cookies else None,
                                 locale="en_IN", guard=guard, harden_js=harden_js)   # M4-G2 fix: proxy + guard
    try:
        yield ctx, guard
    finally:
        await close_scan_context(ctx, guard)       # every page through guard.close, then the proxy
```

Non-obvious code (complete):

```python
def _open_image(png: bytes) -> Image.Image:
    with Image.open(io.BytesIO(png)) as im:
        return im.convert("RGB")


def _encode(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _clamp(rect: Rect, im: Image.Image, pad: int) -> tuple[int, int, int, int] | None:
    x0, y0 = max(0, int(rect.x) - pad), max(0, int(rect.y) - pad)
    x1, y1 = min(im.width - 1, int(rect.x + rect.w) + pad), min(im.height - 1, int(rect.y + rect.h) + pad)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def crop_height(png: bytes, max_height: int) -> bytes:
    im = _open_image(png)
    return png if im.height <= max_height else _encode(im.crop((0, 0, im.width, max_height)))


def draw_masks(png: bytes, masks: list[MaskBox], *, pad: int = 2) -> bytes:
    im = _open_image(png)
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=11)
    for m in masks:
        box = _clamp(m.rect, im, pad)
        if box is None:
            continue
        draw.rectangle(box, fill=MASK_FILL)
        if box[3] - box[1] >= 12 and box[2] - box[0] >= 24:
            draw.text((box[0] + 3, box[1] + 1), m.label[: (box[2] - box[0] - 6) // 6], fill=(169, 180, 204), font=font)
    return _encode(im)


def draw_overlays(png: bytes, boxes: list[OverlayBox], *, variant: str) -> bytes:
    colour = VARIANT_COLOUR[variant]
    im = _open_image(png)
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=11)
    for b in boxes:
        box = _clamp(b.rect, im, 1)
        if box is None:
            continue
        draw.rectangle(box, outline=colour, width=2)
        label = str(b.number)
        w = 7 * len(label) + 9
        bx0, by0 = max(0, box[2] - w - 2), max(0, box[1] - 9)
        draw.rounded_rectangle((bx0, by0, bx0 + w, by0 + 16), radius=8, fill=colour)
        draw.text((bx0 + 4, by0 + 2), label, fill=(6, 17, 15), font=font)
    return _encode(im)


async def field_boxes(frames: dict[int, Frame], fields: list[FieldFacts]) -> list[OverlayBox]:
    await frames[0].evaluate(SCROLL_JS)            # bounding boxes at scroll 0 == document coordinates
    out: list[OverlayBox] = []
    for f in fields:
        if not f.visible or f.frame_index not in frames:
            continue
        box = await frames[f.frame_index].locator(f'[data-dps-field="{f.key}"]').bounding_box()
        if box is not None:
            out.append(OverlayBox(number=f.field_no, rect=Rect(x=box["x"], y=box["y"], w=box["width"], h=box["height"])))
    return out


async def _full_page_png(page: Page, max_height: int) -> bytes:
    await page.evaluate(SCROLL_JS)
    png = await page.screenshot(full_page=True, type="png", animations="disabled", caret="hide", scale="css")
    return crop_height(png, max_height)


async def capture_screen(page: Page, frames: dict[int, Frame], snapshot: PageSnapshot, *,
                         rendered: list[RenderedFinding], prefilled: list[tuple[FieldFacts, str]],
                         max_height: int, unmasked: bool = False) -> bytes | None:
    masks: list[MaskBox] = []
    if not unmasked:
        await page.evaluate(SCROLL_JS)
        rects = await text_rects(page, frames, snapshot, [(r.frame_index, r.i, r.start, r.end) for r in rendered])
        for finding, found in zip(rendered, rects, strict=True):
            if not found:
                return None                                         # cannot locate → withhold, never store unmasked
            masks += [MaskBox(rect=r, label=finding.entity_type) for r in found]
        for f, entity in prefilled:
            frame = frames.get(f.frame_index)
            box = await frame.locator(f'[data-dps-field="{f.key}"]').bounding_box() if frame else None
            if box is None:
                return None
            masks.append(MaskBox(rect=Rect(x=box["x"], y=box["y"], w=box["width"], h=box["height"]), label=entity))
    png = await _full_page_png(page, max_height)
    return draw_masks(png, masks) if masks else png


_T_INDEX = re.compile(r"^\$\.t\[(\d+)\]")


async def obfuscated_capture(page: Page, frames: dict[int, Frame], snapshot: PageSnapshot, *,
                             rendered: list[RenderedFinding], prefilled: list[tuple[FieldFacts, str]],
                             engine: PrivacyEngine, policy: Policy, mode: Literal["obfuscate", "synthesize"],
                             key: KeyMaterial | None, seed: bytes, locale: str, guard: ScanGuard,
                             settings: Settings, page_no: int) -> CaptureResult:
    texts = snapshot.texts
    g_of = {(t.frame_index, t.i): g for g, t in enumerate(texts)}
    doc: dict[str, list[str]] = {"t": [t.text for t in texts], "v": [f.value or "" for f, _ in prefilled]}
    findings: list[Finding] = []
    for r in rendered:
        g = g_of[(r.frame_index, r.i)]
        text = texts[g].text
        whole = r.start <= len(text) - len(text.lstrip()) and r.end >= len(text.rstrip())
        findings.append(Finding(locator=Locator(path=f"$.t[{g}]", start=None if whole else r.start,
                                                end=None if whole else r.end),
                                entity_type=r.entity_type, score=r.score, detectors=r.detectors,
                                sensitivity=entity_info(r.entity_type).sensitivity,
                                value_hash=hash_value(text[r.start:r.end])))
    for j, (f, entity) in enumerate(prefilled):
        findings.append(Finding(locator=Locator(path=f"$.v[{j}]"), entity_type=entity, score=1.0,
                                detectors=["scanner:prefilled"], sensitivity=entity_info(entity).sensitivity,
                                value_hash=hash_value(f.value or "")))
    plan = build_plan(findings, policy, mode, fpe_enabled=engine.fpe_enabled)
    options = ApplyOptions(policy=policy, key=key if mode == "obfuscate" else None,
                           surrogates=SurrogateEngine(seed, default_locale=locale) if mode == "synthesize" else None,
                           fpe_enabled=engine.fpe_enabled, cipher_factory=engine.cipher_factory,
                           token_style="natural", locale=locale)
    applied = apply_plan(plan=plan, options=options, data=doc)
    kept = {(e.finding.locator.path, e.finding.locator.start, e.finding.locator.end) for e in plan.entries if e.method == "keep"}
    originals = collect_originals(findings, data=doc)
    if not verify_output(originals, applied.output, kept=kept).passed:
        return CaptureResult(status="withheld", reason="leakage")
    if not await guard.arm(page, ()):                    # a modified page must never navigate
        return CaptureResult(status="withheld", reason="guard_lock")
    out_t, out_v = applied.output["t"], applied.output["v"]
    text_pairs: dict[int, list[list[Any]]] = defaultdict(list)
    for g, t in enumerate(texts):
        if out_t[g] != t.text:
            text_pairs[t.frame_index].append([t.i, out_t[g]])
    for frame_index, pairs in text_pairs.items():
        await frames[frame_index].evaluate(REPLACE_TEXT_JS, pairs)
    value_pairs: dict[int, list[list[str]]] = defaultdict(list)
    for j, (f, _) in enumerate(prefilled):
        value_pairs[f.frame_index].append([f.key, out_v[j]])
    for frame_index, pairs in value_pairs.items():
        await frames[frame_index].evaluate(SET_VALUES_JS, pairs)
    # Residual sweep over what the page renders NOW: any original still present is boxed; one that
    # cannot be located as text (split across nodes, inside an input) withholds the capture.
    after = await extract_page(page, iframes=len(frames) > 1, shadow_dom=True, max_fields=settings.scan_max_fields_per_page)
    haystack = normalise(json.dumps({"t": [t.text for t in after.texts], "v": [f.value or "" for f in after.fields]}))
    spans: list[tuple[int, int, int, int, str]] = []
    for finding, original in originals:
        loc = finding.locator
        needle = original.strip()
        if (loc.path, loc.start, loc.end) in kept or len(normalise(needle)) < 4 or normalise(needle) not in haystack:
            continue
        located = [(t.frame_index, t.i, t.text.find(needle), t.text.find(needle) + len(needle), finding.entity_type)
                   for t in after.texts if needle in t.text]
        if not located:
            return CaptureResult(status="withheld", reason="residual")
        spans += located
    masks: list[MaskBox] = []
    if spans:
        rects = await text_rects(page, frames, after, [s[:4] for s in spans])
        for span, found in zip(spans, rects, strict=True):
            if not found:
                return CaptureResult(status="withheld", reason="residual")
            masks += [MaskBox(rect=r, label=span[4]) for r in found]
    png = await _full_page_png(page, settings.scan_screenshot_max_height)
    if masks:
        png = draw_masks(png, masks)
    capture_json: bytes | None = None
    if mode == "obfuscate":
        touched = {int(m.group(1)) for e in plan.entries if (m := _T_INDEX.match(e.finding.locator.path))}
        data = {"t": [out_t[g] if g in touched else None for g in range(len(texts))], "v": list(out_v)}
        capture_json = json.dumps({"format": "dps.capture.v1", "page_no": page_no, "mode": mode,
                                   "kid": key.kid if key is not None else None, "data": data,
                                   "reveal_hints": [h.model_dump(mode="json") for h in applied.reveal_hints],
                                   "methods_used": applied.methods_used}, sort_keys=True).encode("utf-8")
    return CaptureResult(status="captured", png=png, capture_json=capture_json, methods_used=applied.methods_used)
```

The capture JSON keeps only the text nodes that held a finding (as tokens) — every other node is `null` — so a value the detectors missed is not copied into a second artefact. A caller holding the key reveals it with `POST /api/v1/reveal {"data": <data>, "key_material": …, "reveal_hints": <reveal_hints>}`.

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_capture.py`:

```python
def _png(w: int = 200, h: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_overlays_draw_numbered_outlines_in_the_variant_colour() -> None:
    out = Image.open(io.BytesIO(draw_overlays(_png(), [OverlayBox(7, Rect(x=10, y=20, w=80, h=30))], variant="synthesize")))
    assert out.size == (200, 100) and out.getpixel((9, 35)) == VARIANT_COLOUR["synthesize"]
    assert out.getpixel((150, 80)) == (255, 255, 255)


def test_masks_fill_rects_and_crop_limits_height() -> None:
    masked = Image.open(io.BytesIO(draw_masks(_png(), [MaskBox(Rect(x=40, y=40, w=60, h=20), "AADHAAR")])))
    assert masked.getpixel((45, 58)) == MASK_FILL
    assert Image.open(io.BytesIO(crop_height(_png(200, 100), 60))).size == (200, 60)


@pytest.mark.browser
async def test_original_of_a_page_rendering_pii_is_masked(chromium, scan_settings, scan_engine, demo_site_url) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        det = scan_engine.detector(scan_engine.policy(None), "en_IN")
        rendered = classify_rendered(snap.texts, detector=det)
        assert {r.entity_type for r in rendered} >= {"PERSON_NAME", "AADHAAR", "CARD_PAN", "EMAIL"}
        png = await capture_screen(page, frames, snap, rendered=rendered, prefilled=[], max_height=12000)
        rects = await text_rects(page, frames, snap, [(r.frame_index, r.i, r.start, r.end) for r in rendered])
        im = Image.open(io.BytesIO(png))
        for found in rects:
            r = found[0]   # sample inside the 2px padding, left of the entity label the mask carries
            assert im.getpixel((int(r.x) - 1, int(r.y + r.h / 2))) == MASK_FILL


@pytest.mark.browser
async def test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key(chromium, scan_settings, scan_engine,
                                                                           demo_site_url, demo_data) -> None:
    key = generate_key_material("key")
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        det = scan_engine.detector(scan_engine.policy(None), "en_IN")
        rendered = classify_rendered(snap.texts, detector=det)
        res = await obfuscated_capture(page, frames, snap, rendered=rendered, prefilled=[], engine=scan_engine,
                                       policy=scan_engine.policy(None), mode="obfuscate", key=key,
                                       seed=seed_bytes(generate_synthesis_seed()), locale="en_IN", guard=guard,
                                       settings=scan_settings, page_no=1)
        shown = normalise(await page.evaluate("document.body.innerText"))
    assert res.status == "captured" and res.png and res.capture_json
    for k in DEMO_VALUE_KEYS:
        assert normalise(demo_data[k]) not in shown, k
    cap = json.loads(res.capture_json)
    revealed = scan_engine.reveal(Payload(data=cap["data"]), key.model_dump(mode="json"), cap["reveal_hints"]).result
    restored = normalise(json.dumps(revealed))
    for k in DEMO_VALUE_KEYS:
        assert normalise(demo_data[k]) in restored, k


@pytest.mark.browser
async def test_synthesize_capture_has_no_reveal_map(chromium, scan_settings, scan_engine, demo_site_url, demo_data) -> None:
    # same as above with mode="synthesize", key=None: status captured, capture_json is None,
    # and no demo value appears in document.body.innerText afterwards
    ...


@pytest.mark.browser
async def test_capture_fails_closed_when_a_finding_cannot_be_located(chromium, scan_settings, scan_engine, demo_site_url) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url, cookies=True) as (ctx, _guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/profile")
        snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
        frames = await frame_map(page, iframes=True)
        ghost = RenderedFinding(frame_index=0, i=99_999, start=0, end=4, entity_type="SSN", score=1.0,
                                detectors=["test"], in_table=False)
        assert await capture_screen(page, frames, snap, rendered=[ghost], prefilled=[], max_height=12000) is None
```

(The synthesize test body is the obfuscate test with `mode="synthesize"`, `key=None`, asserting `res.capture_json is None` and the innerText check — write it out in full.)

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_capture.py` → FAIL (`ModuleNotFoundError: dps.layers.scanner.capture`).

- [ ] **Step 3: Write `dps/layers/scanner/capture.py`** with the code above (imports: `io`, `json`, `re`, `defaultdict`, `dataclass/field`, PIL `Image, ImageDraw, ImageFont`, Playwright `Frame, Page`, M0 `Finding, Locator, hash_value, build_plan, apply_plan, ApplyOptions, collect_originals, verify_output, normalise, SurrogateEngine, entity_info`, and `dom.extract_page, text_rects, SCROLL_JS, REPLACE_TEXT_JS, SET_VALUES_JS`). `VARIANT_COLOUR = {"original": (251, 191, 36), "synthesize": (167, 139, 250), "obfuscate": (45, 212, 191)}`. Also create `tests/scanner/helpers.py`.

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_capture.py && ruff check dps tests && mypy dps` → `6 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/capture.py tests/scanner/helpers.py tests/scanner/test_capture.py
git commit -m "feat(scanner): masked screenshots, numbered overlays and reversible obfuscated captures (fail closed)" -- dps/layers/scanner/capture.py tests/scanner/helpers.py tests/scanner/test_capture.py
```

---

### Task 13: Filling a page in the browser — and recording what the page's own validation said

**Files:**
- Modify: `dps/layers/scanner/personas_fill.py` (add `FillResult`, `fill_page`) — Playwright is imported inside `fill_page` (and under `TYPE_CHECKING` for the annotations) so the module stays importable by the API
- Test: `tests/scanner/test_fill_browser.py` (marker `browser`)

**Interfaces introduced:**

```python
@dataclass
class FillResult:
    status: Literal["filled", "failed"]
    messages: list[dict[str, Any]] = field(default_factory=list)   # VALIDATION_JS output (page text; redacted before storage)

async def fill_page(page: Page, frames: dict[int, Frame], actions: list[FillAction], *, settle_ms: int,
                    timeout_ms: int = 3000) -> dict[str, FillResult]
```

`fill_page` (complete):

```python
async def fill_page(page: Page, frames: dict[int, Frame], actions: list[FillAction], *, settle_ms: int,
                    timeout_ms: int = 3000) -> dict[str, FillResult]:
    """Type persona values or tokens into the page. The caller has armed the guard for ``page``.

    Only ``fill`` / ``select_option`` / ``check`` on the field elements themselves — never a key
    press (no implicit submission), never a click on a button, link or image input. A change event
    and a blur are dispatched so the page's own client-side validation runs; its verdicts are read
    back after ``settle_ms`` as format evidence.
    """
    results: dict[str, FillResult] = {}
    for a in actions:
        frame = frames.get(a.frame_index)
        if frame is None:
            results[a.key] = FillResult(status="failed")
            continue
        loc = frame.locator(f'[data-dps-field="{a.key}"]')
        try:
            if a.kind in ("text", "date", "credential", "neutral_text"):
                await loc.fill(a.value or "", timeout=timeout_ms)
            elif a.kind == "select" and a.option_value is not None:
                await loc.select_option(value=a.option_value, timeout=timeout_ms)
            elif a.kind in ("check", "radio") and a.check:
                await loc.check(timeout=timeout_ms)
            await loc.evaluate("el => { el.dispatchEvent(new Event('change', { bubbles: true })); el.blur(); }")
            results[a.key] = FillResult(status="filled")
        except PlaywrightError:
            results[a.key] = FillResult(status="failed")
    await page.wait_for_timeout(settle_ms)
    for a in actions:
        if results[a.key].status != "filled":
            continue
        with contextlib.suppress(PlaywrightError):
            results[a.key].messages = await frames[a.frame_index].locator(f'[data-dps-field="{a.key}"]').evaluate(VALIDATION_JS)
    return results
```

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_fill_browser.py`:

```python
pytestmark = pytest.mark.browser
POLICY = load_preset("gdpr-pseudonymise")


async def _prepare(page, engine, mode, key=None):  # noqa: ANN001, ANN202
    snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
    frames = await frame_map(page, iframes=True)
    det = engine.detector(POLICY, "en_IN")
    classes = {f.key: classify_field(f, detector=det, min_score=0.6) for f in snap.fields}
    plans = {f.key: plan_field(f, classes[f.key]) for f in snap.fields}
    seed = generate_synthesis_seed()
    persona = persona_values(engine, seed=seed, persona_no=1, locale="en_IN", policy=POLICY)
    tokens = functools.partial(tokens_for, engine, key=key, policy=POLICY, locale="en_IN", persona_no=1) if key else None
    fill = prepare_fill(snap.fields, classes, plans, persona=persona, persona_no=1, seed=seed_bytes(seed),
                        locale="en_IN", mode=mode, tokens=tokens)
    return snap, frames, classes, fill


async def test_identity_form_takes_the_persona_and_the_page_accepts_it(chromium, scan_settings, scan_engine,
                                                                       demo_site_url) -> None:
    before = received(demo_site_url)
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply/identity")
        snap, frames, classes, fill = await _prepare(page, scan_engine, "synthesize")
        assert [classes[f.key].entity_type for f in snap.fields] == [
            "PERSON_NAME", "DATE_OF_BIRTH", "AADHAAR", "PAN_IN", "PHONE", "EMAIL", "STREET_ADDRESS", "GENDER", "NATIONALITY"]
        assert await guard.arm(page, fill.needles)
        results = await fill_page(page, frames, fill.actions, settle_ms=400)
        assert len(results) == 9 and all(r.status == "filled" for r in results.values())
        assert all(r.messages == [] for r in results.values())             # the page's own rules accept every value
        for f in snap.fields:
            if f.tag != "select":
                assert await frames[0].locator(f'[data-dps-field="{f.key}"]').input_value() == fill.shown[f.key]
        assert guard.blocked_total >= 1                                      # the autosave POST and the beacon were stopped
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"]


async def test_det_tokens_break_the_aadhaar_pattern_and_that_is_recorded(chromium, scan_settings, det_engine,
                                                                         demo_site_url) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, guard):
        page = await ctx.new_page()
        await page.goto(f"{demo_site_url}/apply/identity")
        snap, frames, classes, fill = await _prepare(page, det_engine, "obfuscate", key=generate_key_material("key"))
        assert await guard.arm(page, fill.needles)
        results = await fill_page(page, frames, fill.actions, settle_ms=400)
        aadhaar = next(f for f in snap.fields if f.name == "aadhaar")
        native = [m for m in results[aadhaar.key].messages if m["source"] == "native"]
        assert native and "patternMismatch" in native[0]["codes"]
        assert any(m["source"] == "aria" for m in results[aadhaar.key].messages)   # the demo's own message


async def test_file_hidden_and_buttons_are_never_touched(chromium, scan_settings, scan_engine, demo_site_url) -> None:
    async with guarded_context(chromium, scan_settings, demo_site_url) as (ctx, guard):
        for path in ("/fixtures/labels", "/apply/documents"):
            page = await ctx.new_page()
            await page.goto(f"{demo_site_url}{path}")
            snap, frames, classes, fill = await _prepare(page, scan_engine, "synthesize")
            touched = {a.key for a in fill.actions}
            for f in snap.fields:
                if f.input_type in ("file", "hidden"):
                    assert f.key not in touched
            assert await guard.arm(page, fill.needles)
            await fill_page(page, frames, fill.actions, settle_ms=150)
            if path == "/fixtures/labels":
                assert await page.evaluate("document.querySelector('[name=csrf]').value") == "tok-123"
            else:
                assert await page.evaluate("document.querySelector('[type=file]').files.length") == 0
            assert await guard.close(page)
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_fill_browser.py` → FAIL (`ImportError: cannot import name 'fill_page'`).

- [ ] **Step 3: Implement `FillResult` and `fill_page`** as above.

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_fill_browser.py && ruff check dps tests && mypy dps` → `3 passed`, clean. If the first test sees a native `patternMismatch` on the date of birth, `choose_format` did not pick `date:%d/%m/%Y` from the `DD/MM/YYYY` placeholder — fix the placeholder table, not the test.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/personas_fill.py tests/scanner/test_fill_browser.py
git commit -m "feat(scanner): fill pages field by field without any key press or click, recording client-side validation" -- dps/layers/scanner/personas_fill.py tests/scanner/test_fill_browser.py
```

---

### Task 14: The crawler — `ScanRunner`, budgets, SPA clicks, per-page pipeline, sinks

**Files:**
- Create: `dps/layers/scanner/runtime.py` (`ScanSink`, `MemoryScanSink`, `ScanRuntime`, `RunOutcome`, `LeaseLost` — no Playwright import), `dps/layers/scanner/crawler.py` (re-exports them for callers that import from the crawler)
- Modify: `dps/layers/scanner/inventory.py` (add `field_records`), `demo/onboarding/site.mjs` (add `/fixtures/dialogs`), `tests/scanner/helpers.py` (add `make_runtime`)
- Test: `tests/scanner/test_crawler.py` (markers `browser`; the full-site test also `slow`)

**Interfaces introduced:**

```python
# runtime.py (no Playwright import) ------------------------------------------------------------
class ScanSink(Protocol):
    async def save_batch(self, *, pages: list[PageRecord], fields: dict[int, list[FieldRecord]],
                         requests: list[RequestCount], events: list[ScanEvent]) -> None
    async def save_artefact(self, *, kind: str, data: bytes, content_type: str, meta: dict[str, Any]) -> str
    async def progress(self, progress: dict[str, Any]) -> bool          # False: lease lost / canceled -> stop
    async def audit_navigation(self, *, page_no: int, outcome: str) -> None

class MemoryScanSink:                                   # tests and local tools; production uses DbScanSink (Task 16)
    pages: dict[int, PageRecord]; fields: dict[int, list[FieldRecord]]; requests: list[RequestCount]
    events: list[ScanEvent]; artefacts: dict[str, tuple[str, bytes, dict[str, Any]]]; navigations: int
    def __init__(self, *, stop_after_pages: int | None = None) -> None

@dataclass
class ScanRuntime:
    spec: ScanSpec; start_url: str; key: KeyMaterial; seed: SynthesisSeed; storage_state: dict[str, Any] | None
    policy: Policy; locale: str; max_pages: int; max_depth: int; scope: CrawlScope

@dataclass(frozen=True)
class ClickReplay: parent: Target; frame_index: int; ordinal: int
@dataclass(frozen=True)
class Target:
    page_no: int; url: str; depth: int; parent_page_no: int | None
    via: Literal["start", "link", "spa_click", "redirect"]; replay: ClickReplay | None = None

@dataclass
class RunOutcome:
    pages_visited: int = 0; fields: int = 0; truncated: Literal["pages", "time"] | None = None; spa_routes: int = 0
    events_dropped: int = 0; submit_attempts_neutralised: int = 0; dialogs_dismissed: int = 0; popups_closed: int = 0
    websockets_closed: int = 0

class LeaseLost(RuntimeError): ...

# crawler.py ------------------------------------------------------------------------------------
class ScanRunner:
    def __init__(self, *, settings: Settings, engine: PrivacyEngine, runtime: ScanRuntime, sink: ScanSink,
                 browser: Browser, harden_js: bool = True) -> None
    async def run(self) -> RunOutcome

# inventory.py
def field_records(page_no: int, fields: list[FieldFacts], classes: dict[str, FieldClassification],
                  plans: dict[str, FieldPlan], fills: dict[str, PersonaFill], results: dict[str, dict[str, FillResult]],
                  flags: list[RiskFlag], *, detector: Detector) -> list[FieldRecord]
```

`field_records` maps each extracted field to a value-free `FieldRecord`: `label`, `selector`, `name_attr`, `id_attr` through `redact_text`; `pattern` ≤ 200 chars; `options_count` (never the options); classification; `fill_synthesize`/`fill_obfuscate` = `PersonaFill.shown` of the two modes (persona values, tokens of persona values, option labels, neutral samples; `None` for credentials); `obfuscate_method` from the obfuscate fill, `fill_format` from the synthesize fill; `validation_messages` = both modes' messages with `text` through `redact_text(limit=200)`; `placeholder_shape`; `prefilled = bool(value)` (the value itself is dropped); `fill_status` = the plan's status, or `failed` when the synthesize fill of that field failed; `risk_codes` from `risk_codes_by_field`.

Crawler code (complete for the loop, the page pipeline, discovery and replay):

```python
"""The scan loop (spec §8): breadth-first crawl of one registered site in one isolated context
with the request guard installed before the first page exists. Every page is processed in fresh
tabs:

1. visit → extract → classify → risk flags → original and annotated screenshots (masked);
2. parallel view: fill with the display persona (synthesize) and with tokens of it (obfuscate),
   each in a fresh tab armed before the first keystroke and closed after its screenshot;
3. an obfuscated capture when the page renders personal data or arrived pre-filled;
4. discovery: in-scope links (depth/page/template budgets, deny list) and click-only SPA routes.

There is no code path that submits: the only clicks are on in-scope link-like elements outside any
form on unfilled pages (SPA discovery and its replay), fills never press a key, and the guard aborts
every non-GET request and every navigation of a filled page. Page data stays in this process.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from playwright.async_api import Browser, Dialog, Page, Response
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from dps.config import Settings
from dps.engine.policy import Policy
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import SynthesisSeed, seed_bytes
from dps.engine.transform.crypto import KeyMaterial
from dps.jobs_models import ScanSpec
from dps.layers.scanner.browser import close_scan_context, new_scan_context
from dps.layers.scanner.capture import capture_screen, draw_overlays, field_boxes, obfuscated_capture
from dps.layers.scanner.classify import classify_field, classify_rendered, redact_text, value_classifier
from dps.layers.scanner.dom import CLICKABLES_JS, SUBMIT_ATTEMPTS_JS, extract_page, frame_map
from dps.layers.scanner.guard import ScanGuard
from dps.layers.scanner.inventory import field_records, field_risks, rendered_risks, url_risks
from dps.layers.scanner.models import FieldFacts, FieldRecord, PageRecord, PageSnapshot, RequestCount, ScanEvent
from dps.layers.scanner.personas_fill import (FillResult, LeakageError, PersonaFill, fill_page, persona_values,
                                              plan_field, prepare_fill, tokens_for)
from dps.layers.scanner.runtime import LeaseLost, MemoryScanSink, RunOutcome, ScanRuntime, ScanSink  # noqa: F401 (re-exported)
from dps.layers.scanner.urls import CrawlScope, canonical, redact_origin, redact_url, template
from dps.observability import SCAN_FIELDS, SCAN_FILLS, SCAN_PAGES, get_logger

logger = get_logger(__name__)
LOGIN_PATH = re.compile(r"/(log-?in|sign-?in|auth|sso)(/|$)", re.IGNORECASE)
_OTHER_HOST = ("other_origin", "unregistered_host")


class _NavFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _signature(f: FieldFacts) -> tuple[Any, ...]:
    return (f.tag, f.input_type, f.name, f.id, f.label)


class ScanRunner:
    def __init__(self, *, settings: Settings, engine: PrivacyEngine, runtime: ScanRuntime, sink: ScanSink,
                 browser: Browser, harden_js: bool = True) -> None:
        self.s, self.engine, self.rt, self.sink, self.browser, self.harden_js = settings, engine, runtime, sink, browser, harden_js
        self.detector = engine.detector(runtime.policy, runtime.locale)
        self.classify_value = value_classifier(self.detector)
        self.display = runtime.spec.fill.display_persona
        self.seed_raw = seed_bytes(runtime.seed)
        self.persona = persona_values(engine, seed=runtime.seed, persona_no=self.display, locale=runtime.locale,
                                      policy=runtime.policy)
        self.tokens = functools.partial(tokens_for, engine, key=runtime.key, policy=runtime.policy,
                                        locale=runtime.locale, persona_no=self.display)
        self.outcome = RunOutcome()
        self.frontier: deque[Target] = deque()
        self._seen: set[str] = set()
        self._per_template: Counter[str] = Counter()
        self._skip_keys: set[tuple[str, str]] = set()
        self._next_no = 0
        self._seq = 0
        self._pending: list[PageRecord] = []
        self._events: list[ScanEvent] = []

    # -- bookkeeping ---------------------------------------------------------------------------
    def _alloc(self) -> int:
        self._next_no += 1
        return self._next_no

    def _redact(self, url: str) -> str:
        return redact_url(url, self.classify_value)

    def _event(self, kind: str, result: str, *, page_no: int | None = None, method: str | None = None,
               target: str = "", note: str = "", counts: dict[str, int] | None = None) -> None:
        if self._seq >= self.s.scan_max_events:
            self.outcome.events_dropped += 1
            return
        self._seq += 1
        self._events.append(ScanEvent(seq=self._seq, ts=datetime.now(UTC), kind=kind, method=method,  # type: ignore[arg-type]
                                      result=result, page_no=page_no, target=target, note_code=note, counts=counts or {}))

    # -- lifecycle -----------------------------------------------------------------------------
    async def run(self) -> RunOutcome:
        self.guard = ScanGuard(host_allowed=self.rt.scope.network_host_allowed)
        self.context = await new_scan_context(self.browser, self.s, storage_state=self.rt.storage_state,
                                              locale=self.rt.locale, guard=self.guard, harden_js=self.harden_js)
        self.context.on("page", self._on_page)
        start = Target(page_no=self._alloc(), url=self.rt.start_url, depth=0, parent_page_no=None, via="start")
        self._seen.add(start.url)
        self._per_template[template(start.url, self.classify_value)] += 1
        self.frontier.append(start)
        self._pending.append(PageRecord(page_no=start.page_no, url_redacted=self._redact(start.url), depth=0,
                                        parent_page_no=None, status="queued", discovered_via="start",
                                        template=template(start.url, self.classify_value)))
        try:
            async with asyncio.timeout(self.s.scan_job_timeout_seconds):
                while self.frontier:
                    target = self.frontier.popleft()
                    if self.outcome.pages_visited >= self.rt.max_pages:
                        self.outcome.truncated = "pages"
                        self._mark_budget(target)
                        continue
                    await self._process(target)
        except TimeoutError:
            self.outcome.truncated = "time"
            for target in self.frontier:
                self._mark_budget(target)
        finally:
            await close_scan_context(self.context, self.guard)   # never context.close() alone (M4-G2 fix)
            self.outcome.websockets_closed = self.guard.websockets_closed
        if self._pending or self._events:
            await self.sink.save_batch(pages=self._take_pending(), fields={}, requests=self.guard.drain_counts(),
                                       events=self._take_events())
        return self.outcome

    def _mark_budget(self, t: Target) -> None:
        self._pending.append(PageRecord(page_no=t.page_no, url_redacted=self._redact(t.url), depth=t.depth,
                                        parent_page_no=t.parent_page_no, status="skipped", status_reason="budget",
                                        discovered_via=t.via, template=template(t.url, self.classify_value)))

    def _take_pending(self) -> list[PageRecord]:
        out, self._pending = self._pending, []
        return out

    def _take_events(self) -> list[ScanEvent]:
        out, self._events = self._events, []
        return out

    async def _on_page(self, page: Page) -> None:
        if await page.opener() is not None:            # a popup: the scanner never follows one
            self.outcome.popups_closed += 1
            self._event("popup", "closed", note="popup")
            await self.guard.close(page)

    async def _on_dialog(self, dialog: Dialog) -> None:
        if dialog.type == "beforeunload" and self.guard.is_closing(dialog.page):
            return                                       # guard.close answers it ("leave")
        self.outcome.dialogs_dismissed += 1
        self._event("dialog", "dismissed", note=dialog.type)
        with contextlib.suppress(PlaywrightError):
            await dialog.dismiss()

    # -- navigation ----------------------------------------------------------------------------
    async def _open(self, t: Target) -> tuple[Page, Response | None]:
        page = await self.context.new_page()
        self.guard.bind(page, t.page_no)
        page.on("dialog", self._on_dialog)
        timeout = self.s.scan_page_timeout_seconds * 1000
        try:
            response = await self._navigate(page, t, timeout)
            with contextlib.suppress(PlaywrightTimeoutError):
                await page.wait_for_load_state("networkidle", timeout=min(5000, timeout))
            await page.wait_for_timeout(self.s.scan_settle_ms)
            return page, response
        except PlaywrightTimeoutError:
            await self._close(page)
            raise _NavFailed("timeout") from None
        except PlaywrightError:
            await self._close(page)
            raise _NavFailed("blocked") from None

    async def _navigate(self, page: Page, t: Target, timeout: int) -> Response | None:
        if t.replay is None:
            return await page.goto(t.url, wait_until="domcontentloaded", timeout=timeout)
        response = await self._navigate(page, t.replay.parent, timeout)
        await page.wait_for_timeout(self.s.scan_settle_ms)
        frames = await frame_map(page, iframes=self.rt.spec.scope.iframes)
        frame = frames[t.replay.frame_index]
        await frame.evaluate(CLICKABLES_JS, {"frameIndex": t.replay.frame_index,
                                             "shadow": self.rt.spec.scope.shadow_dom})  # same predicate as EXTRACT_JS
        # Click 1 of 2 in the scanner: replaying an SPA route — an in-scope link-like element outside
        # any form, on an unfilled page (the guard still blocks every non-GET request it could cause).
        await frame.locator(f'[data-dps-click="{t.replay.frame_index}:{t.replay.ordinal}"]').click(
            timeout=3000, no_wait_after=True)
        return response

    async def _close(self, page: Page) -> None:
        self.guard.release(page)
        await self.guard.close(page)                     # unload traffic aborted; never page.close()

    # -- one page ------------------------------------------------------------------------------
    async def _process(self, t: Target) -> None:
        started = time.monotonic()
        record = PageRecord(page_no=t.page_no, url_redacted=self._redact(t.url), depth=t.depth,
                            parent_page_no=t.parent_page_no, status="visiting", discovered_via=t.via,
                            template=template(t.url, self.classify_value))
        fields: list[FieldRecord] = []
        try:
            page, response = await self._open(t)
        except _NavFailed as exc:
            record.status, record.status_reason = "failed", exc.code
            self._event("navigate", "failed", page_no=t.page_no, method="GET", target=record.url_redacted, note=exc.code)
            await self.sink.audit_navigation(page_no=t.page_no, outcome="failed")
            await self._finish(record, fields)
            return
        try:
            fields = await self._pipeline(t, page, response, record)
        except (PlaywrightError, LeakageError):
            record.status, record.status_reason = "failed", "error"
            self._event("error", "failed", page_no=t.page_no, note="page_pipeline")
        finally:
            await self._close(page)
        record.timings["total_ms"] = int((time.monotonic() - started) * 1000)
        await self._finish(record, fields)

    async def _pipeline(self, t: Target, page: Page, response: Response | None, record: PageRecord) -> list[FieldRecord]:
        final = canonical(page.url) or t.url
        record.http_status = response.status if response is not None else None
        if not self.rt.scope.network_host_allowed(urlsplit(final).hostname or ""):
            record.status, record.status_reason = "failed", "host"      # a redirect hop left the registered hosts
            return []
        self._event("navigate", str(record.http_status or 200), page_no=t.page_no, method="GET",
                    target=record.url_redacted, note=t.via)
        await self.sink.audit_navigation(page_no=t.page_no, outcome="ok")
        if urlsplit(final).path != urlsplit(t.url).path:
            record.status = "redirected"
            record.status_reason = "auth_redirect" if LOGIN_PATH.search(urlsplit(final).path) else "redirect"
            record.final_url_redacted = self._redact(final)
            await self._consider(final, t, via="redirect")
            return []
        if record.http_status is not None and record.http_status >= 400:
            record.status, record.status_reason = "failed", f"http_{record.http_status // 100}xx"
            return []
        scope = self.rt.spec.scope
        snap = await extract_page(page, iframes=scope.iframes, shadow_dom=scope.shadow_dom,
                                  max_fields=self.s.scan_max_fields_per_page)
        frames = await frame_map(page, iframes=scope.iframes)
        min_score = self.rt.policy.detect.min_score
        classes = {f.key: classify_field(f, detector=self.detector, min_score=min_score) for f in snap.fields}
        plans = {f.key: plan_field(f, classes[f.key]) for f in snap.fields}
        rendered = classify_rendered(snap.texts, detector=self.detector)
        prefilled = [(f, classes[f.key].entity_type or "") for f in snap.fields
                     if f.value and classes[f.key].entity_type and classes[f.key].sensitivity != "LOW"]
        external = [link.href for link in snap.links
                    if (u := canonical(link.href)) is not None and self.rt.scope.navigation_verdict(u) in _OTHER_HOST]
        flags = (field_risks(t.page_no, [(f, classes[f.key]) for f in snap.fields], snap.forms)
                 + url_risks(t.page_no, final, external, classify_value=self.classify_value)
                 + rendered_risks(t.page_no, rendered))
        record.title_redacted = redact_text(snap.title, self.detector)
        record.form_count, record.field_count = len(snap.forms), len(snap.fields)
        record.personal_field_count = sum(1 for c in classes.values() if c.entity_type)
        record.critical_field_count = sum(1 for c in classes.values() if c.sensitivity == "CRITICAL")
        record.forms = [{"form_index": fm.form_index, "method": fm.method, "field_count": fm.field_count,
                         "action_redacted": self._redact(canonical(fm.action, base=final) or final) if fm.action else "",
                         "frame_index": fm.frame_index, "in_shadow": fm.in_shadow} for fm in snap.forms]
        record.rendered_findings = dict(Counter(r.entity_type for r in rendered))
        record.risk_flags = flags
        for c in classes.values():
            if c.entity_type:
                SCAN_FIELDS.labels(c.entity_type).inc()
        if self.rt.spec.capture.screenshots:
            boxes = await field_boxes(frames, snap.fields)
            original = await capture_screen(page, frames, snap, rendered=rendered, prefilled=prefilled,
                                            max_height=self.s.scan_screenshot_max_height,
                                            unmasked=self.s.scan_store_unmasked_originals)
            if original is not None:
                record.artefacts["original"] = await self._png(original, t.page_no, "original")
                record.artefacts["annotated"] = await self._png(draw_overlays(original, boxes, variant="original"),
                                                                t.page_no, "annotated")
        await self._close(page)
        fills: dict[str, PersonaFill] = {}
        results: dict[str, dict[str, FillResult]] = {}
        if self.rt.spec.parallel_view and any(p.kind != "skip" for p in plans.values()):
            for mode in ("synthesize", "obfuscate"):
                done = await self._fill_and_capture(t, mode, snap, classes, plans, record)
                if done is not None:
                    fills[mode], results[mode] = done
        if rendered or prefilled:
            await self._capture(t, record)
        else:
            record.capture_status = "not_needed"
        if t.depth < self.rt.max_depth:
            for link in snap.links:
                if not link.download and (u := canonical(link.href)) is not None:
                    await self._consider(u, t, via="link")
            if scope.spa_clicks and snap.clickables:
                await self._discover_clicks(t, snap)
        record.status = "done"
        return field_records(t.page_no, snap.fields, classes, plans, fills, results, flags, detector=self.detector)

    async def _png(self, data: bytes, page_no: int, variant: str) -> str:
        return await self.sink.save_artefact(kind="screenshot", data=data, content_type="image/png",
                                             meta={"page_no": page_no, "variant": variant})

    async def _fill_and_capture(self, t: Target, mode: str, base: PageSnapshot, classes: dict[str, Any],
                                plans: dict[str, Any], record: PageRecord) -> tuple[PersonaFill, dict[str, FillResult]] | None:
        page, _ = await self._open(t)
        try:
            scope = self.rt.spec.scope
            snap = await extract_page(page, iframes=scope.iframes, shadow_dom=scope.shadow_dom,
                                      max_fields=self.s.scan_max_fields_per_page)
            frames = await frame_map(page, iframes=scope.iframes)
            expected = {f.key: _signature(f) for f in base.fields}
            fresh = [f for f in snap.fields if expected.get(f.key) == _signature(f)]
            try:
                fill = prepare_fill(fresh, classes, plans, persona=self.persona, persona_no=self.display,
                                    seed=self.seed_raw, locale=self.rt.locale, mode=mode,  # type: ignore[arg-type]
                                    tokens=self.tokens if mode == "obfuscate" else None)
            except LeakageError:
                record.validation[mode] = {"failed": 1}
                self._event("fill", "failed", page_no=t.page_no, target=record.url_redacted, note="leakage")
                return None
            if not await self.guard.arm(page, fill.needles):    # a frame could not be locked: no fill
                self._event("fill", "failed", page_no=t.page_no, target=record.url_redacted, note="guard_lock")
                return None
            results = await fill_page(page, frames, fill.actions, settle_ms=self.s.scan_settle_ms)
            with contextlib.suppress(PlaywrightError):
                self.outcome.submit_attempts_neutralised += int(await page.evaluate(SUBMIT_ATTEMPTS_JS))
            rejected = sum(1 for r in results.values() if r.messages)
            record.validation[mode] = {"checked": len(results), "accepted": len(results) - rejected, "rejected": rejected}
            for r in results.values():
                SCAN_FILLS.labels(mode, r.status).inc()
            if self.rt.spec.capture.screenshots:
                untouched = [(f, classes[f.key].entity_type or "") for f in fresh
                             if f.value and plans[f.key].kind == "skip" and classes[f.key].entity_type
                             and classes[f.key].sensitivity != "LOW"]
                png = await capture_screen(page, frames, snap, rendered=classify_rendered(snap.texts, detector=self.detector),
                                           prefilled=untouched, max_height=self.s.scan_screenshot_max_height)
                if png is not None:
                    boxes = await field_boxes(frames, fresh)
                    record.artefacts[f"filled_{mode}"] = await self._png(draw_overlays(png, boxes, variant=mode),
                                                                         t.page_no, f"filled_{mode}")
            self._event("fill", "captured", page_no=t.page_no, target=record.url_redacted, note=mode,
                        counts={"fields": len(results), "rejected": rejected, "persona": self.display})
            return fill, results
        finally:
            await self._close(page)

    async def _capture(self, t: Target, record: PageRecord) -> None:
        page, _ = await self._open(t)
        try:
            scope = self.rt.spec.scope
            snap = await extract_page(page, iframes=scope.iframes, shadow_dom=scope.shadow_dom,
                                      max_fields=self.s.scan_max_fields_per_page)
            frames = await frame_map(page, iframes=scope.iframes)
            classes = {f.key: classify_field(f, detector=self.detector, min_score=self.rt.policy.detect.min_score)
                       for f in snap.fields}
            prefilled = [(f, classes[f.key].entity_type or "") for f in snap.fields
                         if f.value and classes[f.key].entity_type and classes[f.key].sensitivity != "LOW"]
            result = await obfuscated_capture(
                page, frames, snap, rendered=classify_rendered(snap.texts, detector=self.detector), prefilled=prefilled,
                engine=self.engine, policy=self.rt.policy, mode=self.rt.spec.mode, key=self.rt.key, seed=self.seed_raw,
                locale=self.rt.locale, guard=self.guard, settings=self.s, page_no=t.page_no)
            record.capture_status = result.status
            if result.status == "captured" and result.png is not None:
                record.artefacts["masked_capture"] = await self.sink.save_artefact(
                    kind="capture", data=result.png, content_type="image/png", meta={"page_no": t.page_no, "variant": "masked"})
                if result.capture_json is not None:
                    record.artefacts["capture_json"] = await self.sink.save_artefact(
                        kind="capture", data=result.capture_json, content_type="application/json",
                        meta={"page_no": t.page_no, "variant": "reveal_map", "mode": self.rt.spec.mode})
            self._event("capture", result.status, page_no=t.page_no, target=record.url_redacted,
                        note=result.reason or self.rt.spec.mode)
        finally:
            await self._close(page)

    # -- discovery -----------------------------------------------------------------------------
    async def _consider(self, url: str, parent: Target, *, via: str, replay: ClickReplay | None = None) -> None:
        if url in self._seen:
            return
        self._seen.add(url)
        verdict = self.rt.scope.navigation_verdict(url)
        if verdict == "scheme":
            return
        if verdict != "ok":
            self._skip(url, parent, verdict)
            return
        tpl = template(url, self.classify_value)
        if self._per_template[tpl] >= self.s.scan_max_per_template:
            self._skip(url, parent, "template")
            return
        self._per_template[tpl] += 1
        depth = parent.depth if via == "redirect" else parent.depth + 1
        target = Target(page_no=self._alloc(), url=url, depth=depth, parent_page_no=parent.page_no, via=via,  # type: ignore[arg-type]
                        replay=replay)
        self.frontier.append(target)
        self._pending.append(PageRecord(page_no=target.page_no, url_redacted=self._redact(url), depth=depth,
                                        parent_page_no=parent.page_no, status="queued", discovered_via=via,  # type: ignore[arg-type]
                                        template=tpl))
        if via == "spa_click":
            self.outcome.spa_routes += 1
            self._event("spa", "queued", page_no=target.page_no, target=self._redact(url), note="spa_click")

    def _skip(self, url: str, parent: Target, reason: str) -> None:
        shown = redact_origin(url) if reason in _OTHER_HOST else self._redact(url)
        # the template column is value-free (PageRecord checks it): an other-host row keys on the
        # origin's template ("host[:port]/"), every other row on the URL's template
        key = (reason, template(redact_origin(url) if reason in _OTHER_HOST else url, self.classify_value))
        if key in self._skip_keys or len(self._skip_keys) >= self.s.scan_max_skipped_rows:
            return
        self._skip_keys.add(key)
        no = self._alloc()
        self._pending.append(PageRecord(page_no=no, url_redacted=shown, depth=parent.depth + 1,
                                        parent_page_no=parent.page_no, status="skipped", status_reason=reason,
                                        discovered_via="link", template=key[1]))
        self._event("skip", "skipped", page_no=no, method="GET", target=shown, note=reason)

    async def _discover_clicks(self, t: Target, snap: PageSnapshot) -> None:
        base = template(canonical(snap.url) or t.url, self.classify_value)
        for c in snap.clickables[: self.s.scan_max_spa_clicks_per_page]:
            try:
                page, _ = await self._open(t)
            except _NavFailed:
                return
            try:
                frames = await frame_map(page, iframes=self.rt.spec.scope.iframes)
                if c.frame_index not in frames:
                    continue
                frame = frames[c.frame_index]
                await frame.evaluate(CLICKABLES_JS, c.frame_index)
                # Click 2 of 2 in the scanner: SPA route discovery — the same kind of element as the
                # replay click, on a fresh, unfilled tab.
                await frame.locator(f'[data-dps-click="{c.frame_index}:{c.ordinal}"]').click(timeout=3000, no_wait_after=True)
                await page.wait_for_timeout(self.s.scan_settle_ms)
                url = canonical(page.url)
                if url is not None and template(url, self.classify_value) != base:
                    await self._consider(url, t, via="spa_click",
                                         replay=ClickReplay(parent=t, frame_index=c.frame_index, ordinal=c.ordinal))
            except PlaywrightError:
                self._event("error", "failed", page_no=t.page_no, note="spa_click")
            finally:
                await self._close(page)

    async def _finish(self, record: PageRecord, fields: list[FieldRecord]) -> None:
        self.outcome.pages_visited += 1
        self.outcome.fields += len(fields)
        SCAN_PAGES.labels(record.status).inc()
        for b in self.guard.drain_blocked():
            target = redact_origin(b.url) if b.reason == "host" else self._redact(b.url)
            self._event("request", "blocked", page_no=b.page_no, method=b.method, target=target, note=b.reason)
        await self.sink.save_batch(pages=[*self._take_pending(), record], fields={record.page_no: fields},
                                   requests=self.guard.drain_counts(), events=self._take_events())
        logger.info("scan_page", page_no=record.page_no, status=record.status, fields=len(fields),
                    blocked=self.guard.blocked_total)
        ok = await self.sink.progress({"pages": self.outcome.pages_visited, "fields": self.outcome.fields,
                                       "blocked_requests": self.guard.blocked_total})
        if not ok:
            raise LeaseLost("lease_lost")
```

`MemoryScanSink` (complete; in `runtime.py`):

```python
class MemoryScanSink:
    def __init__(self, *, stop_after_pages: int | None = None) -> None:
        self.pages: dict[int, PageRecord] = {}
        self.fields: dict[int, list[FieldRecord]] = {}
        self.requests: list[RequestCount] = []
        self.events: list[ScanEvent] = []
        self.artefacts: dict[str, tuple[str, bytes, dict[str, Any]]] = {}
        self.navigations = 0
        self._progress_calls = 0
        self._stop = stop_after_pages

    async def save_batch(self, *, pages: list[PageRecord], fields: dict[int, list[FieldRecord]],
                         requests: list[RequestCount], events: list[ScanEvent]) -> None:
        for p in pages:
            self.pages[p.page_no] = p
        self.fields.update(fields)
        self.requests += requests
        self.events += events

    async def save_artefact(self, *, kind: str, data: bytes, content_type: str, meta: dict[str, Any]) -> str:
        aid = f"a{len(self.artefacts) + 1}"
        self.artefacts[aid] = (kind, data, meta)
        return aid

    async def progress(self, progress: dict[str, Any]) -> bool:
        self._progress_calls += 1
        return self._stop is None or self._progress_calls < self._stop

    async def audit_navigation(self, *, page_no: int, outcome: str) -> None:
        self.navigations += 1
```

Add to `demo/onboarding/site.mjs` `PAGES`: `'/fixtures/dialogs': { title: 'Dialogs', body: () => "<p>Pop-ups and alerts.</p><script>alert('Welcome to the demo'); window.open('/help', '_blank');</script>" }` (not linked from any page).

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_crawler.py`:

```python
pytestmark = pytest.mark.browser


def _by_path(sink: MemoryScanSink) -> dict[str, PageRecord]:
    return {urlsplit(p.url_redacted).path: p for p in sink.pages.values() if p.status != "skipped"}


@pytest.mark.slow
async def test_full_demo_crawl_in_memory(chromium, scan_settings, scan_engine, demo_site_url, demo_data) -> None:
    before = received(demo_site_url)
    sink = MemoryScanSink()
    outcome = await ScanRunner(settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium,
                               runtime=make_runtime(demo_site_url, scan_settings, scan_engine)).run()
    after = received(demo_site_url)
    pages = _by_path(sink)
    assert {"/apply", "/", "/apply/identity", "/apply/address", "/apply/employment", "/apply/card", "/apply/documents",
            "/apply/us", "/apply/review", "/login", "/profile", "/profile/contact", "/app", "/app/nominee",
            "/app/preferences", "/help", "/help/contact-us", "/help/track"} <= set(pages)
    assert pages["/app/preferences"].discovered_via == "spa_click" and outcome.spa_routes >= 1
    skipped = {(p.status_reason, p.url_redacted) for p in sink.pages.values() if p.status == "skipped"}
    assert {r for r, _ in skipped} >= {"deny", "other_origin", "template"}
    assert sum(1 for p in sink.pages.values() if "/help/faq/" in p.url_redacted and p.status == "done") == 3
    # nothing was submitted, the deny list held, and the demo's POSTs were blocked by the guard
    assert (after["non_get"], after["form_get"], after["logout_hits"], after["admin_hits"]) == (
        before["non_get"], before["form_get"], before["logout_hits"], before["admin_hits"])
    blocked = Counter((r.method, r.reason) for r in sink.requests if r.decision == "blocked" for _ in range(r.count))
    assert blocked[("POST", "method")] >= 3 and blocked[("GET", "host")] >= 1           # autosaves/beacon; tracker pixel
    assert not [r for r in sink.requests if r.decision == "allowed" and r.method not in ("GET", "HEAD")]
    # the identity page: classification, parallel view, validation evidence
    ident = pages["/apply/identity"]
    fields = sink.fields[ident.page_no]
    assert [f.entity_type for f in fields] == ["PERSON_NAME", "DATE_OF_BIRTH", "AADHAAR", "PAN_IN", "PHONE", "EMAIL",
                                               "STREET_ADDRESS", "GENDER", "NATIONALITY"]
    assert set(ident.artefacts) >= {"original", "annotated", "filled_synthesize", "filled_obfuscate"}
    assert ident.validation["synthesize"]["rejected"] == 0
    assert all(f.fill_synthesize and f.fill_obfuscate for f in fields)
    # risk flags where the demo planted them
    kinds = {(urlsplit(p.url_redacted).path, f.flag) for p in sink.pages.values() for f in p.risk_flags}
    assert {("/apply/identity", "autocomplete_enabled_sensitive"), ("/apply/review", "pii_in_query_string"),
            ("/profile", "pii_rendered_unmasked"), ("/login", "password_without_autocomplete_token"),
            ("/help/track", "sensitive_form_uses_get")} <= kinds
    assert pages["/profile"].capture_status == "captured" and "masked_capture" in pages["/profile"].artefacts
    shadow = next(f for f in sink.fields[pages["/apply/address"].page_no] if f.in_shadow)
    card = [f for f in sink.fields[pages["/apply/card"].page_no] if f.frame_index == 1]
    assert shadow.entity_type == "POSTAL_CODE" and {f.entity_type for f in card} >= {"CARD_PAN", "CVV", "CARD_EXPIRY"}
    # no page value anywhere in what would be persisted (rows, events, request log, capture maps)
    persisted = normalise(json.dumps([[p.model_dump(mode="json") for p in sink.pages.values()],
                                      [[f.model_dump(mode="json") for f in fs] for fs in sink.fields.values()],
                                      [e.model_dump(mode="json") for e in sink.events],
                                      [r.model_dump(mode="json") for r in sink.requests]]))
    persisted += normalise(" ".join(d.decode() for k, d, m in sink.artefacts.values() if m.get("variant") == "reveal_map"))
    for k in DEMO_VALUE_KEYS:
        assert normalise(demo_data[k]) not in persisted, k


async def test_page_budget_and_depth_limit(chromium, scan_settings, scan_engine, demo_site_url) -> None:
    sink = MemoryScanSink()
    out = await ScanRunner(settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium,
                           runtime=make_runtime(demo_site_url, scan_settings, scan_engine, max_pages=4, spa=False)).run()
    assert out.pages_visited == 4 and out.truncated == "pages"
    assert any(p.status_reason == "budget" for p in sink.pages.values())
    sink0 = MemoryScanSink()
    await ScanRunner(settings=scan_settings, engine=scan_engine, sink=sink0, browser=chromium,
                     runtime=make_runtime(demo_site_url, scan_settings, scan_engine, max_depth=0)).run()
    assert [p.status for p in sink0.pages.values()] == ["done"]


async def test_without_a_session_profile_redirects_to_login(chromium, scan_settings, scan_engine, demo_site_url) -> None:
    sink = MemoryScanSink()
    await ScanRunner(settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium,
                     runtime=make_runtime(demo_site_url, scan_settings, scan_engine, path="/profile", cookies=False,
                                          max_pages=2, spa=False)).run()
    first = sink.pages[1]
    assert (first.status, first.status_reason) == ("redirected", "auth_redirect") and "/login" in (first.final_url_redacted or "")


async def test_lease_loss_stops_the_crawl(chromium, scan_settings, scan_engine, demo_site_url) -> None:
    sink = MemoryScanSink(stop_after_pages=1)
    with pytest.raises(LeaseLost):
        await ScanRunner(settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium,
                         runtime=make_runtime(demo_site_url, scan_settings, scan_engine)).run()
    assert sum(1 for p in sink.pages.values() if p.status == "done") == 1


async def test_dialogs_dismissed_and_popups_closed(chromium, scan_settings, scan_engine, demo_site_url) -> None:
    sink = MemoryScanSink()
    out = await ScanRunner(settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium,
                           runtime=make_runtime(demo_site_url, scan_settings, scan_engine, path="/fixtures/dialogs",
                                                max_depth=0, spa=False)).run()
    assert out.dialogs_dismissed >= 1 and sink.pages[1].status == "done"
    assert out.popups_closed in (0, 1)        # Chromium's popup blocker may stop window.open first; never followed
```

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_crawler.py` → FAIL (`ModuleNotFoundError: dps.layers.scanner.crawler`).

- [ ] **Step 3: Implement** `runtime.py` (the `ScanSink` protocol, `MemoryScanSink`, `ScanRuntime`, `RunOutcome`, `LeaseLost`) and `crawler.py` (code above, plus `Target` and `ClickReplay`, importing and re-exporting the runtime types), `field_records` in `inventory.py`, the dialogs fixture page, and `make_runtime` in `tests/scanner/helpers.py`:

```python
def make_runtime(base_url: str, settings: Settings, engine: PrivacyEngine, *, path: str = "/apply",
                 deny: tuple[str, ...] = ("/logout", "/admin"), cookies: bool = True, personas: int = 3,
                 max_pages: int = 30, max_depth: int = 3, mode: str = "synthesize", spa: bool = True) -> ScanRuntime:
    spec = ScanSpec(url=base_url + path, mode=mode,  # type: ignore[arg-type]
                    scope=ScanScope(max_pages=max_pages, max_depth=max_depth, deny=list(deny), spa_clicks=spa),
                    fill=FillSpec(personas=personas, locale="en_IN"))
    origin = Origin.of(spec.url)
    return ScanRuntime(spec=spec, start_url=canonical(spec.url) or spec.url, key=generate_key_material("key"),
                       seed=generate_synthesis_seed(), storage_state=demo_cookie_state(origin.host) if cookies else None,
                       policy=engine.policy(None), locale="en_IN", max_pages=max_pages, max_depth=max_depth,
                       scope=CrawlScope(start=origin, same_origin=True,
                                        registered_hosts=frozenset(settings.scan_registered_hosts_list), deny=deny))
```

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_crawler.py && ruff check dps tests && mypy dps` → `5 passed` (the full crawl takes about a minute on a laptop), clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/runtime.py dps/layers/scanner/crawler.py dps/layers/scanner/inventory.py demo/onboarding/site.mjs tests/scanner/helpers.py tests/scanner/test_crawler.py
git commit -m "feat(scanner): ScanRunner — budgeted same-origin crawl, SPA click discovery, parallel view, captures" -- dps/layers/scanner/runtime.py dps/layers/scanner/crawler.py dps/layers/scanner/inventory.py demo/onboarding/site.mjs tests/scanner/helpers.py tests/scanner/test_crawler.py
```

---

### Task 15: The proof — nothing is ever submitted (M4 exit criterion)

**Files:**
- Test: `tests/scanner/test_no_submit.py` (contract §12.3 path; markers `browser`), plus a static source check that runs without a browser

The proof has four layers: (1) a positive control shows the demo site counts every POST and every GET-form submission it receives; (2) full crawls of the whole demo site — **with the JavaScript hardening switched off**, so the page's own autosaves, beacon and auto-submitting select really fire — leave those counters unchanged while the guard's log shows the blocked requests; (3) an adversarial test fills forms and then *itself* tries every way to submit (`requestSubmit`, prototype `submit`, clicking the submit button, pressing Enter, `fetch` POST, `sendBeacon`, submitting the GET form) — still nothing arrives; (4) a static check proves the scanner's source has no submit path to begin with.

- [ ] **Step 1: Write the tests** — `tests/scanner/test_no_submit.py`:

```python
"""M4 exit criterion (spec §11, contract §12.3): DPS never submits anything — proven, not claimed."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import httpx
import pytest

from dps.layers.scanner.crawler import MemoryScanSink, ScanRunner
from dps.layers.scanner.dom import extract_page, frame_map
from tests.scanner.helpers import guarded_context, make_runtime, received

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.browser
def test_positive_control_the_demo_counts_what_it_receives(demo_site_url: str) -> None:
    before = received(demo_site_url)
    httpx.post(f"{demo_site_url}/apply/identity/submit", data={"fullName": "x"})
    httpx.get(f"{demo_site_url}/help/track/result", params={"email": "x"})
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] + 1 and after["form_get"] == before["form_get"] + 1


@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.parametrize("harden_js", [False, True])
async def test_full_scan_never_submits(harden_js, chromium, scan_settings, scan_engine, demo_site_url) -> None:  # noqa: ANN001
    before = received(demo_site_url)
    sink = MemoryScanSink()
    await ScanRunner(settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, harden_js=harden_js,
                     runtime=make_runtime(demo_site_url, scan_settings, scan_engine)).run()
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] and after["form_get"] == before["form_get"]
    assert after["by_path"] == before["by_path"]
    blocked = Counter()
    for r in sink.requests:
        if r.decision == "blocked":
            blocked[(r.method, r.reason)] += r.count
        else:
            assert r.method in ("GET", "HEAD"), r
    assert blocked[("POST", "method")] >= 3
    if not harden_js:   # the auto-submitting select on /apply/card fired a real form POST navigation
        assert any(e.kind == "request" and e.method == "POST" and "/apply/card" in e.target for e in sink.events)


@pytest.mark.browser
async def test_adversarial_submission_attempts_on_filled_pages_all_fail(chromium, scan_settings, demo_site_url) -> None:
    before = received(demo_site_url)
    async with guarded_context(chromium, scan_settings, demo_site_url, harden_js=False) as (ctx, guard):
        for path, field in (("/apply/identity", "fullName"), ("/help/track", "email")):
            page = await ctx.new_page()
            await page.goto(f"{demo_site_url}{path}")
            await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
            assert await guard.arm(page, ["Meera Venkatesan", "meera.v@example.com"])
            await page.locator(f"[name={field}]").fill("meera.v@example.com")
            attempts = [
                "document.forms[0].requestSubmit()",
                "HTMLFormElement.prototype.submit.call(document.forms[0])",
                "fetch('/api/autosave', {method: 'POST', body: 'x'}).catch(() => 0)",
                "navigator.sendBeacon('/api/telemetry/x', 'x')",
                "location.href = '/help/track/result?email=meera.v%40example.com'",
            ]
            for js in attempts:
                try:
                    await page.evaluate(js)
                except Exception:  # noqa: BLE001 - a blocked navigation may tear the context down
                    pass
                await page.wait_for_timeout(150)
            if not page.is_closed():
                try:
                    await page.locator("[name=" + field + "]").press("Enter", timeout=1000)
                    await page.locator("button[type=submit]").click(timeout=1000, no_wait_after=True)
                except Exception:  # noqa: BLE001
                    pass
                await page.wait_for_timeout(300)
            await guard.close(page)                       # the demo flushes a draft on pagehide
        reasons = {c.reason for c in guard.drain_counts() if c.decision == "blocked"}
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] and after["form_get"] == before["form_get"]
    assert {"method", "filled_navigation"} <= reasons


def test_no_submission_code_path_exists_in_the_scanner() -> None:
    sources = {p: p.read_text(encoding="utf-8") for p in
               [*sorted((ROOT / "dps/layers/scanner").glob("*.py")), ROOT / "dps/browser_worker.py",
                ROOT / "dps/layers/api/routers/scans.py"] if p.exists()}
    joined = "\n".join(sources.values())
    code_only = re.sub(r'r?"""[\s\S]*?"""', "", joined)            # page-side JS and docstrings excluded
    assert ".press(" not in code_only and ".keyboard" not in code_only and ".dispatch_event(" not in code_only
    assert "submit" not in re.sub(r"(submit_attempts|SUBMIT_ATTEMPTS_JS|submitAttempts|submissions)", "", code_only).lower()
    assert len(re.findall(r"\.click\(", code_only)) == 2               # SPA discovery + SPA replay only
    assert all("data-dps-click" in line for line in code_only.splitlines() if ".click(" in line)
    guard_src = sources[ROOT / "dps/layers/scanner/guard.py"]
    assert joined.count("route.continue_(") == guard_src.count("route.continue_(") == 1
    assert 'ALLOWED_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})' in guard_src
```

(The adversarial test arms the page by hand exactly as the crawler does before filling. The two `.click(` call sites live in `crawler.py`; both target `[data-dps-click=…]` locators, which the extraction only stamps on link-like elements outside any form. `scans.py` does not exist until Task 18; the static test covers it once it does.)

- [ ] **Step 2: Run** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/scanner/test_no_submit.py` → `5 passed` (1 control, 2 parametrised full scans, 1 adversarial, 1 static). These tests pass on first run because Tasks 6–14 built the behaviour; if any fails, the guard has a hole — fix `guard.py`, never the test.

- [ ] **Step 3: Commit**

```bash
git add tests/scanner/test_no_submit.py
git commit -m "test(scanner): prove no submission ever leaves the scanner — control, full scans, adversarial attempts, static check" -- tests/scanner/test_no_submit.py
```

---
### Task 16: Scan job handlers, finalize, inventory and exports, scan evidence

**Files:**
- Modify: `dps/layers/scanner/__init__.py` (handlers, `DbScanSink`, `open_runtime`, `finalize_scan`), `dps/layers/scanner/inventory.py` (`inventory_rows`, `write_csv`, `build_export`, `build_scan_evidence`), `dps/layers/connectors/evidence.py` (render the `scan` block)
- Test: `tests/scanner/test_inventory_export.py` (unit), `tests/scanner/test_evidence_html_scan.py` (unit), `tests/scanner/test_scan_job_e2e.py` (markers `db`, `browser`)

**Interfaces introduced:**

```python
# dps/layers/scanner/__init__.py
class ScanSetupError(Exception): code: str                # str() == code
async def open_runtime(ctx: HandlerContext, job: ClaimedJob, store: ScanStore) -> ScanRuntime
class DbScanSink:                                            # ScanSink over the metadata DB + ArtefactService + audit
    def __init__(self, ctx: HandlerContext, job: ClaimedJob, store: ScanStore) -> None
async def scan_job(ctx: HandlerContext, job: ClaimedJob) -> None              # JobPhase.scan
async def scan_finalize_job(ctx: HandlerContext, job: ClaimedJob) -> None     # JobPhase.finalize (cancel path, contract §3.1)
async def finalize_scan(ctx: HandlerContext, job: ClaimedJob, runtime: ScanRuntime | None, *, store: ScanStore,
                        outcome: RunOutcome | None, result: Literal["succeeded", "failed", "canceled"]) -> str   # evidence id

# dps/layers/scanner/inventory.py
def inventory_rows(pages: list[ScanPageOut], fields: list[ScanFieldOut]) -> list[InventoryRow]
def write_csv(header: list[str], rows: Iterable[list[Any]]) -> bytes        # formula-injection guard
def build_export(*, job_id: str, runtime: ScanRuntime, pages: list[ScanPageOut], fields: list[ScanFieldOut],
                 personas: list[dict[str, str]], engine: PrivacyEngine) -> tuple[bytes, bytes]   # (json, csv)
def build_scan_evidence(*, evidence_id: str, job: Job, runtime: ScanRuntime | None, pages: list[ScanPageOut],
                        fields: list[ScanFieldOut], counts: dict[str, Any], outcome: RunOutcome | None,
                        result: str, errors: list[dict[str, str]], settings: Settings,
                        exports: dict[str, str]) -> EvidencePack
```

**Import hygiene.** `dps/layers/scanner/__init__.py` is imported by the API (through `dps.layers.scanner.models` and friends), so its module-level imports stay Playwright-free: `scan_job` imports `ScanRunner` and `get_browser` inside its body; `ScanRuntime`, `RunOutcome` and `LeaseLost` come from `runtime.py`; `inventory.py` refers to them through `runtime.py` too (no crawler ↔ inventory cycle).

**Runtime from a claimed job** (`open_runtime`, complete behaviour): `job.key_enc is None` → `ScanSetupError("key_unavailable")`; `open_sealed(ctx.kek, job.key_enc, aad=f"dps_job.key:{job.id}")` → `JobSecret` (any failure → `secret_unavailable`, raised `from None` so nothing sealed is chained); spec = `job.spec` minus `url_origin`, `url` replaced by `secret.scan_start_url`, `auth` reduced to `{"storage_state_ref": ref}`, consistency keys DPS added (`key_wrapped`) dropped, then `ScanSpec.model_validate`; storage state = `secret.scan_auth` (inline, dev) or `json.loads(resolve_secret_ref(ref, settings)["storage_state.json"])` (missing → `secret_unavailable`); `key_material_from_caller(secret.key_material)`, `seed_from_caller(secret.synthesis_seed)`; `policy = ctx.engine.policy(spec.policy)` (`EngineError` → `policy_invalid`); hosts = `store.registered_hosts(conn, tenant)` or, when empty and `settings.env in ("local", "dev")`, `settings.scan_registered_hosts_list`; the start host must still be registered (`host_not_registered` — a tenant may have removed it after accept); `max_pages = spec.scope.max_pages or settings.scan_max_pages`, `max_depth = spec.scope.max_depth if not None else settings.scan_max_depth`; `CrawlScope(start=Origin.of(start), same_origin=spec.scope.same_origin, registered_hosts=frozenset(hosts), allow=tuple(spec.scope.allow), deny=tuple(spec.scope.deny))`.

**DbScanSink** (complete behaviour): `save_batch` runs one transaction on `acquire_queue(job.tenant_id)` — `upsert_page` per page, `replace_fields` per page, `add_request_counts`, `append_events`; `save_artefact` → `ctx.artefacts.save(tenant_id=…, job_id=…, kind=…, data=…, content_type=…, meta=…)`; `progress(p)` → `jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.scan, state=JobState.running, progress=JobProgress(pages=p["pages"], fields=p["fields"], blocked_requests=p["blocked_requests"]).model_dump(mode="json"))` and returns its boolean (False = fenced out: canceled or reaped); `audit_navigation` → `ctx.audit.write(AuditEvent(trace_id=job.id, route="browser:navigate", tenant_id=job.tenant_id, key_id=job.created_by or "-", mode=job.mode or "", policy=<policy name>, outcome=outcome, action="scan.navigate", job_id=job.id))` (every navigation is audited — spec §8 safety rails; the audit row carries no URL).

**Handlers** (complete):

```python
async def scan_job(ctx: HandlerContext, job: ClaimedJob) -> None:
    store = ScanStore(ctx.settings.pg_schema)
    try:
        runtime = await open_runtime(ctx, job, store)
    except ScanSetupError as exc:
        await jobs.advance_job(ctx.worker_id, job.id, phase=JobPhase.scan, state=JobState.failed,
                               error_code=exc.code, error=exc.code, purge_secret=True)
        return
    async with acquire_queue(job.tenant_id) as conn:      # a reaped-and-retried attempt starts clean
        await store.reset_scan(conn, job.id)
    await ctx.artefacts.delete_for_job(job.tenant_id, job.id)
    runner = ScanRunner(settings=ctx.settings, engine=ctx.engine, runtime=runtime,
                        sink=DbScanSink(ctx, job, store), browser=await get_browser(ctx.settings))
    try:
        outcome = await runner.run()
    except LeaseLost:
        logger.info("scan_lease_lost", job_id=job.id)    # canceled or reaped: the new owner finalizes
        return
    await finalize_scan(ctx, job, runtime, store=store, outcome=outcome, result="succeeded")


async def scan_finalize_job(ctx: HandlerContext, job: ClaimedJob) -> None:
    store = ScanStore(ctx.settings.pg_schema)
    try:
        runtime: ScanRuntime | None = await open_runtime(ctx, job, store)
    except ScanSetupError:
        runtime = None                                     # key already purged: evidence without exports
    result = "canceled" if job.progress.outcome == "canceled" else "failed"
    await finalize_scan(ctx, job, runtime, store=store, outcome=None, result=result)  # type: ignore[arg-type]
```

`finalize_scan` (complete):

```python
async def finalize_scan(ctx: HandlerContext, job: ClaimedJob, runtime: ScanRuntime | None, *, store: ScanStore,
                        outcome: RunOutcome | None, result: Literal["succeeded", "failed", "canceled"]) -> str:
    async with acquire_queue(job.tenant_id) as conn:
        pages = await store.list_pages(conn, job.id, limit=100_000)
        fields = await store.list_fields(conn, job.id)
        counts = await store.summary_counts(conn, job.id)
    errors: list[dict[str, str]] = []
    if counts["non_get_allowed"]:            # unreachable by construction; measured anyway, and fatal
        result = "failed"
        errors.append({"code": "submission_detected", "where": "guard"})
    exports: dict[str, str] = {}
    if runtime is not None:
        personas = [persona_values(ctx.engine, seed=runtime.seed, persona_no=n, locale=runtime.locale,
                                   policy=runtime.policy) for n in range(1, runtime.spec.fill.personas + 1)]
        export_json, export_csv = build_export(job_id=job.id, runtime=runtime, pages=pages, fields=fields,
                                               personas=personas, engine=ctx.engine)
        exports["json"] = await ctx.artefacts.save(tenant_id=job.tenant_id, job_id=job.id, kind="export_json",
                                                   data=export_json, content_type="application/json",
                                                   meta={"personas": len(personas)})
        exports["csv"] = await ctx.artefacts.save(tenant_id=job.tenant_id, job_id=job.id, kind="export_csv",
                                                  data=export_csv, content_type="text/csv; charset=utf-8",
                                                  meta={"personas": len(personas)})
    evidence_id = new_id("ev")
    pack = build_scan_evidence(evidence_id=evidence_id, job=job, runtime=runtime, pages=pages, fields=fields,
                               counts=counts, outcome=outcome, result=result, errors=errors,
                               settings=ctx.settings, exports=exports)
    sha = pack_sha256(pack)            # M2 evidence module (contract §7.1): sha256 of the canonical JSON
    html_id = await ctx.artefacts.save(tenant_id=job.tenant_id, job_id=job.id, kind="evidence_html",
                                       data=render_html(pack, sha=sha).encode("utf-8"),
                                       content_type="text/html; charset=utf-8", meta={"evidence_id": evidence_id})
    expires = datetime.now(UTC) + timedelta(days=ctx.settings.retention_days)
    async with acquire_queue(job.tenant_id) as conn:
        await store.insert_evidence(conn, job.tenant_id, job.id, pack, pack_sha256=sha,
                                    html_artefact_id=html_id, expires_at=expires)
    progress = JobProgress(outcome=result, pages=counts["pages_visited"], fields=counts["fields"],
                           blocked_requests=sum((counts["blocked"] or {}).values()), evidence_id=evidence_id)
    code = errors[0]["code"] if errors else None
    await jobs.advance_job(ctx.worker_id, job.id, phase=job.phase, state=JobState(result),
                           progress=progress.model_dump(mode="json"), error_code=code, error=code, purge_secret=True)
    ctx.audit.write(AuditEvent(trace_id=job.id, route="browser:finalize", tenant_id=job.tenant_id,
                               key_id=job.created_by or "-", mode=job.mode or "", policy=pack.policy.get("name", ""),
                               outcome=result, action="scan.finalize", job_id=job.id))
    return evidence_id
```


**Export format** (`dps.scan-export.v1`; synthetic values and tokens of them only):

```json
{"format": "dps.scan-export.v1", "scan_id": "s_…", "generated_at": "…", "mode": "synthesize", "locale": "en_IN",
 "personas": 20, "display_persona": 7,
 "label": "Synthetic personas generated by DPS. Obfuscated values are tokens of these personas, never of page data.",
 "key": {"alg": "dps.v1", "kid": "k_…"}, "seed_fingerprint": "…16 hex…",
 "persona_set": [{"persona": 1, "attributes": {"name": "…", "dob": "…", "aadhaar": "…", "…": "…"}}],
 "fields": [{"page_no": 3, "path": "http://demo-site:8410/apply/identity", "form_index": 1, "field_no": 3,
             "label": "Aadhaar number", "selector": "input#aadhaar[name=\"aadhaar\"]", "entity_type": "AADHAAR",
             "persona_attr": "aadhaar", "fill_format": "shape:#### #### ####", "obfuscate_method": "fpe_digits",
             "values": [{"persona": 1, "synthesize": "9173 2206 4418", "obfuscate": "8036 1194 7721"}]}]}
```

Rules: `persona_set` excludes `credential` and `otp`; text fields re-render each persona with the stored `fill_format`; select/radio fields carry the persona attribute value (the label QA would select) or, for neutral selects, the display persona's stored choice; credential fields carry `"values": []` and `"note": "credential fields are filled but never exported"`; skipped fields (`skipped_*`) carry `"values": []` and their `fill_status`; `obfuscate` values come from `tokens_for(…, persona_no=n, items=…)` per persona (same key → the display persona's tokens equal `dps_scan_field.fill_obfuscate`). CSV: one row per field × persona — `page_no, path, form_index, field_no, label, entity_type, persona_attr, persona, synthesize_value, obfuscate_value, obfuscate_method`.

`write_csv` guards against formula injection without corrupting phone numbers: a cell starting with `=`, `@`, a tab or a carriage return, or with `+`/`-` followed by a non-digit, is prefixed with `'`; `+91 98451 20377` stays as is (JSON is the lossless format for replay).

**Evidence pack (`EvidencePack`, contract §7.1) for a scan:** `job_kind="scan"`, `outcome=result`, `purpose` from the job, `source={"kind": "ui", "url": <redacted start>, "origin": <url_origin>, "scope": {same_origin, max_pages, max_depth, allow, deny, iframes, shadow_dom, spa_clicks}, "auth": "storage_state_ref" | "inline" | "none"}`, `target={"kind": "artefacts", "store": settings.artefact_store, "exports": exports}`, `policy={"name", "hash"}`, `snapshot={}`, `consistency={"scope", "field_scoped", "kid": job.kid, "seed_fingerprint": sha256(seed)[:16], "key_custody": {"returned": True, "wrapped": spec.consistency.key_wrapped, "purged_at": now}}`, `columns=[]`, `reveal_map=[{"path": f"field:{page_no}/{form_index}/{field_no}", "entity_type", "method": obfuscate_method, "params": {}, "anchor_path": None, "reveal": METHODS[method].reversible}]` for every field with a token, `counts={"pages", "forms", "fields", "personal_fields"}`, `verification={"mode": policy.verify.leakage_check, "batches": pages filled, "values_checked": tokens verified, "hits": 0, "passed": result != "failed"}`, `fidelity=None`, `privacy=None`, **`scan`** `= {"pages", "forms", "fields", "by_entity": {type: n}, "risk_flags": [numbered flags], "personas", "display_persona", "submissions": 0, "non_get_allowed": counts["non_get_allowed"], "blocked_requests": {method: n}, "navigations", "validation": {mode: {checked, accepted, rejected}}, "captures": {"captured", "withheld"}, "spa_routes", "truncated", "submit_attempts_neutralised", "websockets_closed", "dialogs_dismissed", "popups_closed", "events_dropped"}`, `timings={"started_at", "finished_at", "duration_s"}`, `partitions=[]`, `errors`, `config_hash = sha256(policy hash + canonical JSON of job.spec)`, and the contract §7.2 label (`"synthetic — measured, not claimed anonymous"` for `mode=synthesize`, `"pseudonymised — still personal data"` for `obfuscate`). `submissions` is 0 by construction (D4: no code path submits) and `non_get_allowed` is the measured counterpart; both are printed on the evidence HTML.

M2 touch point — `render_html` (`from dps.layers.connectors.evidence import pack_sha256, render_html`): when `pack.scan` is present, add a "UI scan" section (pages, forms, fields, by-entity table, numbered risk flags, "Submissions: 0 · Non-GET requests allowed: N · Blocked: POST n …", validation summary) with the same escaping helper the rest of the template uses. Its test lives with the scanner tests: `tests/scanner/test_evidence_html_scan.py::test_scan_block_rendered` — a pack with `scan={"submissions": 0, "non_get_allowed": 0, "blocked_requests": {"POST": 3}, …}` renders "Submissions: 0" and "POST 3", and a pack without `scan` renders exactly as before (M2's own evidence tests stay green unchanged).

- [ ] **Step 1: Write the failing tests**

`tests/scanner/test_inventory_export.py` (unit; `scan_engine` fixture; builds `ScanPageOut`/`ScanFieldOut` literals for an identity page with a name, an Aadhaar, a gender select, a password and a file field):
  - `test_inventory_rows_join_pages_and_fields` — one row per field; `path` is the page's `url_redacted`; `risk_codes` carried; no `fill_*` columns in the inventory.
  - `test_export_rerenders_every_persona_and_matches_the_display_persona` — `build_export(personas=[persona_values(n) for n in 1..3])`: `json["personas"] == 3`; for the Aadhaar field the persona-1 `synthesize` value equals the record's `fill_synthesize` and matches `\d{4} \d{4} \d{4}`, persona-1 `obfuscate` equals `fill_obfuscate`; persona 2's value differs; gender values are persona genders; the password field has `values == []` and the note; `"credential"` is absent from every `persona_set` entry; the CSV has `1 + 3 × (fields with values)` lines.
  - `test_csv_guard` — `write_csv(["a"], [["=HYPERLINK(\"x\")"], ["+91 98451 20377"], ["-SUM(1)"], ["@x"]])` decodes to rows `'=HYPERLINK("x")`, `+91 98451 20377`, `'-SUM(1)`, `'@x`.
  - `test_evidence_pack_shape_and_labels` — `build_scan_evidence(result="succeeded", mode synthesize)`: `pack.job_kind == "scan"`, `pack.scan["submissions"] == 0`, `pack.scan["non_get_allowed"] == 0`, `pack.label == "synthetic — measured, not claimed anonymous"`, `reveal_map` has an entry `{"path": "field:3/1/3", "method": "fpe_digits", "reveal": True}`; the canonical pack JSON contains none of the persona values (`normalise` search).

`tests/scanner/test_scan_job_e2e.py` (`pytestmark = [pytest.mark.db, pytest.mark.browser, pytest.mark.slow]`; M2 fixtures `migrated_db`, `queue`, `kek`, `object_store`; a local `handler_ctx` fixture builds `HandlerContext(settings=db_settings ⊕ scanner settings with DPS_ENV=local, engine=scan_engine, worker_id="test-bw:1", kek=kek, artefacts=ArtefactService(object_store, acquire=db.acquire_queue), audit=MemoryAuditSink())` — the constructor of contract §13, as `dps/worker.py::build_context` uses it):
  - `test_scan_job_end_to_end` — create the job the way the API will (Task 18's `create_scan_job` helper is not yet written, so the test seals `JobSecret(key_material=…, synthesis_seed=…, scan_start_url=f"{demo}/apply", scan_auth=demo_cookie_state("127.0.0.1"))` itself with `aad=f"dps_job.key:{job_id}"`, `job_id = new_id("s")`, and calls `jobs.create_job(job_id=job_id, tenant_id="t-scan", kind=JobKind.scan, phase=JobPhase.scan, spec=redact_scan_spec(...), mode="obfuscate", sealed_secret=…, kid=…)`), claim it with `jobs.claim_jobs("test-bw:1", kinds=(JobKind.scan,), batch=1)`, `await scan_job(ctx, claimed)`, then assert: job `succeeded`, `progress.pages >= 15`, `progress.evidence_id` set, `key_enc IS NULL` (SQL), `dps_scan_page` has ≥ 15 `done` rows and the `/apply/identity` row has four screenshot artefacts whose `GET` through `ArtefactService.open` returns `image/png` bytes starting with `\x89PNG`; `dps_scan_request` has blocked POST rows and **zero allowed non-GET rows**; the evidence row's `pack->'scan'->>'submissions' = '0'` and `non_get_allowed = 0`; the export JSON has 3 personas and the display persona's Aadhaar value equals that field's `fill_synthesize`; `MemoryAuditSink` holds one `scan.navigate` event per `navigate` event row; and a normalised search of every `dps_scan_*` row, the evidence pack, the export JSON/CSV and every `capture` JSON artefact finds none of the demo values.
  - `test_cancel_running_scan_finalizes_canceled` — start `scan_job` as a task; poll `dps_scan_page` until one `done` row exists; `await jobs.cancel("t-scan", job_id)`; await the task (it returns after `LeaseLost`); claim again (the job is now `phase=finalize, state=queued`), run `scan_finalize_job`; assert state `canceled`, an evidence pack with `outcome == "canceled"`, `key_enc IS NULL`, and the export artefacts exist.
  - `test_missing_storage_state_secret_fails_the_job` — `storage_state_ref="dps/ui/does-not-exist"` with `DPS_SECRET_DIR` pointing at an empty tmp dir → job `failed`, `error_code == "secret_unavailable"`, `key_enc IS NULL`.

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_inventory_export.py` → FAIL (`ImportError: cannot import name 'build_export'`).

- [ ] **Step 3: Implement** the handlers, `DbScanSink`, `open_runtime`, `finalize_scan`, the inventory/export/evidence builders and the evidence HTML section.

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_inventory_export.py tests/scanner/test_evidence_html_scan.py` → `6 passed` (4 + 2: the scan block, and a pack without it unchanged); `tools/scratch_pg.sh start && DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/scanner/test_scan_job_e2e.py` → `3 passed`; the full `pytest -q` (M2's evidence tests included) green; `ruff check dps tests && mypy dps` clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/scanner/__init__.py dps/layers/scanner/inventory.py dps/layers/connectors/evidence.py tests/scanner/test_inventory_export.py tests/scanner/test_evidence_html_scan.py tests/scanner/test_scan_job_e2e.py
git commit -m "feat(scanner): scan and finalize handlers, persona/field exports, scan evidence pack, key purge" -- dps/layers/scanner/__init__.py dps/layers/scanner/inventory.py dps/layers/connectors/evidence.py tests/scanner/test_inventory_export.py tests/scanner/test_evidence_html_scan.py tests/scanner/test_scan_job_e2e.py
```

---

### Task 17: The browser-worker process (`python -m dps.browser_worker`)

**Files:**
- Create: `dps/browser_worker.py`
- Test: `tests/scanner/test_browser_worker.py`

**Interfaces introduced:**

```python
async def serve_ops(port: int) -> asyncio.Server        # GET /health, /readyz, /metrics; nothing else
def build_consumer(settings: Settings, ctx: HandlerContext | None = None) -> Consumer
    # kinds=(scan,), handlers {scan, finalize}, no partitions, ctx (M2 build_context when None), role="browser-worker"
async def amain(settings: Settings | None = None) -> None
def main() -> None
```

`dps/browser_worker.py` (complete):

```python
"""Browser worker (contract §3.5): claims `scan` jobs only, runs Playwright Chromium, never submits.

    python -m dps.browser_worker

Ops endpoints on DPS_BROWSER_METRICS_PORT (9401): /health, /readyz (component "browser"), /metrics.
SIGTERM: the M2 Consumer drains (running scans finish their current page, release their leases).
"""
from __future__ import annotations

import asyncio
import json
import signal

from dps import __version__
from dps.config import Settings, get_settings
from dps.db import close_pools, db_configured, init_worker_pool
from dps.jobs import JobKind, JobPhase
from dps.layers.scanner import scan_finalize_job, scan_job
from dps.layers.scanner.browser import close_browser, get_browser
from dps.observability import READINESS, configure_logging, get_logger, metrics_payload
from dps.posture import assert_production_posture
from dps.secrets import load_kek
from dps.worker import Consumer, HandlerContext, build_context

logger = get_logger(__name__)


async def _respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode("latin-1")
        while (await asyncio.wait_for(reader.readline(), timeout=5)) not in (b"\r\n", b"\n", b""):
            pass
        parts = request_line.split()
        path = parts[1].split("?")[0] if len(parts) >= 2 else ""
        if parts[:1] != ["GET"] or path not in ("/health", "/readyz", "/metrics"):
            status, ctype, body = "404 Not Found", "application/json", b'{"detail":"not found"}'
        elif path == "/health":
            status, ctype, body = "200 OK", "application/json", b'{"status":"ok"}'
        elif path == "/readyz":
            snap = READINESS.snapshot()
            status = "200 OK" if snap["ready"] else "503 Service Unavailable"
            ctype, body = "application/json", json.dumps(snap).encode()
        else:
            body, ctype = metrics_payload()
            status = "200 OK"
        writer.write(f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     "Connection: close\r\n\r\n".encode("latin-1") + body)
        await writer.drain()
    except (TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


async def serve_ops(port: int) -> asyncio.Server:
    return await asyncio.start_server(_respond, host="0.0.0.0", port=port)


def build_consumer(settings: Settings, ctx: HandlerContext | None = None) -> Consumer:
    """M2's Consumer for scan jobs (contract §3.5): ``ctx`` is required by M2, and the role is passed
    explicitly so ``dps_worker`` records ``browser-worker`` (the ``browser`` readiness counts those rows)."""
    return Consumer(settings, kinds=(JobKind.scan,),
                    handlers={JobPhase.scan: scan_job, JobPhase.finalize: scan_finalize_job},
                    partition_handler=None, concurrency=settings.browser_concurrency,
                    ctx=ctx if ctx is not None else build_context(settings), role="browser-worker")


async def amain(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.env != "local")
    assert_production_posture(settings)
    if not db_configured(settings):
        raise SystemExit("browser-worker needs the metadata database (DPS_PG_HOST)")
    if load_kek(settings) is None:
        raise SystemExit("browser-worker needs DPS_JOB_KEK to open scan secrets")
    READINESS.set("api-core", True, "browser-worker", version=__version__)
    server = await serve_ops(settings.browser_metrics_port)
    await init_worker_pool(settings)          # build_context's ArtefactService and DbAuditSink use acquire_queue
    ctx = build_context(settings)
    ctx.audit.start()                         # type: ignore[attr-defined]  - DbAuditSink, as in M2's worker main
    consumer = build_consumer(settings, ctx)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, consumer.stop)   # Consumer.run() installs no handlers itself (contract §3.5)
    try:
        browser = await get_browser(settings)
        READINESS.set("browser", True, "chromium", version=browser.version)
        logger.info("startup", component="browser-worker", version=__version__)
        await consumer.run()
    finally:
        READINESS.set("browser", False, "stopped")
        await close_browser()
        await ctx.audit.stop()                # type: ignore[attr-defined]
        await close_pools()
        server.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
```

The `Consumer` registers this process in `dps_worker` with role `browser-worker` (contract §2.4 CHECK) because `build_consumer` passes `role="browser-worker"`: M2's `Consumer` takes the role as an argument (default `"worker"`) and never derives it from `kinds` (contract §3.5). It also requires `ctx` — omitting it is a `TypeError` at start. Task 18's `browser` readiness counts `dps_worker` rows with this role, so the DB test below is the guard for M4 DoD 3 (`browser: ok (1 online)`).

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_browser_worker.py`:
  - `test_ops_server_serves_health_readyz_metrics_only` — start `serve_ops(0)` (port from `server.sockets[0].getsockname()[1]`); `GET /health` → 200 `{"status":"ok"}`; `GET /metrics` body contains `dps_scan_requests_total`; `GET /readyz` → 200 or 503 with JSON `components`; `POST /health` → 404; `GET /anything` → 404.
  - `test_consumer_claims_scan_jobs_only` — `build_consumer(settings, ctx=sentinel)` with `monkeypatch.setattr("dps.browser_worker.Consumer", FakeConsumer)` capturing kwargs: `kinds == (JobKind.scan,)`, `set(handlers) == {JobPhase.scan, JobPhase.finalize}`, `partition_handler is None`, `concurrency == settings.browser_concurrency`, `ctx is sentinel`, `role == "browser-worker"`; then with `monkeypatch.setattr("dps.browser_worker.build_context", lambda s: sentinel2)`, `build_consumer(settings)` passes `ctx is sentinel2` (the context is built when none is given).
  - `test_refuses_to_start_without_db_or_kek` — `amain(Settings(_env_file=None, pg_host=""))` raises `SystemExit` naming `DPS_PG_HOST`; with `pg_host="x"` and no KEK raises `SystemExit` naming `DPS_JOB_KEK` (`init_worker_pool` is never reached: patch it to fail the test if called).
  - `test_worker_row_has_browser_role` (`db`; M2 fixture `handler_ctx`) — `consumer = build_consumer(db_settings, handler_ctx)`; run `consumer.run()` as a task; poll (≤ 5 s) until `SELECT role FROM dps.dps_worker WHERE worker_id = $1` (`handler_ctx.worker_id`) returns `'browser-worker'`; then `consumer.stop()`, await the task, and assert the row is gone (the drain unregisters it).

- [ ] **Step 2: Run to confirm failure** — `pytest -q tests/scanner/test_browser_worker.py` → FAIL (`ModuleNotFoundError: dps.browser_worker`).

- [ ] **Step 3: Write `dps/browser_worker.py`** (above).

- [ ] **Step 4: Run** — `pytest -q tests/scanner/test_browser_worker.py` → `3 passed, 1 skipped` without a DB, `4 passed` with `DPS_TEST_REQUIRE_SERVICES=1` and the scratch DB; `ruff check dps tests && mypy dps` clean.

- [ ] **Step 5: Commit**

```bash
git add dps/browser_worker.py tests/scanner/test_browser_worker.py
git commit -m "feat(scanner): browser-worker process — scan-only consumer, Chromium readiness, ops endpoints on 9401" -- dps/browser_worker.py tests/scanner/test_browser_worker.py
```

---

### Task 18: Scans API and app wiring

**Files:**
- Create: `dps/layers/api/routers/scans.py`
- Modify: `dps/app.py` (router, CSP `blob:`, browser readiness refresher), `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§9: two routes)
- Test: `tests/scanner/test_scans_api.py` (marker `db` except the two app-level tests)

**Routes** (all under `/api/v1`, scope `scan`, rate-limited; errors as `{"detail", "code"}` via `DpsHttpError`):

| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `POST /scans` | `ScanSpec`; header `Idempotency-Key` optional | 202 `ScanCreated {scan, key_material, synthesis_seed, replay}` | key and seed returned **once**; a replay returns `replay: true` and no key material |
| `GET /scans?state=&limit=&cursor=` | — | `{"items": [Job], "next_cursor"}` | `jobs.list_jobs(kind=scan)` |
| `GET /scans/{id}` | — | `ScanDetail {scan, summary}` | console polls every 2 s while non-terminal |
| `GET /scans/{id}/pages?after=&limit=` | — | `{"items": [ScanPageOut], "next_cursor"}` | cursor = last `page_no`; limit ≤ 200 |
| `GET /scans/{id}/pages/{page_no}` | — | `ScanPageDetail {page, fields, requests}` | |
| `GET /scans/{id}/events?after=&limit=` | — | `{"items": [ScanEvent], "next_cursor"}` | contract addition; cursor = last `seq` |
| `GET /scans/{id}/inventory?format=json\|csv` | — | JSON `{"items": [InventoryRow], "risk_flags": [NumberedRiskFlag]}` or CSV | live from the tables |
| `GET /scans/{id}/export?format=json\|csv` | — | the stored export artefact, streamed | 409 `export_not_ready` until finalize |
| `POST /scans/{id}/cancel` | — | `Job` | `jobs.cancel`; 404 unknown, 409 `not_cancelable` when terminal |
| `POST /scans/personas/preview` | `PersonaPreviewRequest` | `PersonaPreview` | contract addition; stateless; same function as the worker |

There is no route that submits, posts to a scanned site, or returns stored key material (the contract §9 closing rule); `test_openapi_has_no_submit_route` holds it.

`create_scan` (complete):

```python
@router.post("", status_code=202, response_model=ScanCreated,
             dependencies=[Depends(require_scope("scan")), Depends(enforce_rate_limit)])
async def create_scan(body: ScanSpec, request: Request,
                      idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> ScanCreated:
    settings: Settings = request.app.state.settings
    engine: PrivacyEngine = request.app.state.engine
    tenant: str = request.state.tenant_id
    principal: Principal = request.state.principal
    if not db_configured(settings):
        raise DpsHttpError(503, "db_unconfigured", "scans need the metadata database")
    kek = load_kek(settings)
    if kek is None:
        raise DpsHttpError(503, "kek_unconfigured", "scans need DPS_JOB_KEK to seal the scan secret")
    store = ScanStore(settings.pg_schema)
    async with acquire(tenant) as conn:
        hosts = await store.registered_hosts(conn, tenant)
    if not hosts and settings.env in ("local", "dev"):
        hosts = settings.scan_registered_hosts_list
    try:
        validated = validate_scan(body, settings=settings, hosts=frozenset(hosts))
        engine.policy(body.policy)
        key = (key_material_from_caller(body.consistency.key_material) if body.consistency.key_material
               else generate_key_material(body.consistency.scope))
        seed = (seed_from_caller(body.consistency.synthesis_seed) if body.consistency.synthesis_seed
                else generate_synthesis_seed())
        key_out = (wrap_key_material(key, body.consistency.wrap_key.get("public_key_pem", ""))
                   if body.consistency.wrap_key else key)
    except ScanSpecError as exc:
        raise DpsHttpError(exc.status, str(exc), str(exc)) from None
    except EngineError:
        raise DpsHttpError(422, "policy_invalid", "the policy does not resolve") from None
    except ValueError:
        raise DpsHttpError(422, "consistency_invalid", "key material, seed or wrap key is invalid") from None
    job_id = new_id("s")
    secret = JobSecret(key_material=key.model_dump(mode="json"), synthesis_seed=seed.model_dump(mode="json"),
                       scan_auth=body.auth.storage_state, scan_start_url=validated.start_url)
    sealed = seal(kek, secret.model_dump_json().encode("utf-8"), aad=f"dps_job.key:{job_id}")
    detector = engine.detector(engine.policy(body.policy), body.fill.locale)
    job = await jobs.create_job(job_id=job_id, tenant_id=tenant, kind=JobKind.scan, phase=JobPhase.scan,
                                spec=redact_scan_spec(validated, redact_url(validated.start_url, value_classifier(detector))),
                                mode=body.mode, sealed_secret=sealed, kid=key.kid,
                                purpose=body.purpose.model_dump() if body.purpose else {},
                                created_by=principal.key_id, idempotency_key=idempotency_key)
    replay = job.id != job_id
    request.app.state.audit.write(AuditEvent(trace_id=trace_id(request), route="/api/v1/scans", tenant_id=tenant,
                                             key_id=principal.key_id, mode=body.mode, policy=str(body.policy or settings.default_policy),
                                             outcome="replay" if replay else "accepted", action="job.create", job_id=job.id))
    if replay:
        return ScanCreated(scan=job, replay=True)
    return ScanCreated(scan=job, key_material=key_out, synthesis_seed=seed)
```

`get_scan` assembles `ScanSummary` from `store.summary_counts` (statuses, forms, fields, personal/critical, blocked by method, `non_get_allowed`, navigations), `number_flags` over every page's `risk_flags`, `store.export_artefacts`, `job.progress.evidence_id`, `job.spec` (`fill.personas`, `fill.display_persona`, `mode`, `fill.locale`) and `elapsed_s` from `started_at`/`finished_at`. `pages_discovered` = all non-skipped rows, `pages_queued` = `queued`, `pages_visited` = `done+redirected+failed`, `pages_classified` = `done+redirected`, `pages_filled` = rows with a `filled_synthesize` artefact. Every route resolves the scan with `jobs.get_job(tenant, id)` and returns 404 `scan_not_found` unless `kind == scan`. CSV responses use `write_csv` and `Content-Disposition: attachment; filename="<scan id>-inventory.csv"` (ids only, never a value). Export streams through `ArtefactService.open`.

`personas_preview`: `seed = seed_from_caller(body.synthesis_seed) if body.synthesis_seed else generate_synthesis_seed()`; for `n in 1..count`: `values = persona_values(engine, seed=seed, persona_no=n, locale=body.locale, policy=engine.policy(None))`; returns `{"synthesis_seed": seed, "personas": [{"persona": n, "values": {label: values[attr] for label, attr in preview_entities(locale)}}], "notes": NOTES[locale]}` where `NOTES` states only what the generators guarantee: en_IN `["Aadhaar numbers are Verhoeff-valid and generated, not issued", "PAN values are format-valid", "one persona per record: name, gender, e-mail and birth date agree"]`; en_US `["SSNs use the never-issued 9xx area", "phone numbers are format-valid for the locale", …]`; en_GB/en_CA/es_MX analogous. The console passes the returned seed back as `consistency.synthesis_seed` in `POST /scans`, so persona 1 of the scan is exactly the preview.

`dps/app.py` touch points: `app.include_router(scans.router)`; CSP `img-src 'self' data: blob:` (screenshots are fetched with the API key and shown as blob URLs; nothing else changes); in the lifespan, when `db_configured(settings)`, a task refreshes `READINESS.set("browser", n > 0, f"{n} online" if n else "none online")` every 15 s from `store.browser_workers_online(conn, heartbeat_seconds=3 * settings.job_lease_seconds // 4)` (no DB → `READINESS.set("browser", False, "db unconfigured")`); `browser` is not a required component (api-core stays the only one, spec §4).

- [ ] **Step 1: Write the failing tests** — `tests/scanner/test_scans_api.py` (M2's `api_client` fixture gives a `TestClient` over a migrated test DB with `DPS_JOB_KEK` set and auth disabled; `scoped_client(scopes)` builds one with `auth_enabled=True` and a static key holding only `scopes`):
  1. `test_create_returns_key_and_seed_once_and_stores_no_secrets` — `POST /api/v1/scans {"url": "http://127.0.0.1:8410/apply?email=ananya.k%40example-mail.in"}` → 202; `scan.id` starts `s_`; `key_material.dek` and `synthesis_seed.seed` present; the DB row's `spec::text` contains neither `ananya` nor the dek nor the seed and has `"url": "…email=<EMAIL>"`; `key_enc IS NOT NULL`; `GET /scans/{id}` has no `key_material` anywhere in its JSON.
  2. `test_create_validation_and_scope` — host `evil.example` → 422 `host_not_registered`; body with `"submit": true` → 422; `capture.har=true` → 422 `har_not_supported`; client whose key lacks `scan` → 403.
  3. `test_wrap_key_returns_only_wrapped_material` — RSA public key in `consistency.wrap_key` → `key_material.dek is None`, `wrapped_dek` set, and `unwrap_dek(wrapped, private_pem)` gives 32 bytes.
  4. `test_idempotent_replay_returns_no_key` — same `Idempotency-Key` twice → same `scan.id`, second `replay is True` and `key_material is None`.
  5. `test_reads_pages_fields_events_inventory_export` — seed one scan's rows through `ScanStore` (two pages, three fields, a blocked POST count, five events) → `GET /scans/{id}` summary `pages_visited == 2`, `blocked_requests == {"POST": 2}`, `non_get_allowed == 0`, risk flag `A1`; `/pages?limit=1` paginates with `next_cursor == "1"`; `/pages/1` returns fields and requests; `/events?after=2&limit=2` returns seqs 3 and 4; `/inventory?format=csv` is `text/csv` with a header row and three data rows; `/export` → 409 `export_not_ready`; after `ArtefactService.save(kind="export_json", …)` → 200 with those bytes.
  6. `test_cancel_queued_scan` — `POST /scans/{id}/cancel` → `state == "canceled"`; again → 409 `not_cancelable`.
  7. `test_persona_preview_is_reproducible_and_matches_the_scan_persona` — preview `{"locale": "en_IN", "count": 2}` → 2 personas, labels `Name, Date of birth, Aadhaar, PAN, Mobile`; calling again with the returned seed gives identical values; `values["Aadhaar"] == persona_values(engine, seed, 1, "en_IN", default policy)["aadhaar"]`.
  8. `test_openapi_has_no_submit_route` — every path in `/api/v1/openapi.json` lacks `submit`; the scan paths' methods are exactly `{"post"}` for `/scans`, `/scans/{scan_id}/cancel`, `/scans/personas/preview` and `{"get"}` for the rest.
  9. `test_csp_allows_blob_images` — `dps.app.CSP` contains `img-src 'self' data: blob:`.
  10. `test_browser_readiness_from_heartbeats` — insert a `dps_worker` row with role `browser-worker`, run the refresher once → `READINESS.snapshot()["components"]["browser"]["ok"] is True`.
  11. `test_api_imports_without_playwright_or_pillow` (unit) — `subprocess.run([sys.executable, "-c", "import sys; sys.modules['playwright'] = None; sys.modules['PIL'] = None; import dps.app, dps.layers.api.routers.scans, dps.layers.scanner"], check=True)` exits 0 (the app image has neither package).

- [ ] **Step 2: Run to confirm failure** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/scanner/test_scans_api.py` → FAIL (404 on `/api/v1/scans`).

- [ ] **Step 3: Implement** the router (`APIRouter(prefix="/api/v1/scans", tags=["scans"])`, the preview route declared before `/{scan_id}` routes), the app changes and the contract §9 rows: `M4 | GET /scans/{id}/events?after=&limit= | scan | navigation log, value-free (dps_scan_event)` and `M4 | POST /scans/personas/preview | scan | {locale, count ≤ 5, synthesis_seed?} → {synthesis_seed, personas, notes}; stateless`.

- [ ] **Step 4: Run** — same command → `11 passed`; `pytest -q` (unit) green, including `tests/scanner/test_no_submit.py::test_no_submission_code_path_exists_in_the_scanner` which now also scans `scans.py`; `ruff check dps tests && mypy dps` clean.

- [ ] **Step 5: Commit**

```bash
git add dps/layers/api/routers/scans.py dps/app.py tests/scanner/test_scans_api.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
git commit -m "feat(api): scans routes — create (key and seed once), pages, events, inventory, export, cancel, persona preview" -- dps/layers/api/routers/scans.py dps/app.py tests/scanner/test_scans_api.py docs/superpowers/plans/2026-10-11-m1-m5-contract.md
```

---

### Task 19: No values in logs (contract §0.1 log-scan test for M4)

**Files:**
- Test: `tests/test_no_values_in_logs_m4.py`

- [ ] **Step 1: Write the tests**
  - `test_scanner_loggers_drop_value_fields` (unit) — configure logging to JSON into a `StringIO`; log through `get_logger("dps.layers.scanner.crawler")` an event `scan_page` with `page_no=3, url="http://h/x?email=a@b.co", label="Ananya", value="2341 2341 2346", storage_state={"cookies": []}`; the captured line has `page_no` and none of `email=`, `Ananya`, `2341`, `cookies`.
  - `test_guard_and_crawl_logs_are_value_free` (`browser`) — capture all log output while a `MemoryScanSink` crawl of `/apply` with `max_pages=6` runs; assert no demo value (normalised), no `?email=`, no `demo_session`, and no persona value of the display persona appears in the captured text.
  - `test_api_logs_are_value_free` (`db`) — capture logs around `POST /scans` with a URL carrying `?email=ananya.k%40example-mail.in` and inline storage state (local settings allow it), then `GET /scans/{id}/pages`; assert `ananya`, `demo-valid` and the returned dek never appear.

- [ ] **Step 2: Run** — `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/test_no_values_in_logs_m4.py` → `3 passed`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_no_values_in_logs_m4.py
git commit -m "test(scanner): scanner, guard and scan API logs carry no page values, keys or sessions" -- tests/test_no_values_in_logs_m4.py
```

---

### Task 20: Browser-worker image and compose services

**Files:**
- Create: `Dockerfile.browser`
- Modify: `docker-compose.yml` (`browser-worker`, `demo-site`), `.dockerignore` (exclude `demo`), `HANDOFF.md` (build/run/ports/traps for M4)

`Dockerfile.browser` (complete):

```dockerfile
# Data Privacy Services — browser-worker image (M4, contract §11.1).
# The base image bakes Chromium build 1187 under /ms-playwright; it matches playwright==1.55.0
# exactly, so this file never runs `playwright install`. Ubuntu 24.04's Python 3.12 has no
# ensurepip, so uv creates the virtualenv.
FROM mcr.microsoft.com/playwright:v1.55.0-noble
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY dps ./dps
ARG WITH_NER=0
RUN uv venv --python /usr/bin/python3.12 /opt/venv \
 && if [ "$WITH_NER" = "1" ]; then \
      uv pip install --python /opt/venv/bin/python ".[fpe,db,connectors,scanner,ner]" \
      && /opt/venv/bin/python -m spacy download en_core_web_sm; \
    else uv pip install --python /opt/venv/bin/python ".[fpe,db,connectors,scanner]"; fi \
 && /opt/venv/bin/python -c "import importlib.metadata as m; assert m.version('playwright') == '1.55.0'"
USER pwuser
EXPOSE 9401
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9401/health', timeout=3).status==200 else 1)"
CMD ["python", "-m", "dps.browser_worker"]
```

`docker-compose.yml` additions. The browser-worker **extends M2's `worker` service** (Compose `extends` within the same file), so it inherits exactly the same database, `DPS_JOB_KEK`, artefact-store and secret settings and volumes — the app seals scan secrets with that KEK and serves artefacts from that store — and overrides only what differs:

```yaml
  browser-worker:
    extends: { service: worker }                # same DB, KEK, artefact store and volumes as M2's worker
    build: { context: ., dockerfile: Dockerfile.browser }
    image: data-privacy-services-browser:local
    command: ["python", "-m", "dps.browser_worker"]
    profiles: ["scanner", "full"]
    depends_on:
      dps-db: { condition: service_healthy }
      migrate: { condition: service_completed_successfully }
    environment:
      DPS_MIGRATIONS_MODE: "off"                 # the migrate service owns DDL
      DPS_SECRET_DIR: /var/run/secrets/dps-local
      DPS_BROWSER_CONCURRENCY: "2"
      DPS_BROWSER_METRICS_PORT: "9401"
      DPS_SCAN_REGISTERED_HOSTS: localhost,127.0.0.1,demo-site
      DPS_FPE_ENABLED: "true"
    volumes:
      - ./docker/secrets:/var/run/secrets/dps-local:ro
    shm_size: "1gb"
    init: true
    mem_limit: 2g
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9401/health', timeout=3).status==200 else 1)"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 30s
    labels: { app: data-privacy-services, component: browser-worker, env: local }

  demo-site:
    image: node:22-slim
    profiles: ["scanner", "full"]
    command: ["node", "/srv/demo/server.mjs", "--host", "0.0.0.0", "--port", "8410"]
    volumes:
      - ./demo/onboarding:/srv/demo:ro
    ports:
      - "127.0.0.1:8410:8410"
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:8410/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval: 10s
      timeout: 5s
      retries: 5
    mem_limit: 256m
    labels: { app: data-privacy-services, component: demo-site, env: local }
```

The `app` service needs no change for M4 (the API validates hosts with the same default `DPS_SCAN_REGISTERED_HOSTS`, and it already shares the artefact store with M2's `worker`, hence with the browser-worker). The storage-state ref for compose is `dps/ui/demo-onboarding` (the file from Task 7, domain `demo-site`). `HANDOFF.md` gains: the image and how its Chromium is pinned; `--profile scanner`; ports 8410 (demo site) and 9401 (internal ops); the traps — host tests need `python -m playwright install chromium` because the host cache holds other builds; Chromium in Docker needs `shm_size` (or `--disable-dev-shm-usage`, which the worker also passes); the demo storage state is a local literal; scans of anything but registered hosts are refused; HAR capture is not supported.

- [ ] **Step 1: Build and run** — `docker compose --profile scanner up -d --build`, then poll until healthy (never `--wait`, the one-shot `migrate` exits): `for i in $(seq 1 60); do docker compose ps --format '{{.Service}} {{.Health}}' | grep -E '^(browser-worker|demo-site|app) ' ; sleep 5; done` → `app healthy`, `browser-worker healthy`, `demo-site healthy`. `docker compose exec browser-worker python -c "import playwright, importlib.metadata as m; print(m.version('playwright'))"` prints `1.55.0`; `curl -fsS http://127.0.0.1:8400/readyz` shows `"browser": {"ok": true, "detail": "1 online"}` within 30 s.
- [ ] **Step 2: A scan through the stack** — `curl -fsS -X POST http://127.0.0.1:8400/api/v1/scans -H 'X-API-KEY: dps_local_dev_key_change_me' -H 'Content-Type: application/json' -d '{"url":"http://demo-site:8410/apply","scope":{"max_pages":25,"deny":["/logout","/admin"]},"auth":{"storage_state_ref":"dps/ui/demo-onboarding"},"fill":{"personas":5,"locale":"en_IN"}}'` → 202 with key material; `GET /api/v1/scans/<id>` reaches `succeeded` within 3 minutes; `curl -fsS http://127.0.0.1:8410/__received` shows `"non_get":0` (fresh demo container).
- [ ] **Step 3: Commit**

```bash
git add Dockerfile.browser docker-compose.yml .dockerignore HANDOFF.md
git commit -m "feat(scanner): browser-worker image on the Playwright 1.55.0 base and compose scanner profile with the demo site" -- Dockerfile.browser docker-compose.yml .dockerignore HANDOFF.md
```

---
### Task 21: Console foundations — scan types, `api.scans`, authenticated images, scanner styles (polling = M2's `usePoll`)

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/styles.css`
- Create: `frontend/src/lib/download.ts`, `frontend/src/components/scanner/ArtefactImage.tsx`, `frontend/src/components/scanner/format.ts`
- Reuse, never recreate: `frontend/src/hooks/usePoll.ts` (M2 Task 29, contract §14) and M2's `types.ts` names (`Job`, `JobState`, `JobProgress`, `Page<T>`, …) — redeclaring any of them is a TS2300 duplicate identifier and overwriting the hook breaks the M2/M3 pages.

**Types** (`types.ts`, mirroring the pydantic models field for field; append only the names below — every one is new; `Job`, `JobState`, `Page<T>`, `KeyMaterial` and `Sensitivity` already exist from M0/M2):

```ts
export interface SynthesisSeed { alg: string; seed: string; created_at: string }
export type ScanMode = 'obfuscate' | 'synthesize';
export interface ScanScope { same_origin: boolean; max_pages: number | null; max_depth: number | null; allow: string[]; deny: string[]; iframes: boolean; shadow_dom: boolean; spa_clicks: boolean }
export interface ScanSpecIn {
  url: string; scope: ScanScope; auth: { storage_state_ref: string | null }; mode: ScanMode;
  fill: { personas: number; locale: string; display_persona: number }; parallel_view: boolean;
  capture: { screenshots: boolean; har: false };
  consistency?: { scope?: 'request' | 'key' | 'entity'; synthesis_seed?: SynthesisSeed; wrap_key?: { type: 'rsa-oaep-256'; public_key_pem: string } };
  policy?: string | null;
}
export interface ScanCreated { scan: Job; key_material: KeyMaterial | null; synthesis_seed: SynthesisSeed | null; replay: boolean }
export type RiskFlagKind = 'autocomplete_enabled_sensitive' | 'password_without_autocomplete_token' | 'pii_in_query_string' | 'sensitive_form_uses_get' | 'pii_rendered_unmasked';
export interface RiskFlag { flag: RiskFlagKind; severity: 'high' | 'medium'; page_no: number; field_nos: number[]; entity_types: string[]; query_keys: string[]; where: 'table' | 'text' | 'url' | 'link' | 'form' | null }
export interface NumberedRiskFlag extends RiskFlag { code: string; path: string }
export interface ScanSummary {
  pages_by_status: Record<string, number>; pages_visited: number; pages_discovered: number; pages_queued: number;
  pages_classified: number; pages_filled: number; forms: number; fields: number; personal_fields: number; critical_fields: number;
  risk_flags: NumberedRiskFlag[]; blocked_requests: Record<string, number>; non_get_allowed: number; navigations: number;
  captures: number; validation: Record<string, Record<string, number>>; personas: number; display_persona: number;
  mode: ScanMode; locale: string; export_artefacts: Record<string, string>; evidence_id: string | null; elapsed_s: number | null;
}
export interface ScanDetail { scan: Job; summary: ScanSummary }
export type PageStatus = 'queued' | 'visiting' | 'done' | 'redirected' | 'skipped' | 'failed';
export interface ScanPage {
  page_no: number; url_redacted: string; title_redacted: string | null; depth: number; parent_page_no: number | null;
  status: PageStatus; status_reason: string | null; discovered_via: 'start' | 'link' | 'spa_click' | 'redirect'; template: string;
  http_status: number | null; final_url_redacted: string | null; form_count: number; field_count: number;
  personal_field_count: number; critical_field_count: number; forms: Record<string, unknown>[];
  artefacts: Partial<Record<'original' | 'annotated' | 'filled_synthesize' | 'filled_obfuscate' | 'masked_capture' | 'capture_json', string>>;
  rendered_findings: Record<string, number>; risk_flags: RiskFlag[]; validation: Record<string, Record<string, number>>;
  capture_status: 'captured' | 'withheld' | 'not_needed' | null; timings: Record<string, number>; created_at: string; updated_at: string;
}
export interface ValidationMessage { mode: ScanMode; source: 'native' | 'aria' | 'aria-invalid'; codes: string[]; text: string }
export interface ScanField {
  page_no: number; form_index: number; field_no: number; selector: string; tag: string; input_type: string | null;
  name_attr: string | null; id_attr: string | null; label: string | null; label_source: string; autocomplete: string | null;
  pattern: string | null; maxlength: number | null; required: boolean; options_count: number | null;
  entity_type: string | null; sensitivity: Sensitivity | null; confidence: number | null; detectors: string[]; evidence: string[];
  fill_synthesize: string | null; fill_obfuscate: string | null; validation_messages: ValidationMessage[];
  frame_index: number; in_shadow: boolean; placeholder_shape: string | null; prefilled: boolean; persona_attr: string | null;
  obfuscate_method: string | null; fill_format: string | null; fill_status: string; risk_codes: string[];
}
export interface ScanRequestCount { page_no: number | null; method: string; resource_type: string | null; decision: 'allowed' | 'blocked'; reason: string; host: string; count: number }
export interface ScanPageDetail { page: ScanPage; fields: ScanField[]; requests: ScanRequestCount[] }
export interface ScanEvent { seq: number; ts: string; kind: string; method: string | null; result: string; page_no: number | null; target: string; note_code: string; counts: Record<string, number> }
export interface InventoryRow { page_no: number; path: string; form_index: number; field_no: number; label: string | null; tag: string; input_type: string | null; name_attr: string | null; autocomplete: string | null; pattern: string | null; maxlength: number | null; required: boolean; entity_type: string | null; sensitivity: Sensitivity | null; confidence: number | null; detectors: string[]; risk_codes: string[]; fill_status: string; persona_attr: string | null; obfuscate_method: string | null }
export interface PersonaPreview { synthesis_seed: SynthesisSeed; personas: { persona: number; values: Record<string, string> }[]; notes: string[] }
```

**`api.ts` additions** (complete; they use M2 Task 29's module-level helpers `request`, `json`, `post` and `qs` — never redeclare `qs`, it already exists in the file):

```ts
const enc = encodeURIComponent;
const blob = (path: string, auth: Auth): Promise<Blob> => request(path, auth).then((r) => r.blob());  // ApiError on !ok

export const scansApi = {
  create: (auth: Auth, spec: ScanSpecIn, idempotencyKey?: string) =>
    post<ScanCreated>('/api/v1/scans', auth, spec, idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined),
  list: (auth: Auth, p: { state?: string; limit?: number; cursor?: string } = {}) => json<Page<Job>>(`/api/v1/scans${qs(p)}`, auth),
  get: (auth: Auth, id: string) => json<ScanDetail>(`/api/v1/scans/${enc(id)}`, auth),
  pages: (auth: Auth, id: string, after = 0, limit = 200) => json<Page<ScanPage>>(`/api/v1/scans/${enc(id)}/pages${qs({ after, limit })}`, auth),
  page: (auth: Auth, id: string, pageNo: number) => json<ScanPageDetail>(`/api/v1/scans/${enc(id)}/pages/${pageNo}`, auth),
  events: (auth: Auth, id: string, after = 0, limit = 200) => json<Page<ScanEvent>>(`/api/v1/scans/${enc(id)}/events${qs({ after, limit })}`, auth),
  inventory: (auth: Auth, id: string) => json<{ items: InventoryRow[]; risk_flags: NumberedRiskFlag[] }>(`/api/v1/scans/${enc(id)}/inventory?format=json`, auth),
  download: (auth: Auth, id: string, what: 'inventory' | 'export', format: 'json' | 'csv') => blob(`/api/v1/scans/${enc(id)}/${what}?format=${format}`, auth),
  cancel: (auth: Auth, id: string) => post<Job>(`/api/v1/scans/${enc(id)}/cancel`, auth),
  personaPreview: (auth: Auth, body: { locale: string; count?: number; synthesis_seed?: SynthesisSeed | null }) =>
    post<PersonaPreview>('/api/v1/scans/personas/preview', auth, body),
  artefact: (auth: Auth, artefactId: string) => blob(`/api/v1/artefacts/${enc(artefactId)}`, auth),
};
```

and `scans: scansApi` inside the exported `api` object (contract §14: `api.scans.*`).

**Polling** reuses M2's hook unchanged (contract §14):
`usePoll<T>(load: () => Promise<T>, intervalMs: number, active: (data: T | undefined) => boolean, deps?: unknown[])
→ { data: T | undefined; error: Error | undefined; refresh: () => void }`. The scanner pages call it as:

```ts
const TERMINAL: ReadonlySet<JobState> = new Set(['succeeded', 'failed', 'dead', 'canceled']);
// nothing to poll before a scan exists: resolve undefined and stay inactive
const detail = usePoll(() => (scanId ? api.scans.get(auth, scanId) : Promise.resolve(undefined)), 2000,
                       (d) => !!d && !TERMINAL.has(d.scan.state), [scanId]);
const running = !!detail.data && !TERMINAL.has(detail.data.scan.state);
// pages/events follow the scan: `running` is a dep, so the terminal flip triggers one final fetch, then they stop
const pages = usePoll(() => (scanId ? api.scans.pages(auth, scanId) : Promise.resolve(undefined)), 2000,
                      () => running, [scanId, running]);
```
`error` is an `Error` (render `error.message`); `data` is `undefined` (not `null`) before the first load.

`lib/download.ts`: `export function saveBlob(blob: Blob, filename: string): void` (object URL → temporary `<a download>` → click → revoke) and `export function saveText(text: string, filename: string, type = 'application/json'): void`. Filenames are built from scan ids only.

`components/scanner/ArtefactImage.tsx` (complete) — screenshots need the API key, so they are fetched and shown as blob URLs (the CSP change of Task 18):

```tsx
import { useEffect, useState } from 'react';
import { useSettings } from '../../hooks/useSettings';
import { api } from '../../lib/api';

export function ArtefactImage({ id, alt }: { id: string | null | undefined; alt: string }): JSX.Element {
  const { apiKey, tenant } = useSettings();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setUrl(null);
    setFailed(false);
    if (!id) return undefined;
    let alive = true;
    let objectUrl: string | null = null;
    api.scans.artefact({ apiKey, tenant }, id)
      .then((blob) => { if (!alive) return; objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id, apiKey, tenant]);
  if (!id) return <div className="shot-empty">Not captured</div>;
  if (failed) return <div className="shot-empty">This capture could not be loaded</div>;
  if (!url) return <div className="skeleton shot-skeleton" aria-hidden="true" />;
  return <img className="shot" src={url} alt={alt} />;
}
```

`components/scanner/format.ts` — pure display helpers, value-free: `pathOf(urlRedacted)` (path + redacted query, with `<v>`/`<ENTITY>` shown as `•••` as in mockup 12's log), `hostOf(url)`, `statusIcon(page)` (`done`→✓ ok, `visiting`→running dot, `queued`→dashed, `skipped`/`failed`→–, `redirected`→↪), `skipLabel(reason)` (`deny`→"deny list", `other_origin`→"other origin", `unregistered_host`→"unregistered host", `not_allowed`→"outside allow list", `depth`→"depth limit", `template`→"template limit, links not followed", `budget`→"page budget"), `flagSentence(flag)` (`autocomplete_enabled_sensitive`→"autocomplete on for {entities}", `password_without_autocomplete_token`→"password field without new-password/current-password", `pii_in_query_string`→"PII in query string: ?{key}=", `sensitive_form_uses_get`→"form sends {entities} in the URL (method GET)", `pii_rendered_unmasked`→"{entities} rendered unmasked{ in a table}"), `entityChip(sensitivity)` (CRITICAL→rose, HIGH/MEDIUM→amber, LOW→slate), `eventNote(event, pagesByNo)` (the sentences mockup 12 shows: "from filled page /apply/address · interception", "deny list", "other origin", "persona 7 · 34 fields · 2 validation messages recorded", "persona 7 · 9 fields · all values accepted", "obfuscated capture", "withheld — a value could not be masked", "SPA route found by clicking"), `evidenceLabel(detectors)` (families joined with " + ": token, label, name, type, pattern, maxlength, value).

`styles.css` — append a `/* --- UI scanner (M4): mockups 12 and 04 --- */` block that ports the mockup classes verbatim (`.nosubmit .ns-icon .ns-t .ns-body .ns-stats .ns-stat .setup .col .hint .two-in .urlrow .sw .switch .deny .addrow .seg.full .persona .ok-line .reqpane .live-dot .prog-top .bar.big .pipe .stats .tree .node .st.* .kids .tree-foot .log .lr .scan .crawl .parallel .frame .bar-url .form .f.real/.syn/.obf .f .n .btnrow .fakebtn .flag` and their media queries), scoping the two names M0 already uses differently: `.setup .field { gap: 6px; margin-bottom: 12px }` and `.setup .err { background: none; border: 0; padding: 0; color: var(--rose) }`; plus `.shot { display: block; width: 100%; height: auto }`, `.shot-empty { padding: 32px; text-align: center; color: var(--ink-3); font-size: 12.5px }`, `.shot-skeleton { height: 360px; border-radius: 0 }`, `.tabs` (reuse `.seg`) and `.switch[aria-disabled="true"] { opacity: .5; cursor: not-allowed }`. Only Vault tokens — no raw palette.

- [ ] **Step 1: Write the files** above.
- [ ] **Step 2: Gates** — `cd frontend && npm run typecheck && npm test && npm run build` → no errors (M2's Vitest suite still green); `git diff --stat frontend/src/hooks/usePoll.ts` is empty (the hook is M2's); `grep -c "export type JobState\|interface PageOf" frontend/src/lib/types.ts` prints `1` (M2's `JobState` only, no `PageOf`); `pytest -q` unaffected.
- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/download.ts frontend/src/components/scanner/ArtefactImage.tsx frontend/src/components/scanner/format.ts frontend/src/styles.css
git commit -m "feat(console): scan types, api.scans client, authenticated screenshot images, scanner styles" -- frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/download.ts frontend/src/components/scanner/ArtefactImage.tsx frontend/src/components/scanner/format.ts frontend/src/styles.css
```

---

### Task 22: Console — New scan and live crawl (`/scanner`, `/scanner/new`, mockup 12)

**Files:**
- Create: `frontend/src/pages/ScanNew.tsx`, `frontend/src/components/scanner/{NoSubmitBanner,CrawlProgress,CrawlMap,NavLog,PersonaPreview}.tsx`
- Modify: `frontend/src/App.tsx` (routes `/scanner`, `/scanner/new` → `ScanNew`; `/scanner/scans/:scanId` → `ScanDetail` placeholder until Task 23 replaces it)

**Page structure (built to `docs/design/mockups/12-scanner-setup.html`, section by section):**

| Mockup element | Implementation | Data |
|---|---|---|
| topbar crumb "UI scanner / New scan · s_…", pill "No submissions · POST interception on" | in-page header row under M0's `Layout` topbar | scan id from `?scan=` |
| page-head: "New scan · <host>", description, **Stop crawl**, **Open parallel view · N pages ready** | Stop → `api.scans.cancel`; Open → `Link` to `/scanner/scans/:id` (disabled while `pages_filled == 0`) | `summary.pages_filled` |
| `NoSubmitBanner` ("DPS never submits forms." + two stats) | left stat = `summary.blocked_requests.POST ?? 0` "POST requests blocked in this scan"; right stat = constant `0` "forms submitted, in any scan" with `title="There is no submission path in DPS (decision D4); every scan's evidence pack records submissions: 0 and the measured non-GET count."` | `GET /scans/{id}` |
| stepper Setup ✓ · Crawl · visited of discovered · Classify · N pages · Parallel view · N pages ready · Export | `.stepper` | summary |
| **Target** card: Start URL, host chip, error line | input; on 422 `host_not_registered` the error line reads "This host is not registered for the tenant. Register it in Admin › UI-scanner hosts (M5); locally the registered hosts are localhost, 127.0.0.1 and demo-site." After a successful create the chip reads "registered host" | POST response |
| **Scope** card: Max pages, Max depth, Never visit chips + Add path (same regex and messages as the mockup), switches Same origin only (+ amber callout when off), Same-origin iframes, Open shadow DOM | controlled inputs into `cfg` | — |
| **Authentication** card: Stored session, hint, **Check session** + result line | text input with `<datalist>` (`dps/ui/demo-onboarding`) and a "none · public pages only" choice; Check session creates a **one-page probe scan** (`max_pages: 1, max_depth: 0, parallel_view: false, capture.screenshots: false, spa_clicks: false`), polls it, then shows "✓ Session valid · <path> returned 200 without a redirect" or "✗ Redirected to <path> — the stored session is not valid" from page 1's `status`/`final_url_redacted` | `POST /scans`, `GET /scans/{id}/pages` |
| **Fill mode** card: seg Synthesize persona fill / Obfuscate captures, chip, Personas (1–50), Locale, **Persona 1 preview**, hint; obfuscate panel text and **Wrap key with** (Return the key in clear (TLS only) / RSA public key PEM textarea); switches Screenshots with field overlays, HAR file (disabled, hint "Not supported: a HAR holds response bodies with personal data") | `PersonaPreview` calls `api.scans.personaPreview({locale, count: 1, synthesis_seed})` on locale change and keeps the returned seed in React state, so persona 1 of the scan equals the preview | `POST /scans/personas/preview` |
| **Request** card: pane `POST /api/v1/scans` JSON preview, "**Restart crawl with these settings**" / "**Start crawl**", "Copy request" | preview = the exact body minus `consistency` (the seed is never shown or copied); Start → `api.scans.create(spec with consistency.synthesis_seed = preview seed, wrap_key when chosen)` → `navigate('/scanner/new?scan=<id>')`; Restart = cancel current (if non-terminal) then Start | — |
| key card (after create) | shows `key_material` (kid, wrapped or clear) and `synthesis_seed` fingerprint with "Copy key material" — held in React state only, never `localStorage`, gone on reload (contract §14) | POST response |
| **Crawl · live** card: chip "crawling · m:ss"/"finished", "X of Y discovered pages visited · Z queued", budget line, progress bar, pipe crawled → classified → filled and captured, stats forms / fields inventoried / personal data fields / risk flags so far | `CrawlProgress` | summary |
| **Crawl map** card: legend, collapsible tree (status dot, path link to the parallel view for pages with forms, depth `d1`, meta "1 form · 30 fields", chips `PII n`, risk codes `A1`, `n POST blocked`, skip reasons), footer | `CrawlMap` builds the tree from `parent_page_no`; per-page blocked counts come from the event feed (`kind=request, result=blocked`) | `GET /scans/{id}/pages`, events |
| **Navigation log** card: newest first, Time / Event / Result / Target / Note, "Showing 13 of N entries", **Download log as JSONL** | `NavLog` (13 newest rows; `fresh` animation on new rows unless reduced motion); download builds JSONL client-side from the events already fetched | `GET /scans/{id}/events?after=` (incremental) |

Polling: M2's `usePoll(load, 2000, active, deps)` (Task 21 shows the call shape) on `get`, `pages` and `events` every 2 s while `scan.state` is non-terminal (`queued|running|waiting|paused`), one final fetch after it turns terminal. `/scanner` without `?scan=` shows the setup column and an empty live column with the text "Start a crawl to see pages appear here." Accessibility as in the mockup: labelled inputs, `role="switch"` with `aria-checked`, `aria-live="polite"` on the log and the request pane, focus-visible rings from the tokens.

- [ ] **Step 1: Write the page and components** per the table (state in one `cfg` object mirroring `ScanSpecIn`; the request preview renders from it).
- [ ] **Step 2: Gates** — `cd frontend && npm run typecheck && npm run build` → clean.
- [ ] **Step 3: Visual check against mockup 12** — with the compose stack up (`--profile scanner`), open `http://127.0.0.1:8400/scanner/new`, paste the bootstrap key in the topbar, set Start URL `http://demo-site:8410/apply`, deny `/logout` `/admin`, session `dps/ui/demo-onboarding`, locale en_IN, Start crawl. Compare side by side with `docs/design/mockups/12-scanner-setup.html` at 1440 px and 390 px, dark and light: card order and titles, the no-submit banner, the two-column `.setup` grid collapsing below 1200 px, the crawl map statuses and chips, the navigation log columns and its mobile layout, no horizontal page scroll at 390 px. The blocked-POST counter rises while `/apply/identity`, `/apply/address` and `/apply/us` are filled.
- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ScanNew.tsx frontend/src/components/scanner/NoSubmitBanner.tsx frontend/src/components/scanner/CrawlProgress.tsx frontend/src/components/scanner/CrawlMap.tsx frontend/src/components/scanner/NavLog.tsx frontend/src/components/scanner/PersonaPreview.tsx frontend/src/App.tsx
git commit -m "feat(console): UI scanner setup and live crawl — scope, session check, persona preview, crawl map, navigation log" -- frontend/src/pages/ScanNew.tsx frontend/src/components/scanner/NoSubmitBanner.tsx frontend/src/components/scanner/CrawlProgress.tsx frontend/src/components/scanner/CrawlMap.tsx frontend/src/components/scanner/NavLog.tsx frontend/src/components/scanner/PersonaPreview.tsx frontend/src/App.tsx
```

---

### Task 23: Console — Scan detail: parallel view, inventory, captures (`/scanner/scans/:scanId`, mockup 04)

**Files:**
- Create: `frontend/src/pages/ScanDetail.tsx`, `frontend/src/components/scanner/{ParallelView,FieldInventory,RiskFlags,CapturesTab}.tsx`
- Modify: `frontend/src/App.tsx` (real `ScanDetail` route)

**Page structure (built to `docs/design/mockups/04-ui-scanner.html`):**

| Mockup element | Implementation | Data |
|---|---|---|
| topbar "UI scanner / <origin>" + no-submission pill | as in Task 22 | `scan.spec.url_origin` |
| page-head "Scan s_… · <host>", summary sentence ("38 pages crawled · 12 forms · 212 fields · 97 classified as personal data · 3 risk flags · auth: storage state …"), **Export inventory**, **Export personas**, **Evidence pack** | Export inventory → `download('inventory','csv')`; Export personas → `download('export','json')` (disabled with a tooltip until `export_artefacts.json` exists); Evidence pack → `Link` to `/reports/evidence/:evidenceId` (disabled until `evidence_id`) | summary |
| stepper Crawl ✓ · Classify ✓ · Parallel view · persona k of N · Export | `.stepper` | summary |
| tabs (contract §14 `?tab=parallel|inventory|captures`) | `.seg` tab list under the stepper; default `parallel`; `?page=` selects the page, `?fill=synthesize|obfuscate` the side (default = the scan's mode) | — |
| **Pages with forms** card: list with chips (`N crit.` rose / count amber / `rendered PII`), selected row highlighted | `RiskFlags` + page list from `GET /scans/{id}/pages` (status `done`, `form_count > 0` or rendered findings) | pages |
| **Risk flags** list: `A1` chips + sentences with paths | `RiskFlags` using `summary.risk_flags` and `flagSentence` | summary |
| **Parallel view · <path>** card: "Filled with" seg **Synthetic persona / Obfuscated tokens**; two `.frame`s with `.bar-url` (dots, `host…path`, chip `original · as captured` amber — plus "· personal data masked" when the page rendered or pre-filled personal data — and `filled · synthetic persona k` violet / `filled · obfuscated tokens (key held by caller)` teal) | `ParallelView`: **Screens** view = `ArtefactImage` of `annotated` (left) and `filled_<mode>` (right); **Fields** view (second small seg "Screens · Fields") = the mockup's replica `.form` grid built from field rows: left `.f.real` shows "(empty)" or "••• masked" for pre-filled fields with the amber number badge; right `.f.syn`/`.f.obf` shows `fill_synthesize`/`fill_obfuscate` | `GET /scans/{id}/pages/{n}` |
| callout under the frames | "Fields are filled in the real page by the browser worker and captured; no button is ever pressed and any form POST is blocked by request interception. Client-side validation accepted all N values (format evidence recorded)." — or "… recorded M validation messages; see the inventory." from `page.validation[mode]` | page |
| **Field inventory · <path>** card with legend; table `# · Label · Input · autocomplete · Entity · Conf. · Fill method · Evidence` (+ **Filled value** column for the active side) | `FieldInventory`: Input = `type · name=…`/`pattern …`/`maxlength=…`; autocomplete shows the token, `on`, `off` or `—`, with the field's risk code chips; Entity chip coloured by sensitivity; Fill method `persona.<attr> / <obfuscate method>`; Evidence via `evidenceLabel`; validation messages expand under a row | page detail |
| **Inventory** tab | the whole-scan table from `GET /scans/{id}/inventory` with filters (entity type, sensitivity, "risk flags only") and CSV/JSON download | inventory |
| **Captures** tab | `CapturesTab`: one card per page with `capture_status` — `captured`: `ArtefactImage` of `masked_capture`, chip `obfuscated · reversible map` (obfuscate) or `synthetic replacement` (synthesize), "Download reversible map" (the `capture_json` artefact) with the hint "Reveal with POST /api/v1/reveal and this scan's key material"; `withheld`: "Withheld — a value could not be masked, so nothing was stored" | pages |

Polling every 2 s with M2's `usePoll` while the scan is non-terminal (the parallel view fills in as pages finish). Responsive: `.scan` and `.parallel` collapse to one column below 1200 px exactly as the mockup's media query.

- [ ] **Step 1: Write the page and components** per the table.
- [ ] **Step 2: Gates** — `cd frontend && npm run typecheck && npm run build` → clean.
- [ ] **Step 3: Visual check against mockup 04** — open the scan from Task 22's crawl, select `/apply/identity`: the two frames show the annotated original (nine amber numbered boxes, empty fields) and the filled screen (violet boxes, persona values); flip to Obfuscated tokens (teal boxes; FPE tokens keep each field's format); switch to Fields view and compare with the mockup's replica layout; the inventory rows match the mockup's nine rows (entity, autocomplete, evidence families); select `/profile` and open Captures: the masked capture shows tokens where the demo profile shows personal data. Dark and light; 1440 px and 390 px; no horizontal page scroll.
- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ScanDetail.tsx frontend/src/components/scanner/ParallelView.tsx frontend/src/components/scanner/FieldInventory.tsx frontend/src/components/scanner/RiskFlags.tsx frontend/src/components/scanner/CapturesTab.tsx frontend/src/App.tsx
git commit -m "feat(console): scan detail — parallel view (synthetic/obfuscated), field inventory, risk flags, captures, exports" -- frontend/src/pages/ScanDetail.tsx frontend/src/components/scanner/ParallelView.tsx frontend/src/components/scanner/FieldInventory.tsx frontend/src/components/scanner/RiskFlags.tsx frontend/src/components/scanner/CapturesTab.tsx frontend/src/App.tsx
```

---

### Task 24: Smoke test extension and the compose-level scanner gate

**Files:**
- Modify: `tools/smoke_test.py`

Add (complete):

```python
SCANNER = os.environ.get("DPS_SMOKE_SCANNER") == "1"
DEMO_FROM_WORKER = os.environ.get("DPS_SMOKE_DEMO_URL", "http://demo-site:8410")      # as the browser-worker sees it
DEMO_FROM_HERE = os.environ.get("DPS_TEST_DEMO_SITE", "http://127.0.0.1:8410")        # as this script sees it
DEMO_VALUES = ("Ananya Krishnan", "2341 2341 2346", "BQPKR4432L", "4111 1111 1111 1111", "ananya.k@example-mail.in")
TERMINAL = ("succeeded", "failed", "dead", "canceled")


def scanner_checks(c: httpx.Client) -> None:
    browser = c.get("/readyz").json()["components"].get("browser", {})
    check("browser worker online", bool(browser.get("ok")), str(browser.get("detail")))
    before = httpx.get(f"{DEMO_FROM_HERE}/__received", timeout=5).json()
    created = c.post("/api/v1/scans", headers=H, json={
        "url": f"{DEMO_FROM_WORKER}/apply", "scope": {"max_pages": 25, "max_depth": 3, "deny": ["/logout", "/admin"]},
        "auth": {"storage_state_ref": "dps/ui/demo-onboarding"}, "mode": "synthesize",
        "fill": {"personas": 5, "locale": "en_IN"}})
    body = created.json() if created.status_code == 202 else {}
    check("scan accepted, key and seed returned once", created.status_code == 202
          and bool((body.get("key_material") or {}).get("dek")) and bool(body.get("synthesis_seed")))
    sid = body["scan"]["id"]
    detail: dict = {}
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        detail = c.get(f"/api/v1/scans/{sid}", headers=H).json()
        if detail["scan"]["state"] in TERMINAL:
            break
        time.sleep(3)
    s = detail.get("summary", {})
    check("scan succeeded", detail.get("scan", {}).get("state") == "succeeded",
          f"{s.get('pages_visited')} pages, {s.get('fields')} fields, {s.get('personal_fields')} personal")
    flags = {f["flag"] for f in s.get("risk_flags", [])}
    check("classification and risk flags", s.get("personal_fields", 0) >= 20 and flags >= {
        "autocomplete_enabled_sensitive", "pii_in_query_string", "pii_rendered_unmasked"})
    after = httpx.get(f"{DEMO_FROM_HERE}/__received", timeout=5).json()
    check("no submission reached the site", after["non_get"] == before["non_get"]
          and after["form_get"] == before["form_get"] and s.get("non_get_allowed") == 0,
          f"blocked {s.get('blocked_requests')}")
    pages = c.get(f"/api/v1/scans/{sid}/pages", headers=H).json()["items"]
    ident = next((p for p in pages if p["url_redacted"].endswith("/apply/identity")), {})
    check("parallel view captured", {"original", "annotated", "filled_synthesize", "filled_obfuscate"}
          <= set(ident.get("artefacts", {})))
    png = c.get(f"/api/v1/artefacts/{ident['artefacts']['filled_synthesize']}", headers=H)
    check("screenshot served", png.status_code == 200 and png.content[:4] == b"\x89PNG")
    export = c.get(f"/api/v1/scans/{sid}/export?format=json", headers=H)
    check("persona export", export.status_code == 200 and export.json().get("personas") == 5
          and not any(v.lower() in export.text.lower() for v in DEMO_VALUES))
    evidence = c.get(f"/api/v1/evidence?job_id={sid}", headers=H).json().get("items", [])
    pack = c.get(f"/api/v1/evidence/{evidence[0]['id']}", headers=H).json()["pack"] if evidence else {}
    check("evidence: submissions 0", (pack.get("scan") or {}).get("submissions") == 0
          and (pack.get("scan") or {}).get("non_get_allowed") == 0)
    check("scanner console served", c.get("/scanner/new").status_code == 200)
```

and in `main()` before the final "console served" check: `if SCANNER: scanner_checks(c)`. (`import time` at the top.) The script prints only check names and counts — never a body.

- [ ] **Step 1: Write the changes.**
- [ ] **Step 2: Run against the stack** — `docker compose --profile scanner up -d --build`; poll health as in Task 20; `DPS_SMOKE_SCANNER=1 python tools/smoke_test.py` → `all N checks passed` (M0's checks plus the ten scanner checks). Without `DPS_SMOKE_SCANNER` the script behaves exactly as before.
- [ ] **Step 3: Commit**

```bash
git add tools/smoke_test.py
git commit -m "test(smoke): scanner checks — scan the demo site end to end, prove zero submissions, captures, export, evidence" -- tools/smoke_test.py
```

---

### Task 25: Documentation as built

**Files:**
- Modify: `HANDOFF.md`, `README.md`
- Create: `docs/technical/api-m4.md`

- `docs/technical/api-m4.md`: every route of Task 18 with request/response examples (synthetic values only), the error codes table (`url_invalid`, `host_not_registered`, `scope_invalid`, `start_url_denied`, `inline_auth_forbidden`, `har_not_supported`, `fill_invalid`, `policy_invalid`, `consistency_invalid`, `db_unconfigured`, `kek_unconfigured`, `scan_not_found`, `export_not_ready`, `not_cancelable`), the job lifecycle of a scan (queued → running → succeeded/failed; cancel → finalize → canceled), the export format `dps.scan-export.v1`, the capture map format `dps.capture.v1` and how to reveal it, the evidence `scan` block, and the D4 statement with the guard's rules.
- `README.md`: "Scan a web front end" — `docker compose --profile scanner up -d --build`, open `/scanner/new`, scan `http://demo-site:8410/apply` with session `dps/ui/demo-onboarding`; host-side tests need `python -m playwright install chromium`.
- `HANDOFF.md`: the M4 config table rows (all `DPS_BROWSER_*`/`DPS_SCAN_*` settings with defaults), the browser-worker image, ports (8410 demo, 9401 internal), secrets by name only (`DPS_JOB_KEK`, `dps/ui/<name>/storage_state.json` refs), the posture rules, and the traps listed in Task 20.

- [ ] **Step 1: Write the three documents.**
- [ ] **Step 2: Commit**

```bash
git add HANDOFF.md README.md docs/technical/api-m4.md
git commit -m "docs: M4 UI scanner — API as built, run instructions, handoff" -- HANDOFF.md README.md docs/technical/api-m4.md
```

---

## Definition of Done (M4)

All of the following, run from a clean checkout of the M4 branch on the Mac, with the outputs recorded in the milestone report:

1. **Unit gates** — `ruff check dps tests tools && ruff format --check dps tests tools && mypy dps && pytest -q` → green (browser/DB tests skip without services).
2. **Console build** — `cd frontend && npm run typecheck && npm run build` → no errors.
3. **Stack** — `docker compose --profile scanner up -d --build`, then poll (never `--wait`): `app`, `dps-db`, `worker`, `browser-worker`, `demo-site` healthy and `migrate` exited 0; `curl -fsS http://127.0.0.1:8400/readyz` lists `browser: ok (1 online)`; `docker compose exec browser-worker python -c "import importlib.metadata as m; print(m.version('playwright'))"` prints `1.55.0`.
4. **Integration gate (contract §12.4 M4)** — on the host with `python -m playwright install chromium` done, the demo site on 8410 and the metadata DB on 5441: `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "browser or db" tests/scanner tests/test_no_values_in_logs_m4.py` → all pass, including **`tests/scanner/test_no_submit.py` (the M4 exit criterion: the demo site's counters stay unchanged through full scans with and without JS hardening and through adversarial submission attempts, while the interception log shows the blocked POSTs)**, the scope/budget/deny tests in `test_crawler.py`, and the end-to-end handler test.
5. **Smoke** — `DPS_SMOKE_SCANNER=1 python tools/smoke_test.py` → all checks pass.
6. **Browser check of the console against the mockups** — Tasks 22 and 23 step 3 done for `/scanner/new` vs `12-scanner-setup.html` and `/scanner/scans/:id` (parallel, inventory and captures tabs) vs `04-ui-scanner.html`, dark and light, 1440 px and 390 px; deviations listed in the report (they are the ones under "Open contract questions" 8–10 and 14–15, nothing else).
7. **Demo outcome (spec §11 M4)** — scan the demo onboarding app, open the parallel view, flip synthetic/obfuscated, export personas and inventory, open the evidence pack: all from the console.
8. **Docs** — HANDOFF, README, `docs/technical/api-m4.md` and the contract additions committed.

## Self-review against the spec

| Spec | Requirement | Where |
|---|---|---|
| §4 processes | `browser-worker` role, Playwright Chromium baked in, durable queue | Tasks 17, 20; M2 `Consumer` |
| §8 request | `url`, `scope{same_origin,max_pages,max_depth,deny}`, `auth.storage_state_ref`, `mode`, `fill{personas,locale}`, `parallel_view`, `capture{screenshots,har}` | Task 4 (`ScanSpec`, plus allow-list and the mockup's switches) |
| §8 crawl | same-origin, depth and page budgets, SPA routes by clicking in-scope links, open shadow DOM, same-origin iframes | Tasks 2, 8, 14 (template budget and time budget added) |
| §8 per-page facts | tag, type, name, id, label via `label[for]` / `aria-label` / `aria-labelledby` / placeholder / nearest text, autocomplete, pattern, maxlength, required, select options; screenshot with numbered overlays; rendered text | Tasks 8, 12 (options counted, never stored; text kept in memory only) |
| §8 classify | autocomplete token map → structural dictionary (EN/ES via M0's T0) → input type and pattern → value detectors on pre-filled/rendered values | Task 9; T3 LLM labelling of leftover fields: the engine module is M1's (`dps/engine/detect/llm_label.py`, M1 plan Task 19D) and M5 wires it into `classify.py` (contract §15), so M4 keeps no M1 dependency |
| §8 inventory + risk flags | autocomplete on sensitive fields, passwords without `new-password`, PII in URL query strings, PII rendered (in tables) | Tasks 10, 16 (plus sensitive GET forms) |
| §8 parallel view (D4) | fill every field with persona values (synthesize) or reversible tokens (obfuscate), capture original and filled screens, record client-side validation messages, never submit; guard blocks non-GET and navigations of filled pages; export persona set and field→entity→value mapping as CSV/JSON | Tasks 6, 11, 13, 14, 15, 16 |
| §8 obfuscate | DOM text replaced, masked screenshot, reversible map | Task 12 (fail closed when a value cannot be masked) |
| §8 safety rails | no submission path in code; allow/deny; page and time budgets; credentials and storage state only in worker memory; every navigation audited; no crawling outside registered hosts | Tasks 4, 6, 14, 15, 16, 18 (host check at accept time, at run time, and per request) |
| §9 UI scanner page | URL + auth → crawl map → annotated screenshots → inventory → parallel view (toggle) → obfuscated captures → export | Tasks 22, 23 |
| §10 | scanner has no submission path; values never in logs; posture | Tasks 1, 15, 19 |
| §11 M4 exit | safety-rail tests: scope, interception proves no POST ever leaves | Tasks 14, 15 |
| Contract §12.3 | `tests/scanner/test_no_submit.py` with the demo-site counter | Task 15 |
| Contract §13 | screenshots PNG, masks drawn before encoding, unmasked originals only with the local flag | Tasks 12, 14 |

Placeholders: none — every task names exact files, interfaces, tests with their assertions, commands and commit messages. Names used across tasks were checked for consistency (`ScanRuntime`, `ScanSink`, `PersonaFill`, `FieldPlan`, `RenderedFinding`, `ScanStore`, `DbScanSink`, `scan_job`/`scan_finalize_job`). Where the plan depends on an M2 name the contract leaves open, the dependency is stated in the task and repeated below.

## Open contract questions

The plan follows the contract where it speaks; these are the places where it is silent, ambiguous, or (in one case) at odds with the task brief.

1. **Demo-site path.** The task brief asks for `tools/demo_sites/onboarding`; the contract (§1, §11) fixes `demo/onboarding/` with `server.mjs`. The plan uses the contract path.
2. **`create_job` needs a known id.** Resolved (contract §3.2, 2026-10-11): `create_job(..., job_id=None)` is M2's; the plan calls `create_job(job_id=new_id("s"), …)`.
3. **Consumer role.** Resolved (contract §3.5, 2026-10-11): M2's `Consumer` requires `ctx` and takes `role`; Task 17 passes `ctx=build_context(settings)` and `role="browser-worker"` (its DB test is the guard).
4. **Progress writes.** The plan writes in-flight scan progress with `advance_job(phase=scan, state=running, progress=…)` — allowed by the signature, not stated by the contract.
5. **Audit fields.** §2.4 has `action` and `job_id` audit columns, but §8.3 does not spell out the `AuditEvent` fields M2 adds. The plan sets `action="scan.navigate" | "scan.finalize" | "job.create"` and `job_id`.
6. **Names the contract leaves open.** Resolved (contract §7.1, §13, §16, 2026-10-11): the renderer is M2's `render_html(pack, *, sha)` with `pack_sha256(pack)`; `ArtefactService(store, *, acquire)`; `JobSecret`/`JobProgress` live in `dps.jobs_models`; `EvidencePack.label` is in §7.1.
7. **Additions this plan makes to the contract** (each in the commit with its code): `dps_scan_event`, extra columns, CHECKs, and the `dps_scan_request` foreign key in 030; `JobSecret.scan_start_url`; `ScanSpec.policy`, `scope.iframes|shadow_dom|spa_clicks`, `fill.display_persona`; `GET /scans/{id}/events` and `POST /scans/personas/preview`; nine `scan_*` settings; posture refusing a headed browser.
8. **Parallel view against mockup 04.** The mockup's obfuscated pane shows tokens of the values *as captured*. The contract forbids storing tokens of page data in `dps_scan_field.fill_obfuscate` (§2.4) and storing unmasked originals of pages that render personal data (§13). So both filled screens come from persona-derived values (the persona itself, or tokens of it); pre-filled page values are masked on the stored original (chip "personal data masked"); and reversible obfuscation of real page data lives in the Captures tab (masked capture plus reversible map).
9. **Persona count.** Mockup 12 lets you enter up to 200 personas; the contract caps `fill.personas` at 50. The plan uses 50.
10. **HAR.** The contract keeps `capture.har: false` in the request shape. A HAR stores response bodies, which carry values, so storing one would break §0.1. The plan refuses `har: true` with 422 `har_not_supported`, and the console renders the mockup's switch disabled with that reason.
11. **Browser image Python.** The contract says "Python 3.12 via uv". The Playwright base already ships Python 3.12.3, but without `ensurepip`, so the plan uses uv to build the venv on that interpreter instead of downloading another CPython. Like M0's Dockerfile, it copies `ghcr.io/astral-sh/uv:latest`; pinning one uv version in both Dockerfiles is recommended.
12. **Host Chromium build.** The environment notes say Chromium is cached on the host, but that cache holds builds 1148, 1234 and 1243 — none of them the build 1187 that `playwright==1.55.0` drives (the image has 1187). Host browser tests therefore need a one-time `python -m playwright install chromium`.
13. **Depth bounds.** §9 says `max_depth ≤ 5` and §10 sets `scan_max_depth = 3`. The plan reads this as default 3, hard maximum 5.
14. **"Check session" (mockup 12)** has no route. The console implements it as a one-page probe scan through `POST /scans`, so it appears as a scan job in lists.
15. **"Forms submitted, in any scan: 0" (mockup 12)** has no route. The console shows a constant backed by D4 (there is no code path to submit), with a tooltip; the measured per-scan figure is `summary.non_get_allowed` and the evidence pack's `scan.non_get_allowed`.
16. **Live counters.** §3.3's `JobProgress` lacks the live crawl figures (discovered, queued, classified, filled). The plan derives them server-side in `GET /scans/{id}` → `summary` and leaves `JobProgress` as the contract defines it.
17. **Third-party host names.** `dps_scan_request.host` records the names of blocked third-party hosts (for example, trackers). They are not customer data, but the bank's data classification should confirm this.
18. **Registered hosts before M5.** Editing registered hosts is M5's Admin page. Until then, local/dev uses `DPS_SCAN_REGISTERED_HOSTS`, and staging/prod need a DBA insert into `dps_tenant.registered_hosts`.
19. **Cancel → finalize for scans.** §3.1's cancel rule (queue a finalize with outcome `canceled`) is written for run jobs. The plan relies on it for scans (Task 16 tests it); if M2 limited it to run jobs, extend `jobs.cancel` (M2 touch point).

## Execution

Run the tasks in order. After Tasks 1, 2 and 4, two lanes can proceed in parallel: the database lane (3 → 5) and the browser lane (6 → 15); they merge at Task 16, then 17 → 20 run in order; the console tasks (21 → 23) need only Task 18's API shapes and can start once Task 4 fixes them. Every task ends with its gates green and one commit made with an explicit pathspec and no trailer.
