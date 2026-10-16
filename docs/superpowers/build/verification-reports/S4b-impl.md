# S4b — Task 24 for the scanner (lane `fimg`): full profile, smoke scan with D4 proof, amd64 + arbitrary-UID images

**Verdict: DONE.** All of S4b's scope is committed on `fimg` and proven on this lane's `--profile full` stack:

- The `full` profile includes the browser-worker and the demo site.
- The smoke runs a scan in which the demo site receives 0 POSTs. The check now has two positive controls, so it cannot pass without proof.
- The app image and the browser image both build for amd64.
- Both images run as an arbitrary UID in group 0 with a read-only root: in the runtime tests on both architectures, and as the whole stack.

The one smoke failure in every run is `api responses are not cacheable`. That is M5 Task 17 S11 on lane `fsec`, not a `fimg` defect, and the check was not weakened.

The worktree is clean. `fimg` = `origin/fimg` = **`3f8af02`**, pushed by `tools/dev/autopush.sh`. The lane stack is stopped (`docker compose stop`).

| | |
|---|---|
| Worktree / branch | `/Users/neelu/document_intelligence/dps-wt-fimg`, `fimg`: `e74ba22` → **`3f8af02`** |
| Commits | `4eeac2a` merge main (earlier attempt) · `7561e89` fix: mocks unprivileged · `f39df5b` feat: D4 positive controls · `93d4b4d` docs: HANDOFF §17→§18 · `9db127f` test: runtime tests check the architecture · `5c54dda` merge main (`8ea220a`) · `4640324` docs: HANDOFF §18.2 + deviation log · `3f8af02` merge origin/fimg (push race, §6) |
| Author / trailers | all `NilenduGanguli <nilendu.ganguli@gmail.com>`, 0 trailers (checked with `%(trailers)`) |
| Date | 2026-10-15, 19:43–21:45 IST (the earlier attempt ran 10:40–11:13 and was paused) |
| Evidence | `~/.cache/dps-build/s4b/`: `logs/` (runs A, B, B2, C, C2, red runs, builds, scans), `probe/` (scripts), `flow-report-{A,B,B2,C}.md`, `m3-shots-from-run/`. The paused attempt's run logs are in `logs/paused-attempt/` |

## 1. State found at resume

- **9 uncommitted files, not 63.** The tracked screenshots had already been restored, and a copy sits in `s4b/m3-shots-from-interrupted-run/`.
- The uncommitted work was test-first changes. Their red runs were already logged in `s4b/logs/red-*.txt`:
  - D4 positive controls (`demo/onboarding/server.mjs`, `tools/smoke_test.py`, `tests/test_tools_m4.py`, `tests/scanner/test_demo_site.py`).
  - The OCR mock and demo site hardened (`docker-compose.yml`, `tests/test_compose_m5.py`).
  - The architecture check in the two image runtime tests.
  - HANDOFF edits.
- I read all of it, kept it, and finished it. One change: I tightened the D4 control (below).
- `main` had gained `8ea220a` (docs). It added "## 17. M4 integrated on main", which clashes with fimg's own §17 (Task 24).

## 2. Changes (each test first)

