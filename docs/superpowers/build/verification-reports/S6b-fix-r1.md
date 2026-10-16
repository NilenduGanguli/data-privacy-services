# S6b fix, round 1: lane `fdemo` (M5 Task 26 / 26B), review `S6b-review-r1.md`

**Result: all 3 blocking findings fixed, plus N1.** Two commits on `fdemo` (worktree
`/Users/neelu/document_intelligence/dps-wt-fdemo`), both by `NilenduGanguli <nilendu.ganguli@gmail.com>`
with no trailers:

| Commit | What |
|---|---|
| `b0bea88` | `fix(fdemo): MongoDB samples survive a rewritten collection; S2 probes nested values; S7 joins the run's own jobs` — `dps/layers/connectors/mongo.py`, `tools/e2e_demo.py`, tests, DEMO-SCRIPT reset note, M5-DEVIATIONS |
| `b57ec86` | `fix(fdemo): the showcase deck is rebuilt from a rehearsal of the fixed demo, after two runs in a row` — `docs/demo/dps-showcase.pptx`, DEMO-SCRIPT status, M5-DEVIATIONS rehearsal record |

I ran three rehearsals in a row on this lane's `full` stack, with no `down -v` and no reset. Before
them I put the MongoDB target back into the 28799 state. All three passed, and each one left the
target failing a plain `$sample` again. The product took the fallback every time, and no sample
call failed.

Evidence: `/Users/neelu/.cache/dps-build/reports/s6b-fix-r1/` (`logs/`, `r1/`, `r2-skip-oracle/`,
`r3-console/` with 18 screenshots, `deck-libreoffice.pdf`, and the probe scripts I used).

## B1: MongoDB `$sample` 28799 (product, `dps/layers/connectors/mongo.py`)

**Root cause.** The pseudo-random cursor is used when `$sample` is the first stage, N < 5 % of the
documents, and the collection has more than 100 documents. Its random descent picks a child at
each level of the tree. After `truncate_insert`, the deleted documents' pages are still in the
tree, and the new documents are appended into the right-most subtree. Most descents therefore land
among deleted records and walk forward to the same live document. After 100 duplicates in a row
the cursor fails. I reproduced this on a scratch collection: a dead zone of large deleted documents
next to many live ones gives `$sample` 500 failing 10/10 and `$sample` 200 passing, which is the
reviewer's pattern.

**Fix.** `_sample_docs` builds its pipeline with a new pure `sample_pipeline(flt, n, project, then,
random_cursor)` and tries the random cursor **twice**. It catches `OperationFailure` code 28799 from
the aggregate or from a getMore; any other code is not retried. After that it samples through
`sample_pipeline(..., random_cursor=False)`:
- a leading `ALL_DOCUMENTS = {"$match": {"_id": {"$exists": true}}}`, because an empty `$match` is
  optimised away and the random cursor comes back (I checked this with `explain`);
- the hint `NATURAL_ORDER = {"$natural": 1}`, which gives a COLLSCAN. Without the hint the planner
  chose IXSCAN on `_id` plus FETCH;
- the same `maxTimeMS` and comment.

Both paths are logged without values (`mongo_sample_retry` with the driver code, and
`mongo_sample_scan`). An error still becomes a value-free `ConnectorError` through `_guard`. This
covers `describe`, `sample` and `sample_keys`. MongoDB 7.0.40 `explain`: the plain pipeline plans
`$sampleFromRandomCursor`/`MULTI_ITERATOR`; the fallback plans `COLLSCAN` + `$sample`.

**Tests (written first).**
- `tests/connectors/test_mongo_sample.py`: new, 8 unit tests with a fake collection.
  - One 28799 is retried; the error comes from the aggregate in one case and a getMore in the other.
  - A persistent 28799 falls back. The test checks the exact pipeline, the hint, `maxTimeMS` and
    the comment.
  - `describe` goes through the fallback.
  - Code 50 is not retried and maps to `statement_timeout`.
  - A failing fallback stays `source_error: mongo:28799` with no message text.
  - The pipeline builder is tested on its own.
  - Before the fix the module did not import, so all of it was red.
- `tests/connectors/test_mongo_connector.py::test_describe_and_sample_survive_a_collection_of_deleted_records`
  (mongo integration):
  - It inserts and deletes 3 000 × 17 KB documents next to 12 000 live ones. It asserts that a raw
    `$sample` 500 fails with 28799, then checks both plans with `explain`.
  - It then runs `describe`, `sample(500)` and `sample_keys(500)`: 500 distinct live documents and
    sorted keys come back.
  - The profiler shows exactly 3 fallback scans, each with `$natural` and `maxTimeMS`.
  - Against the pre-fix `_sample_docs`, swapped in through a pytest plugin, it fails with
    `ConnectorError: source_error: mongo:28799` (`logs/red-mongo-integration-old-code.log`).

