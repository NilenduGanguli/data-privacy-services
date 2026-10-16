# S8-persona: review round 2 (of the B3 fix `b5a9b75`)

**Verdict: PASS (no blocking finding).** Review r1's blocking finding B3 is fixed, the fix is test-first, the
acceptance of the S8-persona brief still holds on L1 and L2, and I found no surrogate that reveals or
contains an original, no order dependence, no regression of one-person or customer + nominee records
and no D-A breach on L2 targets. Gates are green apart from the known environmental failures and one pre-existing flaky test (§1). Two non-blocking findings (§5): the new
two-word joins and short forms also catch some keys that hold no person (`PRI_LST_NAME`, `MID_NAME`,
`PRODUCT_NAME_FAMILY`), and SAP's `NAME_LAST2` (birth name) reads as a second person.

**Reviewed:** branch `fpersona` at `b5a9b75` (on `b18abc7` ← `def4fe1` ← `e437f48` ← `5cef802`),
7 files (`dps/engine/detect/structural.py`, `docs/superpowers/build/M5-DEVIATIONS.md`,
`tests/test_{batch,discovery_classify,methods,service,structural}.py`). Author and committer
`NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer (`git interpret-trailers --parse`: empty),
worktree clean before and after. `origin/fpersona` already points at `b5a9b75`. I modified no
repository file: probes ran from `~/.cache/dps-build/reports/S8-persona-review-r2/probes/` (the two
L2 job probes through pytest with `-p tests.fixtures.*`, outside `tests/`). Baselines `b18abc7` and
`5cef802` ran from `git archive` trees on `PYTHONPATH` (script runs resolve `dps` there; checked).
Every number below is a count; probes use Faker en_IN or invented names only.

## 1. Gates (run by me on this lane: dps-db 5611, source-pg 5612, MinIO 9611, Mongo 27611, Oracle 1611)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check dps tests tools` | 436 files already formatted / all checks passed (`static-gates.log`) |
| `mypy dps` | no issues in 150 source files |
| `./lane.sh python -m pytest -q` (whole suite, stack up, no app image, no OCR mock) | **3810 passed, 95 skipped, 6 failed** in 24:30 (`full-suite.log/.xml`; 3,911 tests, the fixer's count). See the notes below. |
| `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh python -m pytest -q -m "db or pg_source"` (private demo site `127.0.0.1:18622`) | **426 passed, 6 errors** in 10:34 (`gate-db-pgsource.log/.xml`), the same result as r1 and the fix run. |

Notes on the whole suite:
- **5 of the 6 failures are the known environmental ones**, the same five tests that fail in r1's and
  the fix run's logs (and on `5cef802`, per r1):
  - `scanner/test_capture.py::test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key`,
    `scanner/test_capture_residual.py`, `scanner/test_crawler.py` ×2: the local Chromium build.
  - `test_playground_samples.py::test_text_sample_with_ner_on_still_never_returns_the_name`: the OCR
    mock is down, so coverage is `partial`.
- **The 6th, `tests/test_ndjson.py::test_one_audit_event_per_stream`, is a flaky test that was already
  there before this branch.** The stream's audit outcome was `verification_failed` instead of `ok`.
  - It passes **12/12** in isolation (`ndjson-isolated.log`).
  - The same stream, run 600 times (`ndjson_rate.py`), fails **7/600** on `b5a9b75` and **11/600**
    on `5cef802`.
  - The stream holds only e-mails, so no name-part code is involved.
  - Cause (`ndjson_why.py`): the test's two-letter local parts. A random pseudonym that ends in `dq`
    just before the kept domain normalises to a string that contains `d.q@example-mail.in`. The sweep
    then fails closed, about 1 in 1,000 per token.
  - Owner follow-up: lengthen the test's e-mails. Do not change the verifier.

Notes on the db/pg_source gate:
- The 6 errors are the console browser tests (`test_console_m2.py` ×3, `test_console_m3_groups.py`
  ×3). They fail with `app unreachable` because this lane has no app image.
- Passed in **both** runs (checked in the junit files):
  - `test_synthesize_people` 1/1
  - `test_synthesize_identifiers` 8/8
  - `test_demo_pii_survival` 11/11
  - `test_m3_free_text_names` 4/4
  - `test_m3_three_way` 1/1
  - `test_scan_job_e2e` 5/5 (the D4 counters)

## 2. B3 is fixed (independent reproduction, `b18abc7` vs `b5a9b75`)

`NAME_PART_KEYS` gains exactly the review's entries (`lastnames`/`surnames`/`familynames` → last 0.9;
`namefirst`/`namegiven` → first, `namemiddle` → middle, `namelast`/`namefamily` → last, 0.85) plus
the optional short forms `fstname`/`midname`/`lstname` (0.85); `name_part` already joins a key's last
two words, so groups are right (`NAME_FIRST` → `$||`, `CUST_NAME_LAST` → `$|cust|`, `NAME_FIRST_2` →
`$||2`). The `lru_cache` holding raw keys/paths is gone (r1 N2).

