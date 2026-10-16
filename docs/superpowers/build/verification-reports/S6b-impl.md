# S6b — M5 Task 26: the ten-minute demo (seed, automated run, console check, script) and the deck from the real rehearsal

**Verdict: DONE.** Task 26 is implemented test-first on lane `fdemo`, the whole demo ran on this
lane's `--profile full` stack with every step PASS in 3:25 (target < 10:00), the showcase deck was
built `--strict` from that rehearsal and committed, and every page of it was reviewed. The one gap:
**Keynote could not be driven headless** (as in S6a), so the visual review used a LibreOffice
render plus the structural test; the Keynote PDF review stays with the owner. One **product gap**
was found and left open for the owner (a run cannot use a snapshot discovered under a tenant
policy without naming the policy again; §5).

| | |
|---|---|
| Worktree / branch | `/Users/neelu/document_intelligence/dps-wt-fdemo`, `fdemo`: `2b31b7a` → **`493f528`** |
| Commits (author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer, explicit pathspecs) | `764ad6a` feat(demo): idempotent demo seed, automated ten-minute demo with console screenshots · `37ec781` fix(demo): the Playground screenshot shows the obfuscated record, its data key masked · `493f528` docs(demo): showcase deck built from the rehearsal's measured outputs, final presenter script |
| Pushed | not by me: the owner's autopush loop pushed `fdemo` `37ec781` at 00:06:09 and **`493f528` at 00:16:20** (`held=0`) |
| Date | 2026-10-15 22:57 – 2026-10-16 00:20 IST |
| Evidence | `~/.cache/dps-build/s6b/`: `rehearsal/` (final report MD + JSON + 18 screenshots), `logs/` (seed and rehearsal logs, D4 and LLM counters before/after, source-vs-target, privacy scan, OCR, unit suite), `deck-qa2/` (LibreOffice PDF + 12 page images of the committed deck), `probe/` (lane copies of S4b's `svt.py`, `privacy_scan.py`, `ocr_probe.py`), `keynote/` (the failed Keynote attempts) |

## 1. What was built (M5 Task 26)

| File | What |
|---|---|
| `tools/demo_seed.py` | Idempotent seed of tenant `retail-banking-apac`: tenant record (display name, quota 50 000 000, retention 30, hosts `demo-site`/`localhost`/`127.0.0.1`); keys `demo-console` (`*`), `crm-uat-provisioner` (detect, obfuscate, jobs), `qa-onboarding-scanner` (scan, synthesize, obfuscate) → raw keys only in `var/demo/demo-keys.json` (0600, atomic write), stdout names + stored prefixes; keys that still authenticate are reused, a dead one is replaced and its other active namesakes disabled; sources via `tools/seed_sources.py` (`--target postgres/oracle/mongo --rows 20000 --seed 7`, `--reseed` passes `--reset`); seven connections; policies `gdpr-pseudonymise` adopted, `retail-kyc` v2 (employee-id recognizer + declared field `LOANS.APPLICANTS.EMPLOYER_NAME → ORG_NAME`, `CHG-48213`) published once; job group `crm-uat-refresh` → key + seed only in `var/demo/group-crm-uat-refresh.json` (0600; a lost file makes `crm-uat-refresh-<n>`); `--reset-jobs`; value-free summary |
| `tools/e2e_demo.py` | S1–S11 of the plan's table asserted against the stack as `demo-console` + `X-DPS-Tenant`, S12 (`--console`) a Playwright walk of nine pages × light/dark → `reports/demo-screens/<page>-<theme>.png`; `--skip-scanner`, `--skip-oracle`; `reports/e2e-demo-report.md` (last line "demo flow completed in m:ss", API path separately) and `reports/e2e-demo-report.json` (`dps.e2e-demo.v1`, the deck's contract: every scripted step listed, SKIP/NOT RUN, facts per `build_deck.py`); stops at the first FAIL/ERROR (later NOT RUN); value-free details (DemoError's route templates, exception class names) |
| `tests/console/test_console_pages.py` | `browser` + `integration`: the nine routes as `demo-console`, the page's own heading within 15 s, no console error, no request to another origin; ids of the tenant's newest run/scan/pack read through the API; skips without `var/demo/demo-keys.json` |
| `tests/test_demo_seed.py` (10), `tests/test_e2e_demo.py` (22), `tests/test_deck.py` (+1 → 43) | offline fake stack (`httpx.MockTransport`), compose demo-secrets script resolution for the demo tenant, D4 rule with both positive controls, driver, report contract read back by `build_deck.Report`, the committed deck's structure |
| `docker-compose.yml` | `demo-secrets` also copies the demo refs tree to `retail-banking-apac/dps/…` (refs are tenant-scoped) |
| `docs/demo/DEMO-SCRIPT.md` | final: status line with the rehearsal, what the seed makes, connections/targets as built, Playground selector note, recovery rows (`--reseed`, `secret_unavailable`, a stopped rehearsal), `--reset-jobs` as built; the `#### Slide N` blocks unchanged |
| `docs/demo/dps-showcase.pptx` | 12 slides, 958 KB, built `--strict` from the rehearsal at `37ec781`; byte-identical on rebuild |
| `docs/superpowers/build/M5-DEVIATIONS.md`, `HANDOFF.md` §19 | Task 26 (S6b, finish stage) section; build/deploy/demo facts and traps |

Test-first: `tests/test_demo_seed.py` failed at collection before the tool existed (ImportError),
its compose test failed (`SecretUnavailable`) until the one-shot wrote the demo tenant's tree, the
two `--reseed` tests failed before the option; `tests/test_e2e_demo.py` was checked by mutation (the
D4 controls dropped, the driver not stopping → 5 failed, restored → 22 passed); the committed-deck
test fails without the deck and on a placeholder build, passes on the real one.

## 2. The rehearsal (final, at `37ec781`, lane stack ports 8418…)

`python tools/demo_seed.py` (all reused, 1.5 s) then `python tools/e2e_demo.py --console`:

| Step | Status | s | Measured (value-free) |
|---|---|---:|---|
| S1 Overview | PASS | 0.0 | ready, 9/10 components ok (`ner` patterns-only), raw PII to LLM 0 |
| S2 Playground | PASS | 0.0 | 12 findings, 11 types incl. AADHAAR, PAN_IN, EMAIL; obfuscate `@retail-kyc` verified; reveal identical; synthetic holds 0 of 10 detected values; LLM raw 0 |
| S3 Tokenization | PASS | 0.0 | `tk_` token, reveal identical |
| S4 Scanned PDF | PASS | 2.6 | 2 OCR pages, 14 findings, 0 extractable characters, 0 originals in the OCR re-read |
| S5 Discover | PASS | 10.1 | PG 8/16, Mongo 10/17, Oracle 11/16 sensitive/columns, heat-map data, accepted |
| S6 Runs (group key) | PASS | 82.3 | 3 × 20 000 rows, 4 partitions each all `done`, verified, label pseudonymised, kid = group's; 930.9 / 353.8 / 246.3 rows/s (pg/mongo/oracle) |
| S7 Consistency | PASS | 0.3 | 3/3 pairs consistent, 500 of 500 sampled matched in every system |
| S8 Synthetic (copula) | PASS | 18.1 | 20 000 rows, fidelity 0.9597, exact-row matches 0, NNDR 1.4988 (target 0.95), MIA AUC 0.4995 (target ≤ 0.6), utility 0.9696 accuracy |
| S9 UI scanner | PASS | 76.4 | 21 pages, 63 fields, 3 personas, submissions 0, non_get_allowed 0; site counters non_get/form_get/logout/admin +0 while `get` +175; 60 sends blocked; 12 pages with both fills; CSV export with header, 0 demo-site values |
| S10 Reports | PASS | 0.1 | 5 demo packs listed, evidence PDF `%PDF`, 32 audit rows, 0 input values in report/audit/PDF bodies |
| S11 Admin | PASS | 2.1 | posture 5/11, `enforced` false, env local; rotated key (grace 0) → 401; dry-run retention succeeded (its `retention.run` audit row) |
| S12 Console | PASS | 13.1 | 9 pages × 2 themes, 18 screenshots, 0 console errors |

**demo flow completed in 3:25** (API path S1–S11 3:12). Three earlier full rehearsals (13 s FAIL
at S6 → fixes, then 3:32 PASS without console, 3:35 and 3:32 PASS with console) are in `logs/`.
`DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser tests/console/test_console_pages.py` → **9 passed**.

## 3. Deck and QA

- `python tools/build_deck.py --strict` → `docs/demo/dps-showcase.pptx`, 12 slides, no missing input,
  byte-identical on a second build and after the script edits (Slide blocks untouched).
- **Keynote: not possible headless.** Keynote 14.4: AppleScript `open` returned `missing value`
  (-1700) / timed out (AppleEvent -1712); `open -a Keynote <file>` opened nothing, also after
  quitting and relaunching Keynote (it then ran frontmost with 0 documents); `osascript` has no
  assistive access to see a dialog. Same as S6a: a dialog that needs a person. Keynote was left
  running (it was running hidden before; one AppleScript `open` of `reports/deck-trial.pptx` may
  still complete when someone dismisses the dialog).
- Instead: LibreOffice headless → PDF → 12 page images (`deck-qa2/page-01..12.png`), **all read**:
  no overflow or clipped text, every bar (8 bench bars) and diagram drawn, fonts as named, the four
  screenshots in place, footer "rehearsal 2026-10-15 18:28 UTC · commit 37ec781". Slide 5 first
  showed the Synthesize output against a lede about tokens and the key → the walk now runs
  Obfuscate and masks the key card's data-key text (ink box) so no screenshot carries key material
  (`37ec781`), then rehearsed and rebuilt.
- Structural (new test on the committed file): 12 slides, titles, notes = script blocks, no
  placeholder, no `ppt/charts`/embeddings/graphic frames, only Helvetica Neue/Arial, no CRITICAL
  entity, < 20 MB. Deck XML: 0 of the demo keys, group DEK/seed, bootstrap key, KEK; 0 raw `dps_`
  keys, 0 `tk_` tokens.
- Slide 1 prints "v0.2.0" (the version the stack reports); rebuild after Task 28 sets 1.0.0.

## 4. Proofs (counts only)

- **D4:** demo site `/__received` before/after the final rehearsal: non_get/form_get/logout/admin
  0 → 0, `get` 525 → 700 (+175, the scan); same on the two earlier console rehearsals.
- **LLM:** `dps_llm_raw_pii_sent_total` 0.0 before and after every rehearsal.
- **Source vs target** (`logs/svt-demo-final.txt`): `bank_uat.customers_demo`,
  `CRM_MASKED.CUSTOMERS_DEMO`, `crm_uat.profiles_demo` (20 000 each): 0 same-row survivors in every
  personal column; set-equal names, e-mails, mobiles, national ids, tax ids, notes, Oracle address
  0; customer ids 4 and shifted birth dates 12 512 meet other customers' values (domain collisions,
  as on every lane). `bank_uat.customers_synthetic` (20 000): customer ids, e-mails, national ids,
  tax ids 0 equal to a source value (the pack's D-A identifier check: national_id 20 000 and tax_id
  11 989 checked, 0 in source); names 834, mobiles 2 281, birth dates 13 861 equal some source value —
  surrogate-domain collisions: the demo source comes from the same fictional generator (its own
  20 000 mobiles hold only 18 240 distinct values, so the space is small); no row correspondence
  exists in a synthetic table.
