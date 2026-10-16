# S4 review r2: M5 Task 24 (images, `full` stack, smoke M5 block, demo client, flow report)

**Reviewer:** independent review, round 2 · **Branch:** `fimg` @ `fc0d192` (worktree
`/Users/neelu/document_intelligence/dps-wt-fimg`; base `main` `64c46e3`; `main` is now `144cc65`,
docs only, merges cleanly) · **Inputs:** `S4-impl.md`, `S4-review-r1.md`, `S4-fix-r1.md` · **Date:**
2026-10-14 · **Files modified by the review:** none in the repository (worktree clean throughout,
`git status` empty at the end). All outputs, scripts and logs: `~/.cache/dps-build/reports/S4-review-r2/`.

## Verdict: PASS (0 blocking)

- **r1 B1 is fixed and proven.** I checked it live in both directions of a UID change, through
  local `pvc` exports, and with the runtime test on both architectures. The runtime test fails on
  `main`'s pre-change image, so it can fail.
- **r1 N2 is fixed and proven live.** An unreadable object now answers 502 `storage_error` instead
  of 500.
- **r1 N1 still fails, by dependency only.** The smoke's last check, `api responses are not
  cacheable`, needs Task 17 S11. S11 is now committed on `fsec` (`6061767`). I built a probe image
  from fimg plus fsec's code diff and ran it. The smoke passed **all 33 checks** (M0+M1+M5,
  M2 off), including `no-store`. So the merge gate closes when fsec merges.
- **Gates:** every gate reproduces. Privacy proofs are clean.
- **New findings:** all non-blocking. The most useful one (R2-1): the re-permission command in
  HANDOFF §8 does not work as written (measured). A verified replacement is given below.

## Gates (run by the reviewer)

| Gate | Result |
|---|---|
| Worktree prep | `.env`: all 12 lane ports were free, and every host-port variable compose interpolates is set. `.env.lane` matches m3's format (ports only, plus `DPS_TOKENIZER_MOCK_PORT`). `lane.sh` is byte-identical to m3's. Patterns are in `info/exclude`. `.venv` is py3.12. `frontend/node_modules` is real (no symlink). |
| `ruff format --check` / `ruff check dps tests tools` | 383 files formatted / all checks passed |
| `mypy dps` | clean, 134 files. `mypy tools/{demo_client,flow_report}.py` is clean. `smoke_test.py` has 2 errors, which are pre-existing on main (main lines 405/711, shifted +14/+15). |
| `pytest -q` (stack down) | **2211 passed, 492 skipped** (46.6 s); same as the fix report |
| `docker build --no-cache --platform linux/amd64` (review tag, removed after) | **exit 0, 196 s**. Image: amd64, `User 10001`, `HOME=/tmp`, 292 MB. The console stage ran natively (npm ci 22.7 s); apt (55.6 s) and uv (128.7 s) ran emulated. |
| `docker compose --profile full up --build -d` (lane ports, `DPS_IMAGE_TAG=fimg`) | exit 0, 13 services. One-shots exited 0: migrate, minio-init, demo-secrets, seed-sources. |
| `/readyz` | `ready: true`. These components are ok: api-core, llm-gateway (stub), fpe ff1, tokenizer (http), ocr (azure-read-v3.2), db (migrations verified), jobs, keys (db+static). ner is patterns-only, by design. |
| Seed: `seed_sources.py --target all` | pg (already seeded), Oracle and Mongo 20 000 each |
| Smoke `--m3` as uid 10001 (`REQUIRE_OCR=1 REQUIRE_M5=1`) | **48/49**. M5 block: 7/8 pass. The only FAIL is `api responses are not cacheable` (S11). |
| `flow_report.py` as uid 10001 | **PASS: 92 calls, 0 unexpected**, exit 0. 82 as expected, 10 scanner rows skipped (no browser worker on this branch). Wall time 4.5 s. |
| `flow_report.py --demo-keys` (a tenant-scoped key for `default` with every scope, minted for the test, 0600 file, disabled and deleted afterwards) | **PASS: 92 calls, 0 unexpected**. Both `TENANT_KEY_EXPECT` rows answered the documented 403 `tenant_forbidden`. This path is now verified live. |
| Every image role as **1000770000:0** (override on app, worker, migrate, tokenizer-mock, seed-sources) | `docker inspect`: `User 1000770000:0`, `ReadonlyRootfs=true`, `CapDrop=[ALL]`, `no-new-privileges` on all five. `/proc/1/status`: Umask 0002, Gid 0. migrate and seed-sources exited 0. `/readyz` ready. Smoke `--m3` **48/49** (S11 only). Flow **PASS 92/0**. |
| `DPS_TEST_IMAGE=… pytest tests/test_image_runtime.py` | **3/3 arm64** (`:fimg`, rebuilt by compose at HEAD) and **3/3 amd64** (cold `--no-cache` build, emulated). On `main`'s pre-change image `:local`: **2 FAILED** (`HOME=/`; objects `0o600` gid 10001). The test is not vacuous. |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "(db or pg_source or s3) and not browser"` | **335 passed** (6 min 56 s) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "(docs or ocr or oracle or mongo) and not browser"` | **295 passed, 3 skipped**: the same by-design skips (2 need host tesseract, 1 is Mongo's no-composite-keys case). I excluded `browser` on purpose: those M3 console tests rewrite 54 tracked screenshots. The implementer's 341 and 301 include 6 browser tests each. |
| Real `--build-arg EXTRAS=vertex` build | exit 0. `google-genai 2.30.1` imports as 1000770000:0 with a read-only root. `EXTRAS='vertex;id'` stops at the guard (exit 2, rule message). |
| gitleaks (`zricethezav/gitleaks`, as `autopush.sh` runs it) on `main..fimg` | 2 commits, **no leaks**. Both commits are authored by `nilendu.ganguli@gmail.com` with 0 trailers. |
| `git merge-tree` | **clean against `main` `144cc65`**. Expected doc-only conflicts with the other streams: `M5-DEVIATIONS.md` (fsec, fos, fdemo, fdocs), `HANDOFF.md` (fos, fdocs, m4) and `.gitignore` (fdocs). |

## r1 B1 / N2 re-verification (live, value-free: ids, statuses and modes only)

1. **Run A as uid 10001:** smoke `--m3`, flow, and one `pvc` CSV export (`base_path s4r2`). The
   PDF reads stored 8 more `evidence_pdf` objects. Result: 29 artefacts and 9 packs, all 200. The
   volumes held only `d 2775 10001:0` (17) and `f 660 10001:0` (31).
2. **Same volumes, every role as 1000770000:0 (the B1 check):** `GET /artefacts/{id}` gave
   **29/29 → 200**. `/evidence/{id}/html` gave **9/9 → 200** (the console's Evidence page), and
   `/pdf` gave **9/9 → 200**. These PDFs were stored by uid 10001; no new artefact was created.
   r1 measured 11 × 500 and 5 × 502 here before the fix.
3. **Run B as the new UID:**
   - Smoke `--m3` 48/49 (S11 only). Flow PASS.
   - A `pvc` export into the same `base_path` that uid 10001 created: succeeded, 1 object, verified
     pack.
   - All **58 artefacts, 18 html, 18 pdf → 200**. One intermediate pass hit 429s caused by my own
     rapid reads (50 rps rate limit); after a pause every read answered 200.
4. **Back to uid 10001:** all **58 / 18 / 18 → 200**. Final volumes held only 2775 directories and
   660 files of both UIDs, group 0: `d 10001:0` 17, `d 1000770000:0` 11, `f 10001:0` 31,
   `f 1000770000:0` 31.
5. **N2:**
   - I made one stored evidence HTML mode 000 (as its owner).
   - `GET /artefacts/{id}` gave **502 `storage_error`**, and so did `/evidence/{id}/html`.
   - There was no `api_error` log line and no traceback.
   - After I restored 660, the reads answered 200 again.
6. **Code review of the fix** (`dps/storage.py`):
   - `os.open(O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o660)` keeps mkstemp's guarantees and gives
     `0660` under the image umask.
   - A stricter umask still narrows the mode (unit-tested for 000/002/022/077).
   - The S3 upload spool in the shared temp directory stays `0600`.
   - `stream` and `exists` map `OSError` to `storage_error`. The detail is the class name only,
     never a path.
   - `exists` has no caller in `dps/`, so the new raise breaks no caller.

## Proofs

- **Log and output scan** (`privacy_scan.py`, counts only). Re-run after the integration suites:
  75 sources, 6.58 MB. Sources: the saved logs of run A and run B (before each recreate), the
  current containers' logs, and every tool output.
  - Needles, all **0 hits**:
    - the smoke/flow sample record, the SSN probe, the bootstrap key, the tokenizer token and key,
      and the demo KEK;
    - 2 000 sampled source values each of pg `full_name`, `email`, `mobile`, `national_id`,
      `tax_id`, Oracle `FULL_NAME`, `EMAIL`, `MOBILE`, `NATIONAL_ID`, `TAX_ID`, and Mongo
      `fullName`, `email`, `mobile`, `taxId`;
    - raw `dps_`+43 keys, `tk_` tokens and tracebacks.
  - `PermissionError`, `Read-only file system` and `StorageError`: 0, except one line in my own
    probe log of the R2-1 command.
  - The stored artefacts on the volume (evidence JSON/HTML, manifests; 215 kB): 0 hits.
  - The two `pvc` CSV exports (500 rows each): 0 cells equal a source value of the same column,
    for each of the 5 personal columns.
- **Source vs target (counts only):**
  - **PostgreSQL** (`bank_uat.customers_smoke` 20 000 rows, `customers_flow` 500,
    `customers_flow_b` 500):
    - Every target row was paired with its source row on the kept columns (unique for all 20 000
      source rows). **Same-row survivors: 0** in `customer_id`, `full_name`, `email`, `mobile`,
      `national_id`, `tax_id`, `notes` and `date_of_birth`.
    - DOB shifts range from −365 to +365 days, with no zero shift.
    - Set-equality: 0 for names, e-mails, mobiles, national ids, tax ids and notes. Domain
      collisions only: `customer_id` 4/20 000 (FF1); DOB 12 604/20 000 (date occupancy).
  - **Oracle** `CRM_MASKED.CUSTOMERS_SMOKE` (20 000 rows): set-equality 0 for `FULL_NAME`,
    `EMAIL`, `MOBILE`, `NATIONAL_ID`, `TAX_ID` and `ADDR_LINE1`. `NOTES` equal to the source row
    with the same id: 0. Domain collisions: `CUSTOMER_ID` 4, DOB 12 604.
  - **MongoDB** `crm_uat.profiles_smoke` (20 000 docs): 0 for `fullName`, `email`, `mobile`,
    `taxId` and `notes`. Domain collisions: `customerId` 4.
- **Image audit** (amd64 cold build, run as 1000770000:0 with a read-only root):
  - `/app` holds README, dps, frontend, pyproject, tools and var.
  - No private-key, AKIA, raw `dps_` key, demo-KEK or tokenizer literal.
  - The bootstrap literal appears only in `tools/*` and README, as it already did before this
    change (local-only, refused in staging/prod).
  - **0 world-writable** non-sticky paths.
  - Group-0-writable paths outside `/app/var` are Debian defaults only (man dirs, `/var/tmp`,
    debconf).
  - Setuid binaries are the Debian base set (r1 N8).
  - `/app/var` is `2775 10001:0`. Config: `User 10001`, `HOME=/tmp`, entrypoint `entrypoint.sh`
    (`umask 0002`).
  - Finding R2-2 below concerns `tools/dev`.
- **Running as root, writable root fs.** No role of the app image runs as root. All five roles are
  read-only, `cap_drop ALL` and `no-new-privileges`. The roots in `full` are pre-existing local-only
  services: `demo-secrets` (documented in HANDOFF §6) and the OCR mock (R2-4).

## Acceptance (M5 plan Task 24, DoD 5/6, programme plan §2.6 F2 row)

| Item | Status |
|---|---|
| `ARG EXTRAS` / `${EXTRAS:+,$EXTRAS}` | done on both install lines, guarded. A real build with an extra works; a bad value stops the build. |
| Arbitrary UID: group 0, read-only root, `HOME=/tmp` | done, and better than the plan (code `g+rX,g-w`, only `/app/var` group-writable, setgid). Cross-UID now holds for directories **and** objects (B1). |
| `Dockerfile.browser` same lines | not on this branch. A guard test and the HANDOFF §8 recipe stand in; apply the recipe at the M4 slot-in (S4b). |
| Compose: tokenizer-mock in `full`, M5 env, app waits for the mock, `.gitignore` `var/` + `/reports/` | done |
| `demo_client.py` signatures | done (test-pinned) |
| Smoke M5 block, 8 checks, `REQUIRE_M5` | done. Every check can fail: `bool(presets)` guards the hash check, and the key check fails before the disabled-key check could pass vacuously. Unit tests cover the failure paths. |
| `flow_report.py`: every §9 group, value-free, `EXPECT`, exit 1 on mismatch | done. The coverage test and the fake-stack "every `EXPECT` row is called" test hold. I read the whole report: only templates, statuses, durations, key names and error codes. |
| `--profile full up --build -d` → ready → smoke all checks → flow exit 0 | ready ✓, flow ✓, smoke 48/49. The failure is S11, which is proven to close with fsec. |
| §2.6: amd64 images / arbitrary-UID run / smoke all checks | ✓ / ✓ (stable UID **and** a UID change) / after the fsec merge |

## Findings (all non-blocking, most useful first)

### R2-1: the HANDOFF §8 re-permission command fails as written (measured); fix the doc

**Where it appears:**
- HANDOFF §8, which states: "Re-permission once — `docker compose run --rm --no-deps --user 0
  --entrypoint sh worker -c 'chgrp -R 0 … && chmod -R g+rwX … && find /app/var -type d -exec
  chmod g+s {} +'`".
- `M5-DEVIATIONS.md` Task 24, which states: "HANDOFF §8's one-time re-permission … covers them".

**What I measured:**
- I set up a probe compose project, `s4r2probe` (removed afterwards). I filled it with main's
  pre-change image `:local`, which produced directories `755 10001:10001` and files `600 10001:10001`.
- I then ran the documented command verbatim. It fails with **exit 1, `chgrp: … Operation not
  permitted`**, and nothing changes. The reason: `docker compose run` applies the worker's
  `cap_drop: [ALL]`, so root has no `CAP_CHOWN` or `CAP_FOWNER` for files that uid 10001 owns.
- As the owner (`--user 10001`), the chgrp and chmod steps work. The trailing `find /app/var …`
  still exits 1, because `/app/var` itself is on the read-only root.

**Verified replacements.** Each exits 0, leaves `d 2775 10001:0` and `f 660 10001:0`, and a new
UID then reads both volumes.
- **Any owner:** `docker compose run --rm --no-deps --user 0 --cap-add CHOWN --cap-add FOWNER
  --entrypoint sh worker -c 'chgrp -R 0 /app/var/artefacts /app/var/exports && chmod -R g+rwX
  /app/var/artefacts /app/var/exports && find /app/var/artefacts /app/var/exports -type d -exec
  chmod g+s {} +'`
- **Volumes written by compose's uid 10001:** the same command with `--user 10001` and without
  `--cap-add`.

**Impact:** low. It affects only local volumes from older images that are then run under another
UID, and `down -v` still works. But it is the documented remedy for the B1 upgrade gap. Fold it
into S4b, which edits HANDOFF §8 for the browser recipe anyway, or fix it before the merge. The fix
is doc-only.

### R2-2: developer tooling is baked into the shipped image

- `COPY tools ./tools` puts `tools/dev/autopush.sh`, `tools/dev/com.nilendu.dps-autopush.plist`
  and `tools/dev/resume_status.sh` into `/app/tools/dev`. These are the owner's push automation
  and a macOS LaunchAgent, and they contain `/Users/neelu/…` paths.
- This is pre-existing (main's `COPY tools`), but Task 24 owns image hygiene.
- **Fix:** add `tools/dev` to `.dockerignore`. Optionally exclude `tools/scratch_pg.sh` too.
- None of these files contains a secret.

### R2-3: wrong claim about emptyDirs (T25 note)

- `Dockerfile:74` and the docstring of `tests/test_image_runtime.py` say "named volumes and
  emptyDirs inherit this ownership and mode".
- Docker named volumes do. Kubernetes emptyDirs and PVCs do not: they start as kubelet creates them
  (0777, or the `fsGroup` group with setgid under restricted-v2).
- Cross-UID on OpenShift therefore comes from `fsGroup` and kubelet's ownership change, not from
  the image. Task 25 should not rely on group 0 there.
- Fix the wording.

### R2-4: the OCR mock runs as root with a writable root filesystem

- The `azure-ocr-mock` service (in `full`) runs as uid 0, with no `read_only` and no `cap_drop`.
- It is pre-existing (M1), local-only and never deployed.
- Optional hardening: add `read_only`, a `/tmp` tmpfs and `cap_drop: [ALL]`, as the image roles
  have.

### Carried from r1, still open (non-blocking)

- **N1:** the smoke merge gate after `fsec`. It is now proven to pass with fsec's code.
- **N4:** the flow leaves a `flow-*` tenant and policy behind. Each smoke run also leaves a
  disabled `smoke-temp` key.
- **N5:** scanner rows are *skipped* (exit 0) when `/readyz` lacks `browser`. After M4, add a
  require switch, so the DoD-6 run on `full` cannot pass without the scanner. This belongs to S4b,
  together with checking the 10 scanner `EXPECT` rows against `m4`'s routes. On `m4` `8f4c5b0` the
  10 templates match, and cancel answers 409 `not_cancelable`.
- **N6:** 2 pre-existing mypy errors in `tools/smoke_test.py`.
- **N7:** `smoke_test.py` does not import `demo_client`; this is not logged as a deviation.
- **N8:** the Debian setuid binaries.
- **N9:** the linkability of kept columns (F1).

### Optional

- The image has no precompiled bytecode: 0 `.pyc` among 5 600 `.py` files in site-packages. With a
  read-only root, every start compiles in memory; `import dps.app` takes 1.4 s natively.
- `UV_COMPILE_BYTECODE=1` (or `uv pip install --compile-bytecode`) would trade image size for
  startup time.

## Deviations (`M5-DEVIATIONS.md` "Task 24"): assessment

- **r1 items 1–10:** I agree with all of them, as r1 did.
- **Fix r1 deviation** (create the file with mode 0660 less the umask instead of
  `fchmod(0o660)`): **agree.** The result inside the image is the same. A stricter operator umask is
  respected, there is no window with another mode, and the S3 spool stays private.
- **One sentence is wrong:** "HANDOFF §8's one-time re-permission covers them" (R2-1).

## State left behind

- Branch `fimg` is at `fc0d192` and the worktree is clean. I committed nothing, pushed nothing and
  did not run `autopush`.
- The lane stack `dps-wt-fimg` is **down with `-v`**: no containers, volumes or network remain, as
  I found it.
- The probe project `s4r2probe` and the probe container `s4r2-fsec-probe` have been removed.
- My image tags were removed: `:fimg-r2-amd64`, `:fimgfsec-probe` and `:fimg-r2-extras`.
- `data-privacy-services:fimg` and `document_intelligence-azure-ocr-mock:fimg` were rebuilt from
  HEAD by `up --build`. They are the lane's own tags.
- The implementer's `:fimg-amd64` is untouched.
- No other compose project, container, volume or image was touched. Other stacks were only read:
  `git show` and `merge-tree` on other branches, and a read-only look at `m4`'s
  `Dockerfile.browser`.
- Evidence is in `~/.cache/dps-build/reports/S4-review-r2/`:
  - gate logs, build logs and runtime logs (red and green);
  - smoke and flow logs and all three flow reports;
  - container logs of runs A and B;
  - id and status reads, mode histograms, the privacy-scan and SVT outputs;
  - the image audit, the re-permission probes, the fsec probe smoke and the scripts used.
