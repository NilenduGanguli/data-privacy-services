# S1 — main after the M4 merge — post-merge verification FIX round 1

**Verdict: all 9 blocking findings fixed, test-first, on main; stack rebuilt and restarted on the final
code; every gate green.**

| | |
|---|---|
| Date | 2026-10-15, 22:18–23:52 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, branch `main`, from `8ea220a` |
| Commits | `f35bb21` scanner selects (B1/A1, B2/A2, B3) · `a0027b2` cancel counts (regression B1) · `4f0aac4` console B1–B3 · `39405d5` console B1 wording found by the live check · `1ce701d` cancel finalize fences out expired holders (follow-up of `a0027b2`). All on `origin/main` (pushed by the checked auto-push, `1ce701d`). |
| Author | NilenduGanguli &lt;nilendu.ganguli@gmail.com&gt;, no trailer, explicit-pathspec commits |
| Stack | compose project `data-privacy-services`, default ports; app + worker + browser-worker rebuilt (`data-privacy-services:local`, `data-privacy-services-browser:local`) and restarted; all 11 services healthy, `/readyz` ready. **Left running** (the prompt asks for a rebuilt, restarted stack for re-verification). |
| Code identity | the 151 tracked `dps/**/*.py` + `pyproject.toml` files hash the same in the tree and in `app`, `worker` and `browser-worker` (0 differing) |
| Values | only made-up values in tests and probes; counts, ids, states and needle names printed; no customer value, key, seed or token |
| Probes / logs | this folder: `S1-M4-fix-r1/` (`prefix/` = `git archive 8ea220a` + the new tests, for the failing-first runs; `live_cancel.*`; `console/` = the console lens's probes re-run with this agent's private Playwright venv `~/.cache/dps-build/pw-s1-m4-fix`, static key; `smoke.txt`; `gate_*.txt`) |

## 1. Scanner — captures-privacy r1 B1–B3, adaptations r1 A1–A2 (`f35bb21`)