- **Test-first verified.** The branch's five test files run against `git archive b18abc7` code:
  **67 failed, 356 passed** (`new-tests-on-b18abc7.out`), all B3 cases (32 structural spellings, 18
  roles + 1 group, 6 + 6 service layouts, 2 discovery, 2 batch). On the branch they pass.
- **L1 service, NER off, both synthetic presets, 40 records each, 3 key-order permutations per record**
  (`l1_r2.py`; `l1-r2.b18abc7.out` vs `l1-r2.b5a9b75.out`). New-key one-person layouts
  (`NAME_FIRST`/`NAME_LAST`, + `NAME_MIDDLE`, `NAMEFIRST`/`NAMELAST`, `nameGiven`/`nameFamily`,
  `first_names`/`surnames`, `given_names`/`family_names`, `first_name`/`last_names`,
  `fstName`/`lstName`, `CUST_NAME_FIRST`/`CUST_NAME_LAST`, Siebel `FST_NAME`/`MID_NAME`/`LAST_NAME`,
  numbered `NAME_FIRST_2`, nested `customer`/`nominee` SAP keys, multi-word `NAME_FIRST`, a double
  surname in `surnames`, `surnames` and `family_names` arrays):
  - `b18abc7`: records holding an original word in the output **40/40** in each of them, e-mail
    agreeing 0–1/40, verification passing — the B3 leak. The two array layouts are the exception:
    there `b18abc7` refused 37–40 of 40 records, and the few it returned kept originals.
  - `b5a9b75`: **0/40** originals in every layout and preset (multi-word `NAME_FIRST` and two-word
    `surnames` included), e-mail agrees **40/40** where the layout has one, verified **40/40**, the two
    numbered people and the nested customer/nominee distinct **40/40**, key-order permutations
    differing **0/120**.
- **L2 rows (BatchTransformer), 80 rows × 2 presets, 6 random column orders each** (`l2_r2.py`).
  SAP (`NAME_FIRST`/`NAME_LAST` + `NOMINEE_NAME_GIVEN`/`_FAMILY`, either column order), plural
  (`first_names`/`surnames` + `nominee_first_names`/`nominee_last_names`) and Siebel
  (`FST_NAME`/`LST_NAME` + nominee) layouts with e-mails and titled notes:
  - `b18abc7`: e-mail and nominee e-mail agree **0/80**; outputs change with column order (1,274 cell
    differences for SAP/Siebel, 58 for plural).
  - `b5a9b75`: verified, rows holding a source name word **0**, e-mail **80/80**, nominee e-mail
    **80/80**, nominee distinct **80/80**, the note's `Mr./Mrs. <surname>` = the customer's synthetic
    last name **80/80**, column-order differences **0**; the classic `FIRST_NAME`/`LAST_NAME` layout is
    identical on both trees.
- **Real L2 synthesize jobs on this lane's source-pg** (`probes/l2job/test_r2_l2_job.py`,
  `l2-job-r2.out`; 200 rows each, default preset, discovery accepted as proposed):
  - SAP partner table (`NAME_FIRST`, `NAME_MIDDLE`, `NAME_LAST`, `EMAIL`, `NOMINEE_NAME_FIRST`,
    `NOMINEE_NAME_LAST`, `NOMINEE_EMAIL`, `PAN`): every name column `PERSON_NAME` → `surrogate`, `PAN`
    → `PAN_IN` → `surrogate`; job **succeeded**, verification **passed**; target value equal to the
    source row's **0** in every column; rows holding any source name part **0**; e-mail **200/200**,
    nominee e-mail **200/200**, nominee distinct **200/200**, middle ≠ first **200/200**.
  - Plural table (`first_names` (some two-word), `surnames` (some two-word), `family_names`,
    `email`): all `PERSON_NAME` → `surrogate`; succeeded, passed; equal to source **0**; rows holding a
    source name part **0**; e-mail agrees **200/200**; `surnames`' last word = `family_names`
    **200/200** (one person).