| Change | Red first | Green |
|---|---|---|
| **D4 positive controls** (`f39df5b`). The demo site's `/__received` now also counts the GETs it served: `get` covers pages and files, not `/health` or the counters, and the key is additive. "No submission reached the site" now also requires two things. First, `get` must move by **at least the scan's `pages_visited`** (and at least 1); the site sends `no-store`, so every visit fetches its page. Second, `blocked_requests` must hold at least one non-GET request. A site without `get` fails. The verdict prints counts only. I tightened the earlier attempt's "`get` moved" to "≥ pages visited": a few stray GETs on another lane's demo site must not pass. | 4 cases (`s4b/logs/red-test_tools_m4.txt`) and then `[20]` < 21 visited plus the verdict text (`red-test_tools_m4-visited.txt`); `test_received_counts_the_pages_and_files_it_served` (`KeyError: 'get'`) | `tests/test_tools_m4.py` 21 passed, `tests/scanner/test_demo_site.py` 8 passed |
| **Mocks unprivileged** (`7561e89`, review S4 r2 R2-4). `azure-ocr-mock` runs as `10001:0`, read-only, with a `/tmp` tmpfs of 128m, `cap_drop ALL` and `no-new-privileges`. `demo-site` runs as `user: node` (uid 1000), read-only, with `cap_drop ALL` and `no-new-privileges`. The data stores keep their own users, and `demo-secrets` stays root. | `test_the_local_mocks_run_unprivileged_with_a_read_only_root` | compose tests 23 passed; live, both healthy in every run (`roles-*.txt`) |
| **Runtime tests check the architecture** (`9db127f`). Under `DPS_TEST_IMAGE_PLATFORM` the probes must report `platform.machine()`: `x86_64` for amd64, `aarch64` for arm64. | `KeyError: 'machine'` (both tests) | 6/6 arm64 and 6/6 amd64 on the HEAD images |
| **HANDOFF renumbering** (`93d4b4d`). fimg's Task 24 record goes from §17/§17.1 to **§18/§18.1**, with every reference updated, before merging main. The merge `5c54dda` then kept both sections: main's §17, then fimg's §18. | — | the HANDOFF-reading tests pass (58) |
| **Docs** (`4640324`). HANDOFF §1, §2 (the amd64 browser build line, the architecture check), §3 (OCR mock row), §6 (mocks), §15 (demo-site row, smoke paragraph), and a new **§18.2** (S4b: changes, verification, traps). `M5-DEVIATIONS.md` gets an "S4b" entry under Task 24, marked finish-stage. | — | — |

The contract is unchanged. Nothing under `dps/` changed, so the product code equals main's `40d8b34`.

## 3. Gates (at `5c54dda`; only the docs changed after it)

| Gate | Result |
|---|---|
| ruff format/check `dps tests tools`; `mypy dps` | clean: 444 files formatted; mypy found no issues in 150 files |
| **Whole suite, every service up**: `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_TESSERACT_CMD=<app image tesseract> pytest -q` | **3688 passed, 14 skipped, 0 failed** (28 min). The skips: 1 Mongo composite-key case (by design), 2 that need a host tesseract, the 6 opt-in runtime tests (run separately), 3 MOSTLY/pandas, 2 spaCy. The run rewrote 54 tracked m3-shots (A-N2); I copied them to `s4b/m3-shots-from-run/` and restored them with `git checkout --`. |
| Frontend | `npm ci`; 44 node tests + 377 Vitest tests; `typecheck`; `build` (bundle check clean) |
| Images at `5c54dda` | `docker compose --profile full build` (arm64). `docker build --platform linux/amd64` for `data-privacy-services:fimg-amd64` (1.17 GB) and `data-privacy-services-browser:fimg-amd64` (4.38 GB). Every layer was cached from identical inputs except `COPY tools`. Each image is user `10001` with the entrypoint script (`images-HEAD.txt`). |
| Runtime tests, both images | arm64 **6 passed**. amd64 (`DPS_TEST_IMAGE_PLATFORM=linux/amd64`, emulated) **6 passed**, including the cross-image case: the app image reads, streams and replaces what the browser image stored. |

## 4. Stack runs (`down -v` first; 15 services, browser-worker and demo-site included)

