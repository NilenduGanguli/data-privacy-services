# S8-spec — schema synthesis from a sample at realistic sizes (fspec)

## Update 2026-10-15 (continuation run): review r1's blocking findings fixed

**Verdict: DONE.** The first implementation (`9e38165`, `a46d1c5`, section "First implementation"
below) was reviewed (`S8-spec-review-r1.md`: FAIL, 2 blocking findings); the fix round directory
`S8-spec-fix-r1/` was empty (the owner pause stopped it before any change). This run resumed from
there: worktree clean on `a46d1c5`, nothing uncommitted, so it fixed B1 and B2, implemented N3
and recorded the owner decisions, test-first.

| Commit | What |
|---|---|
| `ec74985` | fix(schema): the digit rule refuses copies across whitespace and long numbers anywhere, ID-like keys avoid their sampled values, wide decimal bounds stay in the column (code, tests, `api-m1.md`, a comment in `batch.py`) |
| `41f30d6` | docs(schema): M1-DEVIATIONS records fix round 1 (B1, B2, N3 and the owner decisions N1, N2, N4-N6) |

Author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer, explicit pathspecs. Pushed by the owner's checked `tools/dev/autopush.sh` (run by hand after the commits: gitleaks, author and trailer checks passed; log `pushed fspec 41f30d6`).

### What changed