| Finding | Cause | Fix |
|---|---|---|
| **B1 / A1** (regression of `40d8b34`) `<option label="" selected>` drew its text but was neither classified by it nor masked, even under "Account number" | `EXTRACT_JS` `selectDrawn` pushed `o.label` only; the IDL attribute is `""` when the attribute is present and empty | `selectDrawn` reads `o.label \|\| o.text` (HTML's "label of an option", as `OptionFacts.drawn`); a whitespace-only label is a label and draws nothing (control) |
| **B2 / A2** a real value starting with pick/please/select/choose/`--` behind an empty value, a disabled option or an option past the 300 listed was treated as "Choose…": boxed but not swept | `PLACEHOLDER_OPTION` prefix match in `draws_placeholder` | `draws_placeholder` is exact: every drawn string is the label of a **listed** selected option with an empty/blank value (disabled or not) and is, whole, a short placeholder phrase (`PLACEHOLDER_TEXT` / `is_placeholder_text`: a choosing verb, "please", filler words, punctuation; ≤ 40 chars, no digit, no "@"); a valued option (disabled or not), text beyond the listed labels, a customizable select and a listbox are never placeholders. The fill keeps its broad prefix rule (it only decides which option the fill never picks). |
| **B3** (pre-existing since M4) a listbox with nothing selected was never boxed, even when classified ACCOUNT_NUMBER / PERSON_NAME | `shown_texts` returned only selected options | `FieldFacts.listbox` (`el.multiple \|\| el.size > 1`); a listbox's `drawn` is every option's label (no 300 cap; the text budget still withholds); boxed whole when classified or a detector hits an option, whatever is selected; every option swept |

Docs: `api-m4.md` ("when it is not empty", listboxes, the exact placeholder rule); `M4-DEVIATIONS.md` entry.

**Tests first** — `tests/scanner/test_capture_select_shapes.py`, 42 tests (made-up values, counting server):

| Run | Result |
|---|---|
| On `8ea220a` (`prefix/`, `git archive`) | **37 failed, 5 passed** (the passes are the controls: `ph_control_copy`, `ph_valued_copy`, `listbox_names_selected` OCR, the whitespace-label OCR and the selected-listbox pixel test) — `scanner_prefix_fast.txt` 13 failed / 1 passed, `scanner_prefix_ocr.txt` 24 failed / 4 passed |
| On the fix | **42 passed** (`scanner_fixed_ocr.txt` 28/28 OCR; 14/14 fast) |

Coverage: `draws_placeholder` unit cases ("Pickering …", "Pleasence …", "-- Ottoline … --", "pickering…@…" with an empty value; disabled with a value; past the 300 listed; real placeholders stay placeholders); sweep of such values; listbox `shown_texts`/classification/box/sweep; **real Chromium extraction** (label="" under "Account number", "Beneficiary name" and a neutral label with and without a value → classified by value and masked; whitespace control; listboxes incl. 400 options); pixel test (0 select-background pixels in the masked capture, 10 pages); **OCR** of the masked capture and both obfuscated captures (17 pages incl. a customizable select without a button and a same-origin frame; controls read 0 before and after); **crawler** (`ScanRunner`, both modes): every stored screen (`original`, `annotated`, both `filled_*`, `masked_capture`) reads 0 on 4 pages incl. a fill that keeps or picks a label-less option. Every plain control screenshot reads its values; 0 non-GET requests at the counting server.

Demo scan unchanged (smoke): 21 pages, 63 fields (51 personal), 3 captures (3 masked), POST 60 + GET 1 blocked, `non_get_allowed` 0.

## 2. Cancel counts — regression r1 B1 (`a0027b2`)

| | |
|---|---|
| Cause | `jobs.cancel` flips running partitions to `canceled`; `checkpoint()` was fenced on `state='running'`, so the holder's batch already committed to the target (and its ledger mark) was abandoned uncounted, and a ledgered cancel finalize cleared the ledger |
| Fix (a) | `checkpoint()` fenced on the holder (`locked_by`) and `state IN ('running','canceled')`: the holder records its committed batch under its lease (usage charged in the same transaction), then `partition_control` → `cancel` → abandon; `_finalize` already waits for live canceled leases. Another worker, and an abandoned partition, still match nothing. |
| Ledger cross-check | `_clear_ledger` reads each canceled partition's ledger mark before clearing; a mark ahead of the checkpoint (holder died / lost its lease between write and checkpoint) is recorded first (`jobs.record_ledger_batches`, only when no live holder owns the partition, usage charged); the finalize re-reads partitions and job progress for the pack |
| Fence (`1ce701d`) | once no canceled partition holds a live lease, the cancel finalize releases the canceled partitions whose holder's lease ran out (as the reaper does) before it reads the counts, so a holder that wakes after the pack is sealed cannot checkpoint into it (progress stays = pack). Without a ledger, a batch whose holder died before checkpointing is recorded nowhere but the target: the pack counts checkpointed batches (crash limit of a non-ledgered load, recorded in `M2-DEVIATIONS.md`). |
| Contract | §3.1 amendment 2026-10-15 + §3.2 signature; `api-m2.md` (Cancel); `M2-DEVIATIONS.md` entry |
| Tests first | `tests/connectors/test_run_cancel_counts.py` (writer patched to cancel right after it commits; 2 partitions running): PG `table` load; ledgered `in_place` run; ledgered run whose holder dies before its checkpoint (lease expired). Pack = job progress = Σ partitions = target rows (or rows changed in place), ledger cleared, `dps_tenant_usage` delta = those rows. **On `8ea220a`: 3/3 failed** (0 vs 400, 200 vs 600, 0 vs 400); fixed: 3/3 pass. A 4th test (stale `table` holder waking after the cancel finalize is refused; progress = pack) **failed on `a0027b2`**, passes with `1ce701d`. `tests/connectors/test_clear_ledger.py` pinned `_clear_ledger`'s old return shape (the whole suite's only 3 failures, §5): `parts` is optional again, the test pins `{"deleted": 3, "recovered": 0}` and gains an offline cross-check case (a canceled partition's mark ahead → recorded; equal mark / done partition → left alone). `tests/db/test_partitions_queue.py::test_partition_control_and_cancel` encoded the defect (asserted the holder's checkpoint fails after cancel): now asserts the holder records, another worker cannot, nobody after abandon. |