## 3. Acceptance of the brief, re-checked on `b5a9b75`

- **(1) The live record** `{"first_name": "Arjun", "last_name": "Mehta", "email":
  "arjun.mehta@example.in"}`, 40 seeds × both synthetic presets: first name is a first name **40/40**,
  last name a last name **40/40**, e-mail = `<first>.<last>` **40/40**, original kept **0**, key-order
  permutations differing **0/120** — identical to `b18abc7`.
- **Customer + nominee stay two people** (`l1_r2b.py`, 7 layouts × 2 presets × 40, with titled notes
  and without notes; classic, SAP, SAP + full-name nominee, plural nominee, SAP given/family nominee,
  plural own + `NOMINEE_LAST_NAMES`, Siebel): originals in the output **0**, e-mail **40/40**,
  nominee e-mail **40/40**, nominee distinct and first names differ **40/40**, permutations differing
  **0**. One refusal per preset (1/40, "own + nominee plural" with notes) is the surname `Date` in a
  titled note: `Mr. Date` is not detected, so the sweep refuses the record (fail closed). Same with
  classic keys and on `5cef802` (`date-surname.out`): pre-existing, key-independent, outside this
  task. The classic layout's results are identical on `b18abc7` and `b5a9b75`.
- **(2) Titles**: unchanged code path; the L2 note's title surname equals the customer's 80/80 (§2).
- **(3) L2**: `test_synthesize_people` (300-row job) passes in the db/pg_source gate.

## 4. Defect hunt

- **Reveal / containment.** The r1 adversarial cases re-keyed with the B3 spellings
  (`adversarial_newkeys.py`: swapped customer/nominee names via `NOMINEE_NAME_FAMILY`/`_GIVEN`, nominee's
  first = customer's last, shared surname under `FST_NAME`/`LST_NAME`, substring first/last in
  `first_names`/`surnames`, the same first name in nested SAP groups, a titled note with multi-word
  `first_names`, reversed full-name nominee next to `CUST_NAME_*`, the same word in `NAME_FIRST` and
  `NAME_LAST`, middle = last, `NAME_FIRST_2`/`_LAST_2` = `NAME_FIRST`/`_LAST`): 10 layouts × 60 × 2
  presets = **1,200 runs: 0 originals in the output, 0 refusals, 0 errors**. (`b18abc7`: originals kept
  in 7 of the 10 layouts, refusals in the other 3.)
- **Order dependence**: none (L1: **0 of 7,674** key-order-permuted runs differ from their base run,
  0 unverified; L2: 0 cell differences over 6 column orders × 5 layouts × 2 presets).
- **Regressions**: classic one-person and customer + nominee layouts are identical between `b18abc7`
  and `b5a9b75` on L1 and L2 (§2, §3). Records that combine an undetected bare `NAME` (0.55) with
  `NAME_FIRST`/`NAME_LAST` now fail closed in L1 (40/40 both presets), where `b18abc7` returned them
  with every real name copied: the same verdict the classic `name` + `first_name` + `last_name` gets on
  `5cef802` (`vf-debug-*.out`) — an improvement, not a regression. A bare `name` alone is kept with
  verification `partial` (`bare-name.out`), the documented NER-off limit.
- **D-A on L2 targets**: the two real jobs pseudonymised every key (source keys in target **0**,
  unmatched **0**, every target key maps back through `KeyPseudonyms`) and synthesized every PAN
  (source PANs in target **0**); `test_synthesize_identifiers` passes in the gate. D2/D4/P1: no key,
  scanner-network or LLM code touched; no log/print added.

## 5. Non-blocking findings

