# M4 — merge of the console sub-lane (m4ui) into m4, wired to the API as built

Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`. Author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers.

## Commits

| Commit | What |
|---|---|
| `e2c0ba4` | `merge: M4 console` — `git merge --no-ff m4ui` (5 m4ui commits over merge base `986ff89`; **no conflicts**; 39 files, frontend + `docs/superpowers/build/M4-DEVIATIONS-console.md`) |
| `9aef742` | `fix(m4): wire the console to the API as built` — 17 files (frontend + the console deviation log) |

Resume check at start: worktree clean, `m4ui` not yet merged, no earlier report. Nothing redone.

## What was checked against the real API

I compared the console's types and client (`frontend/src/lib/{types,api,scans}.ts`) with `dps/layers/api/routers/scans.py`,
`dps/jobs_models.py::ScanSpec` and `dps/layers/scanner/models.py`. Routes, verbs, query params
(`after`/`limit` cursors, `format`), the `Idempotency-Key` header on create, the `scan` scope, the artefact route
(`jobs`|`scan` scope plus the scope of the job's kind) and every request/response model field matched one for one.
`ScanPage`, `ScanField`, `ScanEvent`, `ScanSummary`, `NumberedRiskFlag`, `InventoryRow`, `PersonaPreview`,
`KeyMaterial` and `SynthesisSeed` all matched.

Then I walked the console against this lane's compose stack (`--profile scanner`: app 127.0.0.1:8414, worker,
browser-worker, demo-site 127.0.0.1:8420, dps-db) in a private Playwright (`/Users/neelu/.cache/dps-build/pw-m4`,
playwright 1.55.0). The scripts are in `/Users/neelu/.cache/dps-build/pw-scripts/m4_real_walk.py` and
`m4_revealmap.py`. Screenshots are in `/Users/neelu/.cache/dps-build/shots-m4merge/`, with the mockups rendered at
the same sizes for comparison (`mock-04-ui-scanner-*`, `mock-12-scanner-setup-*`). The API key was read from the
app container's environment into a process variable; it was never printed.

- **New scan (`/scanner`)**, 1440/390 × dark/light: persona preview loads (5 tokens), no horizontal scroll, no console errors, no API refusals.
- **Check session**: valid with `dps/ui/demo-onboarding` ("✓ Session valid · /apply returned 200 without a redirect"). With no session on `/profile` it reads "✗ Redirected to /login?next=••• — the stored session is not valid".
- **Refusals**: an unregistered host gives `host_not_registered`, shown under the Start URL. `start_url_denied` was a mismatch, fixed below.
- **Full crawls through the console**, synthesize and obfuscate (twice each, before and after the fix):
  - 21 pages, 14 forms, 63 fields, 51 personal, 9 risk flags.
  - Inventory tab: 63 rows. Captures tab: 3 masked captures.
  - Export inventory (CSV) and Export personas (JSON) download. The evidence pack link opens.
  - Live crawl map and navigation log work; Download log as JSONL gives 9 value-free keys per line.
- **Obfuscate with an RSA wrap key**: I generated a throwaway 2048-bit test key in the scratchpad and deleted the private half afterwards.
  - The key card shows "key wrapped with your RSA public key".
  - Stop crawl gives "Crawl stopped" and the scan detail reads "Crawl stopped · 2 pages crawled …".
  - Download reversible map (`capture_json`) on an obfuscate scan returns `{data, format, kid, methods_used, mode, page_no, reveal_hints}`. A synthesize scan correctly has no reveal map.
- **Scan detail**, all three tabs at 1440/390 × dark/light: no horizontal scroll, no errors. The layout matches mockup 04, apart from the deviations m4ui already documented.
- **Evidence pack, Reports, Jobs and Overview** for scans: mismatches found, fixed below.

## Mismatches fixed (test-first; listed in `M4-DEVIATIONS-console.md` under "Merge into m4")

1. **Refusal wording.** The API's `start_url_denied`, `fill_invalid`, `policy_invalid`, `key_material_invalid`, `wrap_key_invalid` and `kek_unconfigured` were shown as raw codes ("422 start_url_denied: the scan was refused: start_url_denied").
   - They now all have words (`CREATE_ERRORS`).
   - Start-URL refusals (`url_invalid`, `host_not_registered`, `start_url_denied`) show under the Start URL field and clear when it is edited. A live check confirmed this.
   - `scope_invalid` now names the server cap: `DPS_SCAN_MAX_PAGES`, 50 by default, where the console's ceiling is 500.
2. **Paths.** `NumberedRiskFlag.path` and `InventoryRow.path` are the page's redacted **URL** as built (`url_redacted`), not a path.
   - Risk flags and the whole-scan inventory printed `http://demo-site:8410/apply/review?email=<EMAIL>&ref=<v>`. They now print `/apply/review?email=•••&ref=•••` via `pathOf`.
   - The typed fixtures now carry the API's shape.
