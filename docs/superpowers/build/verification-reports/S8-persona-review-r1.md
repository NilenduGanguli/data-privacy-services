# S8-persona: review round 1 (of the fix: `def4fe1` + `b18abc7`)

**Verdict: FAIL, with 1 narrow blocking finding (B3).**
- The earlier review's B1 (order dependence) and B2 (role without detection) are fixed. I verified both independently on L1 and L2.
- Gates are green apart from environmental failures (§1).
- Acceptance (1) to (3) still holds.
- B3: the shared name-part table still misses two key families. These are plural surname keys (`surnames`, `last_names`, `family_names`) and the name-first order of SAP's business-partner table (`NAME_FIRST` / `NAME_LAST`). With NER off, a synthesize run copies those real names into the synthetic output and verification still **passes**. This is proven on L1, on an L2 BatchTransformer run, and on a real L2 job on this lane's source-pg. When those columns are classified as names anyway, the brief's own bug (1) comes back.
- The fix is a few table entries and tests.

**Reviewed:** branch `fpersona` at `b18abc7`. That is `def4fe1` (fix for review r1 B1 + B2) and `b18abc7` (docs), on top of `e437f48` (round 0) and `5cef802`.
- The worktree is clean.
- Both commits are authored by `NilenduGanguli <nilendu.ganguli@gmail.com>` and have no trailer.
- Neither commit has been pushed yet. The implementer did not run autopush.

**About this file.** The orchestration re-ran "review round 1" after the implementer had already answered the first r1 review (of `e437f48`: FAIL, B1 + B2). That earlier report is preserved unchanged at `S8-persona-review-r1/S8-persona-review-r1.e437f48.md`. This file reviews the fix. Its finding is numbered B3 so that it does not clash with the B1/B2 that the code comments and M5-DEVIATIONS cite.

I did not modify any repository file.
- Logs, junit files, probe scripts and outputs are in `~/.cache/dps-build/reports/S8-persona-review-r1/b18abc7/`.
- The probes use only Faker en_IN personas or invented names. Every number below is a count.

## 1. Gates (run by me on this lane: dps-db 5611, source-pg 5612, MinIO 9611, Mongo 27611, Oracle 1611)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check dps tests tools` | 436 files already formatted / all checks passed |
| `mypy dps` | no issues in 150 source files |
| `./lane.sh python -m pytest -q` (whole suite, stack up, no app image, no OCR mock) | **3736 passed, 95 skipped, 5 failed** in 24:18 (`full-suite.log/.xml`). This is the implementer's result exactly. The 5 failures are the known environmental ones, which the previous review showed also fail on `5cef802`:<br>• `test_capture.py::test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key`<br>• `test_capture_residual.py::test_masked_capture_boxes_every_copy_of_a_detected_value`<br>• `test_crawler.py::test_full_demo_crawl_in_memory` and `::test_dialogs_dismissed_and_popups_closed` (all four: the local Chromium build)<br>• `test_playground_samples.py::test_text_sample_with_ner_on_still_never_returns_the_name` (OCR mock down, so coverage is `partial`)<br>`test_scan_job_e2e` passed in this run. |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh python -m pytest -q -m "db or pg_source"` | **425 passed, 1 failed, 6 errors** (11:33). The 1 failure is a test artefact and passes in isolation, see below. Net result: **426 passed, 6 errors**, the same as the previous review's run on `e437f48`. |

Notes on the failures and errors:
- **The 6 errors are console browser tests** (`test_console_m2.py` ×3, `test_console_m3_groups.py` ×3), which fail with `app unreachable`. This lane has no app image.
- **The 1 failure was `tests/scanner/test_scan_job_e2e.py::test_scan_job_end_to_end`**, on the D4 counter (`non_get` 7 vs 0). It was caused by my own test setup. I ran the gate at the same time as the whole suite, and the `demo_site_url` fixture reuses a demo site that is already up on 8612. The whole-suite process had started that site, and its own scanner tests were writing to it at the time, so the gate read counters that the other process had raised.
  - Re-run in isolation on a private demo site (`DPS_TEST_DEMO_SITE=http://127.0.0.1:18612`), the whole file passes: **5 passed**, and the D4 counters are unchanged (`scan-job-e2e-isolated.log`).
  - The diff does not touch the scanner's network rail. It only widens `classify_key`.
