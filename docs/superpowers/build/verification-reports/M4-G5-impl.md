# M4 G5 — implementation report (Tasks 19, 20)

Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Lane: COMPOSE_PROJECT_NAME
`dps-wt-m4`, DPS_IMAGE_TAG `m4`, app 8414, dps-db 5463, demo-site 8420. Base `0cf0140`.

## Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers)

| Commit | Task | Paths |
|---|---|---|
| `21bd69c` test(scanner): scanner, guard and scan API logs carry no page values, keys or sessions | 19 | tests/test_no_values_in_logs_m4.py, docs/superpowers/build/M4-DEVIATIONS.md |
| `87a80af` feat(scanner): browser-worker image on the Playwright 1.55.0 base and compose scanner profile with the demo site | 20 | Dockerfile.browser, docker-compose.yml, .dockerignore, HANDOFF.md, tests/test_compose.py, docker/secrets/default/dps/ui/demo-onboarding/storage_state.json, docs/superpowers/build/M4-DEVIATIONS.md |

Working tree clean after the second commit. Nothing pushed, no tags.

## Task 19 — M4 log scan (`tests/test_no_values_in_logs_m4.py`, 21 tests; plan: 3)

- Static (offline, as M2's scan): every log call in all 16 M4 modules (14 files of
  `dps/layers/scanner/`, `dps/browser_worker.py`, `dps/layers/api/routers/scans.py`) uses a
  snake_case literal event, no positional args, no `**` splat, allow-listed fields only; no
  `print`, no stdlib logging. All 16 clean.
- Self-test: a value logged via an allow-listed field is caught verbatim and with separators
  changed; matching normalises per JSON string / per line (never across the whole log).
- `test_scanner_loggers_drop_value_fields` (unit): plan's check.
- `test_guard_and_crawl_logs_are_value_free` (browser): in-memory crawl of `/apply?email=…`
  (query added so the "no ?email=" check is not vacuous), max_pages 6; needles = demo values, name
  parts, query (raw + encoded), session cookie name/value, DEK, seed, every persona value and token
  produced; asserts real work (>=3 pages done, >=5 persona values/tokens, one scan_page line per page)
  and the demo site received no non-GET / GET-form.
- `test_api_logs_are_value_free` (db): plan's `api_client` audits to a MemoryAuditSink, so the API
  logged nothing (vacuous). The test builds its own app with M0's LogAuditSink inside the capture
  window, drives a refused create (host_not_registered with query + inline state), a create with
  inline storage state, GET pages/events/summary/inventory and a cancel; asserts the 3 audit lines
  were logged + allow-listed and that none of ananya / query / refused host / demo_session /
  demo-valid / DEK / seed appear.
- Mutation checks (reverted, not committed): crawler logging a persona value as `reason` -> crawl
  test fails (2 found); scans router logging the inline storage state -> API test fails (2 found).

## Task 20 — browser-worker image + compose

- `Dockerfile.browser`: Playwright v1.55.0-noble base (Chromium 1187, no `playwright install`),
  uv pinned 0.11.21 (plan: :latest), `/opt/venv` with `.[fpe,db,connectors,scanner]` (+ner with
  WITH_NER=1), version assert 1.55.0, **USER 10001** (plan: pwuser — would fail to write the shared
  artefact/export volumes owned by 10001), health on 9401.
- compose: `browser-worker` (extends worker; lane-safe image tag; DPS_FPE_ENABLED follows the app's
  variable; ./docker/secrets mounted ro as DPS_SECRET_DIR) and `demo-site` (lane-safe port
  `${DPS_DEMO_SITE_PORT:-8410}`), profiles scanner/full.
- Demo storage state at `docker/secrets/default/dps/ui/demo-onboarding/storage_state.json`
  (tenant-scoped SEC-2 layout; plan path lacked the tenant folder). Local demo literal cookie.
- `.dockerignore` += `docker/secrets` (`demo` already excluded).
- HANDOFF §12 (image, compose, commands, demo session, verification, 7 traps); §2 and lane list
  updated.
- `tests/test_compose.py` +4 offline tests (written first, failed before the files existed).

## Gates (exact)

- Before commit 1: `ruff format dps tests tools` (292 unchanged), `ruff check` all passed,
  `mypy dps` no issues (119 files), `pytest -q` **2110 passed, 87 skipped** (dps-db up);
  `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/test_no_values_in_logs_m4.py` **21 passed**.
- Before commit 2: ruff format/check clean, mypy clean, `pytest -q` **2114 passed, 87 skipped**
  (dps-db + compose demo-site up); `tests/test_compose.py` 12 passed.
- Skips: the 87 are service markers whose containers this group does not run (pg_source, s3, ocr,
  oracle, mongo, tesseract binary, ner extra) — not M4 code paths.
- No frontend changes (no npm gates needed).

## Live verification (Task 20 steps 1-2)

- `docker compose --profile scanner up -d --build`: app, worker, browser-worker, demo-site, dps-db
  healthy; migrate exited 0. Rebuilt again after the .dockerignore change: all healthy, storage
  state visible in the container.
- `docker compose exec browser-worker … m.version('playwright')` -> `1.55.0`; `id` -> uid 10001.
- App `/readyz` -> `browser: {"ok": true, "detail": "1 online"}`; worker `:9401/readyz` -> ready,
  chromium 140.0.7339.16.
- Plan's scan request (demo-site, storage_state_ref dps/ui/demo-onboarding, 5 personas, max 25) ->
  202 with key + seed -> `succeeded` in 68 s: 21 done / 4 skipped pages, 63 fields, 9 risk flags,
  blocked POST 73 + GET 1, non_get_allowed 0, evidence + CSV/JSON exports; logged-in `/profile`
  scanned (storage state read).
- Demo `/__received` before and after: `non_get 0, form_get 0, logout_hits 0, admin_hits 0`.
- Screenshot stored by browser-worker served by the app: `GET /api/v1/artefacts/{id}` 200 image/png.
- Compose log scan (168 lines): 0 hits for demo values, name parts, session cookie, scan DEK, seed.
- `DPS_TEST_REQUIRE_SERVICES=1 pytest -q tests/test_no_values_in_logs_m4.py
  tests/scanner/test_demo_site.py tests/scanner/test_no_submit.py tests/test_compose.py` against the
  compose demo-site: **45 passed**.
- `docker compose --profile scanner down` done (volumes kept).

## Image sizes (arm64)

- data-privacy-services-browser:m4 4.22 GB (base 3.53 GB); data-privacy-services:m4 926 MB.

## Deviations

All recorded in `docs/superpowers/build/M4-DEVIATIONS.md` (Task 19, Task 20 sections). No contract
name changed.

## Open issues / notes

- The browser image is large (4.22 GB) because the Playwright base ships Firefox and WebKit too;
  a slimmer image (e.g. installing only chromium-headless-shell 1187 onto a python base) is a
  possible later optimisation, not done (contract fixes the base).
- `.env`/`.env.lane` files are blocked from reading in this session; ports were read via the lane
  wrapper's environment.
- The demo storage state is committed as a local literal (by plan design); flag for WP-F security
  review if policy says no cookie literals in git.
