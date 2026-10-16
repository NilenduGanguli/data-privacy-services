# FIMG — `m4` merged into `fimg`: the browser-worker image and the scanner services in the `full` stack (S4b)

**Verdict: DONE.** The merge is committed, the stream's work is adapted to what came in (test first), and every gate is green on this lane's `--profile full` stack. The one exception is the known smoke check `api responses are not cacheable`. That check is M5 Task 17 S11, which lives on lane `fsec`. It is not a `fimg` defect, and the check was not weakened. The stack is down (`-v`), the worktree is clean, and nothing was pushed by me. The owner's autopush loop had already pushed `fimg` up to `58fd122`. My gitleaks, author and trailer check of the last two commits is clean.

| | |
|---|---|
| Worktree / branch | `/Users/neelu/document_intelligence/dps-wt-fimg`, `fimg`: `fc0d192` → **`e74ba22`** |
| Merged | `m4` @ **`cbbac38`** (S2 fix r2; contains main up to `144cc65`). `main` merged this same `m4` commit as `5959a75` |
| Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer) | `83bc5f3` merge · `d8c629c` feat (adaptations) · `58fd122` test fix · `78f0017` fix (smoke `on` path) · `e74ba22` docs (HANDOFF §17.1, deviation log) |
| Date | 2026-10-15, 09:00–10:20 IST |
| Evidence | `~/.cache/dps-build/fimg-m4/`, with `logs/`, `probe/` (scripts and outputs), the flow reports, the build and runtime logs, and `m3-shots-from-run/` |

## 1. Merge

- `git merge --no-ff m4 -m "merge: m4 into fimg (…)"`. **One conflict: `HANDOFF.md`**, in two places, and both sides were kept:
  - **§1 table:** M4's browser image row and UI scanner row were added. The two smoke rows were combined: fimg's row plus M4's scanner sentence.
  - **Section numbers:** M4's §15 and §16 (with 16.1 and 16.2) keep their numbers. fimg's own Task 24 record moved from §15 to **§17**, and its one `(§15)` reference now reads §17.
  - **Check:** a script showed that every non-blank line of both conflict sides is in the result. The only differences are the three intended edits.
- **Auto-merged and reviewed:**
  - `.dockerignore`: M4's `docker/secrets` sits beside fimg's `reports`.
  - `docker-compose.yml`: `browser-worker` and `demo-site` are in profiles `scanner`/`full`. `demo-secrets` is now also in `scanner`. `browser-worker` inherits the M5 `x-dps-env` settings, `read_only`, `cap_drop ALL`, `no-new-privileges` and the `/tmp` tmpfs from `worker`; checked with `docker compose config`.
  - `dps/storage.py`: fimg's `OBJECT_MODE` sits beside M4's `delete_for_job(kinds, ids, keep)`, in separate hunks.
  - `tests/test_compose.py` and `tools/smoke_test.py`.
- **Before the merge commit:**
  - ruff format/check and `mypy dps` (150 files) were clean.
  - The unit suite (stack down) gave 2975 passed, 612 skipped, 1 failed. The failure was a transient I caused: I edited `smoke_test.py` while `inspect.getsource` ran, and the test passes on a rerun.
  - The static image, compose, flow and tools tests gave 65 passed. The only failure was the expected red `test_the_browser_image_is_arbitrary_uid_safe`.

## 2. Adaptations (each test first; details in `M5-DEVIATIONS.md` "Task 24 › M4 slot-in" and HANDOFF §17.1)