**Live re-check on the rebuilt stack** (`live_cancel.py`; `live_cancel.run1.json` on `39405d5`'s images, `live_cancel.run2.out` / `.run3.out` on the final `1ce701d` images; cancel sent once ≥ 3 000 rows were checkpointed) — identical figures on both image sets:

| Run | job progress | pack `counts.rows_written` | Reports row | target `count(*)` | other |
|---|---|---|---|---|---|
| PG `bank.customers` (20 000 rows) → `bank_uat.customers_s1m4fix1_cxl` | 4 000 | 4 000 | 4 000 | **4 000** | partition `batch_no` 4, canceled, attempts 1 |
| Oracle `CRM.ADDRESS_HISTORY` (key-less, ledgered) → `CRM_MASKED.ADDR_S1M4FIX1_CXL` | 4 000 | 4 000 | 4 000 | **4 000** | ledger rows of the job 0, pack `write_ledger_cleared: true` |

Before the fix the same shape read 3 000 vs 4 000 (regression report §7). Run 2's Oracle leg found `CRM.ADDRESS_HISTORY` at 400 rows (the M3 tests of the whole suite reseed the Oracle demo tables at test size) and simply succeeded (400 = 400 = 400 = 400); after `tools/seed_sources.py --target all` (20 000 customers, 40 000 address rows) run 3 canceled it as above. Objects left on main's stack: those two target tables (`bank_uat.customers_s1m4fix1_cxl`, `CRM_MASKED.ADDR_S1M4FIX1_CXL`).

## 3. Console — B1–B3 (`4f0aac4`, `39405d5`)

| Finding | Fix |
|---|---|
| **B1** job page / wizard deep link of a job whose connection was deleted: 404 console errors, head "— · bank.customers" | new `lib/paging.ts` `resolveConnections` reads `GET /connections` (a list, `api.connections.list` takes a cursor) — never by id; job detail, the wizard `?snapshot=` link and the job-group hook use it. A deleted connection is "connection deleted (c_…)"; the head's engine falls back to the snapshot's; a proposed snapshot whose connection is gone offers no "Review and accept" and says why; the wizard explains without a failing request. The delete rule is unchanged. |
| **B2** Jobs showed the newest 200 jobs as all of them | `loadJobsWindow` pages `GET /jobs` through the 7 days the head names (200/page, cap 25 pages, re-read every 30 s; the newest page re-read every 5 s while a job runs); "Showing N of the newest M jobs · counts are over these" + "Load older jobs" when older pages remain; a Status filter over an incomplete list asks the server (`GET /jobs?state=…`) |
| **B3** evidence Summary scrolled sideways at 390 px | `overflow-wrap: anywhere` on `.evidence-page .lead` (screen and print) and `dl.kv dd` |

**Tests first:** `src/lib/paging.test.ts` (8: two-page fixture with failed jobs on page 2 listed, counted and searchable; window stop; cap; state query; the documented query strings; list-only connection lookup), `src/pages/JobDetail.test.tsx` (3: static render of the head and the snapshots card for a deleted connection, wording once), `EvidencePack.test.tsx` (+1: a 62-char target and 56-char policy inside the lead), `tests/evidence_layout.test.ts` (2 node tests: the CSS rules). With `8ea220a`'s sources swapped in: all failed except the lead-content render (paging module missing, 3 JobDetail, the CSS rule).

**Live (rebuilt stack, private Playwright, `console/logs/*.json`):**
- `repro_deleted_conn.py` (creates and deletes a connection; 1440 dark, 390 light): **0 console errors, 0 HTTP errors**, head "PG PostgreSQL · bank.customers / Discovery · connection deleted (c_01M50A…4W)"; `repro_wizard_link.py`: no review link offered, 0 events.
- `fix_deleted_conn_pages.py`: the repro job, `j_01M4XKWTTXT9AMM41Y3G6SQFWP` (MongoDB run: "MDB MongoDB · people → people_idem_x4b5d19") and `j_01M50433D1QPEPCB464WA6F48K`, plus all three wizard deep links, at 1440 dark and 390 light: **0 console errors, 0 HTTP errors, 0 failed requests**, no horizontal scroll; the wizard reads "Could not open the snapshot: its connection was deleted (c_…). Start a new discovery …".
- `jobs_counts_probe.py` / `b2_jobs_evidence.py`: API 1 011 jobs, 988 table jobs, 27 failed table jobs → page **"Showing 988 of 988 jobs"**, Status **"Failed (27)"**, "Canceled (7)" = API; the Failed filter lists the 14 Oct runs and a search for `j_01M4XD46RY4DE9TGKM2RE97D52` finds it. Overview "28 failed" = the 27 + 1 failed UI scan (scans are listed in the UI scanner, not Jobs).
- `resume_lead_overflow_u.py`: the three 38-character packs (`…VRQVDF`, `…T5E4ZF`, `…V538T1`): **scrollX 0 at 390, 375 and 360** (was 2/17/32 px); the bold name ends 33–42 px inside the viewport.

## 4. Gates

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check` / `mypy dps` | 437 files formatted / all checks passed / no issues in 150 files |
| `-m browser` (M4), `DPS_TEST_TESSERACT_CMD` = the app image's tesseract (own `--network none` container) | **310 passed**, 0 failed (21:09) — demo counters +8/+2/+1/+1 exactly (the deliberate positive controls; 16/4/2/2 → 24/6/3/3) |
| whole suite except browser (`-m "not browser"`, `DPS_TEST_REQUIRE_SERVICES=1`, tesseract wrapper) | **3 330 passed, 8 skipped, 3 failed** (10:37) — the 3 failures were `test_clear_ledger.py` pinning `_clear_ledger`'s old shape (§2), fixed in `1ce701d`; then `tests/connectors tests/db tests/test_quota.py` re-run on the final code: **845 passed, 1 skipped** (Mongo composite keys, by design). The 8 skips are the known ones (spaCy ×2, pandas/mostlyai extras ×3, host tesseract ×2, Mongo composite ×1). |
| frontend: `npm test` (node + Vitest), `npm run build` (tsc + vite + check-bundle) | node **46/46**, Vitest **30 files, 389 passed**; build OK, no dev fixture code |
| `DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 tools/smoke_test.py --m3` | `39405d5` images: **all 52 passed** (`smoke.txt`); final `1ce701d` images, reseeded sources: **all 52 passed** (`smoke2.txt`) — scan 21 pages / 63 fields (51 personal), 3 captures (3 masked), POST 60 + GET 1 blocked, `non_get_allowed` 0; M3 runs 20 000/20 000/20 000, consistency 3/3, evidence 3/3 verified; D4 counters +0 (24/6/3/3 before and after); `dps_llm_raw_pii_sent_total` 0 |
| Code identity (final images) | 151 tracked `dps/**/*.py` + `pyproject.toml` files: 0 differing in `app`, `worker`, `browser-worker` |

The browser gate's console screenshot tests rewrote the 54 tracked PNGs in `docs/superpowers/build/m3-shots/` (known A-N2); they were restored from HEAD (`git checkout --`), nothing of them committed.

## 5. State left behind

- `main` = `origin/main` = `1ce701d`, clean tree (the 54 `m3-shots` PNGs the browser gate rewrote were restored from HEAD).
- Main's stack **running** on the final images (all 11 services healthy, `/readyz` ready), demo sources reseeded to the
  20 000-row data model (they were at Mongo/Oracle test size when this round started; the M3 tests shrink them).
- Objects created on main's stack: the two cancel targets above, the console repro's connection `s1m4c1-tmp-<hex>`
  (deleted by the repro, as designed) and its discover job, the smoke runs' objects.
- The private tesseract container `s1m4fix-r1-tess` is removed; the Playwright venv `~/.cache/dps-build/pw-s1-m4-fix` stays.

## 6. Notes

- Not changed (non-blocking items of the lenses, owner decisions pending): captures-privacy N1 (persona fill values in Cookie/Referer headers), N2 (persona OTP drawn in filled `/login` screens), N3 (a `visibility:hidden` customizable select with a visible button).
- "Select your country"-style placeholders are now treated as values (swept, and they take an obfuscated capture): over-masking, accepted (recorded in `M4-DEVIATIONS.md`).
- Re-check commands: `tests/scanner/test_capture_select_shapes.py` (with `DPS_TEST_TESSERACT_CMD`),
  `tests/connectors/test_run_cancel_counts.py`, `S1-M4-fix-r1/live_cancel.py [pg|oracle]`,
  `S1-M4-fix-r1/console/run.sh {repro_deleted_conn,fix_deleted_conn_pages,jobs_counts_probe,b2_jobs_evidence,resume_lead_overflow_u}.py`.
