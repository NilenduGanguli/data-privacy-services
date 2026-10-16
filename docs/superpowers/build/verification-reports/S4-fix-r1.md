# S4 fix r1: review S4 r1 B1 (artefact and export objects were 0600), with N2 and N3

**Stream:** S4 (M5 Task 24, finish stage F2) · **Worktree:** `/Users/neelu/document_intelligence/dps-wt-fimg`
· **Branch:** `fimg` · **Commit:** `fc0d192` "fix(fimg): store objects 0660 so a new UID of group 0 reads
earlier artefacts" (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer, explicit pathspec, 6 files)
on top of `74625d6` · **Review:** `~/.cache/dps-build/reports/S4-review-r1.md` · **Date:** 2026-10-14 ·
**Logs:** `~/.cache/dps-build/s4fix/`

## Verdict

**B1 is fixed, test first, and proven live in both directions of a UID change.** N2 (wrong error
class for unreadable objects) is fixed with it. N3 (HANDOFF §6 overstated compose hardening) is
corrected in the docs. All gates are green. The only smoke failure is still `api responses are not
cacheable`. That check depends on M5 Task 17 S11 (lane `fsec`), as the review's N1 says: it is a
merge gate, not a `fimg` defect, and I did not add the middleware here.

## What changed

| File | Change |
|---|---|
| `dps/storage.py` | **B1:** `_spool` no longer uses `tempfile.mkstemp`, which always creates mode 0600. A new `_create_spool_file(directory, mode)` makes `.dps-spool-<16 hex>` with `os.open(O_WRONLY\|O_CREAT\|O_EXCL\|O_NOFOLLOW, mode)`, retrying on a name clash (100 tries). This keeps mkstemp's guarantees: never an existing path, never through a symlink. `LocalObjectStore.put` passes `OBJECT_MODE = 0o660`. The S3 upload spool in the system temp dir passes `0o600`. **N2:** `LocalObjectStore.stream` maps every `OSError` to `StorageError("storage_error", <class>)`, both at open and mid-stream (`FileNotFoundError` and `IsADirectoryError` still give `storage_not_found`). `exists` does the same. Messages carry the class name only, never the path. Module docstring updated. |
| `tests/test_storage_local.py` | 8 new tests (details below). |
| `tests/test_image_runtime.py` | The `write_bytes` test is replaced by `test_a_new_uid_reads_and_replaces_what_the_previous_one_stored`. It goes through `dps.storage.LocalObjectStore`, as the review asked. |
| `Dockerfile` | The arbitrary-UID comment now says what holds: directories 2775, objects 0660 (`OBJECT_MODE`), and a later UID reads, replaces and adds to what the previous one stored. Comment only; every layer was cached on the rebuild. |
| `HANDOFF.md` | §2 corrected (directories 2775, objects 0660 less umask, what the runtime test checks). §6 (N3): which roles have a `/tmp` tmpfs; `demo-secrets` runs as root. §8 trap: objects stored by any image up to `74625d6` are 0600, measured 502; the existing one-time re-permission fixes it. §15: a "Review fix r1" record with the verification. |
| `docs/superpowers/build/M5-DEVIATIONS.md` | Task 24: the "2775/664" statement is corrected. New bullet "Review fix (S4 r1 B1, N2, N3)" with the cause, the fix, the deviation from the review's suggested code, and the tests. |

### Deviation from the review's suggested code (option A, kept in spirit)

The review suggested `os.fchmod(fd, 0o660)` right after `mkstemp`. That call sets 0660 whatever the
process umask is. Instead I create the file with mode 0660 and let the kernel apply the umask
atomically. There is no portable way to read the umask without a thread race.

- **Inside the image** (entrypoint umask 0002) the result is identical: **0660**.
- **With a stricter umask** an operator's choice still narrows the mode, which a hard-coded fchmod
  would override: 0022 gives 0640 (group can still read), 0077 gives 0600.
- **Other users never get any bit**, not even under umask 0.

Cross-UID behaviour does not depend on the file's group-write bit. Replace (`os.replace`) and
delete only need write access to the directory.

The S3 spool in the system temp dir stays owner-only. That temp dir is shared on a host, so widening
it would be a real leak. A unit test pins this.

## Test-first evidence