- **Logs and artefacts** (`logs/privacy-scan-final.txt`, 49 sources, 13.7 MB + 0.96 MB of artefact
  and export volumes): 0 hits of the demo seed's raw keys, the group's DEK and seed, the bootstrap
  key, the KEK, the tokenizer token/key, the console's KYC sample, the smoke sample, the SSN probe,
  the demo site's values and cookie, 2 000 sampled values per personal column of PG/Oracle/Mongo,
  raw `dps_` keys, `tk_` tokens, permission/storage errors, tracebacks.
- **OCR** of the rehearsal scans' stored screens (app image tesseract): filled screens 0 demo values,
  positive control holds (persona values readable); masked captures 0 except `application_ref` on
  page 25 `/apply/review` — M4's known N8/N9, as on m4, main, fimg and the merge run.
  Final scan `s_01M50D9ER74E2T1DXRHKF1V76K` (`logs/ocr-demo-scan-final.txt`): filled_synthesize 12
  images 0 demo hits / 221 persona hits, filled_obfuscate 12 images 0 / 12, masked_capture 3 images,
  only page 25 `application_ref`; the earlier rehearsal scan gave the same (`logs/ocr-demo-scan.txt`).

## 5. Deviations and findings (detail in M5-DEVIATIONS "Task 26 — … S6b")

