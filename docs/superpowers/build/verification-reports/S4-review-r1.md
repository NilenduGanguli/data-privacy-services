# S4 review r1: M5 Task 24 (images, full stack, smoke M5 block, demo client, flow report)

**Reviewer:** independent review, round 1 · **Branch:** `fimg` @ `74625d6` (worktree
`/Users/neelu/document_intelligence/dps-wt-fimg`, from `main` `64c46e3`) · **Implementer report:**
`~/.cache/dps-build/reports/S4-impl.md` · **Date:** 2026-10-14 · **Files modified by the review:** none
(all outputs under the session scratchpad; lane stack brought down with `down -v` afterwards, as found).

## Verdict: FAIL (1 blocking finding, small fix)

Every gate the implementer reported reproduces, number for number. A cold `--no-cache` amd64 build
works, every role of the image runs as an OpenShift-style UID with a read-only root, and the privacy
proofs are clean.

One property that the commit adds and documents does **not** hold. The commit says files on the
artefact and export volumes "stay writable for the next UID of group 0". It does not hold for the
app's own artefact files: `LocalObjectStore` writes them through `tempfile.mkstemp`, which always
uses mode **0600**. The entrypoint's `umask 0002` cannot change that. Measured on the live stack: after
moving from uid 10001 to 1000770000:0 on the same volumes, all 11 earlier artefacts answer **500**.
The Evidence-pack HTML of all 5 earlier packs answers **502 `storage_error`**, which means the
console's Evidence page is broken for older jobs. The test that is supposed to pin this property
writes with `write_bytes` (which respects the umask) and never touches the app's real write path, so
it cannot catch the failure.

The smoke's last check (`api responses are not cacheable`) fails because it depends on Task 17 S11
(stream `fsec`). It is not a Task 24 defect. It is carried as a merge gate (N1).

## Gates (run by the reviewer)