| What | Red first | Green |
|---|---|---|
| **`Dockerfile.browser` gets the arbitrary-UID rules** (the §8 recipe): `useradd --gid 0`; `docker/entrypoint.sh` as ENTRYPOINT (umask 0002); `chown 10001:0`; `chgrp -R 0 /app && chmod -R g+rX,g-w /app && chmod -R g=u /app/var`; setgid `/app/var`; `ENV HOME=/tmp`. `/ms-playwright` is untouched: 0777, and a chmod there would add 1.46 GB, so the read-only root stays mandatory | Static guard failed on the merged file | Static guard passes |
| **New `tests/test_browser_image_runtime.py`** (opt-in `DPS_TEST_BROWSER_IMAGE`, + `DPS_TEST_IMAGE` for the cross-image case). It runs as uid 1000770000:0 with `--read-only`, `cap_drop ALL`, `no-new-privileges` and shm 1g. Checks: Chromium starts through the scanner's own `get_browser` and takes a screenshot; HOME=/tmp; umask 0002; the extras import; `/app`, `/opt/venv` and `/ms-playwright` are not writable; the volumes are writable. Through `LocalObjectStore`, a new UID reads, streams and replaces what uid 10001 stored, and the app image does the same with what the browser image stored | **3 failed on `m4`'s image** (`:m4`): umask 0022; `storage_error: PermissionError` | 6/6 on arm64 (browser 3 + app 3) at HEAD; browser 2/2 on **amd64** (cross-image case skipped: no amd64 app image of this commit) |
| **Finding fixed:** uv leaves `/opt/venv/.lock` mode **0666** (world-writable) in the browser image. Fix: `find /opt/venv -perm /022 ! -type l -exec chmod go-w {} +` in the permissions layer; it touches one 0-byte file, and the layer is 86 kB | The new runtime test failed on it | passes |
| **Smoke:** `DPS_SMOKE_SCANNER` now works like `DPS_SMOKE_M2`. `auto` is the default and runs the 11 scanner checks when the demo site answers `/health`, i.e. under `scanner` or `full`. `on`/`1` runs them whatever the stack, so a missing browser worker or demo site fails. `off`/`0` skips them. Before, M4's switch was opt-in only, so a plain smoke on `full` never covered the scanner. `scanner_block` makes `on` without a demo site a clean failed check (`demo site answers`) instead of a traceback | M4's `test_scanner_block_runs_only_when_asked` was replaced; new switch, probe and block tests failed first | `tests/test_tools_m4.py` 15/15 |
| **Flow report** (review S4 r2 N5): `--require-scanner` is the `full` gate; scanner rows become "not run" failures instead of skips. The scanner block re-reads `/readyz` (unrecorded) for up to 60 s while it reads `unknown` (fresh app), or always under the switch. The route coverage test no longer allows rows the app doesn't serve | 3 new tests failed | `tests/test_flow_report.py` 16/16 |
| **`58fd122`:** the coverage test counted the console routes `GET /` and `GET /{full_path:path}`, which the app registers only when `frontend/dist` is built. This was a latent fimg bug: the test had only ever run without a dist | Failed in the whole run after `npm run build` | passes with and without a dist |
| `tests/test_compose_m5.py`: `full` must hold `browser-worker` and `demo-site`; the browser-worker test no longer skips. Review S4 r2 fixes: R2-2 `.dockerignore` gets `tools/dev` (test first; checked it is absent from the image). R2-3 corrects the emptyDir wording. R2-1 puts the measured re-permission command (`--cap-add CHOWN --cap-add FOWNER`) in HANDOFF §8 | the R2-2 test failed first | 9/9 |

HANDOFF §1, §2, §3 (the `full` gate commands), §6, §8 and the new §17.1 (verification and traps) are updated. The contract is unchanged.

## 3. Gates (this lane: app 8417 … demo site 8427, `DPS_IMAGE_TAG=fimg`)

| Gate | Result |
|---|---|
| ruff format/check `dps tests tools`; `mypy dps` | clean: 436 files / 150 files |
| `docker compose --profile full up --build -d` | 15 services. The one-shots (migrate, minio-init, demo-secrets, seed-sources) exited 0. `/readyz` ready, every component ok except `ner` (patterns-only by design); `browser: 1 online` |
| **Whole suite with every service up**: `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_TESSERACT_CMD=<app image tesseract> pytest -q` | **3581 passed, 14 skipped, 1 failed** in 25 min. The failure was the route test, fixed in `58fd122`. By marker (from the junit XML): `docs or ocr` 139 + 2 skipped (host tesseract); `db or pg_source or s3` **409**; `oracle or mongo` 162 + 1 skipped (by design); `browser` **238**. The same numbers as lane `m4`'s gates. Other skips: 6 opt-in image runtime tests (run separately), 3 `ml` (MOSTLY not installed), 2 NER (no spaCy model) |
| Frontend | `npm ci`, 44 node tests + **366 Vitest**, `typecheck`, `build` (bundle check clean) |
| Images | `compose build` arm64; the browser image is 4.24 GB, of which the permissions layer is 86 kB. `docker build --platform linux/amd64 -f Dockerfile.browser` took 665 s cold. The final `:fimg` tags were rebuilt at HEAD; runtime tests 6/6 |
| Smoke A (uid 10001): `DPS_SMOKE_SCANNER=on REQUIRE_OCR=1 REQUIRE_M5=1 … --m3` | **59/60**: Layer 1, M1, scanner 11/11, M2 9/9, M3 7/7, M5 7/8. The failure is S11 only |
| Flow A: `--require-scanner` | **PASS, 92 calls, 92 as expected, 0 skipped.** The 10 scanner calls ran, and cancel answered 409 `not_cancelable` |
| Run B: every role of both images as **1000770000:0** (compose override: app, worker, migrate, tokenizer-mock, seed-sources, browser-worker; all read-only) on run A's volumes | Ready, browser 1 online; the worker processes run with umask 0002 and HOME=/tmp. **103/103** of run A's artefacts (76 screenshots and 3 captures the browser-worker stored) answered 200. Their 10 packs answered 200 for html and pdf when read slowly; a burst hit the 50 requests/s limit with 429. Smoke `--m3` **59/60** (S11); flow `--require-scanner` **PASS 92/0** |
| Run C: back to uid 10001 | **215/215** artefacts and 20/20 packs answered 200. The volumes hold only `2775` directories and `660` files, group 0, from both UIDs |
| Smoke C, **auto** (no `DPS_SMOKE_SCANNER`) after the whole suite and a reseed | **59/60**; the scanner block ran (S11 is the only failure). Flow in default mode: PASS 92/0 |
| Demo site stopped (live check of both paths) | auto prints the note and skips the block, giving 32/33 (S11). `on` gives `FAIL demo site answers` (24/25) and no traceback |

