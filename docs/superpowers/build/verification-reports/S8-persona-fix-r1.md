# S8-persona: fix round 1 (review r1 B3)

**Status: B3 fixed, test first, gates green (only the known environmental failures).** Branch
`fpersona` in `/Users/neelu/document_intelligence/dps-wt-fpersona`: commit `b5a9b75` on top of `b18abc7`.

Evidence (counts only; Faker en_IN personas or invented names, no customer value) is in
`~/.cache/dps-build/reports/S8-persona-fix-r1/`.

## 1. The finding

B3 (review `S8-persona-review-r1.md` §4): the name-part table that detection and
`methods.name_field` share (`structural.NAME_PART_KEYS` / `name_part`) had

- **no plural surname forms**: `surnames`, `last_names`, `lastnames`, `family_names`, `familynames`
  scored nothing and got no part;
- **nothing for the name-first order** of SAP's business partner (`BUT000.NAME_FIRST` / `NAME_LAST` /
  `NAME_MIDDLE`, run together `NAMEFIRST` …): only `name` 0.55, below the 0.7 floor, and no part.

With NER off (default image) a synthesize run copied the real names and verification passed; when
`NAME_FIRST` / `NAME_LAST` were classified as names anyway, the last-name column held a first name
and the e-mail did not agree.

## 2. The fix (`dps/engine/detect/structural.py`)

`NAME_PART_KEYS` gains, exactly as the review asked:

| keys (a key's last words run together, lower case) | part | score |
|---|---|---|
| `lastnames`, `surnames`, `familynames` | last | 0.9 |
| `namefirst`, `namegiven` | first | 0.85 |
| `namemiddle` | middle | 0.85 |
| `namelast`, `namefamily` | last | 0.85 |
| optional (review N1): `fstname` / `midname` / `lstname` (Siebel `S_CONTACT.FST_NAME`, `MID_NAME`; `LST_NAME`) | first / middle / last | 0.85 |

`name_part` already joins a key's last two words, so `NAME_FIRST` → `namefirst` (n = 2) and the words
before it name the person: `CUST_NAME_LAST` → group `cust` (same as `CUST_FIRST_NAME`), `NAME_FIRST` →
the record's own group `$||` (same as `FIRST_NAME`, `surnames`), `NAME_FIRST_2` → `$||2` (same as
`FIRST_NAME_2`). Phrase rules unchanged: detection only grows.

Also (review N2, non-blocking, a two-line deletion): the `@lru_cache(maxsize=4096)` on
`_name_part_candidate` is gone. It kept raw keys/paths for the life of the process, and an L1 path can
hold a value (a document keyed by an account number). The function is a parse plus dict lookups;
`classify_key` already runs its whole regex table uncached on every call.

## 3. Tests (written first)

`tests-before-fix.log`: on `b18abc7` + the new tests, **67 failed, 191 passed** (the selected
name-part tests). `tests-after-fix.log`: **258 passed**.

- `tests/test_structural.py`: 32 more spellings in `NAME_PART_PATHS` (the invariant: a role ⇒
  `classify_key` and `classify_path` ≥ 0.7 `PERSON_NAME`, also as an L2 column name): `surnames`,
  `SURNAMES`, `last_names`, `lastNames`, `LASTNAMES`, `lastnames`, `family_names`, `familyNames`,
  `familynames`, `NOMINEE_SURNAMES`, `NAME_FIRST`, `NAME_LAST`, `NAME_MIDDLE`, `NAME_GIVEN`,
  `NAME_FAMILY`, `NameFirst`, `NameLast`, `nameFirst`, `namelast`, `NAMEFIRST`, `NAMEMIDDLE`,
  `NAMELAST`, `CUST_NAME_FIRST`, `CUST_NAME_LAST`, `NAME_FIRST_2`, `name_last2`, `FST_NAME`, `fstName`,
  `MID_NAME`, `midname`, `LST_NAME`, `lstname`. Guards (stay undetected, pass on both trees):
  `$.username_first`, `$.name_first_seen`, `$.NAME_LAST_UPDATED`, `$.name_lastly`, `$.midnight`,
  `$.list_names`.
- `tests/test_methods.py`: 18 roles (`surnames`, `last_names`, `LASTNAMES`, `lastNames`,
  `family_names`, `familynames`, `NAME_FIRST`, `NAME_GIVEN`, `NAME_MIDDLE`, `NAME_LAST`, `NAME_FAMILY`,
  `NameFirst`, `namelast`, `NAMEMIDDLE`, `CUST_NAME_LAST`, `FST_NAME`, `MID_NAME`, `LST_NAME`); groups
  (`NAME_FIRST`/`NAME_LAST`/`surnames` are the record's own group; `CUST_NAME_LAST` = `CUST_FIRST_NAME`
  ≠ `NAME_LAST`; `NAME_FIRST_2` = `LAST_NAME_2` = `FIRST_NAME_2`); 4 guards get no part
  (`$.username_first`, `$.name_first_seen`, `$.NAME_LAST_UPDATED`, `$.list_names`).