- Demo tenant secrets: `demo-secrets` copies the demo refs to `retail-banking-apac/` (SEC-2).
- Seven connections (one per role) instead of the plan's source+target ones (credentials split).
- `--rows` 20 000 (compose seed); `--reseed` added (the test suite leaves 200 rows → exit 3).
- Retention jobs are system-tenant: waited for via the `retention.run` audit row.
- S4 uses the committed synthetic scan; S8 writes `bank_uat.customers_synthetic` (no `synth_demo`
  schema) with `utility_label $.segment`; S6 `partition_rows` 5 000.
- **Open product gap (for the owner):** `dps/layers/connectors/jobs_run.py:483` resolves a run's
  policy as `spec.policy or snapshot.policy_name`; for a snapshot discovered under `@retail-kyc` the
  name `retail-kyc` is not a preset → every such run fails `policy_invalid` unless the run names
  `@retail-kyc` again (the demo does). The console wizard discovers with presets, so it is not hit
  there; API callers using tenant policies are. Not fixed here (L2 + Task 15, outside the demo).
- Keynote QA not possible headless (above).

## 6. Gates

ruff format/check `dps tests tools` clean (451 files); `mypy dps` clean (150); mypy of the demo and
deck tools and their tests clean (7); targeted tests 146 passed (demo seed, e2e, deck, compose ×3,
demo client, flow report); console pages 9 passed on the stack; unit suite
`./lane.sh pytest -q -m "not integration"`: **3412 passed, 86 skipped** (288 deselected) in 11:22
(`logs/pytest-unit.log`). The whole integration suite was not rerun here: the `fimg` merge ran it on
`2b31b7a` (3714 passed) and this change adds only the demo tools, their tests and the demo-secrets
copy, whose compose tests (`test_compose*.py`) pass.

## 7. State left behind

- `fdemo` at `493f528`, worktree clean (`reports/` and `var/` are git-ignored: rehearsal reports,
  screenshots, `deck-trial.pptx`, demo keys and group file stay local).
- Demo tenant seeded on this lane's stack (volumes kept; the final rehearsal's targets, packs and
  scan are there). Stack stopped: `docker compose --profile full stop` (15 containers exited,
  volumes kept, not `down -v`). Only this lane's compose project and files were touched.
- For the release: rebuild the deck after Task 28 (version 1.0.0) from a fresh rehearsal on the
  release stack (`demo_seed.py`, `e2e_demo.py --console`, `build_deck.py --strict`); the owner's
  Keynote PDF review is still open.