1. **Unit tests, red before the fix:** 6 failed, 15 passed.
   - `test_a_stored_object_is_shared_with_the_group_and_never_with_other_users` (under umask 0002): got `0o600`, expected `0o660`.
   - `test_a_stricter_umask_narrows_…` for umask 000, 002 and 022: all got `0o600`. The 077 case passed both before and after.
   - `test_an_unreadable_object_is_a_storage_error_on_every_read`: a bare `PermissionError` came from `stream` and `exists`.
   - `test_a_read_failure_mid_stream_is_a_storage_error_and_closes_the_file`: a bare `OSError`.
   - `test_an_s3_upload_spools_privately_in_the_temp_directory` was a guard. It passed before and after: 0o600 even under umask 0.
   - **After the fix: 21 passed.**
2. **Runtime test, red on the pre-fix image.** `data-privacy-services:fimg` was the image of `74625d6`, sha256:fbbb9eca5c0a. Log: `runtime-red-arm64.log`.
   - Result: `{'artefacts': {'file': {'mode': '0o600', 'gid': 0}, 'dir': {'mode': '0o2775', 'gid': 0}}, 'exports': …0o600…}`.
3. **The review's B1 reproduced on the same pre-fix image** (`b1-repro-prefix-image.log`):
   - Uid 10001 stores through `LocalObjectStore.put`, which gives 0o600 in a 2775 directory.
   - Uid 1000770000:0 then fails: `dps.storage.StorageError: storage_error: PermissionError`.

## Gates (worktree, at `fc0d192`)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` / `ruff check dps tests tools` | 383 files formatted / all checks passed |
| `./lane.sh mypy dps` | clean, 134 files |
| `./lane.sh pytest -q` (stack down) | **2211 passed, 492 skipped** (review baseline 2203/492; +8 new tests). The 1 warning is pre-existing: a Starlette testclient deprecation, also present at `74625d6`. |
| Image builds with the fix | arm64 `docker compose build app`: exit 0, 4 min 26 s. amd64 `docker build --platform linux/amd64 -t data-privacy-services:fimg-amd64 .`: exit 0, 4 min 59 s. Rebuilt from HEAD: all 15 steps `CACHED` on both. The IDs changed only through BuildKit metadata, and the Dockerfile change is a comment. |
| `DPS_TEST_IMAGE=… pytest -q tests/test_image_runtime.py` | **3 passed on arm64** (`data-privacy-services:fimg`, 4a3cf077f70c) and **3 passed on amd64** emulated (`:fimg-amd64`, 6d7a39db3769). Run both after the build and again on the HEAD rebuild. No probe volume left behind. |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "db or pg_source or s3"` (lane stack up) | **341 passed** (7 min 43 s) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m "docs or ocr or oracle or mongo"` | **301 passed, 3 skipped**: the same 3 skips as before (2 need a host `tesseract`, 1 is MongoDB "no composite keys"). Its M3 console tests rewrote the 54 tracked screenshots in `docs/superpowers/build/m3-shots/`; I restored them from HEAD and committed none. |
| gitleaks (`zricethezav/gitleaks`, as `autopush.sh` runs it) on `main..fimg` | exit 0, "no leaks found", 2 commits. Both are authored by nilendu.ganguli@gmail.com with no trailer. |

## Live cross-UID check (lane project `dps-wt-fimg`, `--profile full`, fresh volumes, the fixed image)

Value-free throughout: ids, kinds, HTTP statuses, modes and counts only. Scripts:
`~/.cache/dps-build/s4fix/cross_uid_check.py` and `pvc_export.py`. Override:
`arbitrary-uid.override.yml`, which sets `user: "1000770000:0"` on app, worker, migrate,
tokenizer-mock and seed-sources. Under the override, `/proc/1/status` showed Uid 1000770000, Gid 0,
Umask 0002, read-only root.

1. **Run A as uid 10001.** `/readyz` was ready: api-core, llm-gateway stub, fpe, tokenizer, ocr azure, db, jobs and keys; ner is off by design. The one-shots exited 0.
   - Seed: `tools/seed_sources.py --target all`.
   - `DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 smoke_test.py --m3`: **48/49**. The only FAIL is S11.
   - `flow_report.py`: **PASS, 92 calls, 0 unexpected**.
   - Result: 18 artefacts and 8 evidence packs, all read 200 as uid 10001. My PDF reads stored 7 more `evidence_pdf` objects, for 25 in total.
   - The volume held **25 files `660 10001:0`** and 12 directories `2775 10001:0`.
