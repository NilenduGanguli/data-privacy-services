# S4b review r1: Task 24 for the scanner on `fimg`

**Verdict: PASS. Nothing blocks.**

- Reviewed `fimg` = `origin/fimg` = **`3f8af02`**. The worktree is clean and no repository file was changed.
- This review was interrupted at about 22:02 IST and resumed at the same HEAD. Run A and the builds below come from the first part (`logs/`, `HEAD.txt`). Run B, the runtime tests, the cross-UID reads, the log scan and the code review come from the resumed part.
- Evidence: `~/.cache/dps-build/reports/S4b-review-r1/`. It holds `logs/`, `probe/` (scripts) and `override/xuid-amd64.yml`, the compose override for run B.

## Acceptance

| Criterion | Result | Proof |
|---|---|---|
| The `full` profile includes browser-worker and demo-site | ✅ | `compose config`: both are under `full`, and 15 services ran live. Covered by `test_the_full_profile_is_every_service`. |
| The smoke covers a scan and the site receives 0 POSTs | ✅ | The scanner block runs by default on `full`, because `auto` sees the demo site answer: a live check of `scanner_wanted` on this lane gives True. It also runs under `on`. The D4 check has two positive controls. Live result: `non_get +0` in runs A and B, and during both flow scans. |
| The app and browser images build for amd64 | ✅ | `docker build --platform linux/amd64 --no-cache` at HEAD: app exit 0 (257 s), browser exit 0 (223 s). |
| Both images run under an arbitrary UID with a read-only root | ✅ | **Run B**: the amd64 builds, every role as `1000830000:0`. The runtime tests pass 6/6 on amd64. |
| No image role runs as root | ✅ | Every role is uid 10001 (A) or 1000830000 (B), gid 0, umask 0002. The only root container is the local `demo-secrets` one-shot, which is documented. |

## Gates I ran or reused (all at `3f8af02`)

### 1. Lint and unit tests
- ruff format/check (444 files) and `mypy dps` (150 files): clean.
- The S4b test files: 127 passed, plus 6 opt-in tests skipped offline.
- Live against the lane, the same files plus `tests/scanner/test_demo_site.py`: **135 passed, 0 skipped** (`logs/unit-s4b-live.log`).

### 2. Images
The four images under test:
- arm64: `data-privacy-services:fimg` and `data-privacy-services-browser:fimg`;
- amd64, built by this review: `data-privacy-services:fimg-s4br1-amd64` and `data-privacy-services-browser:fimg-s4br1-amd64`.

I copied files out of each image and compared them with HEAD:
- `dps/` has 0 differing files in all four; the entrypoint and `pyproject.toml` are identical too;
- `tools/` is identical in both app images, and there is no `tools/dev`;
- no Dockerfile has changed since `d8c629c`, which is older than every image.

Each image runs as `User=10001` with `HOME=/tmp` and the entrypoint script.

### 3. Runtime tests on the amd64 images
`DPS_TEST_IMAGE_PLATFORM=linux/amd64 pytest tests/test_image_runtime.py tests/test_browser_image_runtime.py`: **6 passed** (`logs/runtime-amd64-s4br1.log`). This covers:
- the `x86_64` architecture check;
- uid 1000770000:0 with a read-only root;
- Chromium rendering a page;
- the app image reading what the browser image stored.

### 4. Run A: arm64, uid 10001, fresh volumes (`down -v`)
- Ready, `browser: 1 online`.
- **Smoke 59/60**, run with `DPS_SMOKE_SCANNER=on DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 DPS_SMOKE_M2=on … --m3`. The only failure is S11.
- D4: every counter +0; `pages served +175 (21 visited)`; 60 POSTs blocked; `non_get_allowed` 0.
- Flow `--require-scanner`: **PASS 92/0**, including the 10 scanner calls.
- Probe obfuscate scan: 72 artefacts answered 200, with 0 secret hits in the logs or the metadata DB.

### 5. Run B: new in this review
- **Setup.**
  - Images: the amd64 no-cache builds.
  - User: `1000830000:0` on app, worker, migrate, tokenizer-mock, seed-sources and browser-worker. This is neither the image's 10001 nor the implementer's 1000770000.
  - Everything else is compose as committed: read-only root, `cap_drop: [ALL]`, `no-new-privileges`, the same tmpfs mounts.
- **Processes** (`logs/roles-B.txt`): `x86_64`, uid 1000830000, gid 0, umask 0002. Migrate and seed-sources exited 0.
- **Smoke** (same environment as run A; M3 runs of 20 000 rows on PG, Oracle and Mongo; 356 s under emulation): **59/60**, and S11 is the only failure. D4 gave:
  - `non_get +0, form_get +0, logout_hits +0, admin_hits +0`;
  - `pages served +175 (21 visited)`;
  - 60 non-GET requests blocked;
  - `non_get_allowed` 0.
- **Flow** `--require-scanner`: **PASS 92/0**, with all 10 scanner calls. During the flow, `get` moved +21 and non-GET +0.
- `dps_llm_raw_pii_sent_total` was 0 before and after.
- **Reads across UIDs** (`logs/xuid-B.out`): the amd64 app, running as 1000830000, answered 200 for:
  - all **281 artefacts**: 218 screenshots, 12 captures, the evidence HTML, PDF and JSON, the exports and the manifests;
  - all **21 packs**, as HTML and as PDF.

  Of the stored files, 178 belong to uid 10001 (run A, arm64) and 122 to uid 1000830000. The volumes hold only `2775` directories and `0660` files, all of group 0.