**On the lane's target.** The state the reviewer left was gone. When the stack stopped, MongoDB's
shutdown checkpoint rebuilt the tree, and `$sample` 500 on `crm_uat.profiles_demo` passed 5/5.
- Filler documents inserted after the live ones and then deleted did not reproduce it: 5/5 passed.
- One server-side rewrite of the same 20 000 documents did reproduce it: `delete_many`, then
  `$merge` back from an `$out` copy, so no document left the server. That is what a
  `truncate_insert` run does. Result: `$sample` 500 **failed 5/5 with 28799**
  (`logs/mongo-target-state.log`).

## B2: S2's synthetic-leak probes (`tools/e2e_demo.py`)

- `detected_values(record, findings)` returns every string leaf of every top-level field a finding
  points into, using the existing `leaves()`. The nested `card.pan` and `card.expiry` are now
  probes, never a dict repr. Probes must be ≥ 4 characters.
- The haystack is the synthetic answer's leaves plus its JSON with `ensure_ascii=False`.
- The detail counts only the probes it actually checks. On the live stack that is
  "0 of 11 detected values" (before the fix: 10, one of them a repr).
- This check cannot flake by chance: the product never draws a surrogate that equals or contains
  any of the record's detected values (`surrogates.py`, "Never revealing" / "Never a part of the
  record").
- Tests:
  - the probe set;
  - a fake-client S2 that passes a clean synthetic record ("0 of 9");
  - S2 **fails** when the synthetic record echoes the card number, the expiry, or an e-mail in
    another field.
  - The card-number and expiry cases were red before the fix (`logs/red-demo-tests.log`).

## B3: S7 joins this run's jobs (`tools/e2e_demo.py`)