| Run | Roles | Result |
|---|---|---|
| **A** | arm64; every role uid 10001; read-only, `cap_drop ALL`, `no-new-privileges`; umask 0002, gid 0 | ready 29 s, browser 1 online. Smoke full gate (`DPS_SMOKE_SCANNER=on DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 … --m3`): **59/60** (S11 only). D4: `non_get +0, form_get +0, logout_hits +0, admin_hits +0; pages served +175 (21 visited); non-GET blocked 60; non_get_allowed 0`. Flow `--require-scanner`: **PASS 92/0**, with the 10 scanner calls. The fresh demo site had received **0** non-GET requests in total. |
| **B** | the **amd64** builds (emulated) of both images as **1000770000:0**, set by an override on app, worker, migrate, tokenizer-mock, seed-sources and browser-worker; read-only; processes run `x86_64`, gid 0, umask 0002 | ready 33 s; migrate and seed-sources exited 0. All **117** objects of run A answered **200**, including 76 screenshots and 3 captures the arm64 browser-worker stored as uid 10001; so did the 17 packs, html and pdf. Smoke **59/60** (S11), D4 `+175 (21 visited)`, 60 POSTs blocked. Flow **PASS 92/0**. |
| **B2** | as B, after reseeding Oracle and Mongo to 20 000 rows (the suite leaves 200) | smoke **59/60** (S11), with M3 runs of 20 000 rows on every engine and consistency 3/3; 473 s under emulation; 61 POSTs blocked. Flow **PASS 92/0**. Plus a probe scan whose DEK and seed were held in memory only. |
| **C / C2** | back to arm64 uid 10001 | Smoke with `DPS_SMOKE_SCANNER` **unset (auto)**: **59/60**, and the scanner block ran. Flow **PASS 92/0**. Finally all **526** objects (370 screenshots and 18 captures from every run's browser-worker) and all **48** packs (html and pdf) answered **200**. The volumes hold only `2775` directories (29 of uid 10001, 21 of uid 1000770000) and `660` files (248 and 297), all group 0. |

## 5. Proofs (counts only)

- **D4.** The demo site's non-GET counters moved only during the whole suite: +8 / +2 / +1 / +1. Those are exactly its own positive-control tests, as on lanes m4 and main.
  - They did not move during the 4 smokes, the 4 flows or the probe scan.
  - `non_get_allowed` was 0 everywhere.
  - `get` moved +175 per smoke (21 pages) and +21 per flow scan.
  - `dps_llm_raw_pii_sent_total` stayed 0 before and after every smoke.
- **OCR** of every stored screen of smoke scans A (arm64) and B (amd64), using the app image's tesseract with psm 11 and 3:
  - Filled screens (12 synthesize, 12 obfuscate): **0** demo values. The positive control is readable: personas gave 221/14 hits on A and 213/14 on B.
  - Masked captures (3 per scan): 0, except `application_ref` on `/apply/review`. That is M4's known N9: it is not a detected entity, and lane m4 and main see it too.
- **Logs and artefacts.** Scanned: the container logs of runs B2 and C1 (saved) and the data stores over the whole session; every saved tool output (161 sources, 22.4 MB); and the artefact and export volumes (1.0 MB). Results:
  - **0** hits of every needle: the sample record, the SSN probe, the bootstrap key, the tokenizer token and key, the demo KEK, the probe scan's DEK and seed, the demo site's values and cookie, 2 000 sampled values per personal column of PG, Oracle and Mongo, raw `dps_` keys and `tk_` tokens.
  - **0** `PermissionError`, `Read-only file system` or `storage_error`, and **0** tracebacks, including under the amd64 arbitrary-UID roles.
  - `dps-db` and `source-pg` log only the suite's deliberate refusals.
  - **Gap:** run A's role-container logs, which include the whole suite, were lost. The switch to run B recreated those containers before I saved them. That trap is now in HANDOFF §18.2.
  - The first B2 scan matched 17 needles in a saved copy of HANDOFF that documents literals and error names. I moved the copy out of the scanned tree, and the rerun was clean.
- **Source vs target** after B2:
  - `bank_uat.customers_smoke` (20 000 rows), `customers_flow` and `customers_flow_b` (500 each), Oracle `CRM_MASKED.CUSTOMERS_SMOKE` and Mongo `crm_uat.profiles_smoke` (20 000 each): **0** same-row survivors.
  - **0** target values equal to any source value for names, e-mails, mobiles, national ids, tax ids and notes.
  - Customer ids (4 in 20 000) and shifted birth dates match other customers' values. These are domain collisions only; the same-row count is 0.

## 6. Incident: auto-push race (resolved without force)

- The merge `2622429` was first committed with git's `# Conflicts:` comment lines in its message, because `--no-edit` does not strip them.
- The branch was unpushed when I amended the message (`--only`) to `5c54dda`. But the owner's auto-push loop pushed `2622429` at 20:22:48, inside the amend window.
- `fimg` was then held as "diverged" from 20:32 to 21:40.
- The two commits have the same tree and the same parents. I joined them with `3f8af02` ("merge: origin/fimg into fimg …"):
  - its tree equals `4640324`;
  - the one redundant HANDOFF conflict was resolved with `--ours`.
- I then ran `tools/dev/autopush.sh`, which pushed `fimg 3f8af02` at 21:42. No force-push, reset or rebase was used.
- History therefore holds two equivalent "merge: main into fimg" commits. That is harmless.

## 7. Open items and notes for the orchestrator

1. **S11** (`Cache-Control: no-store` on `/api/`) is the only red smoke check. It closes when `fsec` merges.
2. `mypy tools/smoke_test.py` still reports 3 pre-existing errors: 2 from main (N6) and 1 in M4's scanner block (`ident`). The gate is `mypy dps`. They are cheap F4 polish.
3. Release merge:
   - fimg's HANDOFF now has main's §17 plus fimg's §18/§18.1/§18.2, so the HANDOFF numbering is already reconciled with main.
   - `smoke_test.py`'s scanner block differs from the M4 version that `fsec` and `fllm` carry. Those branches did not change those lines, so the merge should take fimg's version cleanly.
   - fdemo's `build_deck.py` reads `posts_received` from the same counters. The new `get` key is additive.
4. Observed, not mine: the **main checkout** (`data-privacy-services`) has modified tracked `m3-shots` PNGs. Another stream is presumably running console tests there. I did not touch them.
5. The demo site now runs as uid 1000 with a read-only root. Anything that expects to write inside it would fail; nothing does today.

## 8. State left behind

- `git status` in the worktree is clean. `fimg` = `origin/fimg` = `3f8af02`.
- The lane stack is **stopped** (`docker compose --profile full stop`): 0 running containers, and the volumes are kept.
- Kept images: `data-privacy-services:fimg`, `:fimg-amd64`, `data-privacy-services-browser:fimg`, `:fimg-amd64`, `document_intelligence-azure-ocr-mock:fimg`.
- Only this lane's compose project and worktree were touched. The local `.env`, `.env.lane` and `lane.sh` were reused unchanged.

## 9. Re-invocation, 2026-10-15 22:03 IST (verification only)

The workflow started this job again after a restart. Nothing was left to do, so nothing was redone:

- The worktree is clean. `fimg` = `origin/fimg` = **`3f8af02`**. `fimg..main` is empty: `fimg` already contains main `8ea220a`. No new commits.
- The lane stack was **running**. The interrupted **S4b review r1** had brought it up (`down -v`, then up at about 21:54; its evidence is in `reports/S4b-review-r1/`). Its last request reached the app at 22:02:52, and the stack had been idle since then.
- The partial evidence from that review independently confirms this report at `3f8af02`:
  - **amd64 builds without cache**: the app and browser images both exit 0.
  - **Roles**:
    - app and browser-worker run as uid 10001, gid 0, read-only, `cap_drop ALL`, `no-new-privileges`;
    - the OCR mock runs as `10001:0` with a read-only root;
    - the demo site runs as uid 1000 with a read-only root.
  - **Smoke**: 59/60, and S11 is the only failure. D4: `non_get +0`, with `get +175` for 21 visited pages and 60 POSTs blocked. `dps_llm_raw_pii_sent_total` was 0 before and after.
  - **Flow**: PASS 92/0.
  - **OCR of the smoke scan**: 0 demo values on the filled screens, with readable positive controls. The masked captures show only N9 `application_ref`.
  - **Probe obfuscate scan**: 72 artefacts answered 200. 0 secret hits in the logs and 0 in the metadata DB. `non_get_allowed` was 0.
  - **Unit and lint**: 127 passed and 6 skipped. ruff and `mypy dps` are clean.
- `mypy tools/smoke_test.py` still reports the 3 errors listed in §7.2. The `ident` error at line 983 comes from `a54745f` (M4; the same line is on main at 890), so it is not a `fimg` change. Left as F4 polish.
- I stopped the lane stack with `docker compose --profile full stop` once it had been idle for more than 2 minutes. The volumes are kept.