- **OCR** of smoke B's scan (`logs/ocr-smokeB.out`):
  - the filled screens (12 synthesize, 12 obfuscate) show **0** demo values;
  - the positive control is readable: the personas give 20–67 hits;
  - the 3 masked captures show only `application_ref` on `/apply/review`. That is M4's known N9: it is not a detected entity, and main has it too.
- **Log scan** (`logs/logscan-B.json`; 53 sources, 3.2 MB):
  - Sources: all 15 container logs, the data stores included, plus the saved outputs.
  - Needles: 10 demo-site values, 7 values from the smoke sample and the SSN probe, 2 496 values sampled from the source tables, 5 secrets.
  - **0 hits** of any needle.
  - **0** `Traceback`, `PermissionError`, `Read-only file system`, `storage_error`, `Errno 13`, raw `dps_` keys or `tk_` tokens.
- **The counters work**: after the live demo-site tests, the site's counters read `non_get 7, form_get 1, logout 1, admin 1`. These are the tests' own positive controls; the counters moved only then.

### 6. S11 is a dependency on another lane, not a `fimg` defect
- `Cache-Control: no-store` on `/api/` is M5 Task 17. It is implemented on `fsec` (`dps/app.py` middleware, branch at `fee0041`), and `fimg` and main do not have it yet.
- Live on this lane: `/api/v1/policies/presets` sends no `Cache-Control` header.
- It is the last check of the smoke, so no check after it went unrun.
- **The release gate must re-run the full smoke after `fsec` merges and expect 60/60.**

## Code review: what I looked at and found sound

- **The D4 check** (`f39df5b`):
  - `served >= max(1, pages_visited)`, plus at least one blocked non-GET, plus every counter +0, plus `non_get_allowed == 0`.
  - A site without a `get` counter fails the check.
  - Stray GETs can only add to the served count, never make the check fail.
  - The verdict prints counts only.
  - In `server.mjs`, `get` is counted after `/health` and `/__received` and after the non-GET branch, so a POST is never counted as a GET.
  - The scanner code is unchanged by S4b. The scanner merges come from main and are already reviewed there.
- **Mocks** (`7561e89`): the OCR mock runs as `10001:0` and the demo site as `node`. Both have a read-only root, `cap_drop ALL` and no-new-privileges, and both were healthy live.
- **Images**:
  - The app and browser `/app` trees are root-owned, `g+rX,g-w`. `/app/var` is 10001:0, setgid, `g=u`.
  - Objects are created at `OBJECT_MODE 0660` with `O_EXCL|O_NOFOLLOW`. S3 spools are 0600.
  - `HOME=/tmp`; the entrypoint sets umask 0002.
  - `/ms-playwright` stays 0777. That is a recorded deviation (M5-DEVIATIONS: +1.46 GB otherwise), and the runtime test proves the read-only root keeps it unwritable. `fos` sets `readOnlyRootFilesystem: true` on browser-worker and uses `args` rather than `command`, so the entrypoint's umask applies.
- **Git**: all commits are by `NilenduGanguli <nilendu.ganguli@gmail.com>` with 0 trailers. The join merge `3f8af02` has the same tree as `4640324`. `2622429` and `5c54dda` have the same tree and the same parents. No force-push.

## Findings (none blocking)

- **N1. The smoke's `auto` skip does not show in the verdict line.**
  - With `DPS_SMOKE_SCANNER` unset, a full stack whose demo site is down skips the 11 scanner checks and prints only a `note:` line.
  - `skipped` is not incremented, so the last line reads `all N checks passed`. That happens even while `/readyz` reports `browser: 1 online`.
  - The gate uses `on`, which fails correctly. Optional fix: under `auto`, run the block when `/readyz` reports a browser worker, so a missing demo site fails; or count the skipped block in the tail.
- **N2. The browser image has no init process.**
  - Compose's `init: true` reaps orphaned Chromium processes. The image itself has no tini or dumb-init, and `fos`'s `deployment-browser-worker.yaml` has no `shareProcessNamespace`, so on OpenShift Python is PID 1.
  - Playwright's Docker guidance recommends an init to avoid zombie processes.
  - Not observed: the live process tree had no orphans. A follow-up for M4/`fos`: tini in `Dockerfile.browser`'s ENTRYPOINT, or `shareProcessNamespace: true`.
- **N3. The local `demo-secrets` one-shot (`0:0`) has no `cap_drop` or `no-new-privileges`.** It is intended and documented. It could be hardened if reading the bind mount needs no DAC override. Nit.
- **N4. The demo site does not count non-GET requests to `/__*` paths.** This is M4's design. Because of it, the D4 instrument cannot see a POST to `/__received`. The guard blocks every non-GET request and the demo pages never send there. Optional: count those too.
- **N5. `mypy tools/smoke_test.py` reports 3 errors.** They predate S4b: two come from main and one from M4 (the `ident` error at line 983). The mypy gate is `mypy dps`.
- **N6. The published `2622429` keeps git's `# Conflicts:` lines in its message.** Cosmetic; already joined by `3f8af02`. No action.

## State left
- The lane stack is **stopped** with `docker compose stop`, and the volumes are kept.
- The role containers were last created by run B's override: amd64 images, UID 1000830000. A plain `docker compose --profile full up -d` recreates them with the defaults.
- The role logs are saved in `logs/containers-B.log`; the log scan found no values in them.
- Images this review built: `data-privacy-services:fimg-s4br1-amd64` and `data-privacy-services-browser:fimg-s4br1-amd64`. The owner may remove them.
- Nothing else touched: only this lane's compose project and read-only greps of the `fos` and `fsec` worktrees.
