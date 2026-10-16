# S6b review, round 1: M5 Task 26 (ten-minute demo) and the showcase deck, lane `fdemo`

**Verdict: FAIL.** There are 3 blocking findings. My first full rehearsal passed every step (12/12 PASS in
2:59). The demo then failed twice on the same stack. The cause is a product defect in the MongoDB
connector (B1), and the next rehearsal hits it every time. Two demo assertions also need fixing.
The S2 synthetic-leak check cannot see the nested card values (B2). The S7 join is not tied to the
run's own jobs, so the documented `--skip-oracle` fallback cannot pass on a stack that has already run
a full rehearsal (B3).

The deck itself checks out:
- It is a byte-identical rebuild of the implementer's saved rehearsal.
- Every number on it traces to the report or the bench files.
- It holds no keys, tokens or values.
- It uses no chart parts and only Helvetica Neue and Arial.

| | |
|---|---|
| Reviewed | `/Users/neelu/document_intelligence/dps-wt-fdemo`, branch `fdemo` at **`493f528`** (S6b commits `764ad6a`, `37ec781`, `493f528` on `2b31b7a`) |
| Implementer report | `/Users/neelu/.cache/dps-build/reports/S6b-impl.md` |
| Evidence | `/Users/neelu/.cache/dps-build/reports/s6b-review-r1/` (`logs/`, `rehearsal/` = my full run with 18 screenshots, `skip-oracle/`, `rerun/`) |
| Repository files modified | none (outputs went to the evidence directory and the session scratchpad; `var/demo` was reused, not rewritten) |
| Date | 2026-10-16 00:19 – 01:00 IST |

## 1. Gates run by me