| Gate | Result | Implementer |
|---|---|---|
| Worktree prep (`.env` lane ports, `.env.lane` in m3's format, `lane.sh` = m3's, `info/exclude` patterns, `.venv` py3.12, `npm ci`) | as required; all 12 lane ports were free before the run | same |
| `./lane.sh ruff format --check dps tests tools` / `ruff check dps tests tools` | 383 files formatted / all checks passed | same |
| `./lane.sh mypy dps` | clean, 134 files | same |
| `mypy tools/demo_client.py tools/flow_report.py tools/smoke_test.py` (not a gate) | new tools clean; 2 errors in `smoke_test.py` are **pre-existing on main** (same code at main's lines 405/711), none in the M5 block | n/a |
| `./lane.sh pytest -q` (stack down) | **2203 passed, 492 skipped** (46.7 s) | 2203 / 492 |
| `docker build --no-cache --platform linux/amd64` (review tag, removed afterwards) | **exit 0, 233 s**; amd64, `User 10001`, `HOME=/tmp`; console stage native (`npm ci` 15 s, `$BUILDPLATFORM`); no Dockerfile warnings | 591 s cold (with cache misses) |
| `docker compose --profile full up --build -d` (lane ports, `DPS_IMAGE_TAG=fimg`) | exit 0, build fully cached at HEAD; all 13 services up, one-shots exit 0 | same |
| `/readyz` | ready: api-core, llm-gateway (stub), fpe ff1, **tokenizer up**, ocr up, db up, jobs ready, keys db+static; ner patterns-only (by design) | same |
| Smoke as uid 10001 (`DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1`) | **41/42**, the only FAIL is `api responses are not cacheable` (S11) | 41/42 |
| Smoke `--m3` as uid **1000770000:0** (app, worker, migrate, tokenizer-mock, seed-sources; read-only, `cap_drop ALL`, `no-new-privileges`; `/proc/1/status` Umask 0002, Gid 0) | **48/49**, the same single FAIL (M3 block 7/7: 3×20 000 rows, 3/3 pairs consistent, 3/3 packs verified) | 48/49 |
| `tools/flow_report.py` (operator key) as uid 10001 and as the arbitrary UID | **PASS, 92 calls, 0 unexpected, exit 0** both times (82 as expected, 10 scanner rows skipped: no browser worker on this branch); 4.5 s | same |
| `DPS_TEST_IMAGE=… pytest tests/test_image_runtime.py` | **3/3 arm64**, **3/3 amd64** (the cold `--no-cache` build, emulated) | 3/3 + 3/3 |
| Jobs on the stack after both runs | 13 runs + 9 discovers, all `succeeded`, no `error_code` | n/a |

## Proofs

- **Source vs target (counts only),** on `bank_uat.customers_smoke` (20 000 rows), `customers_flow`
  and `customers_flow_b` (500 each) against `bank.customers`:
  - Set equality: 0 for full_name, national_id, tax_id, email and mobile in all three tables. Also 0
    for whole `notes` values, for source e-mails inside target `notes`, for source mobile digits
    inside target `notes`, and for (name, e-mail) pairs. `customer_id` gives 8/20 000 hits in smoke
    and 0 in the flow tables; DOB gives about 63%. Both are format-preserving domain collisions.
  - To settle that: kept columns (`created_at`, `updated_at`, `relationship_manager_id`,
    `risk_score`) are unique per source row, so I paired all 21 000 target rows with their own source
    rows. **Same-row survivors: 0 in every sensitive column.** DOB shifts range from −365 to +365
    days, with 0 zero shifts. I repeated the check after run B: still 0.
- **Log scans (counts only):** run A covered 524 kB (13 containers plus smoke and flow outputs); run B
  covered 1.27 MB (the arbitrary-UID containers plus the `--m3` smoke, flow and seed outputs). The
  needles were the sample record, the SSN probe, the bootstrap key, the tokenizer token and key, the
  demo KEK, 2 000 sampled source values of each of full_name, email, mobile, national_id and tax_id,
  raw `dps_`+43 keys and `tk_` tokens. **Result: 0 hits** for all of them. There were no tracebacks.
  Run B shows 11 value-free `error=PermissionError` lines, which my B1 probe caused.
- **Image audit** (`data-privacy-services:fimg`, run as 1000770000:0 with a read-only root):
  - `/app` holds only README, dps, frontend, pyproject, tools and var.
  - No `.env`, key, `var/demo` or reports output; no private-key or AKIA patterns; no raw `dps_` key
    in the console bundle.
  - The other demo literals (KEK, tokenizer token, minio, oracle) are absent. The bootstrap literal
    appears only in tools and README, as it already did before this change; it is local-only and
    refused in staging/prod.
  - `/app/var` is `2775 10001:0`; `/tmp` is 1777.
  - World- or group-writable paths exist only in Debian base defaults (`/var/tmp`, `/run/lock`, man
    directories, debconf). These are moot under a read-only root.
  - **Who runs as root:** only the pre-existing local `demo-secrets` one-shot (`user: "0:0"`). All
    Task 24 roles are non-root, read-only and `cap_drop ALL`.

## Acceptance (M5 plan Task 24, DoD 5/6, programme plan §2.6 F2 row)

| Item | Status |
|---|---|
| `ARG EXTRAS`, install line `…,tabular${EXTRAS:+,$EXTRAS}` | done, on both install lines, guarded (tested with the real RUN line) |
| arbitrary UID: `chgrp -R 0 /app`, group perms, `ENV HOME=/tmp`; read-only root | done. The deviation (`g=u` only on `/app/var`) is correct. The extra cross-UID property is **not** met for reads (B1) |
| `Dockerfile.browser` same lines | not on this branch; guard test plus HANDOFF recipe (accepted, see deviations). Not re-verified: M4 is not on `fimg` |
| Compose: tokenizer-mock in `full`, M5 env on app and worker, `.gitignore` `var/` + `reports/` | done (shared anchor, overridable; `/reports/` anchored; `.dockerignore` `reports`) |
| `demo_client.py` with the plan's signatures | done (test-pinned) |
| Smoke M5 block, 8 checks, "all N checks passed" | 8 checks implemented as the plan writes them, none can pass vacuously (reviewed each condition), `DPS_SMOKE_REQUIRE_M5` turns skips into failures. **Not all green:** 1 FAIL by dependency on S11 (N1) |
| `flow_report.py`: every §9 route group, value-free, `EXPECT` table, exit 1 on mismatch | done. 85 rows (75 served + 10 M4 scanner rows). Coverage test fails on any new route without a row. Report holds only templates, statuses, durations and key names (I read the whole report) |
| `--profile full up --build -d`, `/readyz` ready, smoke, flow → exit 0 | ready; flow exit 0; smoke exit 1 (N1) |
| §2.6: images build `--platform linux/amd64` | yes (cold `--no-cache` build) |
| §2.6: arbitrary-UID run | yes for a stable UID (all roles, smoke `--m3`, flow); **no** across a UID change on an existing volume, which the commit claims (B1) |
| §2.6: smoke all checks | no, 41/42 and 48/49 (N1, after the `fsec` merge) |
| §2.6: kustomize + kubeconform | Task 25, not in this task |

## Blocking finding

### B1: artefacts are 0600, so "the next UID of group 0" cannot read what the previous UID wrote (Evidence pages break)

**Cause:**
- `dps/storage.py:122`: `_spool` creates every stored object with `tempfile.mkstemp` (mode 0600 by
  design, ignoring the umask). `os.replace` then moves it into place.
- `LocalObjectStore` serves both the artefact store and local `files` exports (`dps/storage.py:366`,
  `:385`).
- The commit's mechanism (image user in `--gid 0`, setgid `/app/var` directories, entrypoint
  `umask 0002`) therefore makes **directories** `2775 …:0`, but every **file** stays `600 <uid>:0`.