- **Key synthesize tests in the gate all passed:**
  - `test_synthesize_people` (the 300-row job)
  - `test_synthesize_identifiers` 8/8, `test_demo_pii_survival` 11/11, `test_synthesize_copula` 7/7
  - `test_m3_free_text_names` 4/4
- **Test-first claim checked.** I copied the six changed test files onto a `git archive e437f48` tree. With `dps` resolved to that tree they give **48 failed, 375 passed**, which is exactly the implementer's number. On the branch they pass.

## 2. The earlier blocking findings: fixed

**B1 (order dependence): fixed.**
- **L2 BatchTransformer, 80 rows, both synthetic presets.** I compared `NOMINEE_FAMILY_NAME` before `NOMINEE_GIVEN_NAME` against `NOMINEE_FIRST_NAME` / `NOMINEE_LAST_NAME` (`l2_regress.py`). It is the same synthetic nominee in **80/80** rows; on `e437f48` it was 0/80. The customer is unchanged in 80/80.
- **L2 column-order permutations** (`l2_perm.py`). This used 60 rows and 12 random column orders of `FIRST_NAME, LAST_NAME, EMAIL, NOMINEE_GIVEN_NAME, NOMINEE_FAMILY_NAME, NOMINEE_EMAIL, NOTES`, plus a full-name nominee layout. Every column's output was identical in every order, and there were 0 verification failures.
- **L1 key-insertion-order permutations** (`l1_probe2.py order`). Over 13 layouts × 40 records × 4 orders (1,600 runs), there were 0 differences.

**B2 (a role without detection): fixed by construction.**
- `methods.name_field` and `classify_key` now read one table (`structural.NAME_PART_KEYS` / `name_part`).
- **Detection sweep** (`detection-sweep-diff.txt`). I classified 2,618 keys collected from the repo's tests, tools, demo, docs and source, plus bank column names, in 6 path shapes on `e437f48` and on `b18abc7`. Only 17 keys changed, all in the intended direction: `PERSON_NAME` was added or raised and nothing was lost or changed entity. The 17 are:
  - `FName`, `LName`, `custFName`, `custLName`, `f_name`, `l_name`, `foreName`, `surName`
  - `first_names`, `firstnames`, `forenames`, `given_names`
  - `first`, `given`, `middle`, `last`, `family` (only under a `name` / `full_name` parent)
- **Dotted L2 columns are consistent too.** `$['GIVEN.NAMES']` and `$['FAMILY.NAME']` are detected at 0.9 with roles first and last. (A raw `classify_key("GIVEN.NAMES")` misses, but that is not the path L2 uses.)
- **New shapes through the L1 service, NER off, both presets, 60 runs each** (`l1_probe2.py shapes`). Layouts:
  - `given_names+surname`, `forenames` (2 words) + `surname`, `first_names+last_name`
  - `FName/LName`, `custFName/custLName`, `f_name/l_name`, `foreName/surName`, `FIRSTNAME2/LASTNAME2`
  - `name.{first,last}`, `customer.name.{given,family}`, `name.{first,middle,last}`, `middle_names`

  Result: **0 originals kept and 0 verification failures**.
- **Real L2 synthesize job on this lane's source-pg** (`test_l2_name_columns_probe.py`, 200 rows, `l2-job-name-columns.out`).
  - Discovery: `given_names`, `surName`, `FName`, `LName`, `f_name`, `l_name` and `middle_names` are all `PERSON_NAME` → `surrogate`.
  - Target vs source: **0** values equal to the source row's in each of those 7 columns, and **0** rows where those columns plus the e-mail hold any source name part.
  - The job succeeded and verification passed.

## 3. Acceptance, re-checked on `b18abc7`

(1) The live record's name fields agree, and customer and nominee stay two people. **Still met.**
- L1 and L2 outputs for every layout without a family-first secondary group are **byte-identical to `e437f48`**:
  - L1 corpus: 13 layouts × 40 records × 2 presets = 1,040 runs (`l1_probe2.py dump`). Only `own_split_nominee_family_first` differs, which is the intended B1 change.
  - L2 (`l2_regress.py`): full name only; split + full-name nominee + titled notes; split + given-first nominee; two upper-case full names. All are identical; only the family-first nominee columns differ.