- `tests/test_service.py` (L1, NER off): six more `NAME_PART_LAYOUTS` (`first_names`/`last_names`,
  `first_name`/`surnames`, `given_names`/`family_names`, `NAME_FIRST`/`NAME_LAST`,
  `NameFirst`/`NameMiddle`/`NameLast`, `FST_NAME`/`MID_NAME`/`LAST_NAME`): no original word in the
  output, e-mail = `<first>.<last>`; new `test_sap_and_plural_name_keys_keep_no_real_name_with_ner_off`:
  `{NAME_FIRST, NAME_LAST, EMAIL}`, `{first_names, last_names, email}`, `{first_name, surnames, email}`
  × synthetic-realistic / synthetic-strict: verification passes, no original kept, the parts are an
  en_IN first / last name, e-mail agrees.
- `tests/test_discovery_classify.py`: `test_name_part_columns_are_names_with_ner_off` now runs under
  both synthetic presets and includes `surnames`, `last_names`, `family_names`, `NAME_FIRST`,
  `NAME_MIDDLE`, `NAME_LAST`, `NAMEMIDDLE`, `FST_NAME`, `MID_NAME`: each `PERSON_NAME` with a
  synthesize method other than `keep`.
- `tests/test_batch.py` (L2): `test_sap_name_first_columns_hold_the_row_persona_s_parts`, both
  presets: SAP `BUT000` rows (`NAME_FIRST`, `NAME_MIDDLE`, `NAME_LAST`, `EMAIL`) planned as names: each
  column holds the row persona's part (first in en_IN first names, last in en_IN last names), the
  e-mail agrees, no original kept, verification passes. (This is the review's bug (1) reproduction.)

## 4. Proofs and gates

### Proofs (the review's own probes, re-run on the fix; counts only)

- **L1 service, NER off, en_IN, 40 records per preset** (`sap-plural-l1.out`, review's `sap_probe.py`):
  `NAME_FIRST`/`NAME_LAST`, `first_names`/`last_names`, `first_name`/`surnames` under both presets:
  first kept **0**, last kept **0**, last field holds a first name **0**, e-mail agrees **40/40**,
  verified 40/40 (review on `b18abc7`: kept 40/40, e-mail 0–1/40).
- **L2 BatchTransformer, 100 rows planned as names** (`sap-l2-batch.out`, review's
  `l2_sap_strict.py`): `NAME_FIRST`/`NAME_LAST` under both presets: last column holds a first name
  **0/100**, e-mail agrees **100/100** (review: 99/100 and 0/100); `FIRST_NAME`/`LAST_NAME` unchanged
  (0 and 100/100).
- **L2 discovery, NER off** (`discovery-fixed.out`, review's `discovery2.py`): `NAME_FIRST`,
  `NAME_LAST`, `NAMEMIDDLE`, `surnames`, `last_names`, `family_names` are `PERSON_NAME` /
  `det_token` / `surrogate` under synthetic-realistic and synthetic-strict (review: `None`/`keep`).
  Unchanged and still missed under realistic: `NAME_LST2`, `AGENT_NAME` (known limits, §6).
- **Real L2 synthesize job on this lane's source-pg** (`l2-job-name-columns.out`; the review's
  `test_l2_name_columns_probe.py`, copied as `probes/test_zz_s8_b3_probe.py` with the survivor check
  widened from the 7 old columns to all 11; run from `tests/connectors/` and removed again):
  200 rows; discovery: all 11 name columns `PERSON_NAME` → `surrogate`; job **succeeded**,
  verification **passed**, 0 unmatched keys; value equal to the source row's: **0** in every column
  (`NAME_FIRST`, `NAME_LAST`, `surnames`, `last_names` were 200/200 each on `b18abc7`); rows whose 11
  name columns + e-mail hold any source name part: **0**.
- **Detection sweep** (`detection-sweep-diff.txt`; review's `collect_keys.py` / `sweep_detect.py` /
  `sweep_diff.py`): 2,625 keys × 6 path shapes, `b18abc7` (`git archive`, on `PYTHONPATH`) vs the fix:
  **15 keys changed** (`family_names`, `FST_NAME`, `last_names`, `lastnames`, `MID_NAME`,
  `name_first`, `NAME_FIRST`, `name_last`, `NAME_LAST`, `name_middle`, `NAME_MIDDLE`, `NameFirst`,
  `NameLast`, `NameMiddle`, `surnames`), 90 cells, every one to `PERSON_NAME` 0.85/0.9 from nothing
  (24) or `name` 0.55 (66). Nothing lost, no entity changed.
- **No regression elsewhere**: the review's L1 corpus (`l1_probe2.py dump`, 13 layouts × 40 × 2
  presets = 1,040 runs) and L2 regression (`l2_regress.py`) outputs are **byte-identical** between
  `b18abc7` and the fix. Adversarial L1 (`adversarial.py`, 7 layouts × 120 = 840 runs): 0 errors,
  0 unverified, 0 originals in the output (`adversarial.out`).

### Gates

Run on this lane (dps-db 5611, source-pg 5612, MinIO 9611, Mongo 27611, Oracle 1611); logs and
junit files in `S8-persona-fix-r1/`.

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check dps tests tools` | 436 files already formatted / all checks passed (`static-gates.log`) |
| `mypy dps` | no issues in 150 source files |
| touched files (`test_structural`, `test_methods`, `test_discovery_classify`, `test_batch`, `test_service`, `test_surrogates`) | **498 passed** |
| `./lane.sh python -m pytest -q` (whole suite, stack up, no app image, no OCR mock) | **3810 passed, 95 skipped, 6 failed** in 23:14 (`full-suite.log/.xml`; 3,911 tests = the review's 3,836 on `b18abc7` + the 75 new cases). 5 failures are the known environmental ones, with the **same assertions** as the review's run (`test_capture` leakage withheld, `test_capture_residual`, `test_crawler` ×2: the local Chromium build; `test_playground_samples` NER-on: OCR mock down). The 6th, `tests/e2e/test_m3_three_way.py::test_three_way_consistency_join` (`rows_written` 200 ≠ 2400 after 9.6 s), is my parallel-run artefact: the db/pg_source gate ran the same test (markers `db`, `pg_source`) at the same time against the same fixed source tables (`m3e2e.customers`, `CRM.CUSTOMERS`, `crm.profiles`) and passed it (42.7 s). Re-run alone: **1 passed** (`three-way-isolated.log`). No key in the seed model or e2e fixtures is touched by the new table entries. |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh python -m pytest -q -m "db or pg_source"` (private demo site `127.0.0.1:18612`) | **426 passed, 6 errors** in 11:04 (`gate-db-pgsource.log/.xml`): the 6 errors are the console browser tests (`test_console_m2.py` ×3, `test_console_m3_groups.py` ×3), `app unreachable` (no app image on this lane), the same as the review. `test_synthesize_people`, `test_scan_job_e2e` and the three-way test pass. |

Net: green apart from the known environmental failures; for a round-2 re-run, do not run the two
gates in parallel (the three-way e2e shares fixed tables), or give each its own stack.

## 5. Commit

`b5a9b75` on `fpersona` — `fix(fpersona): plural surnames and SAP's name-first keys are name parts,
detected with NER off`. Author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer, explicit
pathspec (7 files: `dps/engine/detect/structural.py`, `docs/superpowers/build/M5-DEVIATIONS.md`,
`tests/test_{batch,discovery_classify,methods,service,structural}.py`). Worktree clean afterwards.
Not pushed (autopush not run: a fix round awaiting review r2).

## 6. Known limits, recorded in M5-DEVIATIONS (owner follow-up)

Still missed with NER off (pre-existing; the review's N1): `<role>_name` keys outside the structural
list (`agent_name`, `officer_name`, `witness_name`); the `*_NM` abbreviations (`FIRST_NM`, `LAST_NM`,
`CUST_FRST_NM`, `CUST_LST_NM`, `GIVEN_NM`, `SURNM`, `FIRSTNM`/`LASTNM`, `MID_INIT`); other languages
(`vorname`/`nachname`, `prenom`/`nom`, `apellidos`; `nombre`/`apellido` detected but no part); SAP's
second surname `NAME_LST2` (a trailing number also reads as a second person, so not a plain table
entry). Suggested follow-up: a lexicon-backed person-name detector when NER is off, or per-system key
lists. Review r1 N3 (owner decisions of the earlier r1) unchanged.

## 7. Environment

- Only this lane's compose project (`dps-wt-fpersona`): `docker compose --profile sources --profile
  oracle start dps-db source-pg minio mongo oracle`, stopped again at the end (`docker compose stop`).
- The db/pg_source gate ran in parallel with the whole suite on a private demo site
  (`DPS_TEST_DEMO_SITE=http://127.0.0.1:18612`), as the review advised.
- Nothing pushed; autopush not run (fix commit only; the owner's/orchestrator's call).
- Temporary probe file `tests/connectors/test_zz_s8_b3_probe.py` was removed after its run; scratch
  trees (the `b18abc7` archive for the sweep) are in the session scratchpad only.