3. **Evidence pack page for a scan.** It rendered as a table run: "— — → —", "read 0 rows from —", Row counts "review", "verified · 0 leaks".
   - New `components/scanner/ScanEvidence.tsx` renders a scan pack: title, summary and stats, configuration (redacted start URL, scope, auth kind, kid, seed fingerprint, key purge, export ids), UI scan (entities, risk flags, the page's own validation), execution and verification (submissions, non-GET allowed, never-visit paths reached, stored fill values).
   - The seal chip now reads "0 submissions · 0 non-GET allowed".
4. **Download JSON** for scan packs failed ("the pack has no JSON artefact"): the scan finalize stores only `evidence_html`.
   - The console now saves the pack as the API returned it, in canonical form (`canonicalJson`).
   - Verified live: sha256 of the downloaded file == `pack_sha256` for both scan packs.
5. **Reports list.** Scan rows read "s_… · — → — · 0 rows", layer Connectors, linking to the table-job page.
   - They now link to `/scanner/scans/:id`, read "UI scan → captures and exports · nothing written to the scanned site", layer "UI scanner", "passed" with no leak count.
   - The list carries no job kind, so the console tells scans apart by the contract id prefix `s_` (`dps/ids.py` `PREFIX_SCAN`).
6. **Jobs, Overview and Job detail.** `GET /jobs` lists scans too.
   - The Jobs table (batch jobs over PostgreSQL) now leaves scans out (`tableJobs`).
   - Overview's recent jobs open a scan in the scanner (`jobHref`).
   - `/data-sources/jobs/<scan id>` redirects to the scan detail (verified live).

Fixture path: the dev-only fixture mode stays as m4ui built it, inert without `VITE_DPS_FIXTURES=1` under `npm run dev`. `npm run build` runs `check-bundle.mjs`, which reports no fixture marker in `dist/assets` (4 markers checked).

## Privacy proofs

- **Counting server (D4).** I read demo-site `/__received` immediately before and after two full console scans (synthesize and obfuscate):
  - Before and after were identical: `non_get 24, form_get 6, logout_hits 3, admin_hits 3`. The console's scans added **0** submissions, 0 GET-form submissions and 0 logout/admin hits.
  - The earlier non-zero totals come from the scanner pytest suite's control and adversarial cases, which ran against the same demo-site.
  - Per scan, the API reports `non_get_allowed 0`; POSTs blocked were 69 (synthesize) and 70 (obfuscate).
- **Log scan.** `docker compose logs app worker browser-worker` (225 lines, after every walk) was checked against the 11 demo-site customer values from `demo/onboarding/data.json`:
  - 0 exact hits and 0 digit-run hits.
  - 0 occurrences of the API key, `-----BEGIN`, `"dek"` or `storage_state`.

## Gates (all green before the commit)

- `./lane.sh ruff format dps tests tools`: 292 files unchanged.
- `./lane.sh ruff check dps tests tools`: all checks passed.
- `./lane.sh mypy dps`: no issues in 119 source files.
- `./lane.sh pytest -q`: **2114 passed, 87 skipped** (442 s), with the lane's scanner stack up. `tests/scanner` alone: 544 passed, 16 skipped.
  - All skips are integration groups whose profiles this change does not touch, or extras that are not installed:
    - source-pg not up (51)
    - azure-ocr-mock (3)
    - minio (3)
    - demo connections needing `smoke_test.py` (3)
    - `[ner]` extra with a spaCy model (1)
    - tesseract binary (2)
    - presidio_analyzer (1)
  - No Python file changed.
- Frontend: `npm ci` OK; `npm test` OK (node tests 42 pass, 0 fail; vitest 11 files, **179 passed**); `npm run build` OK (tsc, vite, check-bundle clean).
- Stack: `docker compose --profile scanner down` once done; no containers of this project left. No other project was touched.

## Open issues (not fixed here: backend, outside "wire the console")

1. **The scan pack's `verification` is not a measured sweep.** `dps/layers/scanner/inventory.py::build_scan_evidence` writes `hits: 0` as a constant and `passed: result != "failed"`; `values_checked` is the number of token fields.
   - The M2 HTML renderer and the Reports summary present this block as a leakage verification.
   - The console no longer calls it a leakage sweep for scans ("Stored fill values").
   - The backend should either compute a real check (e.g. stored `fill_*` and capture text vs values read on the page, counts only) or rename it in the pack and the HTML renderer.
2. **Scan finalize stores no `evidence_json` artefact.** `_FINAL_KINDS` in `dps/layers/scanner/__init__.py` holds only `evidence_html`; runs store both through `save_evidence`. The console works around it with a canonical JSON that matches the hash. Adding the artefact would give parity.
3. **Docstrings say "path" but the value is a URL.** `NumberedRiskFlag.path` / `InventoryRow.path` docstrings say "redacted path", while `number_flags` and `inventory_rows` pass the redacted URL. The console now handles both; the docstring or the value should be aligned.
4. **`EvidenceSummary` (`GET /evidence`) has no `job_kind`.** The console relies on the `s_` id prefix. Adding `pack->>'job_kind'` to the list query would be cleaner.
5. **The console cannot see the page cap.** It allows max pages up to 500, but the service cap is `scan_max_pages` (default 50); a larger value gets the worded `scope_invalid`. The API could expose the cap, e.g. on `/readyz` or a settings route.