- So the acceptance evidence of round 0 still applies.

(2) Titles: unchanged by this fix. The titled-notes layouts are identical to `e437f48`.

(3) L2: `test_synthesize_people` passes, with counts all 0.

## 4. Blocking finding

### B3. Plural surname keys and SAP's name-first keys get no role and no detection, so synthesize copies real names (or brings back bug (1))

- **Where:** `dps/engine/detect/structural.py`, in `NAME_PART_KEYS` / `name_part`. This table is shared with `methods.name_field`.
- **The defect.** There are two gaps in the table.
  - **(a) No plural surname forms.** The table has plural forms for the first name (`firstnames`, `givennames`, `forenames`) and the middle name (`middlenames`), but none for the last name. So `surnames`, `last_names`, `lastnames`, `family_names` and `familynames` score **nothing**: the phrase rules need the word `name`, not `names`. They get no role either.
  - **(b) Name-first word order.** The order of SAP's business-partner table (`BUT000.NAME_FIRST`, `NAME_LAST`; also `NAME_MIDDLE` and the run-together `NAMEFIRST` / `NAMELAST` / `NAMEMIDDLE`) scores only `name` 0.55, below the 0.7 floor, and gets no role.
- **Effect.** All of this is with NER off, the default image. Outputs are in `sap-plural-l1.out`, `discovery-*.out`, `l2-job-name-columns.out` and `sap-l2-batch.out`.
  - **L1 service, en_IN, 40 records per preset:**
    - `{NAME_FIRST, NAME_LAST, EMAIL}` keeps **both real names 40/40** under synthetic-realistic **and** synthetic-strict.
    - `{first_names, last_names}` and `{first_name, surnames}` keep the **real surname 40/40**, next to a synthetic first name and e-mail.
    - Verification passed in every one of these runs.
  - **L2 discovery.** Under synthetic-realistic, `NAME_FIRST`, `NAME_LAST`, `surnames`, `last_names` and `family_names` are classified entity `None`, `keep`. Under synthetic-strict, the plural keys are still `keep`.
  - **Real L2 job on this lane's source-pg** (200 rows, the same job as in §2): `NAME_FIRST`, `NAME_LAST`, `surnames` and `last_names` are written to the target **equal to the source row's value in 200/200 rows each**. The job **succeeded** and the evidence pack says verification **passed**.
  - **When NAME_FIRST / NAME_LAST are classified as names.** That happens under synthetic-strict discovery (0.55 is enough there) or through a reviewer's override. With no role, the brief's bug (1) is back. In a BatchTransformer run of 100 rows, the last-name column holds a first name in **99/100** rows and the e-mail agrees in **0/100**. The same rows under `FIRST_NAME` / `LAST_NAME` give 0/100 and 100/100.
- **Why it blocks.**
  - **Not a regression.** `e437f48` and `5cef802` classify these keys the same way.
  - **Same class as r1 B2.** A key that holds a part of a person's name is missed with NER off, so a synthesize run copies real names and verification passes. This class is the reason for the finish-stage work ("`fname`/`lname` … were not detected at all with NER off, so a synthesize run copied the real names").
  - **The commit overclaims.** `def4fe1` says "every name-part key is detected".
  - **It is common in banks.** `NAME_FIRST` / `NAME_LAST` is SAP Business Partner's standard layout. Under the default preset, a bank's SAP extract would put real customer names into "synthetic" UAT data, with a passing evidence pack.
  - **It is half of the r1 B2 fix.** Plural surnames are the counterpart of the plural given-name keys that fix just added.