- **S5** stores `customer_id_path(columns)` for each engine. It picks the snapshot's `CUSTOMER_ID`
  column the server would join: not `keep`, not free text, ordered key, then anchor, then shortest
  path (`consistency._mapped`'s order). S5 fails if a snapshot has no such column. Live values:
  `$.customer_id` (PG), `$.customerId` (Mongo), `$.CUSTOMER_ID` (Oracle).
- **S7** posts `{"pairs": consistency_pairs(runs, join_paths), "sample": 500}`: one member per S6
  job. The last job submitted comes first and anchors the sample, as before, so a full run keeps
  Oracle as the anchor.
- S7 fails unless the result's member job ids are exactly the S6 job ids. The members' paths are now
  in the report facts.
- The plan's "customer id path per job" now holds, so no deviation remains to record. The change is
  written up in M5-DEVIATIONS.
- Tests:
  - pair building, including `--skip-oracle` and a missing path;
  - customer-id path selection (`keep`, free text, key, anchor, shortest path);
  - S7 posts exactly the pairs and passes, for 3 and for 2 members;
  - S7 **fails** when the check joined an older run, missed a member, or added one. These were red
    before.
- DEMO-SCRIPT's reset note now says that `e2e_demo.py` checks its own run's jobs.

## N1 (done; the review recommended fixing it in the same round)

`run_steps` maps any other exception of a step body to `ERROR` with the class name only, for
example Playwright's `TimeoutError` in S12. The run therefore still writes its reports, instead of
leaving the previous ones behind for `build_deck.py --strict`. The test was red before the fix.

## Rehearsals (images rebuilt from `b0bea88`; app and worker confirmed to carry the fix)

| # | Command | Result | Fallback events (app + worker) | Target afterwards |
|---|---|---|---|---|
| 0 | (target put into the 28799 state, see B1) | — | — | `$sample` 500: fail 5/5 (28799) |
| R1 | `e2e_demo.py` | **PASS** 11/11 + S12 not requested, **2:49**; S6 3 × 20 000 rows; S7 3/3 pairs 500/500, members = the S6 jobs | scan 3, retry 6, failed sample calls 0 | fail 5/5 (28799) |
| R2 | `--skip-oracle --skip-scanner` | **PASS**, **1:14**; S7 1/1 pair on the run's 2 jobs (the group already had Oracle runs) | scan 4, retry 8, failed 0 | fail 5/5 (28799) |
| R3 | `--console` | **PASS 12/12**, **3:17** (API path 3:04); 18 screenshots, 0 errors | scan 3, retry 6, failed 0 | fail 5/5 (28799) |

- In R1, the worker scanned at the S6 prepare `describe` (the reviewer's run-3 failure point) and at
  the post-write sample (the reviewer's `post_sample` error). The app scanned at the consistency
  check's `describe` (the reviewer's 502).
- Every R1 pack has a real `verification.post_sample` result, not an error.
- R3 facts:
  - S2 12 findings, 0 of 11 probes leaked.
  - S5 PG 8/16, Mongo 10/17, Oracle 11/16.
  - S8 fidelity 0.9718, exact matches 0, NNDR 1.4343 (target 0.95).
  - S9 21 pages, 63 fields; D4 +0 on non_get, form_get, logout and admin; get +175; 60 sends
    blocked.
  - S10 5 packs, 32 audit rows, 0 values. S11 posture 5/11, rotated key 401.
- `dps_llm_raw_pii_sent_total` 0.0 after the runs.

## Deck (`b57ec86`)

- `build_deck.py --strict` from R3. The footer reads "rehearsal 2026-10-15 21:29 UTC · commit
  b0bea88" (the fix commit). Slide 1: "ran end to end in 3:17".
- A second build is **byte-identical**: sha256 `beaed796…e516`.
- `tests/test_deck.py`: 43 passed, including the committed-deck test.
- LibreOffice render: 12 pages, all viewed. No overflow, bars and diagrams drawn, 4 screenshots in
  place. Embedded fonts are HelveticaNeue and HelveticaNeue-Bold only.
- Value and key scan, counts only, over the deck XML, the R1/R2/R3 reports, the e2e and seed logs
  and the app/worker logs of all three runs:
  - **0** demo API keys, group key material or seed, bootstrap key, compose KEK, tokenizer token,
    S2 sample values, demo-site values or SSN;
  - 0 raw `dps_` keys and 0 `tk_` tokens.
- OCR (tesseract 5.5.0 in the OCR mock) of R3's 18 screenshots:
  - 0 of every key, secret and token class, and no readable "dek".
  - The only value hits are in the two Playground shots: the committed fictional sample in the
    input pane, which is intended. 6 of the 10 demo-site values are the same fictional persona.
- The Keynote PDF review stays with the owner (not possible headless, as before).

## Gates

| Gate | Result |
|---|---|
| `ruff format --check` / `ruff check` (`dps tests tools`) | 452 files formatted; all checks passed |
| `mypy dps`; mypy of the demo/deck tools and tests + `test_mongo_sample.py` | clean (150 / 8 files) |
| Unit suite `pytest -q -m "not integration"` (with `dps-db` up) | **3433 passed, 86 skipped, 0 failed** (11:49) |
| `-m "mongo and not oracle" tests/connectors` | 64 passed, 1 skipped (by design: mongo has no composite keys) |
| `-m "mongo and oracle" tests` | 10 passed |
| `-m browser tests/console/test_console_pages.py` | 9 passed |
| `tests/test_deck.py`, `test_e2e_demo.py`, `test_demo_seed.py`, `test_demo_docs.py` | 43 + 35 + 10 (+ docs) passed |

## Side effects handled, and the state I left

- `-m "mongo and oracle"` reseeds the Oracle and MongoDB sources and rewrites 54 committed
  `docs/superpowers/build/m3-shots/*.png`:
  - The PNGs were written 03:13:10–03:13:20, during my run. I restored them with `git restore` and
    the worktree is clean.
  - `tools/demo_seed.py` then reseeded both sources to 20 000 customers, seed 7. The demo state is
    intact.
- The unit suite reuses the running demo site, and its positive-control tests move `/__received`.
  This happened after R3, and S9 measures its own deltas, so it does not affect the rehearsals.
- No unit-suite flake this time (the reviewer's N7 did not recur).
- Stack stopped with `docker compose --profile full stop`; volumes kept, this lane only. When it
  stopped, MongoDB's shutdown checkpoint will have rebuilt the tree as before, so the target will
  probably pass a plain `$sample` again on the next start. To recreate the 28799 state, use
  `logs/rewrite.py crm_uat profiles_demo 1` (one server-side rewrite), or the deterministic
  integration test.
- I did not run `tools/dev/autopush.sh` by hand. The project rule is no push without the owner's
  request, and the owner's LaunchAgent pushes checked commits every 10 minutes.

## Not done (non-blocking, for the owner or a later round)

N2 (S8 target assertions, `rows_written` equal to the source rows, failing closed on a post-sample
error), N3, N4 (slide 5 lede and policy), N5 ("obfuscate into in place" label), N6 (`sldSz` type),
N7 (flake ticket), N9 (policy-name gap), N10 (v0.2.0 until Task 28).