2. **Every role as 1000770000:0 on the same volumes (the B1 check).** Before the fix the review measured 11 × 500 and 5 × 502 here.
   - `GET /artefacts/{id}`: **25/25 → 200** (8 evidence_html, 8 evidence_json, 8 evidence_pdf, 1 manifest).
   - `GET /evidence/{id}/html`: **8/8 → 200**. This route reads the stored HTML, so it is the console's Evidence page.
   - `GET /evidence/{id}/pdf`: **8/8 → 200**, all of them PDFs stored by uid 10001.
3. **Run B as the new UID:** smoke `--m3` **48/49** (S11 only); flow **PASS, 92 calls, 0 unexpected**.
4. **Back to uid 10001 (the reverse direction):** all **43 artefacts → 200**, all **16 packs → 200** on `/html` and `/pdf`.
5. **Local `pvc` exports** (CSV, a `files` target with `base_path` `s4fix`):
   - Uid 10001: `succeeded`, 1 object plus manifest, verified pack.
   - The new UID, into the same `base_path` that uid 10001 created: `succeeded`, verified pack.
   - The exports volume then held only `2775` directories and `660` files of both UIDs, group 0.
6. **Final state, as the new UID:** all **56 artefacts and 18 packs → 200**.
   - Volumes: `{'d 2775 10001:0': 17, 'd 2775 1000770000:0': 11, 'f 660 10001:0': 37, 'f 660 1000770000:0': 25}`.
   - No `0600` file and no other mode.

## Privacy proofs

- **Log scan** (the review's `log_scan.py`, counts only): 21 sources, 1.08 MB. They are every
  container of the lane in its final state, plus both smoke logs, both flow logs and reports, and
  both pvc export logs.
  - Needles: the smoke/flow sample record, the SSN probe, the bootstrap key, the tokenizer token and
    key, the demo KEK, and 2 000 sampled source values each of full_name, email, mobile, national_id
    and tax_id. All give **0 hits**.
  - Raw `dps_`+43 keys and `tk_` tokens: **0**.
  - `PermissionError`, `Permission denied`, `Read-only file system`, `StorageError`, `storage_error`: **0**.
  - Tracebacks: **0**.
  - **Limitation:** the app and worker logs of runs A to C were discarded when those containers were
    recreated for the UID switches. Their tool outputs are in the scan; the final containers' logs
    cover run D.
- **Source vs target:** not re-run. This change touches only file modes and error mapping, not any
  value transformation. The review's counts on the same code stand: 0 same-row survivors. Every run
  above ended with a verified evidence pack, and the smoke's raw-PII-to-LLM count is 0.
- **D2, D4, P1, D-A:** no code on those paths changed.

## Not addressed (non-blocking review items, outside this fix)

- **N1:** the S11 merge gate. After `fsec` merges, `DPS_SMOKE_REQUIRE_M5=1 smoke_test.py` must report all checks passed.
- **N4:** flow-report residue (tenant and policies).
- **N5:** a scanner-row require switch.
- **N6:** the pre-existing mypy errors in `tools/smoke_test.py`.
- **N7:** the unlogged smoke/demo_client import.
- **N8:** the Debian setuid bits.
- **N9:** linkability of kept columns. This is for F1.

## State left behind

- Branch `fimg` at `fc0d192`, worktree clean, not pushed. I did not run `autopush.sh`: this is a
  review-fix commit, not a milestone, and the owner's loop pushes it.
- Lane stack **down with `-v`**: no `dps-wt-fimg` containers or volumes remain. No other compose
  project, container, volume or image was touched.
- Images: `data-privacy-services:fimg` (arm64) and `:fimg-amd64` rebuilt at HEAD.
  `document_intelligence-azure-ocr-mock:fimg` is unchanged.
- Logs: `~/.cache/dps-build/s4fix/`. They hold the build logs, the red and green runtime logs, the
  B1 reproduction, both smoke runs, both flow runs and reports, the pvc logs, the id lists (ids only),
  the read results, the modes, the log scan and the gitleaks output.