- **N1. The new joins and short forms also catch keys that hold no person** (`fp-sweep.*.out`,
  `fp-effect.out`, `l2-fp-job.out`). Now `PERSON_NAME` 0.85 with a role (was `name` 0.55, below the
  realistic floor, or nothing): `PRI_LST_NAME`, `PRICE_LST_NAME`, `DIST_LST_NAME`, `MAIL_LST_NAME`
  (top entity was `EMAIL` 0.60) — Siebel's own abbreviation `LST` is "list" (`S_PRI_LST`,
  `S_LST_OF_VAL`; EIM tables carry `PRI_LST_NAME`), and its contact's last name is `LAST_NAME`, not
  `LST_NAME`; `MID_NAME`, `MERCHANT_MID_NAME`, `TERMINAL_MID_NAME` (MID = merchant id in card
  acquiring); and `<noun>_NAME_FIRST/LAST/FAMILY` (`HOST_NAME_FIRST`, `TABLE_NAME_FIRST`,
  `FILE_NAME_LAST`, `PRODUCT_NAME_LAST`, `PRODUCT_NAME_FAMILY`, `FONT_NAME_FAMILY`). Effects, measured:
  - L1: the value is replaced by a person-name part, and a record that repeats it in another field is
    **refused** (`PRI_LST_NAME` + `PRI_LST_DESC`, `MERCHANT_MID_NAME` + a note, `PRODUCT_NAME_FAMILY` +
    a description: VerificationFailed 5/5; on `b18abc7` returned, verification `partial`).
  - L2 job (60 rows, 5 price lists): `PRI_LST_NAME` discovered `PERSON_NAME` → `surrogate`, job
    succeeded, the 5 list names became **55** distinct person names (per-row personas), so a column
    that EIM uses as a lookup key no longer resolves; the description column still names the source
    list (no person data involved).

  Privacy is not weakened (more is masked); it is a precision/utility cost the r1 review invited by
  suggesting `lstname`/`midname` as optional. Suggested (cheap): drop `lstname` (no system known to
  use it for a surname); keep or guard `midname`; optionally ignore `name_first/last/family` after a
  known non-person head noun. Owner's call.
- **N2. SAP `NAME_LAST2` is a second person.** In `BUT000`, `NAME_LAST2` is the same partner's name at
  birth. It is now detected (0.85, was 0.55 and kept under realistic: better), but the trailing number
  makes it the last name of person `$||2`, so it gets another persona's surname (plausible for a birth
  name, but not the same persona). M5-DEVIATIONS' known limits mention only `NAME_LST2`; worth one
  line there.
- **Carried over (unchanged):** the r1 owner decisions (P3 cost of titles, first title wins,
  `first_name_en`, prefixed own group) and the NER-off known limits recorded in M5-DEVIATIONS.

## 6. Environment and artefacts

- Docker: only this lane's project (`docker compose --profile sources --profile oracle start dps-db
  source-pg minio mongo oracle`), stopped again at the end (`docker compose --profile sources
  --profile oracle --profile docs stop`; a plain `docker compose stop` leaves the profiled services
  running). All six containers are `exited`; the demo data stays in the volumes.
- The db/pg_source gate ran on a private demo site (`DPS_TEST_DEMO_SITE=http://127.0.0.1:18622`)
  in parallel with the whole suite. The three-way e2e test did not collide this time: both runs passed it.
- Artefacts in `~/.cache/dps-build/reports/S8-persona-review-r2/`: `static-gates.log`,
  `full-suite.log/.xml`, `gate-db-pgsource.log/.xml`, `new-tests-on-b18abc7.out`,
  `l1-r2.{b18abc7,b5a9b75}.out`, `l1-r2b-{notes,nonotes}.{b18abc7,b5a9b75}.out`,
  `l2-r2.{b18abc7,b5a9b75}.out`, `l2-r2-family-first.b5a9b75.out`, `l2-job-r2.out`,
  `adversarial-newkeys.{b18abc7,b5a9b75}.out`, `vf-debug-{5cef802,b18abc7,b5a9b75}.out`,
  `date-surname.out`, `bare-name.out`, `fp-sweep.{b18abc7,b5a9b75}.out`, `fp-effect.out`,
  `l2-fp-job.out`, `ndjson-isolated.log`, `ndjson-rate.{b5a9b75,5cef802}.out`, and `probes/`. (The first `l2-r2.b5a9b75.out` run had a probe bug in the
  family-first layout, which put the given name in the family column; `l2-r2-family-first.b5a9b75.out`
  is the corrected run.)