| Gate | Result |
|---|---|
| `ruff format --check` / `ruff check` (`dps tests tools`) | 451 files formatted, all checks passed |
| `mypy dps` and mypy of the 7 demo/deck tools and tests | clean (150 and 7 files) |
| `docker compose --profile full build` then `up -d` (this lane) | built from HEAD: the app image was rebuilt because `tools/` had changed since the 22:55 image; browser and OCR images came from cache. 11 services healthy; one-shots exited 0. |
| `demo_seed.py` | ready; all reused (3 keys, 3 sources at 20 000 rows, 7 connections, `retail-kyc` v2, group). 1.6 s. `var/demo/*.json` mode 600. Stdout prints names and stored prefixes only. |
| **`e2e_demo.py --console`** (my run, outputs under `rehearsal/`) | **12/12 PASS, demo flow completed in 2:59** (API path 2:46). Summary of facts: S2 12 findings; S4 2 OCR pages, 0 characters, 0 originals; S5 PG 8/16, Mongo 10/17, Oracle 11/16; S6 3 × 20 000 rows, 4/4 partitions each, verified, kid of the group; S7 3/3 pairs, 500/500; S8 fidelity 0.9588, exact 0, NNDR 1.6024, MIA 0.4796; S9 21 pages, 63 fields, D4 +0 on non_get/form_get/logout/admin, get +175, 60 sends blocked; S10 5 packs, 32 audit rows, 0 values; S11 posture 5/11, rotated key 401, retention succeeded; S12 18 screenshots, 0 errors. |
| D4 counting server | demo site `/__received` before: non_get 0, form_get 0, logout 0, admin 0, get 0. After: 0, 0, 0, 0, **get 175** |
| LLM counter | `dps_llm_raw_pii_sent_total` 0.0 before, 0.0 after |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m browser tests/console/test_console_pages.py` | **9 passed** |
| `pytest tests/test_deck.py tests/test_demo_seed.py tests/test_e2e_demo.py` | **75 passed** (43 + 10 + 22) |
| Unit suite `pytest -q -m "not integration"` | **Green, apart from one pre-existing flake (N7).** Run 1 (`-x`) stopped after 2585 passed on `test_ndjson.py::test_one_audit_event_per_stream`, which then passed 25/25 alone. Run 2 (full): 3263 passed, 86 skipped, plus 45 failed and 145 errors. Those 190 all come from 20 DB-backed files that lost the metadata DB when I stopped the stack mid-run; that was my error, not a code fault. With `dps-db` up, those 20 files: **265 passed, 2 skipped** (`logs/pytest-unit-2-rerun.log`). |
| Whole integration suite | **not run.** It reseeds the sources to 200 rows, which would disturb the demo state. `dps/` is unchanged since `2b31b7a`, and the implementer reports 3714 passed there in the `fimg` merge; I did not re-verify that. The integration tests this change adds (console, 9) pass. |
| `demo_seed.py --reset-jobs` (documented reset path, live) | 0 unfinished jobs canceled; retention succeeded (its audit row); retention back to 30 days |
| Deck rebuild | `build_deck.py --strict` from the implementer's saved rehearsal (`~/.cache/dps-build/s6b/rehearsal/`) gives a file **byte-identical** to the committed `docs/demo/dps-showcase.pptx` (sha256 `d965867f…`). The worktree's `reports/e2e-demo-report.json` equals the saved one. |
| Slide numbers vs report | Every number on slides 1–12 occurs in the rehearsal report JSON or in `docs/technical/bench/*.json`, after rounding. The only exception is "15 18" from the footer timestamp, which is the report's own `generated_at`. |
| Deck values and keys | Every XML part was searched for the 3 demo keys, the group DEK and seed, the bootstrap key, the compose KEK, the tokenizer token, the S2 sample values, the demo-site values and the SSN: 0 of each. Also 0 raw `dps_` keys and 0 `tk_` tokens. `docProps/core.xml` carries no person (creator "Data Privacy Services"). |
| Keynote safety | No `ppt/charts`, embeddings, `graphicFrame`, `AlternateContent`, SmartArt or OLE. Typefaces are only Helvetica Neue (334), Arial (13) and theme references. The LibreOffice PDF embeds HelveticaNeue and HelveticaNeue-Bold only. |
| Keynote PDF export | **Not possible headless**, same as the implementer. `osascript` open → AppleEvent -1712. Keynote shows one untitled window, 0 documents. System Events has no assistive access. |
| Visual QA | Own LibreOffice headless render, all 12 pages read. No overflow or clipping. 8 bench bars and every diagram are drawn. The 4 screenshots are in place. |
| Screenshots | OCR (the image's tesseract 5.5.0) of my 18 console screenshots found 0 demo keys, 0 group DEK or seed, 0 bootstrap key, 0 raw `dps_` keys, 0 `tk_` tokens and no readable "dek" line (the mask works). The only demo values are the committed fictional sample record in the Playground input pane, which is intended. The deck's 4 images are byte-identical to the implementer's rehearsal screenshots. I viewed all 4. |
| Reports and logs | Searched my and the implementer's report JSON/MD, the seed and e2e logs, DEMO-SCRIPT, HANDOFF, M5-DEVIATIONS and the new tools and tests for the probes above. 0 demo keys, DEK, seed or values. The only hits are committed local literals (bootstrap key, tokenizer token), metric names and the fictional SSN or sample fixtures in tests. |
| Git | 3 commits by `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers, conventional messages. The worktree is clean (`reports/` and `var/` are git-ignored). |

## 2. Blocking findings

### B1. MongoDB `$sample` error 28799 breaks the demo after a few rehearsals on one stack (product, `dps/layers/connectors/mongo.py`)

`MongoConnector._sample_docs` (mongo.py:413) issues `[{"$sample": {"size": n}}]` with no retry or fallback.
On a collection that has been rewritten several times by `truncate_insert`, MongoDB's pseudo-random
cursor fails with **28799** ("could not find a non-duplicate document"). The connector maps that to
`source_error`.

Reproduction on this lane (`logs/mongo-sample-repro.txt`, counts only):
- `crm_uat.profiles_demo` holds 20 000 docs with 58.9 MB of storage for 33.5 MB of data. A `$sample`
  of 500 **failed 10/10** with 28799.
- Samples of 10, 200 and 2000 pass. 2000 is ≥ 5% of the documents, which avoids the random cursor.
- The source `crm.profiles` passes at every size.

Effects:
- Run 1 `e2e_demo --console`: all PASS.
- Run 2 `--skip-oracle --skip-scanner`: S7 ERROR, `consistency-check` → **502 `source_error`**. Every
  explicit pair that contains a MongoDB member then returned 502 (8/8), while Oracle + PostgreSQL
  passed.
- Run 3, a plain full run (`--skip-scanner`): **S6 FAIL, "mongo run failed source_error"**. The worker
  logged `mongo:28799 op=sample` at prepare, because `describe` samples `min(500,
  discovery_sample_rows)` documents of the target.
- Run 2's MongoDB pack also shows the same failure in a best-effort path: `post_sample: {"error":
  "source_error"}` with `verification.passed: true` and `review_required: false`.

The presenter script expects repeated rehearsals ("Reset between rehearsals", the T−30 dress rehearsal,
then the live "Open consistency check"). On this stack every rehearsal now fails at S6.

**Fix:**
1. In `_sample_docs`, retry the aggregate on `OperationFailure` code 28799 (MongoDB itself calls it
   sporadic).
2. If it persists, fall back to a pipeline that cannot use the pseudo-random cursor. The server uses
   that cursor only when `$sample` is the first stage, N < 5% of the documents and there are more than
   100 documents. Verify the fallback with `explain` that no random cursor is used, and keep
   `maxTimeMS`.
3. Add a unit test with a fake collection that raises 28799 and then succeeds or falls back.
4. Add a `mongo` integration test that reproduces the state, for example by rewriting a collection
   several times or by deleting and reinserting most of it, then calls `describe` and `sample(500)`.
5. Re-rehearse **twice in a row on this lane's stack without `down -v`**. Its MongoDB target is in the
   failing state now and is the reproduction. Both runs must PASS.

### B2. S2's synthetic-leak assertion cannot see nested values, including the CRITICAL card number (`tools/e2e_demo.py:435-443`)

`detected` is built as `str(record[key])` for each finding's top-level key. For the sample's `card`
object (findings `CARD_PAN` at `$.card.pan` and `CARD_EXPIRY` at `$.card.expiry`), that string is the
Python dict repr `{'pan': …, 'expiry': …}`. It can never occur in `json.dumps(result)`.

So the step checks the card number and expiry against nothing. Its detail "synthetic carries 0 of 10
detected values" counts that dead probe as one of the 10. A probe of the live stack confirmed it: 1 of
the 10 probes is a dict repr. The product is fine: in 5 `/synthesize` calls a per-leaf check found 0
card leaves. This is exactly the class "a demo step that skips its assertion". No test covers S2's
probe set.

**Fix:**
- Probe every string leaf of every detected top-level field, using the existing `leaves()` the way S10
  does, for example via a small pure helper `detected_values(record, findings)`.
- Unit-test it: a nested card PAN must be a probe, and a synthetic answer echoing it must fail.

### B3. S7 checks whatever the group wrote, not this run's jobs; the documented `--skip-oracle` fallback cannot pass after a full rehearsal (`tools/e2e_demo.py:671-690`)

S7 posts `{"sample": 500}` only, so the server derives one member per distinct target the group ever
wrote, using the newest job (`consistency.py:derive_members`). The plan's S7 says "(customer id path
per job)", and the API accepts explicit `pairs` (`job_id` + `path`). This deviation is not in the
deviation log.

Consequences:
- **(a) Wrong jobs.** The members are not asserted to be the S6 jobs. They happened to be the S6 jobs
  in my full run.
- **(b) `--skip-oracle` fails.** On a stack whose group has ever had an Oracle run (any earlier full
  rehearsal), `--skip-oracle` gets 3 derived members, and `check(len(members) == n)` fails "3 members
  (2 expected)". This is by code reading: in my run B1's 502 came first. The DEMO-SCRIPT offers that
  flag as the fallback when Oracle is slow.
- **(c) Old targets.** An old member whose target no longer exists (Oracle has no volume, so `down`
  resets it) makes the derived check fail. This is by code reading.

**Fix:** S7 posts `pairs`, one per S6 job: its `job_id` and the customer-id column path of the snapshot
S5 accepted for it. Examples on this stack: `$.customer_id` (PG), `$.customerId` (Mongo),
`$.CUSTOMER_ID` (Oracle). Also assert that the result's member job ids equal the S6 job ids, and add
a unit test for building the pairs.

After B1 to B3, re-rehearse with `--console`. Then rebuild the deck `--strict` from that rehearsal and
commit it, so its footer names a commit whose `e2e_demo.py` has the fixed assertions. Check the result
with `tests/test_deck.py` and the byte-identical rebuild.

## 3. Non-blocking findings (recommended)

- **N1. S12 failure path.** Playwright's `TimeoutError` and `Error` are not caught by `run_steps`
  (`except (KeyError, IndexError, TypeError, ValueError, httpx.HTTPError)`); I confirmed this with a
  simulation. If a console heading does not show within 15 s, which is S12's own assertion, the whole
  run crashes with a traceback and **writes no report**. The previous `reports/e2e-demo-report.json`
  stays behind for `build_deck.py --strict`. Map any other exception of a step body to `ERROR` by class
  name (5 lines) and add a test. Fix it in the same round if possible.
- **N2. S8 targets and S6 checks.** S8 asserts fidelity, exact matches and "NNDR measured", but not
  `nndr.ratio ≥ nndr.target` or `mia.auc ≤ mia.target`, which slide 9 and the script present as
  targets. S6 does not assert `rows_written` equal to the source rows. S6 accepts a pack whose
  best-effort `post_sample` errored. On the product side, a failed post-write sample leaves
  `review_required` false; consider failing closed or surfacing it in the pack.
- **N3. S10 scope.** The S10 body scan probes the S2 record, the SSN and the demo-site values, not
  source-table values. The implementer's offline privacy scan covers logs and artefacts.
- **N4. Slide 5.** The screenshot runs the console default `gdpr-pseudonymise`, while S2 asserts
  `@retail-kyc`. Its output shows `pan` and `address` as base32 strings and the card expiry unchanged,
  so the lede "obfuscation keeps formats" overstates for those fields. Reword it, or select
  `retail-kyc` in the walk if the selector lists it.
- **N5. Slide 7 wording.** The bench labels read "obfuscate into in place" and "into files", a grammar
  slip from `build_deck.py`'s label (S6a).
- **N6. Slide size type.** `ppt/presentation.xml` has `<p:sldSz cx="12191695" cy="6858000"
  type="screen4x3"/>`: a 16:9 size with the template's 4:3 type label. LibreOffice renders it 16:9.
  Omit the type or set it to custom.
- **N7. Unit-suite flake.** `tests/test_ndjson.py::test_one_audit_event_per_stream` failed once
  (outcome `verification_failed`, hits 1) and passed 25/25 in isolation. It is pre-existing: neither
  `dps/` nor the test changed in this stream. It is likely a random-key collision in the leakage
  sweep; worth an owner ticket.
- **N8. Keynote.** The Keynote PDF review stays with the owner, as the implementer said. My scratch
  `open` of a copy of the deck may complete when someone dismisses the Keynote dialog; that is
  harmless.
- **N9. Policy-name gap.** The product gap reported by the implementer is consistent with the code:
  `jobs_run.py:276` `ctx.engine.policy(spec.policy or name)` with `name = snapshot.policy_name`. It
  stays for the owner.
- **N10. Version.** Slide 1 prints v0.2.0; rebuild after Task 28 (known).

## 4. Acceptance (plan Task 26 / 26B)

| Item | Status |
|---|---|
| `demo_seed.py` idempotent; files 0600; value-free summary | met (live rerun; `--reset-jobs` live OK) |
| `e2e_demo.py --console`: all PASS, report and screenshots, < 10 min | met **once** (2:59); **not repeatable** on the same stack (B1); fallback flag broken (B3) |
| Every promise asserted | **not met:** S2 nested values unchecked (B2); S7 not tied to the run (B3); S8 targets (N2) |
| JSON report = deck contract; MD last line wall time | met |
| Console test 9 passed | met |
| DEMO-SCRIPT final | met (status, seed, recovery rows); the `--skip-oracle` advice fails after a full rehearsal until B3 is fixed |
| Deck from the real rehearsal, 12 slides, notes, no invented numbers, Keynote-safe | met (byte-identical rebuild, numbers traced, no chart parts, Helvetica Neue/Arial) |
| Keynote PDF review | not possible headless (owner) |

## 5. State left behind

- No repository file changed. `fdemo` is still at `493f528` with a clean worktree.
- The rebuilt `data-privacy-services:fdemo` image is from HEAD (`63f29f2c94ab`).
- The demo tenant now also holds my runs:
  - the full run: S6 `j_01M50ES3…`, S8 `j_01M50EV0BK…`, scan `s_01M50EVE3Q…`;
  - the skip-oracle PG and Mongo runs;
  - one **failed** MongoDB run, `j_01M50FRMD9G4G0YBB3023AHVSV`.

  The console's newest jobs include them.
- The MongoDB target `crm_uat.profiles_demo` is in the 28799 state, which is B1's reproduction.
  Do not `down -v` before the fix is verified.
- Stack stopped with `docker compose --profile full stop`. Volumes were kept; this lane only. `dps-db`
  was briefly restarted for the unit-test rerun and stopped again.