**Measured (live, lane stack):**
- After run A as uid 10001, the volumes held 9 directories `2775 10001:0` and **12 files
  `600 10001:0`**.
- After recreating app, worker, migrate, tokenizer-mock and seed-sources as `1000770000:0` on the same
  volumes:
  - `GET /api/v1/artefacts/{id}` for the 11 run-A artefacts gave **11 × 500 `internal_error`**
    (`api_error error=PermissionError` in the app log). The same 11 gave 200 as uid 10001.
  - `GET /api/v1/evidence/{id}/html` for the 5 run-A packs gave **5 × 502 `storage_error`**. This
    route reads the stored HTML, so this is the console's Evidence page.
  - `GET …/pdf` gave 1 × 502 (a stored PDF) and 4 × 200 (rendered fresh by the new UID).

**Deterministic reproduction:** a throw-away volume, the image's own entrypoint and
`LocalObjectStore.put` as uid 10001 give mode `0o600`, gid 0, in a `2775` directory. Then
`LocalObjectStore.get` as 1000770000:0 fails with `storage_error: PermissionError`. The new UID can
still create files; uid 10001 reads its own file.

**Why it is blocking:**
- The property is the commit's own design goal. It is stated in `Dockerfile:74-80` ("a later UID … keeps
  writing where the previous one wrote"), in binding `HANDOFF.md:104-109` ("everything a process
  creates on the artefact and export volumes stays writable for the next UID of group 0 (… a redeploy
  under another UID)"), and in `M5-DEVIATIONS.md:1134` ("what any UID creates there is
  `2775`/`664`").
- That statement is false for every artefact and every local export.
- `tests/test_image_runtime.py:169-210` ("pins" the property) writes with `Path.write_bytes` and so
  passes regardless.
- The implementer's live "same volumes, new UID" proof did not catch it because smoke and flow only
  read artefacts created in the same run.
- Task 25 (OpenShift) and operators would rely on the HANDOFF statement.
- Operationally it bites only on a UID change over a persistent local volume. Under restricted-v2 the
  namespace UID is normally stable, and an S3 artefact store is unaffected.

**Fix (test first), option A (preferred):**
1. Make `LocalObjectStore` honour the group: right after `mkstemp` in `_spool`, `os.fchmod(fd,
   0o660)`, which gives owner and group rw and nothing to others.
2. On the volume (group 0, setgid directories) every UID of group 0 can then read and replace
   artefacts and exports.
3. This widens nothing in practice: the volume is mounted only into DPS containers, which all run in
   group 0.

**Tests for option A:**
- A unit test that a stored object's mode is `0o660` (group read/write, no others).
- Extend `test_a_new_uid_keeps_writing_where_the_previous_one_wrote` (or add a runtime test) to write
  **through `dps.storage.LocalObjectStore.put`** as the image user and `get` it as `1000770000:0`.
  This test fails today.
- Re-run the runtime tests on arm64 and amd64.
- Re-run the live cross-UID check: run-A artefacts and evidence HTML must answer 200 as the new UID.

**Option B (if 0600 is deliberately kept):**
- Correct the four statements above to say that a UID change on an existing local volume needs the
  one-time `chmod -R g+rwX` re-permission (the command already in HANDOFF §8).
- Make the runtime test assert the real behaviour.

## Non-blocking findings and observations

- **N1: smoke `api responses are not cacheable` fails by dependency.**
  - `Cache-Control: no-store` on every `/api/` response is M5 Task 17 **S11**. The M5 plan lists
    `dps/app.py` `_SecurityHeaders` under Task 17.
  - Confirmed absent on `fimg` and on `fsec`: both have only per-route `no-store` in admin, jobs,
    groups, artefacts, reports and evidence. `fsec` has uncommitted `tests/security/`, so it is in
    progress.
  - The check itself is correct and has a unit test proving it can fail.
  - **Carry as a merge gate:** after `fsec` merges, `DPS_SMOKE_REQUIRE_M5=1 python tools/smoke_test.py`
    must report all checks passed. Do not add the middleware in `fimg`: that would duplicate or
    conflict with S11.
- **N2: wrong error class for unreadable artefacts.** `LocalObjectStore.stream` (`dps/storage.py:195`) catches only
  `FileNotFoundError`/`IsADirectoryError` at open. A `PermissionError` therefore surfaces as **500
  `internal_error`** on `/artefacts/{id}`, while `get` maps it to `storage_error` (502). This is M2
  code; map all `OSError` to `StorageError("storage_error", …)` as `get` does. It is worth doing
  together with B1.
