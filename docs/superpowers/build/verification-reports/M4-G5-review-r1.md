# M4 G5 review, round 1 (Tasks 19 and 20), branch `m4` @ `87a80af`

Reviewer: independent review agent. Working copy `/Users/neelu/document_intelligence/dps-wt-m4`.
I did not trust `M4-G5-impl.md`; every claim below comes from code I read or a command I ran on 2026-10-13/14.
No repository file was modified. Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m4-G5/`.

**Verdict: PASS. No blocking findings.**

## 1. Gates (run by me through `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 292 files already formatted (I ran `--check`, not the formatting command, so no file changed) |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 119 source files |
| `pytest -q` (full suite, with the lane's scanner stack up) | **2114 passed, 87 skipped**, 1 warning (Starlette httpx deprecation), 443 s |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/test_no_values_in_logs_m4.py tests/test_compose.py tests/scanner/test_demo_site.py tests/scanner/test_no_submit.py` against the lane's containers | **45 passed** in 134 s |
| `pytest -q tests/test_no_values_in_logs_m4.py` | 21 passed |
| Frontend | Not touched by G5, so the frontend gates were not needed |

The 87 skips are tests for services that were not running (for example Oracle and Mongo). That is allowed because G5 does not touch them. Everything G5 touches was run with `DPS_TEST_REQUIRE_SERVICES=1`.

## 2. Live checks: Task 20 steps 1 and 2, repeated independently

- `docker compose --profile scanner up -d --build` (project `dps-wt-m4`, tag `m4`, app 8414, demo-site 8420) exited 0. Health afterwards:
  - app: healthy
  - browser-worker: healthy (port 9401, no host port)
  - demo-site: healthy (`127.0.0.1:8420->8410`)
  - dps-db and worker: healthy
  - migrate: exited
- The image runs the current code. The sha256 of `/app/dps/layers/scanner/crawler.py` inside the container equals the sha256 of the file in the worktree.
- Inside the container:
  - Playwright version is `1.55.0`.
  - The process runs as uid 10001.
  - `/ms-playwright` holds `chromium-1187` and `chromium_headless_shell-1187`.
  - The root filesystem is read-only (`touch /app/x` fails).
  - `/tmp` is a 512 MiB tmpfs.
  - All capability sets are 0 (`cap_drop ALL`); `no-new-privileges` and `init` are set.
  - shm is 1 GiB and the memory limit is 2 GiB.
  - `/app` holds only `README.md dps pyproject.toml var`. The only `storage_state.json` on the filesystem is the bind-mounted one, so `docker/secrets` is not in the image.
- App `/readyz` reports `"browser":{"ok":true,"detail":"1 online"}`. The browser-worker's `:9401/readyz` reports `chromium 140.0.7339.16`.
- `/metrics` on 9401: the `dps_scan_*` series carry only decision, method, reason, outcome and entity type. No values.
- **Scan through the stack** (the plan's exact request, with storage state ref `dps/ui/demo-onboarding`):
  - Response 202 with key material and seed.
  - Reached `succeeded` within about 100 s: 21 pages done, 4 skipped (`/logout`, `/admin`, the foreign host, and one FAQ page over budget), 63 fields, 73 requests blocked.
  - `/profile` ("My profile") was scanned, so the mounted storage state was used.
  - The demo site's `/__received` was `non_get 0, form_get 0, logout_hits 0, admin_hits 0` both before and after the scan, on a fresh container.
- **Log scan of the stack** (`probe-m4-G5/logscan.py`). It scanned 76 compose log lines, 45 of them from the browser-worker. It looked for 62 strings: the demo values (except dob), each part of the applicant's name, the session cookie name and value, the scan's DEK and seed, and the scan's 46 persona values and tokens. The search was case-insensitive and also matched with separators removed. **0 hits.**
- **Database scan** (`probe-m4-G5/dbscan.py`). I dumped `dps.dps_audit_log` (47 rows), `dps_scan_page` (50), `dps_scan_field` (126), `dps_scan_event` (253), `dps_scan_request` (168), `dps_job` (3) and `dps_evidence` (2), and searched them for the same demo values, session, DEK and seed. **0 hits.** The scan audit rows hold only `page_no`, `duration_ms` and zero counts.
- **Tenant isolation of the storage state.** I created the same scan as tenant `acme` (`X-DPS-Tenant: acme`) with ref `dps/ui/demo-onboarding`. The API accepted it (202). The worker then failed it closed with `secret_unavailable`: it neither read tenant `default`'s session nor crawled without one.
- After the test runs the demo counter read `non_get 16, form_get 4, logout 2, admin 2`. All of it comes from the tests' own positive controls in `test_demo_site.py` and `test_no_submit.py`, which call `httpx.post` and `/logout`/`/admin` directly, or use a plain unguarded context for the draft flush. Those tests assert before/after deltas. The scan itself added nothing.
- `docker compose --profile full config -q` succeeds. With `--env-file .env --env-file docker/compose.sources.env --profile scanner --profile sources`, the browser-worker inherits `DPS_ARTEFACT_STORE=s3`, the MinIO endpoint and the S3 keys. `boto3` imports in the browser image (from the `connectors` extra).
- I ran `docker compose --profile scanner down` at the end. No other project's containers were touched.

## 3. Task 19: is the log scan a real test?

I read `tests/test_no_values_in_logs_m4.py` in full.

**What it checks:**
- **Static check** over every `logger.<level>` call in the 14 scanner modules, `browser_worker.py` and `routers/scans.py`: the event is a snake_case literal, there are no positional arguments, no `**` splat, only allow-listed keywords, no `print` and no stdlib `logging`.
- **I confirmed the M4 log calls are value-free.** They only log `job_id`, `error_code`, `outcome`, `pages`, `page_no`, `status`, `fields`, `blocked`, `component` and `version`. No `reason=`/`error=` free text, and no `str(exc)` anywhere in the M4 modules.
- **The worker's last-resort handler** (`dps/worker.py:212`) logs only `type(exc).__name__`.

**Positive controls, so the scan cannot pass vacuously:**
- The crawl test requires at least 3 pages done, at least 5 persona values or tokens, and at least one allow-listed `scan_page` line per page.
- The API test requires the three audit lines it expects and at least 3 allow-listed JSON lines.
- The self-test proves a value planted under `reason`/`error` is caught.
- `configure_logging` uses `cache_logger_on_first_use=False`, so `capsys` really receives structlog's output.

**My own mutation runs** (plugin `probe-m4-G5/plug/leakprobe.py`, loaded with `-p`; no repository change):
- Making the crawl log one persona value through `reason`, upper-cased and with `-` for spaces, gives `2 values reached the logs at positions [27, 28]`. The test fails, as it should.
- Logging one token through `error` gives `1 values ... [28]`. The test fails, as it should.

**Deviations:** all justified, and all of them make the test stronger:
- 21 tests instead of 3.
- A query on the start URL, so the "no `?email=`" check is no longer vacuous.
- A real `LogAuditSink`, because the plan's `MemoryAuditSink` made the API test vacuous.
- Per-piece normalisation.
- dob excluded, because it is timestamp-shaped.

## 4. Task 20: image and compose against the plan, contract and spec

- `Dockerfile.browser` matches the plan and contract §11.1: the Playwright v1.55.0-noble base, no `playwright install`, `.[fpe,db,connectors,scanner]` (plus `ner` only under `WITH_NER=1`), an assertion on the installed Playwright version, a non-root user, health on 9401, and `CMD python -m dps.browser_worker`.
- The compose services match the plan and the contract §11 table: `extends: worker`, profiles `scanner`/`full`, no host port for the browser-worker, demo-site on `${DPS_DEMO_SITE_PORT:-8410}`, health checks, memory limits and labels.
- The contract §0 lane-safe rules are honoured: the image tag is `${DPS_IMAGE_TAG:-local}` and the port comes from its variable.

Judgement of each Task 20 deviation-log entry:
1. **uv pinned to 0.11.21 instead of `:latest`.** Correct; this is the app image's rule.
2. **uid 10001 instead of `pwuser`.** Correct and necessary, because the shared volumes are owned by 10001. It matches the app Dockerfile, and I verified it live: artefacts were written and the scan succeeded.
3. **Lane-safe image tag, demo port and `DPS_FPE_ENABLED` variable.** Required by contract §0. The FPE setting equals the app's and the worker's default.
4. **Demo storage state under `docker/secrets/default/...`.** Correct, because SEC-2 resolves references tenant-scoped. The other-tenant probe confirms the tenant scoping holds.
5. **`.dockerignore` excludes `docker/secrets`.** Correct (verified in the image).
6. **HANDOFF §12.** Accurate against what I measured (pages, blocked count, uid, versions, size 4.22 GB).
7. **Offline compose tests.** They pin the important properties.

## 5. Defect hunt: nothing blocking

| Hunted for | Found |
|---|---|
| Values in logs, metrics, audit rows or scan rows | None (log scan, DB scan and metrics review above) |
| Key material in logs or the image | None. The DEK and seed are absent from logs and the DB; no secret is in any image layer |
| Tenant isolation | Cross-tenant storage-state reference is refused (`secret_unavailable`, fails closed) |
| Submissions | Counter stayed at 0 for the scan |
| Contract names broken for other lanes | None (service names, ports and variables follow §0 and §11) |
| Half-applied or double-applied writes | Not in G5's scope (only tests and packaging changed); finalize fencing is G4 |

## 6. Non-blocking observations and nits

1. **Chromium runs with `--no-sandbox`.** This is Playwright's default for non-root containers; I confirmed the flag on the running process. The guard and scope proxy do the D4 work, but Chromium's own sandbox is a defence-in-depth layer that is missing when crawling pages that may be hostile. Suggestion for M5 production images: a seccomp profile with `chromiumSandbox=True`, or document the accepted risk. Not required by the plan, contract or spec.
2. **The crawl log test only captures `sys.stdout`/`sys.stderr`** (`capsys`). Output the Playwright node driver writes straight to fd 2 is not scanned by the unit test; `capfd` would cover it. My live compose log scan, which does include the driver's stderr, found nothing.
3. **The self-test is fragile.** Its short needle `"ab"` is asserted absent from the whole raw capture; a future logger name or field containing "ab" would break the self-test, not the product.
4. **Image size is 4.22 GB.** The base ships Firefox and WebKit too. Worth slimming for OpenShift (M5).
5. **The browser-worker inherits unused worker settings.** Through `extends` it picks up `DPS_WORKER_CONCURRENCY=4` and `DPS_WORKER_METRICS_PORT=9400`. They are unused but confusing in `docker inspect`.
6. **The whole `./docker/secrets` tree is mounted into the browser-worker,** including every tenant folder. The resolver is tenant-scoped (verified), so this is acceptable for local use only.
7. **Locally the browser-worker logs use the console renderer** (`DPS_ENV=local`), the same as app and worker; staging and prod log JSON. Not a defect.

## 7. Open issues

None blocking. Items 1 and 4 belong to the M5 hardening backlog.