## 4. Proofs (counts only)

- **D4.** The `/__received` counters moved **+0** around smoke A, smoke B, smoke C, flow A and flow C, and around the privacy probe scan. `non_get_allowed` was 0, and the guard blocked 60 POST and 1 GET per smoke scan. The whole suite moved them by +8/+2/+1/+1. That is exactly its own positive control (`test_positive_control_the_demo_counts_what_it_receives`), the same as on lane `m4` (+8/+2/+1/+1 per run).
- **OCR** of every stored screen of smoke scan A, using the app image's tesseract with `--psm 11` and `--psm 3`:
  - The filled screens (12 synthesize, 12 obfuscate) hold **0** demo values. The positive control holds: the scan's own personas are readable, 220 and 12 hits.
  - The masked captures (3) hold 0, except `application_ref` on `/apply/review`. That is M4's known N8/N9: it is not a detected entity, and lane `m4` sees it too.
- **Logs and artefacts:**
  - Every container log of the stack plus every saved tool output (74 sources, 6.97 MB) holds 0 of the following: the sample record; the SSN probe; the bootstrap key; the tokenizer token and key; the demo KEK; the demo site's 10 customer values and its session cookie; a probe scan's DEK and seed, held in memory only; 2 000 sampled source values per personal column of PG, Oracle and Mongo; raw `dps_` keys; `tk_` tokens.
  - The artefact and export volumes (0.8 MB) also hold 0 of these.
  - No container logged a traceback. `dps-db` and `source-pg` log only the integration suite's deliberate refusals: append-only, RLS, grants, check constraints, bad passwords and a read-only transaction.
  - `dps_llm_raw_pii_sent_total` stayed 0.
- **Source vs target**, after the last smoke:
  - `bank_uat.customers_smoke` (20 000 rows), `customers_flow` and `customers_flow_b` (500 each): **0 same-row survivors** in every personal column. 0 values equal to any source value for names, e-mails, mobiles, national ids, tax ids and notes.
  - Oracle `CRM_MASKED.CUSTOMERS_SMOKE` and Mongo `crm_uat.profiles_smoke` (20 000 each): 0 for every personal column.
  - Domain collisions only: an FF1 customer id equals another customer's id 6 times in 20 000, and shifted dates of birth land on occupied days.

## 5. Open items and notes for the orchestrator

1. **S11** (`no-store` on `/api/`) is the only red smoke check. It closes when `fsec` (commit `6061767`) merges, as S4 r2 proved on a probe.
2. **`m4` @ `cbbac38` still has an open S2 r3 FAIL** (`S2-captures-privacy-r3` B1: closed-select edge cases are unmasked; S2 console r3 also FAIL). `fimg` carries that state. If the m4 stream commits fix round 3, merge `m4` (or main after its M4 re-merge) into `fimg` again.
   - The merge should be small. `main` merged this same `cbbac38` (`5959a75`), so the merge base of `fimg` and `main` is now `cbbac38`.
3. **HANDOFF numbering at the `fimg` → `main` merge.** `fimg` adds §17 (Task 24) and §17.1. If `main` adds its own "§17 M4 integrated on main", renumber one of them.
4. **Not done; optional and pre-existing:**
   - 3 `mypy` errors in `tools/smoke_test.py`: 2 come from main (N6) and 1 from M4's scanner block (`ident`). The gate is `mypy dps`.
   - The OCR mock and the demo site run as root without `read_only` (R2-4). They are local-only.
   - No amd64 *app* image was rebuilt for this commit. Its Dockerfile is unchanged since S4 r2's amd64 build.
5. The M3 console tests rewrote 54 tracked shots (A-N2). I copied them to `~/.cache/dps-build/fimg-m4/m3-shots-from-run/` and restored them with `git show HEAD:<path>`; none was committed.

## 6. State left behind

- `fimg` @ `e74ba22`, and `git status` is clean.
- The `.env`, `.env.lane` and `lane.sh` were reused unchanged; they already covered every compose variable, including `DPS_BROWSER_WORKER_METRICS_PORT`. The venv gained the `scanner` extra.
- The lane stack is **down with `-v`**: 0 containers, 0 volumes, and the network was removed. The probe volumes `dps-uid-probe-*` were removed.
- Kept images: `data-privacy-services:fimg`, `data-privacy-services-browser:fimg`, `data-privacy-services-browser:fimg-amd64`. S4's app tag `:fimg-amd64` is untouched. Compose rebuilt the lane's own OCR-mock tag `document_intelligence-azure-ocr-mock:fimg` from cache.
- Only this lane's project was touched. The `m4` image was used read-only, once, for the red run.
- I did not run `tools/dev/autopush.sh`; the owner's loop pushes `fimg`.