- **N3: HANDOFF §6 overstates compose hardening.** It says compose runs "every role with a read-only
  root, a `/tmp` tmpfs, `cap_drop: [ALL]` and `no-new-privileges`". In fact `migrate` and
  `tokenizer-mock` have no `/tmp` tmpfs, so `HOME=/tmp` is unwritable there (harmless today).
  `demo-secrets` runs as root without `cap_drop`; this is a pre-existing local one-shot. Fix the
  wording, or add the tmpfs and `cap_drop` (optional hardening).
- **N4: the flow report leaves records behind.** Each run leaves a tenant (`flow-<stamp>`) and a
  tenant policy, which cannot be deleted. Its job group, jobs and evidence also stay in the operator
  key's default tenant. These appear on the console's Admin and Policies pages. F3's
  `e2e_demo.py --console` screenshots should run on a fresh stack, or before any flow run, or the flow
  should reuse fixed names.
- **N5: scanner rows can be skipped silently after M4 merges.** When `/readyz` lacks `browser`, the
  10 scanner rows are *skipped*, not failed. Add a require switch (like `DPS_SMOKE_REQUIRE_M5`) so the
  DoD-6 run on `full` cannot pass without exercising the scanner. Also update the scanner `EXPECT`
  templates if the M4 pre-merge renamed any; the coverage test enforces this.
- **N6: two pre-existing mypy errors in `tools/smoke_test.py`.** They come from main and are outside
  the `mypy dps` gate.
- **N7: unlogged plan deviation.** The plan says `smoke_test.py` imports `demo_client`; it does not,
  and keeps its own httpx client. Trivial.
- **N8: setuid binaries from the Debian base.** `su`, `passwd`, `mount` and similar remain. They are
  inert under `no-new-privileges` and restricted SCC. Optional hardening for F1/T25 (strip the setuid
  bits).
- **N9 (out of scope, for F1): kept columns can link UAT rows back.** The kept columns `created_at`,
  `updated_at`, `relationship_manager_id` and `risk_score` are a unique quasi-identifier in
  `bank.customers`. Every row of an obfuscated UAT copy can therefore be paired 1:1 with its source
  row; that is how I proved 0 same-row survivors. This is inherent to pseudonymised copies, but
  verbatim timestamps also let UAT data be linked to other datasets. Consider a line in the security
  review's linkability notes, or a default `date_shift` on audit timestamps in the presets.

## Deviations (`M5-DEVIATIONS.md` "Task 24"): assessment

1. `g=u` only on `/app/var`, code `g+rX,g-w`: **agree.** The plan's `chmod -R g=u /app` would make
   the code writable for every OpenShift UID.
2. `--gid 0`, setgid, entrypoint `umask 0002`: **agree with the direction, incomplete** (B1: files
   0600).
3. Console stage on `$BUILDPLATFORM`: **agree.** Verified that the cold amd64 build runs npm natively.
4. No recursive chmod of `/ms-playwright` (+1.46 GB): **agree.** The read-only root is mandatory for
   the browser-worker; the compose test pins this, and T25 must do the same. Not re-measured.
5. M5 env in the shared anchor (overridable); `app depends_on tokenizer-mock` (`required: false`):
   **agree.** Readiness showed the tokenizer up under `full`.
6. `/reports/` anchored, `.dockerignore reports`: **agree.** `dps/layers/reports` is still tracked and
   present in the image.
7. Smoke: 8th check `db key disabled`, poll instead of `sleep`, 64-hex check, `REQUIRE_M5`:
   **agree.**
8. `demo_client` additions, `load_keys` also refuses world-writable files: **agree.** Errors carry the
   template and code only, and never a URL.
9. `flow_report`: operator key by default, `--demo-keys` opt-in, two grouped runs, outcome checks:
   **agree** (see N4 and N5).
10. Contract unchanged: **agree.**

## State left behind

- Branch `fimg` is at `74625d6`; the worktree is clean (`git status` is empty) and nothing was
  committed.
- The lane stack (`dps-wt-fimg`) is **down with volumes removed**, as the implementer left it.
- My review image tag `data-privacy-services:fimg-review-amd64` was removed. The implementer's images
  (`:fimg`, `:fimg-amd64`, the OCR mock `:fimg`) are untouched.
- No other compose project, container, volume or image was touched.
- Reviewer outputs are in the session scratchpad `…/scratchpad/s4r/`: smoke and flow logs and both
  flow reports, the log-scan script and results, the source-vs-target SQL, the build log, and the
  override file.
