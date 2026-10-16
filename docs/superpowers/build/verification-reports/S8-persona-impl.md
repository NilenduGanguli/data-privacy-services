# S8-persona — implementation report

**Branch / worktree:** `fpersona` in `/Users/neelu/document_intelligence/dps-wt-fpersona`.
**Commits:** `e437f48` (round 0, the task: name fields agree, titles give the gender, L2 rows) and
`def4fe1` (round 1: the two blocking findings of `S8-persona-review-r1.md`), plus `b18abc7` (docs: a
pre-existing NER-off limit seen while re-running the review probes, §R1.6), all on top of `5cef802`,
author NilenduGanguli, no trailer. **Verdict:** done; review r1 B1 and B2 fixed test-first, gates
green (environmental failures listed in §R1.4).

This run was launched with the original S8-persona prompt after round 0 had already been committed
(`e437f48`, pushed) and reviewed (`S8-persona-review-r1.md`: FAIL, B1 + B2). Per the resume rule I
did not redo round 0; I continued from that state and fixed the review's blocking findings. The
round-0 report follows after §R1, unchanged except its title (a copy is in
`S8-persona-fix/S8-persona-impl.r0.md`).

## R1. Review round 1 fixes

### R1.1 B1 — a secondary person's identity depended on the order of their name fields

`_people` keyed a group's person on its fields joined in **declaration** order (L1: path order; L2:
column order), so `nominee_family_name` + `nominee_given_name` ("family" sorts first) was keyed on
"Nair Priya" and a full `nominee_name` "Priya Nair" (or `nominee_first_name`/`_last_name`) on
"Priya Nair": the same nominee became two synthetic people (P3). Fix
(`dps/engine/synth/surrogates.py`): the group's fields are joined in name order (first, middle,
last; stable) before keying. The record's own group was never affected.

### R1.2 B2 — name-part keys with a role but no detection kept real names (NER off)