- **Fix** (narrow; the reviewer of round 2 can check it mechanically).
  1. Add to `NAME_PART_KEYS`:
     - plural last forms: `lastnames`, `surnames`, `familynames` → `("last", 0.9)`;
     - name-first order, run together: `namefirst`, `namegiven` → `("first", 0.85)`; `namemiddle` → `("middle", 0.85)`; `namelast`, `namefamily` → `("last", 0.85)`.

     `name_part` already joins a key's last two words, so `NAME_FIRST` becomes `namefirst` at n = 2, and the group stays the words before it: `CUST_NAME_FIRST` → group `cust`, `NAME_FIRST` → the own group `$||`.
  2. Tests first:
     - add these spellings to `NAME_PART_PATHS` (the invariant test) and to `test_methods` roles: `NAME_FIRST`, `NAME_LAST`, `NAME_MIDDLE`, `NameFirst`, `namelast`, `CUST_NAME_LAST`, `surnames`, `last_names`, `LASTNAMES`, `family_names`;
     - an L1 service test with NER off: `{NAME_FIRST, NAME_LAST, EMAIL}` and `{first_names, last_names, email}` keep no original, and e-mail = `<first>.<last>`;
     - a discovery test: `NAME_FIRST`, `NAME_LAST`, `surnames`, `last_names` → `PERSON_NAME` with a synthesize method other than `keep`;
     - the guard list keeps `$.username_first` and `$.name_first_seen` undetected.
  3. M5-DEVIATIONS: record the change. Also make "Known limits" complete about what is still missed with NER off (see N1 below), so the owner sees the whole class.

## 5. Non-blocking findings

- **N1. Other spellings are still missed with NER off.** All of these are pre-existing, in the same class as the documented `agent_name` limit. I suggest them as an owner follow-up: a lexicon-backed name detector when NER is off, or per-system key lists.
  - Siebel CRM's `S_CONTACT.FST_NAME` and `MID_NAME`: `name` 0.55, so not detected under synthetic-realistic.
  - Abbreviations: `FIRST_NM`, `LAST_NM`, `CUST_FRST_NM`, `CUST_LST_NM`, `GIVEN_NM`, `SURNM`, `FIRSTNM` / `LASTNM`, `MID_INIT`: not detected at all.
  - Other languages: `vorname` / `nachname`, `prenom` / `nom`, `apellidos`: not detected. `nombre` / `apellido` are detected (0.85) but get no role.
  - `NAME_LST2` (SAP's second surname).

  If the fixer is in that table anyway, `fstname` → first, `midname` → middle and `lstname` → last would be cheap. They are not required for PASS.
- **N2. Process-global cache of raw paths.** `@lru_cache(maxsize=4096)` on `_name_part_candidate(key)` keeps raw key/path strings for the life of the process. L1's ensemble passes document paths, and those hold values when a document keys a map by a value (`$.accounts['<account no>'].balance`). Nothing reads them out, so this is not a leak. But it is the first process-wide cache keyed by request data; the surrogate engine keeps request data only per engine.
  - Suggestion: drop the cache. The function is a regex plus dict lookups.
- **N3. Owner decisions from the earlier r1 are still open.** These are its N1 (the P3 cost of titles), N2 ("first title wins"), N3 (`first_name_en` gets no role) and N4 (a prefixed own group does not own the record persona). They are documented in M5-DEVIATIONS and suggested for RESUME's open-decisions list.

## 6. Regression and property checks that passed

- **No reveal or containment.** I ran adversarial L1 records (`adversarial.py`): 7 layouts × 60 × 2 presets = 840 runs. Layouts: customer and nominee with swapped names; the nominee's first name equal to the customer's surname; a shared surname; a first name that is a substring of the last name; the same first name under `name.{first,last}` and `nominee.name.{given,family}`; multi-word `given_names` with a titled note; split `custFName/custLName` plus a reversed full nominee name.
  - No output holds an original name part.
  - 0 verification failures and 0 errors.
- **One-person and customer + nominee records** are byte-identical to `e437f48` on L1 and L2 (§3).
- **D-A.** The L2 job (§2) pseudonymised every key: the target keys map back through `KeyPseudonyms` with 0 unmatched rows. The D-A identifier tests pass, and no key or identifier code changed.
- **D2, D4 and P1 are untouched.**
  - The diff touches no key, scanner-network or LLM code.
  - The scanner gains `PERSON_NAME` classifications through `classify_key`. The D4 job e2e passes in isolation (§1).
  - No log or print call is added (grep of `git diff e437f48 b18abc7 -- dps`).

## 7. Resume and environment

- Docker: only this lane's project was used (dps-db, source-pg, minio, mongo, oracle), started from the stopped containers. I stopped them at the end with `docker compose stop`, and the demo data stays in the volumes.
- For round 2: `docker compose --profile sources --profile oracle start dps-db source-pg minio mongo oracle`.
- When running gates in parallel, give the second process its own `DPS_TEST_DEMO_SITE`. The demo-site fixture reuses a running site, so its D4 counters are shared between processes.