- **B1, the digit rule (blocking).** S8's first rule (an all-digit original only as a whole run of
  digits once separators are removed) let 4 real-copy forms through the route with the value in
  the body (`1001, 1002`, `1001 1002`, a sampled date in `2016-09-02 10:15:00`) and, at sweep
  level, a sampled mobile with a country code (`+91 9810010012`, `+91-98100-10012`). The rule
  now, shared by the guard and the sweep (`schema_profile._SampleValues`):
  - an all-digit original of `LONG_DIGITS` = 9 digits or more is found anywhere in the digits (M0):
    longer than a date's 8, so nothing generated holds one by accident;
  - a shorter one where it starts and ends at a break, the edge of a run of digits or whitespace in
    the output string (each string normalised word by word); `-`, `/`, `.`, `:` and a comma with no
    space after it still join digits (dates, amounts, ranges);
  - a whole-number string with a zero fraction (`1001.00`, `1,001.0`) is read as its integer
    (the review's optional extra), as integral floats already were.
  The reviewer's validated prototype (`p7_hybrid_rule.py`, `combo`), implemented with the index:
  long digit originals join the 4-character head index, short ones are looked up per span of
  whitespace-delimited segments of each run (≤ 8 digits).
- **B2, wide decimal bounds (blocking).** `_column_top` now computes the bound under
  `localcontext(prec=digits + scale + 2)`: `NUMBER(30,10)` 9.999999999999998e19 (was 1e20),
  `NUMBER(38,10)` 9.999999999999997e27 (1e28), `DECIMAL(38,18)` below 1e20, `DECIMAL(65,30)`
  9.999999999999998e34 (1e35).
- **N3, ID-like keys (implemented, not only recorded).** The task asks that key/ID-like integer
  fields avoid exact sample values; S8 did so only for fields the sweep looks at. A key whose last
  word is `id ids key ref no nbr num number seq pk` (snake, kebab, camel) keeps its sampled values
  (`FieldStats.id_values`, never in a prompt or response; integer and string fields), and
  `schema_profile.sample_ids` → `execute_spec(avoid_in=...)` steers that field around its own
  values (redraw; a `sequence` skips). Values shorter than `SWEEP_MIN_LEN` are not avoided (row
  numbers). Not swept: a field that cannot avoid them (range = the sample's) keeps them and only
  that field stops being steered (`_Steering.give_up`); swept fields are still steered, nothing is
  refused for it.
- **Recorded (M1-DEVIATIONS, new last section):** N1 absence numbers (one-year dates: precision
  0.80-1.00, recall 1.00; 1 000-row sample refused 3/3) as an owner decision; N2 (pre-existing P1
  key exposure since `0fd76d0`); N4 (3-digit IDs); N5 (replay without the sample reveals which values
  coincided; also one sentence in `api-m1.md`); N6 (`batch.CHANCE_EQUAL_ENTITIES` comment fixed).

### Tests (test-first)

- New API cases (4 forced copies, the mobile with a country code, an `id` key's `sequence`) failed
  6/6 on `a46d1c5` before the change; the profile tests failed to import (`sample_ids`).
- `tests/test_schema_profile.py` 61 → 63 cases, `tests/test_api_schema.py` 19 → 25; schema files
  plus `test_schema_gen`, `test_llm_egress`, `test_no_values_in_logs_m1`: 142 passed.
- No assertion was weakened: `LEAKS` only grew; `_naive_sweep` (the independent oracle of the
  indexed sweep) now encodes the r1 rule position by position.

### Proofs (in process, the real route: TestClient over `create_app`; outputs in `reports/S8-spec/fix-r1/`)

| Probe | Result |
|---|---|
| live sample (LIVE) and an independent variant (MINE), 100 rows a call | 20/20 + 20/20 patterns, 20/20 + 20/20 Presidio, 300/300 + 300/300 patterns, 100/100 + 100/100 Presidio: **0 refusals in 880 calls** (`p1-*.txt`) |
| independent client-side check of those outputs | 0 generated values equal to a sampled value of the same swept column; raw sampled-ID tokens only as a balance's integer part (`<ID>.99` / `<ID>.9`, 10 strings in 300 MINE calls, whose sample has balance 1001.0; `p1b-token-class.txt`) |
| planted copies after the executor (`p2_planted.py`) | **22 of 23 real-copy forms 422 `leakage`, value not echoed** (patterns and Presidio); `1003-07` 200 by design; the 4 accidents (ID inside a date, inside `21001.5`, as `1001.25`'s integer part, an exempt city) 200 |
| sweep-level phone forms (`p12`) | `+91 9810010012`, `+91-98100-10012`, `1001.00`, `1001, 1002`, `1001 1002` now refused (as M0); `1003/1004`, `1003-07` not (joined like a date/range) |
| DDL bounds through the route (`p4`) | all 10 columns within the column, incl. `NUMBER(30,10)` 9.999999999999998e19, `NUMBER(38,10)` 9.999999999999997e27, `DECIMAL(65,30)` 9.999999999999998e34; JSON Schema edge cases no 500 |
| realistic Faker samples (`p5`) | 1 000 → 10 000 rows 3/3 in 3.9-4.2 s (review on `a46d1c5`: 3.8-3.9 s); + mobile/PAN 3/3 in 4.9-5.3 s (4.4-4.6 s; long numbers are scanned as substrings now); 200 → 1 000 5/5; 1 000 (+phone) → 1 000 5/5; same-column equality 0 everywhere; redraw counts unchanged (~5 850 / ~6 950 per 10 000 rows) |
| doomed sample (IDs fill the range, `p10`) | 422 `leakage` in 0.09 s (100 rows) / 0.42 s (10 000 rows), nothing echoed |
| no sample (`p6_identity.py`) | executor output byte-identical to `e1f3a3a` (3 equal hashes, base tree exported to the scratchpad) |

### Live HTTP (lane stack `dps-wt-fspec`, image `data-privacy-services:fspec` rebuilt from `41f30d6`; sha256 of `schema_profile.py`, `schema_gen.py` and the router in the container = committed files) — `fix-r1/live-proof-r1.txt`

1. Live sample 20 × 100: 20/20 200, verification passed (checked 16); independent client-side check: 0 same-column equalities, 0 output strings holding a swept value (r1 rule, own code).
2. 1 000-row Faker sample × 10 000 rows ×3: 200 in 4.8-5.6 s (other lanes loading the host); same-column survivors 0, any-field 0.
3. Real copies refused: a sample whose IDs fill the range → 422 `leakage`; a sampled name as a column name → 422 `leakage`; neither echoes a value; bad seed → 422 `invalid_seed`.
4. Same with mobile + PAN columns × 10 000 ×3: 200 in 5.6-5.9 s; survivors 0 for cust_id, name, e-mail, opened, dob, mobile, PAN; any-field 0.
5. ID-like keys (`id` 7001..7004, `order_ref` R-7000..R-7003) 20 × 100: 20/20 200 (`id:integer`, `order_ref:pattern`), **0 sampled id / order_ref values drawn**.
6. DDL through `/schema/generator-spec`: `NUMBER(3)` 999, `NUMBER(5,2)` 999.99, `(30,10)`, `(38,10)`, `(38,18)`, `(65,30)` all ≤ the column (checked at 200-digit precision).
- `tools/smoke_test.py` (`DPS_SMOKE_M2=off`, `DPS_SMOKE_REQUIRE_OCR=1`): **25/25 PASS** (`smoke.txt`; with M2 on, `m2 connections` fails only because the `sources` profile is not up).
- App log scan (`log-scan.txt`, 142 lines): 0 of 7 696 whole values, 0 of 829 name parts, 0 of 1 887 IDs (trace ids and timings excluded), 0 of 2 000 mobiles/PANs.

### Gates

- `ruff format --check dps tests tools` (435 files), `ruff check`, `mypy dps` (150 files): clean.
- Full suite (lane venv, lane `dps-db` + OCR mock; `fix-r1/pytest-full.log`): **3 289 passed, 354 skipped, 6 failed** (962 s). 5 are the pre-existing, unrelated failures the review reproduced on an exported `e1f3a3a` tree (scanner capture ×2, crawler ×2, playground NER coverage). The 6th, `scanner/test_guard.py::test_https_popups_opened_while_arm_runs_relay_nothing[False-0]` ("Execution context was destroyed, most likely because of a navigation"), is a browser race under the load of the other lanes' suites: **4/4 passed on re-run**.
- M1 gate `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"`: 139 passed, 2 skipped (tesseract).
- Stack stopped (`docker compose --profile docs stop`), worktree clean.

### Still open (owner decisions, recorded in M1-DEVIATIONS)

1. **N1 absence / narrow value spaces**: steering over a small space (one-year dates) reveals the sample's values by absence, and a sample covering most of the space is refused (1 000-row one-year sample 3/3 422). Steer / exempt / refuse per field.
2. **N2 (pre-existing, P1)**: identifier-like data keys the detector does not flag (`arjun_mehta`) reach the model; unchanged since `0fd76d0`. Option: alias every sample key that is not a known schema word.
3. Digit forms joined like a date or range (`1003-07`, `1003/1004`) are not refused (the trade-off that keeps dates and amounts from matching); 3-digit IDs are neither swept nor avoided (M0's `SWEEP_MIN_LEN`).
4. From the first implementation: single-word name columns at the vocabulary limit are refused; exempt types (city, card expiry, gender, nationality) are not swept.

---

## First implementation (9e38165, a46d1c5; reviewed in S8-spec-review-r1)

**Verdict: DONE.** Branch `fspec` (worktree `/Users/neelu/document_intelligence/dps-wt-fspec`), on top of `e1f3a3a`:

| Commit | What |
|---|---|
| `9e38165` | fix(schema): synthesis from a sample works at realistic sizes — whole-digit IDs, steered draws, exempt small spaces, exact bounds (code, tests, `docs/technical/api-m1.md`) |
| `a46d1c5` | docs(schema): M1-DEVIATIONS records the e1f3a3a rule change and every S8 rule |

Author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer, explicit pathspecs. Not pushed by me (left to the owner's
autopush loop; the repo's CLAUDE.md forbids pushing without the owner's explicit request). Worktree clean.

### 1. Starting state

The paused attempt left 5 uncommitted files on `e1f3a3a` (verify.py, schema_gen.py, schema_profile.py, the schema router,
tests/test_schema_profile.py): digit-bounded matching inside M0's `verify_output`, a per-field redraw guard, `_canon`,
exact DDL bounds, `sample_redraws`. I read and measured it before changing anything:

- It fixed the 4-row live sample (20/20 calls 200) but still refused a realistic sample: a 1 000-row sample → 422 3/3
  at 1 000 and 10 000 rows, and a 10 000-row call took 34-37 s (the M0 sweep loop plus 20 redraws a row on a doomed run).
- It broke `tests/test_api_schema.py::test_generated_rows_holding_a_sample_value_fail_closed` (its premise changed).
- Gaps found by new tests (8 failed on the WIP): an entity redraw took another persona's e-mail (name/e-mail
  disagree); `sequence` and `uuid` were never steered; a value stuck after 20 draws kept costing 20 draws a row; a
  rule-constraint target was steered for nothing; cities were steered (impossible at volume, see §3); a flagged key
  with label values was still categorical (guaranteed 422); `NUMBER(18,2)`'s top became 1e16 (above the column);
  integer fields took `int()` of fractional bounds (1.5 → 1).

Kept: `_canon`, `sample_guard` idea, `sample_redraws`, exact-bound intent. Reverted: the `verify_output(digit_bounded=...)`
change (M0's verifier is untouched again; `verify.py` only gains the public walker `output_strings`).

### 2. What was wrong (reproduced on e1f3a3a)

`POST /api/v1/schema/synthesize`, the live sample (cust_id 1001-1004, full_name, email, city, segment, balance, opened),
20 calls × 100 rows, through the real route (TestClient over `create_app`, stub LLM, en_IN):
- patterns only: **20/20 → 422 leakage**; with real Presidio (en_core_web_sm): **18/20 → 422**.
- 20 seeded runs, hit classes: 19 refused; 15 runs had a sampled ID inside a date / amount / e-mail digits, 17 a sampled
  city, 3 a sampled date (exact ID repeats also occur: 1-3 per 20 runs in other seeds).
- realistic Faker en_IN sample (1 000 rows: 4-digit IDs, names, e-mails, cities, segment, balance, opened, dob): 422 at
  1 000 and 10 000 rows (3/3), 14 s per 10 000-row call (13.4 s of it the sweep).

### 3. Changes (9e38165)

**Item 1 — false positives, without weakening real detection**
- **Whole digit runs.** An all-digit sample value matches only as a whole run of digits (separators removed, as in all
  sweeps), integral floats as integers: `1001` is found in `1001`, `1001.0`, `１００１`, `CUST-1001`, `x1001@…`
  (and `10.01`, same digits — avoided by a redraw), never in `2010-01-04`, `21001.5`, `1001.25`, `1001-2020`.
  Text values stay substrings (M0).
- **One matcher for guard and sweep** (`schema_profile._SampleValues`): an index of the sample's values by their first
  4 characters; `sample_guard` (executor) and `verify_against_sample` (sweep) both use it, so they agree on every value
  (test), and the sweep is linear in the output: 13.4 s → 0.29 s for 10 000 rows vs a 1 000-row sample.
  `verify_against_sample` returns the same Verification shape (checked, hits once per path+type, original order);
  equality with the naive loop and with `verify_output` (text originals) is tested on 300 random rows.
- **Steering in the executor** (`execute_spec(avoid=...)`, `schema_gen.STEERING`): with a sample, a generated value
  holding a sampled one is replaced before the sweep — integer/decimal/normal/lognormal/date/datetime/pattern/text/
  uuid: drawn again; `sequence`: skipped (stays unique, ascending; a model proposing `sequence{start:1000}` for the
  flagged `cust_id` no longer writes 1001-1004 every run); `entity`: the row's whole persona is redrawn, so name,
  e-mail and birth date still agree. `constant`/`choice`/`boolean` are never steered (a spec literal holding a sample
  value is refused). Rule-constraint targets are not steered. After 20 new draws a value is kept and steering stops
  (the sweep refuses; no 20-draw-per-row cost on a doomed run). `report.sample_redraws` counts discarded draws.
  The guard is built in the worker thread (it normalises every sampled value), not on the event loop.
- **Key/ID integers avoid exact sample values**: the integer redraw and the sequence skip (above).
- **CITY (and STATE/COUNTRY):** steering was preferred, but it is impossible at realistic sizes, so `SWEEP_EXEMPT`
  gains **CITY** with gender/nationality's documented rationale — and **CARD_EXPIRY**, the same class (48 values).
  Measured with them swept *and* steered (1 000 generated rows, 5 runs): 4-row sample 0/5 refused; 50 rows (46 cities,
  31 expiries) 4/5; 200 rows (150 cities, 47 expiries) 5/5; 1 000 rows (298 of 318 cities) 5/5. Where steering works
  it leaks by absence: steered over 10 000 rows the 4 sampled cities appeared 0 times, every other city ≥ 17 times.
  The taxonomy has **no STATE or COUNTRY** type; other places are NER `LOCATION`, generated value-shaped (not from a
  list) — kept swept (probe with Presidio: no LOCATION typing of place columns, 0 refusals).
- **A flagged key is never categorical** (its values are swept as originals, and DPS's `choice` wrote them back →
  every run refused; Presidio flags `cust_id`, `ifsc` among 49 common keys).

**Item 2 — exact DDL / JSON Schema bounds**: `NUMBER(3)` → `integer{0..999}`, `NUMBER(5,2)` → `decimal{0..999.99}`,
`NUMBER(20)` → `10**20 - 1` (exact int), a decimal top no float holds rounds down (`NUMBER(18,2)` →
9999999999999998.0, `DECIMAL(20,12)` → 99999999.99999999), integer fields take ceil/floor of fractional bounds;
sampled ranges keep e1f3a3a's rules. Generated values checked ≤ the decimal bound (Decimal of their repr).

**Item 3 — recorded**: `docs/superpowers/build/M1-DEVIATIONS.md` new last section: the e1f3a3a rule change (flagged keys
aliased in the prompt only and mapped back, replacing M1 Task 12's "stays `field_N` in the spec and output"; plus its
three other rules) and every S8 rule above, tests, measurements, open items. `docs/technical/api-m1.md` describes the
steering, the digit rule, the exempt types, `report.sample_redraws`, the replay caveat and exact bounds.

**Determinism**: without a sample the executor is byte-identical to e1f3a3a (output hash `bc8f3144…` equal under both
codebases for every generator, unique retries, nulls, constraints, 4 locales × 4 seeds × 150 rows). With a sample, rows
are a function of (spec, seed, rows, sample); a row differs from the sample-free rows only where it held a sample value
(and a skipping sequence from there on), so replaying the returned spec+seed without the sample differs exactly where
`sample_redraws` steered (documented; the replay test now uses a fixed seed with 0 redraws).

### 4. Tests (test-first)

`tests/test_schema_profile.py` 46 → 61 cases; `tests/test_api_schema.py` 10 → 19. New tests failed first: 8 on the WIP
(persona coherence, sequence, uuid, rule targets, stuck cost, cities/expiry, flagged key, bounds); on e1f3a3a the profile
module fails to import (`sample_guard`) and 3 API tests fail (20×100 realistic, drawn-again, replay). Real leaks still
fail closed (API, 422 `leakage`, value not in the body): a sampled e-mail, name, re-cased name in another field, ID, ID
as an amount, ID inside an e-mail, and a date forced into the rows after execution; a spec literal holding a sampled
e-mail. One M1 test changed meaning by design (`…_fail_closed` → `…_drawn_again`: the executor now avoids a value
added to the sample; fail-closed is covered by the forced-leak tests).

### 5. Proofs

In-process real route (TestClient over `create_app`):
| Case | e1f3a3a | now |
|---|---|---|
| live sample, 20 × 100 rows, patterns only | 20/20 refused | 0/20 (redraws 0-1/run) |
| same, real Presidio NER | 18/20 refused | 0/20 |
| Faker sample 200 rows → 1 000 rows ×3 | 3/3 refused | 3/3 ok, 0.32-0.35 s |
| 1 000 rows → 1 000 ×3 | 3/3 refused | 3/3 ok, 0.37-0.46 s |
| 1 000 rows → 10 000 ×3 | 3/3 refused, 14 s | 3/3 ok, 3.3-3.8 s (Presidio 3.6-4.5 s; 6-digit IDs 3.2-3.3 s) |

Live HTTP, lane stack `dps-wt-fspec` (image `data-privacy-services:fspec` rebuilt from `9e38165`; container source hash
of schema_gen.py = committed file), `reports/S8-spec/live-proof.txt`:
- 20/20 calls of the live sample × 100 rows → 200, verification passed (checked 16); independent client-side
  comparison (own normaliser, not DPS code): **0** generated values equal to a sampled value of the same column
  (cust_id, full_name, email, opened), **0** output strings holding one; sampled cities drawn 20 times (exempt).
- 3 calls of the 1 000-row Faker sample × 10 000 rows → 200 in 3.5-3.7 s; same-column survivors 0 (cust_id,
  full_name, email, opened, dob), any-field 0.
- Real copies refused: a sample whose IDs fill the generated range (1000..1999 → range 1000..2000) → 422 `leakage`;
  a sampled name used as a column name → 422 `leakage`; neither body echoes a value. Bad seed → 422 `invalid_seed`.
- `tools/smoke_test.py` (docs profile, `DPS_SMOKE_REQUIRE_OCR=1`, M2 off): **25/25 PASS** (`smoke.txt`).
- App log scan (`log-scan.txt`): 0 of 4 144 whole values (names, e-mails, dates, cities), 0 of 711 name parts (5+
  letters), 0 of 1 002 IDs outside trace ids and timings (one `duration_ms` integer part equalled a random sample ID).

Other measurements: names probe (`scripts/names_probe.py`), exempt probe (`exempt_probe.py`), sweep timing
(`sweep_time.py`), identity hash (`identity_check.py`), base hit classes (`base_classes.py`) — all in `reports/S8-spec/scripts/`.

### 6. Gates

- `ruff format --check dps tests tools` (435 files), `ruff check`, `mypy dps` (150 files): clean.
- Full suite, lane venv, lane DB up (`pytest-unit-final.log`): **3285 passed, 351 skipped, 5 failed**. The 5 are
  pre-existing and unrelated (scanner capture/crawler ×4, playground NER coverage ×1): they fail identically on an
  exported `e1f3a3a` tree in this lane (5 failed, run in `scratchpad/s8/basetree`).
- M1 gate `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"`: 139 passed, 2 skipped (tesseract).

Lane environment fixes (git-ignored files only): installed the missing `scanner` extra (`playwright==1.55.0`) so the
scanner tests collect; removed `DPS_WORKER_METRICS_PORT` from `.env.lane` (a Settings field: exporting it made
`test_m2_defaults_keep_l1_database_free` fail; `.env` keeps it for compose). Stack stopped with `docker compose stop`.

### 7. Open (not changed; owner decisions)

1. **Single-word name columns at the sample limit**: a sample with separate `first_name`/`last_name` columns large
   enough to hold most of the en_IN vocabulary (533 given names, 554 surnames; ~1 000 rows) is refused 5/5 — no persona
   avoids every sampled word (4/50/200 rows pass). Whether a common given name in a generated name is a "copy" is a
   policy call; the sweep stays fail-closed. (The heuristic also writes full names into such a column.)
2. **Aggregate absence**: steered values never equal sampled ones; when the output dwarfs a field's value space (opening
   dates over 9 years vs 10 000 rows) the never-drawn days are mostly the sampled days. No row is tied to a sample record.
3. **Exempt types are not swept**: a copy of a city or card expiry would not be refused (same trade-off as gender).
4. A key that is data reaches the output as the caller's column name since e1f3a3a (recorded; the sweep still refuses
   it when it equals a sampled value, proven live).
5. `batch.CHANCE_EQUAL_ENTITIES`' comment cross-references `SWEEP_EXEMPT` (now a superset); left untouched (L2 code).