`methods.name_field` gave roles to keys the detector missed: `given_names`, `forenames`,
`first_names`, `middle_names`, `f_name`/`l_name`, `FName`/`LName`, `custFName`/`custLName`, and also
(found while writing the invariant test, beyond the review's list) `surName`/`sur_name`,
`foreName`/`fore_name`, a number run on (`FIRSTNAME2`, `first_name2`, `FNAME1`, `lastName2`,
`surname3`), and a bare `first`/`given`/`middle`/`last`/`family` under a key ending in `name`
(`$.name.first`, `$.customer.name.family`, `$.customerName.first`). Fix: **one table** for the role
and the detection, so the invariant holds by construction:

- `dps/engine/detect/structural.py`: `NAME_PART_KEYS` (run-together last words → role, score),
  `BARE_NAME_PART_KEYS`, `key_words`, `name_part(key, parent)`; `classify_key` adds `PERSON_NAME`
  from it (0.9; `middlename(s)` 0.85; `lname` 0.8; `fname` 0.75; a bare part under a name key 0.85,
  evidence `key:<rule>` / `path:name part`; cached per key). `classify_path`, L1's ensemble, L2
  discovery and the scanner all go through `classify_key`. `firstnames` joined the table. The phrase
  rules are unchanged (nothing detected before is lost; an existing equal or higher phrase score keeps
  its evidence).
- `dps/engine/transform/methods.py`: `name_field` reads role and word count from `name_part` (its own
  copy of the table, `_key_words` and `_KEY_WORD` removed); groups are computed exactly as before.
- Still not detected (guard test): `$.first`, `$.last`, `$.given`, `$.address.last`, `$.filename`,
  `$.file_name`, `$.name.last_updated`, `$.username.first`.

### R1.3 Tests (written first; 48 new cases, each fails on `e437f48`, all pass now)

Proof: the new test files copied into a `git archive e437f48` tree, run there with the lane venv
(`dps` resolved to the archive): **48 failed, 375 passed**; on the branch: all pass.

- `tests/test_structural.py`: `test_every_key_that_holds_a_name_part_is_detected_as_a_name` — 72
  spellings (each asserted to have a `name_field` role) → `classify_key` and `classify_path` top is
  `PERSON_NAME` ≥ 0.7, also as an L2 column name without `$.` (35 cases failed on `e437f48`);
  `test_words_near_a_name_part_are_not_names` (guard, passes on both).
- `tests/test_methods.py`: 16 new role spellings (the plural/camel ones failed on `e437f48`) and 4
  more non-name keys.
- `tests/test_surrogates.py`: `test_a_person_s_split_name_is_the_same_person_in_any_field_order` —
  family-first, given-first and full-name nominee give one synthetic name (40 seeds).
- `tests/test_service.py`: `test_a_nominee_is_one_synthetic_person_whatever_their_fields_are_called`
  (one seed, one anchor: `nominee_given/family_name`, `nominee_first/last_name`,
  `nominee_forename/surname` == the full `nominee_name`, and the customer's first name unchanged);
  `test_every_name_part_key_is_synthesised_with_ner_off` × 7 layouts (`surname`+`given_names`,
  `FName`/`LName`, `custFName`/`custLName`, `f_name`/`l_name`, `foreName`/`surName`,
  `name.{first,last}`, `customer.name.{given,family}`): no original word in the output, verification
  passed, e-mail = `<first>.<last>`.
- `tests/test_batch.py`: `test_a_nominee_s_split_columns_are_the_same_person_as_their_full_name_elsewhere`
  — Oracle-style `CUSTOMERS(LAST_NAME, FIRST_NAME, EMAIL, NOMINEE_FAMILY_NAME, NOMINEE_GIVEN_NAME)`
  and `ACCOUNTS(HOLDER_NAME, JOINT_HOLDER_NAME)` under the `CUSTOMER_ID` anchor: joint holder ==
  nominee, e-mail agrees, holder's last name == customer's.
- `tests/test_discovery_classify.py`: `test_name_part_columns_are_names_with_ner_off` — `given_names`,
  `forenames`, `first_names`, `middle_names`, `f_name`, `l_name`, `surName` → `PERSON_NAME`, synthesize
  method not `keep`.

### R1.4 Gates and the reviewer's own probes (this lane's stack: dps-db 5611, source-pg 5612, MinIO 9611, Mongo 27611, Oracle 1611)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools`, `ruff check dps tests tools` | clean (436 files) |
| `mypy dps` | no issues (150 files) |
| `./lane.sh python -m pytest -q` (whole suite, stack up: dps-db, source-pg, MinIO, Mongo, Oracle; no app or OCR mock) | **3736 passed, 95 skipped, 5 failed** in 24:08 (`S8-persona-fix/full-suite.log/.xml`); the reviewer's run on `e437f48`: 3625 / 95 / 5 |

- The 5 failures are the known environmental ones, identical to round 0 and to the reviewer's run
  on `e437f48` and `5cef802`: `tests/scanner/test_capture.py::test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key`,
  `tests/scanner/test_capture_residual.py::test_masked_capture_boxes_every_copy_of_a_detected_value`,
  `tests/scanner/test_crawler.py::test_full_demo_crawl_in_memory`, `::test_dialogs_dismissed_and_popups_closed`
  (local Chromium build), `tests/test_playground_samples.py::test_text_sample_with_ner_on_still_never_returns_the_name`
  (OCR mock down → coverage `partial`).
- The 95 skips are browser/app tests (scanner 73, console 12, documents 3, 6 top-level, 1 Mongo
  conformance): this lane has no app image. DB-backed synthesize tests ran and passed:
  `test_synthesize_people` (the 300-row job, counts all 0), `test_synthesize_identifiers` 8/8,
  `test_demo_pii_survival` 11/11, with the Mongo and Oracle suites.
- Separately: scanner classification/fill/risk/guard units 186 passed; `test_ensemble`,
  `test_schema_gen`, `test_schema_profile`, `test_discovery_mixed*` passed.
- No log or print call added (`git diff dps/` grep: 0).

Reviewer's probes re-run on the fix (outputs in `S8-persona-fix/`):
- `order_engine.py`: family-first 50/50, given-first 50/50 (was 0/50 family-first).
- `props_probe.py`: O1 given/family == full-name field 40/40 (was 0/40); first/last and
  forename/surname 40/40; P1 (720 records) and P2 (150): 0 errors, 0 verification failures, 0
  e-mail disagreements, 0 originals in the output, 0 first == last, 0 nominees sharing a first name;
  titles Mr→male, Mrs/Ms→female 100/100 each.
- `l2_probe.py` (family before given) and `l2_probe_given_first.py`: joint holder == customers'
  nominee **400/400** in both orders (was 0/400 family-first); T1 e-mail, nominee e-mail, nominee
  distinct, upper case 400/400; title gender 134/134; source name parts in output 0; verification
  passed both tables.
- `discovery_probe.py`: `given_names`, `forenames`, `first_names`, `middle_names` → `PERSON_NAME`,
  synthesize `surrogate` (were `None`/`keep`).
- `plural_probe.py`, `camel_probe.py`, `misc_probe.py`: no original kept in any field, no
  verification failure. `edge_probe.py`: verdicts identical to `e437f48` for all 20 cases × 3 seeds
  (the two `VerificationFailed` cases, a single name field next to a full name in notes, fail closed
  on both trees); `nested_name` now `Wishi / Bhatti / wishi.bhatti…@` (was the originals kept).

### R1.5 Privacy and security

Detection only grew (a strict superset: the new rule adds candidates, never removes or lowers one).
D2/D4/P1/D-A untouched (no key, scanner-network, LLM or identifier code changed; the scanner gets the
wider name classification through `classify_key`, which only masks/fills more fields as names). No
log calls added; tests and probes print counts or invented names only.

### R1.6 Open items for the owner (non-blocking, recorded in M5-DEVIATIONS)

- N1 (P3 vs titles, measured on L2 by the review: titled customers' first names agree 58–67/134
  across two tables; untitled 266/266; last names 400/400) and N2 ("Mrs. Mehta called on behalf of
  her husband" gives the male customer a female persona) are owner decisions; suggested for the
  RESUME open-decisions list (I did not edit RESUME.md on this feeder branch).
- N3 (`first_name_en` gets no role), N4 (prefixed own group), N6–N8: unchanged, documented.
- N5 (doc contradiction): fixed in M5-DEVIATIONS.
- **New, pre-existing (not caused here; follow-up suggested):** with NER off (the default image), a
  person-name key whose role word is not in the structural role list scores only `name` 0.55 and is
  not detected, so its real value is kept in a synthetic record with verification passing. Seen in
  the reviewer's `misc_probe.py` case `agent_first`: `agent_name` (an invented full name) is copied
  verbatim on both `e437f48` (re-run on an archive of it) and `def4fe1`; `scan_names` finds no name
  in an untitled full name. Same for any `<role>_name` outside the list (`agent`, `officer`,
  `manager`, `witness`, `introducer`, ...). Fix options: extend the role-before-`name` rules for bank
  roles, or a lexicon-backed name detector when NER is off. Not a name-part key, so outside B2.

### R1.7 Environment

Docker: project `dps-wt-fpersona` only (dps-db, source-pg, minio, mongo, oracle), started from the
stopped containers, stopped at the end (`docker compose stop`); the PG/Mongo/Oracle demo data stay
seeded in the volumes. For a round-2 review: `docker compose --profile sources --profile oracle start
dps-db source-pg minio mongo oracle`. Push: I did not push. The autopush LaunchAgent is not loaded
(`launchctl list` shows none; the last `autopush.log` entry is 22:04, a hand run), so `def4fe1` and
`b18abc7` are local only until `tools/dev/autopush.sh` runs (it checks author, trailers, gitleaks).

---

# Round 0 (commit `e437f48`) — the original report

**Branch / worktree:** `fpersona` in `/Users/neelu/document_intelligence/dps-wt-fpersona`, base `5cef802`
(one persona per person). **Commit:** `e437f48` (pushed by `tools/dev/autopush.sh`: `pushed fpersona e437f48`) (author NilenduGanguli, no trailer). **Verdict:** done;
all three items implemented test-first, gates green (pre-existing environment failures listed below).

## 0. Starting point

The paused attempt had left 5 uncommitted files (names.py, llm_text.py, surrogates.py, apply.py,
methods.py) and **no tests**. I read the whole diff, kept its design (a `NameHint` from the key, threaded
through `TransformContext`, a separate `name_surrogate`, title genders through `PersonaFactory`'s existing
`gender_hint`), fixed one bug in it and extended it. The new tests were then run against an exported copy
of `5cef802` (scratch dir, `git archive`): every one fails there and passes on the branch.

Bug fixed in the WIP: `_TITLE_TAIL` ended in `$`, which also matches before a trailing line break, so a
title ending the previous line (`Mrs.\nArjun`) counted. Now `\Z`.

## 1. What was wrong (measured on 5cef802)

- `{"first_name": "Arjun", "last_name": "Mehta", "email": "arjun.mehta@…"}` → e.g. `Bachittar / Reva /
  bachittar.balasubramanian…`: a single-word name was always a persona's **first** name, and the two single
  words (no shared part) were declared as **two people**; the e-mail followed the first.
- Text: `Mr.`/`Mrs.` were followed by a name of the other gender about half the time (`Mr. Lakshmi …`).
- L2: a 300-row synthesize job (new integration test) → e-mail disagreeing with the name columns on
  **300/300** rows, title gender wrong on **138/300**.
- With NER off, `fname`/`lname`/`forename`/`FIRSTNAME`/`LASTNAME` keys were **not detected at all**, so a
  synthesize run copied those real names (found while writing the service test).

## 2. What changed

| Area | Change |
|---|---|
| `dps/engine/transform/methods.py` | `NameRole`, `NameHint(role, group, gender)`, `name_field(path)` (role from the **key only**; case-insensitive; words split at `_`, case changes, digits; `first_name`/`given_name`/`fname`/`forename(s)` → first, `last_name`/`lastname`/`lname`/`surname`/`family_name`/`familyname` → last, `middle_name(s)` → middle; bare `first`/`last`/`given`/`family`/`middle` only under a key ending in `name`; not `mname` — also a mother's name). Group = parent path + other key words + trailing number. `NameAwareSurrogates` protocol; `declare_record(..., hints=())` (additive); `TransformContext.name_hint`; the `surrogate` method calls `name_surrogate` for a role-bearing field, else `surrogate()` (a test double without it is unchanged). |
| `dps/engine/synth/surrogates.py` | `declare_record(anchor, originals, hints=())`; `_people` returns `_People(people, names, groups, roles)`: a group's fields are **one person**; the record's **own** top-level group (`$||`) is processed first, so it owns the record's persona even when `father_name` sorts before `first_name`; other groups in declaration order (join a covering person, else new). **Name-word roles**: a single name word in text is the part it was declared as for its person (a field's part; else the last name when it ends one of that person's declared multi-word names, no comma; else first). **Genders**: `_Person.gender` (first title wins); `persona(..., gender=None)`; `_gender()` resolves the person from the anchor (`|person:<key>`). New public `name_surrogate()`; `surrogate()` unchanged (same signature, still pure in seed, type, canonical original, declaration, anchor). Middle names: same-gender first name at `<person>|middle<n>`, never the first/last name. |
| `dps/engine/detect/names.py` | `TITLE_GENDER`, `title_gender(text, start)` — the title right before a name, same line (male: Mr, Shri, Shree, Sri, Sir, Lord, Sr, Don, Señor, Herr; female: Mrs, Ms, Miss, Smt, Shrimati, Kumari, Km, Sushri, Dame, Lady, Sra, Srta, Doña, Dña, Señora, Señorita, Mme, Mlle, Frau; Dr/Prof/Mx/ranks/Fr none). Reads the title, never the name. |
| `dps/engine/transform/apply.py` | `_name_hint(entry, value)`: whole value → `name_field(path)`; span → `title_gender` on the input; `_record_originals` returns the hints; `_ctx` sets `name_hint`. |
| `dps/engine/synth/llm_text.py` | `_title_hints(ph)` from the **placeholder template** (`Mrs. {PERSON_NAME_2}`), so with or without the model; nothing new reaches the model (P1). |
| `dps/engine/detect/structural.py` | New key rules: `firstname`, `lastname`, `givenname`, `familyname`, `forename` 0.9, `middlename` 0.85, `lname` 0.8, `fname` 0.75 (all ≥ the 0.7 structural-only floor). More detected, nothing less. |
| `docs/superpowers/build/M5-DEVIATIONS.md` | New section "Finish-stage fixes from the live Vertex run" → "S8-persona" (decisions, trade-offs, follow-ups, tests). |

Expected shape now: `Alfred / Williams / alfred.williams…@…`; customer + nominee (full names or split
fields) stay two people with their own e-mails; `Mr. Mehta` in a note next to `last_name: Mehta` →
`Mr. Williams`; `Ms. Arjun Mehta` → a woman's name.

## 3. Tests (written first; each fails on 5cef802, passes on the branch)

- `tests/test_methods.py` — 27 key spellings → role; 13 non-part keys → None (incl. `mname`, `$.first`,
  `username`); groups; routing to `name_surrogate` and fallback for providers without it.
- `tests/test_surrogates.py` — one person per group in either declaration order (40 seeds), case, middle
  name, two groups, a group matching a declared person, name words in text, titles → persona gender incl.
  `GENDER` and the first-title rule, purity / order independence, records without hints unchanged, the
  record's own fields own its persona when another name is declared first.
- `tests/test_names.py` — `title_gender` (16 cases incl. the previous-line title).
- `tests/test_structural.py` — run-together and short keys classify `PERSON_NAME` ≥ 0.7.
- `tests/test_service.py` — the live record under both synthetic presets × 8 key spellings (5 runs each);
  two people's split fields; titles in text and in a JSON note decide the gender whatever the name;
  surnames alone; `father_name` before `first_name` with a generic e-mail.
- `tests/test_llm_text.py` — titles with and without the model; gateway faked at `_stellar` (real
  `complete()` and strict egress verifier run); asserts no original name in any prompt.
- `tests/test_batch.py` — L2 rows through `BatchTransformer`: e-mail, notes (`KYC call with <first>;
  Mr. <last> …`), the notes' title gender on the first-name column, nominee distinct, no original word kept.
- `tests/connectors/test_synthesize_people.py` (markers db, integration, pg_source) — **300-row synthesize
  job** on this lane's `source-pg` (profile `sources`) into a `table` target. Counts only, all 0:
  e-mail 0, nominee e-mail 0, notes 0, title gender 0, nominee sharing the first name 0; source-vs-target:
  source name parts in the row's output 0, source e-mails 0, source keys 0 (pseudonymised via
  `KeyPseudonyms`); evidence verification passed. On 5cef802: e-mail 300, title gender 138.

## 4. Gates (this lane's stack: dps-db 5611, source-pg 5612, MinIO 9611, Mongo 27611, Oracle 1611)

- `ruff format --check`, `ruff check` (dps tests tools): clean. `mypy dps`: no issues (150 files).
- Full suite on the committed code (`pytest -q`, stack stopped so service tests skip): **3067 passed, 653
  skipped, 5 failed**; an earlier run with the stack up (before the last `_people` ordering change):
  3447 passed, 269 skipped, 6 failed. Every failure is environmental and fails identically on 5cef802
  (exported tree, same venv and stack):
  `tests/scanner/test_capture.py::test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key`,
  `tests/scanner/test_capture_residual.py::test_masked_capture_boxes_every_copy_of_a_detected_value`,
  `tests/scanner/test_crawler.py::test_full_demo_crawl_in_memory` and `::test_dialogs_dismissed_and_popups_closed`
  (the local Chromium build), `tests/test_playground_samples.py::test_text_sample_with_ner_on_still_never_returns_the_name`
  (OCR mock not running → coverage `partial`); the earlier sixth, `tests/test_config_m2.py::test_m2_defaults_keep_l1_database_free`,
  came from this lane's `.env.lane` exporting `DPS_WORKER_METRICS_PORT` (removed; 8/8 pass).
- `DPS_TEST_REQUIRE_SERVICES=1 -m "(db or pg_source or s3) and not browser"`: **423 passed, 0 failed** (11 min; with PG + Mongo + Oracle demo data seeded and MinIO up).
- `-m "mongo and not oracle and not browser"`: 68 passed, 1 skipped (Mongo demo seeded with
  `tools/seed_sources.py --target mongo`). `-m "oracle and not browser"`: 104 passed (Oracle demo seeded),
  incl. the e2e Oracle notes test.
- PG demo seeded with `tools/seed_sources.py --target postgres --rows 20000 --seed 7 --restore-copy` (the
  `seed-sources` one-shot needs an image this lane never built); then the seeded-demo synthesize tests
  (copula and surrogate, D-A identifier checks) pass: `test_synthesize_copula.py`,
  `test_synthesize_identifiers.py`, `test_demo_pii_survival.py`, `test_synthesize_people.py` 27 passed.
- Log scan: the integration test run captured at DEBUG (2.2 KB) holds 0 of its 1,196 source names/e-mails.
- Not run: browser/console tests (no app image for this lane), `ml` (no synth-ml extra).

## 5. Privacy and security properties

Unchanged: D2 (no keys touched), D4 (scanner untouched; its personas use `$.name` → no hint), P1 (titles
are read from the placeholder template; the provider-seam test asserts no original in any prompt), D-A
(pseudonymised keys and the identifier check untouched; source-vs-target counts 0). The leakage sweep,
`held_by`, `_reveals` and every verifier are untouched. Structural detection only grew. No value is
logged; tests print counts only.

## 6. Decisions and trade-offs for the owner

1. **P3 vs titles (asked-for approach has a cost).** A persona's gender was a function of its anchor
   alone; a title now sets it, so a person titled in one record (CRM notes) and untitled in another
   (customers table) can get two **first names** (last name, birth date, address stay equal). Measured,
   same anchor with/without `Mrs.`, 300 seeds: last name and birth date equal 300/300, first name 155/300.
   Alternative that keeps P3: keep the persona and rewrite the title (`Mrs.`→`Mr.`) — touches text outside
   detected spans (span positions, `segments`, document rendering); not done.
2. **Kept `GENDER` columns (not changed).** Both synthetic presets keep `GENDER` (LOW, `keep_non_sensitive`),
   so a row's real gender sits next to a first name drawn without it (disagree ~half the time). Reading the
   kept value as the persona's gender would fix rows at the same P3 cost. Making `GENDER` a surrogate fixes
   it in L2, but the L1 strict sweep refuses ~half of such requests (measured 20/40: a chance-equal gender
   counts as a leak in L1, not in L2's per-row sweep) — pre-existing, outside this task.
3. **Primary person:** the record's own top-level name fields (beyond the brief's wording, to keep the
   customer's persona stable across tables whatever other people a record names; a generic e-mail/phone/DOB
   stays theirs). Nested groups keep declaration order.
4. **Keys not given roles:** `mname` (mother's name too), Spanish `nombre` (may hold a full name) and
   `apellido_paterno`/`_materno` (two surnames); a separate title/salutation column is not read.

## 7. Follow-ups (not done here)

- **fspec / schema generation:** `schema_gen._generate` calls `surrogate(entity, "First Last", …, "row")` for
  every `PERSON_NAME` field, so a generated spec with `first_name` and `last_name` still gets a full name in
  each. Fix: `name_surrogate(template, locale, "row", name_field(f"$.{field.name}"))` for name fields (fspec
  has uncommitted `schema_gen.py` changes; I did not touch it). Same for MOSTLY linked generation.
- Relationship keys (`father_name`, `husband_name` → male; `mother_name`, `wife_name` → female) and
  `S/O`, `D/O`, `W/O` in text could give genders the same way (same P3 cost); not done.

## 8. Environment notes

- Installed `playwright==1.55.0` (the `scanner` extra) into the lane venv so `tests/scanner` collects.
- `.env.lane`: removed `export DPS_WORKER_METRICS_PORT` / `DPS_TOKENIZER_MOCK_PORT` (not `DPS_TEST_*`;
  `Settings` read the first one). `.env` unchanged.
- Docker: project `dps-wt-fpersona` only (dps-db, source-pg, minio + minio-init, mongo, oracle); stopped
  at the end (`docker compose stop`).
