# S6b review, round 2: M5 Task 26 / 26B (ten-minute demo and showcase deck), lane `fdemo`

**Verdict: PASS.** The three round-1 blockers (B1 MongoDB `$sample` 28799, B2 S2 nested probes, B3 S7
joined to this run's jobs) and N1 are fixed in code, covered by tests that were red before, and hold
on the live stack: I put the MongoDB target back into the 28799 state and ran three rehearsals in a
row on this lane's `full` stack, with no reset. All three passed: `--console` 12/12 in **2:57**,
`--skip-oracle --skip-scanner` in **0:59** (S7 joined the run's own 2 jobs, although the group has
Oracle runs), and `--console` 12/12 in **2:57** again. The committed deck is a byte-identical
rebuild of the fixer's rehearsal. Every number on it traces to that report or to the bench files.
It holds no key, token or value, and it has no chart parts and no fonts other than Helvetica Neue
and Arial. No blocking finding remains. Non-blocking items are in §3.

| | |
|---|---|
| Reviewed | `/Users/neelu/document_intelligence/dps-wt-fdemo`, branch `fdemo` at **`b57ec86`** (round-1 fixes `b0bea88` and `b57ec86` on top of `493f528`) |
| Reports read | `S6b-impl.md`, `S6b-review-r1.md`, `S6b-fix-r1.md` |
| Evidence | `/Users/neelu/.cache/dps-build/reports/s6b-review-r2/`: `logs/` (gates, seed, three rehearsals, tests, source vs target), `r1-console/`, `r2-skip-oracle/`, `r3-console/` (reports and 2 × 18 screenshots), `deck-qa/` (LibreOffice PDF and 12 page images), `keynote/` (the failed Keynote attempt) |
| Repository files modified | none. The worktree is clean at `b57ec86`. My outputs went to the evidence directory and the session scratchpad. |
| Date | 2026-10-16 03:16 – 03:41 IST |

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `ruff format --check` / `ruff check` (`dps tests tools`) | 452 files already formatted; all checks passed |
| `mypy dps`; mypy of the demo and deck tools, their tests, `test_mongo_sample.py` and the console test | clean (150 and 8 files) |
| `docker compose --profile full build` then `up -d` (this lane only) | Build was fully cached. The app image already carried the fix: the container's `mongo.py` has the fallback constants. All 11 services were healthy and the one-shots exited 0. |
| `demo_seed.py` | ready in 1.5 s, everything reused (3 keys, 3 sources × 20 000, 7 connections, `retail-kyc` v2, group). `var/demo/*.json` are mode 600. Stdout shows names and stored prefixes only. |
| MongoDB target before the rehearsals | `$sample` 500 on `crm_uat.profiles_demo` passed 5/5, because the stop/start checkpoint had rebuilt the tree. One server-side rewrite of the same 20 000 documents (the fixer's `rewrite.py`, value-free) gave **28799 on 5/5**. |
| **R1 `e2e_demo.py --console`** | **12/12 PASS in 2:57.** S2: 12 findings, 0 of 11 probes leaked. S5: PG 8/16, Mongo 10/17, Oracle 11/16. S6: 3 × 20 000 rows, verified. S7: 3/3 pairs at 500/500, members equal the S6 job ids. S8: fidelity 0.9697, NNDR 1.5177, MIA 0.5023. S9: 21 pages, 63 fields, D4 +0, get +175, 60 sends blocked. S10: 5 packs, 32 audit rows, 0 values. S11: 5/11, 401, retention succeeded. S12: 18 screenshots, 0 errors. App and worker logged 2 `mongo_sample_scan` and 5 `mongo_sample_retry`, with **0 failed sample calls**. The target was still 28799 5/5 afterwards. |
| **R2 `--skip-oracle --skip-scanner`** (no reset) | **PASS in 0:59.** S7 checked 1/1 pair on exactly the run's 2 jobs; before B3 this case failed with "3 members (2 expected)". 4 scans, 8 retries, 0 failed. |
| **R3 `--console`** (no reset) | **12/12 PASS in 2:57.** S7 members equal the S6 job ids. S8: fidelity 0.9638, NNDR 1.4404, MIA 0.4984. 3 scans, 6 retries, 0 failed. |
| D4 counting server (`/__received`) | non_get, form_get, logout and admin stayed **0** through all three rehearsals. get went 0 → 175 (R1) → 175 (R2, no scan) → 350 (R3). |
| LLM counter | `dps_llm_raw_pii_sent_total` was **0.0** before and after every run |
| Source vs target, after R3 (counts only, `logs/svt-after-r3.txt`) | PG `customers_demo`, Oracle `CUSTOMERS_DEMO` and Mongo `profiles_demo` (20 000 each): **0 same-row survivors**, and 0 set-equal names, e-mails, mobiles, national ids, tax ids, notes and Oracle addresses. Customer ids 4 and shifted birth dates 12 512 are domain collisions, as on every lane. `customers_synthetic`: customer ids, e-mails, national ids, tax ids and notes **0**. Names 840, mobiles 2 222 and birth dates 13 857 are surrogate-domain collisions, the same as the implementer's figures. |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser tests/console/test_console_pages.py` | **9 passed** |
| `test_mongo_connector.py::…survive_a_collection_of_deleted_records` (B1 reproduction on the live MongoDB) | **1 passed** |
| `-m "mongo and not oracle" tests/connectors` (the whole MongoDB connector suite, against the changed `_sample_docs`) | **64 passed, 1 skipped** (by design). The demo data was intact afterwards: `crm.profiles` and `crm_uat.profiles_demo` hold 20 000 each, and no `dps_t_*` database was left behind. |
| `pytest tests/test_deck.py tests/test_e2e_demo.py tests/test_demo_seed.py tests/connectors/test_mongo_sample.py tests/test_demo_docs.py` | **114 passed** |
| Unit suite `pytest -q -m "not integration"` (with `dps-db` up, after the rehearsals) | **3433 passed, 86 skipped, 289 deselected, 0 failed** in 11:01 (`logs/pytest-unit.log`); the worktree was still clean afterwards |
| Whole integration suite | **Not run.** Its `mongo and oracle` part reseeds the demo sources to 200 rows and rewrites 54 committed `m3-shots/*.png`, and a reviewer may not modify repository files. Instead I ran the integration tests this change touches: console 9, the MongoDB connector suite 64 + 1 skipped, and the B1 reproduction. |

## 2. Round-1 findings: verification

| Finding | Status | How verified |
|---|---|---|
| **B1** MongoDB `$sample` 28799 (`dps/layers/connectors/mongo.py`) | **Fixed** | `_sample_docs` tries the random cursor twice. It catches `OperationFailure` 28799 from the aggregate or a getMore, and no other code. It then samples through `[{$match:{_id:{$exists:true}}}, {$sample}]` with hint `{$natural:1}`, under the same `maxTimeMS` and comment. Errors still go through `_guard`, which is value-free. All three callers (`describe`, `sample`, `sample_keys`) go through it, and no other `$sample` exists in `dps/`. Unit tests (8) cover retry, fallback (exact pipeline, hint, `maxTimeMS`, comment), `describe`, no retry for code 50, and a value-free failing fallback. The live integration test passes. Three rehearsals on a target in the 28799 state took the fallback 2–4 times each with 0 failed sample calls, at prepare, at the post-write sample and at the consistency check. |
| **B2** S2 nested probes (`tools/e2e_demo.py`) | **Fixed** | `detected_values` probes every string leaf of every detected top-level field, so the card PAN and expiry are probes and no dict repr is used. The haystack is the answer's leaves plus its JSON with `ensure_ascii=False`. Tests: the probe set; a clean record passes; an echoed card number, expiry or e-mail fails. Live: "0 of 11 detected values" (it was 10, one of them a repr). |
| **B3** S7 joins this run's jobs | **Fixed** | S5 records `customer_id_path` per snapshot, using the server's order: not `keep`, not text, then key, anchor, shortest. S7 posts explicit `pairs` with the last-submitted job first and fails unless the member job ids equal the S6 job ids. Tests cover an older run, a missing member, an extra member, and `--skip-oracle`. Live R2: `--skip-oracle` after full runs passes with exactly its 2 jobs. All three report JSONs show members equal to S6 jobs, with paths `$.CUSTOMER_ID`, `$.customerId` and `$.customer_id`. |
| **N1** unforeseen step exceptions | **Fixed** | `run_steps` maps any other exception to `ERROR` with the class name only, so the reports are still written. A test covers it. |
| N2–N10 | open, non-blocking | See §3 |

## 3. Non-blocking findings (no blocker)

1. **Slide 5 says what its screenshot does not show (r1 N4, still open).** The lede says "obfuscation keeps formats". The speaker note says "an identifier still looks like that identifier". The screenshot, however, is the console default `gdpr-pseudonymise`. In it, `pan` (PAN_IN) and `address` come out as 40+ character base32 strings, and `card.expiry` stays `09/28`. The Playground selector lists only presets, so the walk cannot select `retail-kyc`. Reword the lede and the note to the visible cases (Aadhaar keeps its last four digits, the e-mail keeps its domain, the card keeps BIN and last four), then rebuild. This is the most visible residual for a bank audience.
2. **The demo asserts less than the deck shows (r1 N2).** S8 does not assert `nndr.ratio ≥ target` or `mia.auc ≤ target`; the plan asks only for "measured", but slide 9 prints both targets. S6 does not assert `rows_written` equal to the source rows. Its partition check is vacuously true for a job with 0 partitions. Its pack check accepts a best-effort `post_sample` error. Every live run had 20 000 rows, 4 partitions and real `post_sample` results, so no number on the deck is affected.
3. **The MongoDB fallback scans the whole collection.** It is bounded by `connector_statement_timeout_seconds` (default 300 s). On a very large `table` target emptied and refilled by every run, the fallback (at prepare, the post-write sample and the consistency check) could reach that limit and fail with `statement_timeout`. Before the fix it failed with `source_error`, so nothing is worse than before. A ranged fallback, for example random `_id` windows from `_id_bounds`, would be cheaper. This belongs with the owner's scale targets (D14).
4. **HANDOFF §19 was not updated by the fix round.** It still records the verification "at `37ec781`: S1–S12 PASS in 3:25", while the committed deck's footer is `b0bea88` at 3:17. It has no operator line on the MongoDB fallback: the `mongo_sample_retry` and `mongo_sample_scan` log events and the full-scan cost. DEMO-SCRIPT and M5-DEVIATIONS were updated. A one-paragraph refresh belongs in Task 27.
5. **Cosmetic issues.**
   - Slide 7 labels read "PostgreSQL obfuscate into in place" (r1 N5).
   - `ppt/presentation.xml` declares a 16:9 size with `type="screen4x3"` (r1 N6).
   - M5-DEVIATIONS says "PASS 12/12 (S12 not requested)" for a run without `--console`; it is 11/11.
   - S5's step title names Oracle under `--skip-oracle`.
   - Every S11 leaves a disabled `demo-rotation-check` key pair in the tenant's key list.
6. **Still open for the owner, unchanged.**
   - The policy-name product gap (`jobs_run.py`: a run without `policy` resolves a tenant-policy snapshot's name as a preset; r1 N9).
   - Slide 1 shows v0.2.0 until Task 28 (N10).
   - The `test_ndjson.py` flake ticket (N7).

## 4. Deck checks (`docs/demo/dps-showcase.pptx`, sha256 `beaed796…e516`)

- **Rebuild.** `build_deck.py --strict` from the fixer's R3 rehearsal
  (`s6b-fix-r1/r3-console/e2e-demo-report.json` and its `demo-screens/`) is **byte-identical** to the
  committed file and to `git show b57ec86:docs/demo/dps-showcase.pptx`. The 4 embedded images are
  byte-identical (sha1) to that rehearsal's `playground-light`, `job-light`, `scan-parallel-light`
  and `evidence-light`.
- **Numbers.** Every figure on slides 1 and 5–11 is in that report, after rounding:
  - slide 1: v0.2.0, 3:17 (197.2 s), 21:29 UTC, commit `b0bea88`;
  - slide 5: 12 findings and 11 types;
  - slide 6: 2, 14, 0, 0;
  - slide 7: 20 000 rows and 4 partitions each, at 949.6, 400.0 and 276.0 rows/s;
  - slide 8: 500 of 500 ×3;
  - slide 9: 0.97, 0, 1.43 (target 0.95), 0.48 (target 0.60), 0.99, 20 000;
  - slide 10: 0, 175, 60, 21, 63, 3;
  - slide 11: 5 of 11, 0, 5, 32, 401, succeeded.

  The 8 bench bars (2 911.7, 3 195.7, 2 474.0, 4 127.6, 4 828.4, 2 021.1, 941.6, 860.3) and
  "18 CPUs" are the `rows_per_second` values and CPU counts of `docs/technical/bench/*.json`. Slides
  2–4 and 12 are static text. Among the speaker notes, only slide 7 has a number ("2"), and it is
  wording.
- **No values, keys or tokens.** All 86 XML parts were searched, counts only. There are 0 of: the 3
  demo keys, the group DEK, the group seed, the bootstrap key, the compose secret defaults, the S2
  sample values, the SSN and the demo-site values. There are also 0 raw `dps_` keys and 0 `tk_`
  tokens. The same scan over my three rehearsal reports, the fixer's R3 report, every log and the
  app/worker logs of my runs also found **0**. `docProps` names no person.
- **Screenshots.** I read the 4 images and OCR'd them with the lane image's tesseract, together with
  my R3 run's 18 screenshots. They hold 0 keys, DEK, seed, raw `dps_` keys or `tk_` tokens, and **no
  readable `dek` line** (the mask works). Only `kid` and the `kcv` check value show, and neither is
  key material.
- **Keynote safety, structure.** No `ppt/charts`, embeddings, `graphicFrame`, `AlternateContent`,
  tables, OLE or SmartArt. Shapes are only rect, roundRect, ellipse, line and groups, with
  `noAutofit` text and no autofit shrink.
  - Run typefaces: Helvetica Neue (334) and Arial (13).
  - Theme major and minor fonts: Arial.
  - The LibreOffice PDF embeds HelveticaNeue and HelveticaNeue-Bold only.
- **Visual.** My own LibreOffice headless render (`deck-qa/`) produced 12 pages, and I read every
  one. There is no overflow or clipping. The bars, the flow, the triangle and the architecture
  diagram are all drawn. The footer reads "rehearsal 2026-10-15 21:29 UTC · commit b0bea88".
- **Keynote PDF export: not possible headless (third attempt).**
  - Keynote 14.4 now answers AppleScript: version and `count of documents` = 0.
  - `open` of a copy of the deck still times out (AppleEvent −1712), leaving 0 documents.
  - Quick Look produced no preview.
  - `screencapture` has no display permission.
  - So the Keynote PDF review stays with the owner. My `open` of
    `s6b-review-r2/keynote/dps-showcase-copy.pptx` may still complete once someone dismisses what
    Keynote is waiting on; that is harmless.

## 5. Acceptance (plan Task 26 / 26B)

| Item | Status |
|---|---|
| `demo_seed.py` idempotent; files 600; value-free summary | met (live) |
| `e2e_demo.py --console`: every step PASS, report and screenshots, < 10:00 | **met, and repeatable**: 2:57, 0:59 (fallback flags) and 2:57 in a row on one stack, without a reset, with the MongoDB target in the 28799 state |
| Every promise asserted | met for the plan's assertions (S2 nested values, S7 per-job paths). S6 rows and S8 targets are not asserted beyond the plan (§3.2). |
| JSON report = deck contract; Markdown last line wall time | met |
| Console test, 9 passed | met |
| DEMO-SCRIPT final | met |
| Deck from the real rehearsal; 12 slides; notes; no invented numbers; Keynote-safe parts and fonts | met (byte-identical rebuild, numbers traced, structure checked) |
| Keynote PDF review | not possible headless (owner) |
| Git | 2 fix commits by `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers, conventional messages; worktree clean |

## 6. State left behind

- No repository file changed. `fdemo` is at `b57ec86` and its worktree is clean.
- The demo tenant now also holds my three rehearsals' runs, scans, packs and two `demo-rotation-check`
  keys (disabled). The MongoDB target `crm_uat.profiles_demo` is in the 28799 state, which is the
  normal state after a `truncate_insert` run, and the product now handles it.
- The stack was stopped with `docker compose --profile full stop`. Volumes were kept, and only this
  lane was touched.
