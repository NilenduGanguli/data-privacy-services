# S8-spec fix round 2 (review `S8-spec-review-r2` at `c5b5fc2`): B1 and B2

**Result: both blocking findings fixed** on `fspec`, with the tests written first. Commit:
`a0e2e59` (`fix(fspec): S8-spec fix round 2 ...`, parent `c5b5fc2`), author
`NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers. Not pushed.

- **B1** is fixed as the reviewer prototyped it, with two refinements:
  - Digits are counted in any script: `\d` in the lookahead instead of `[0-9]`.
  - The whole-number reading (`_INTEGRAL_TEXT`) uses the same comma grouping.
- **B2** is fixed exactly as prototyped: `_words` applies NFKC first.

All evidence is in `/Users/neelu/.cache/dps-build/reports/S8-spec-fix-r2/`:
- the review's probes, copied and re-run on the fix (`probes/`, `r1probes/`);
- their outputs (`*.txt`);
- the failing-first test log;
- the full-suite log.

No customer value, secret or key was printed. Samples are fictitious, outputs are counts and
booleans, and the new assertions report counts, not values.

## 1. B1: a comma joins digits only before a group of two or three (`dps/engine/synth/schema_profile.py`)

- New `_BREAK = re.compile(r"((?:[^\w\-/.:,]|_|,(?!\d{2,3}(?!\d)))+)")`.
  - A comma is a joiner only before exactly two or three digits that no digit follows: thousands
    (`1,001`, `48,250.50`), lakh (`10,01,002`) or a decimal comma (`1001,25`).
  - Anywhere else it is a break: before 4+ digits, 1 digit, a space, a bracket or the end. So
    `1001,1002` and `[1001,1002]` hold their IDs as tokens. A one-place decimal comma (`1001,5`)
    is therefore read strictly too; no amount generator writes one, and a generated value that
    holds a sampled ID is drawn again (documented in `_BREAK` and M1-DEVIATIONS).
  - The guard (`_SampleValues.__call__`) and the sweep (`found`) share `_words`, so they still
    agree on every value.
- **Refinement 1: `\d`, not `[0-9]`.**
  - `normalise` folds every decimal digit (Nd) to ASCII, so the hay's digit runs count digits of
    any script. An exhaustive check over all code points found that, after NFKC, exactly the
    characters `\d` matches normalise to an ASCII digit (0 exceptions), and `\d` equals
    `str.isdecimal()` (0 mismatches).
  - With `[0-9]`, two mixed-script forms read the other way from their ASCII twins:
    - `1001,10०२` (normalised `1001,1002`) would be joined, so it would be missed;
    - `1001,२५` (normalised `1001,25`) would be a break.
  - With `\d`, the rule depends only on the normalised digits. On ASCII text the two agree.
- **Refinement 2: `_INTEGRAL_TEXT = \s*([+-]?)(\d+(?:,\d{2,3})*)\.0+\s*`.**
  - The old `(\d[\d,]*)` accepted any comma, so `1001,1002.00` was read as the integer
    10 011 002 and hid `1001`. This is the same root cause as B1.
  - Now a whole-number string's commas must be the same grouping commas. `1,002.00` and
    `10,01,002.00` are still whole numbers.
- **Still joined (documented trade-off):** a comma before exactly two or three digits next to an
  ID (`[1001,25]`, `1001,100`) reads as grouping or a decimal comma, in the same class as `1003-07`.

## 2. B2: the whole-number check after NFKC

- `_words` starts with `text = unicodedata.normalize("NFKC", text)`. The `_INTEGRAL_TEXT` check
  then sees ASCII, so a full-width `１００４．００` is the integer 1004.
- `normalise(NFKC(s)) == normalise(s)` (NFKD of NFKC is NFKD), so the concatenation property is
  unchanged.

## 3. Tests (failing first)

`tests-before-fix-at-c5b5fc2.log` records the new tests run on the unchanged code: **6 failed,
90 passed**. The failures:
- `test_the_sample_sweep_matches_numbers_as_whole_digit_runs` (`１００４．００` passed the sweep);
- `test_the_indexed_sweep_equals_the_naive_one` (the naive rule now breaks list commas);
- three planted route forms: `1001,1002`, `[1003,1004]` and `１００４．００` gave 200;
- the new route test `test_comma_lists_of_ids_do_not_repeat_a_sampled_id` (shared tokens).

After the fix, the three schema files pass: **133 passed** (`test_schema_profile` 65,
`test_api_schema` 31, `test_schema_gen` 37).

`tests/test_schema_profile.py` (65 cases, unchanged count):
- `LEAKS` gains `1001,1002`, `[1001,1002]`, `1001,1002.00`, `1001,10०२` (Devanagari) and
  `１００４．００`.
- `JOINED` loses the two lists and gains `10,01,002`, `1001,25`, `1001,250.50`, `48,250.50` and
  `1001,२५`.
- `_naive_found` reads the string after NFKC first, whole-number check included. That check is
  its own non-regex grouping test. A comma is a break unless exactly 2-3 `isdecimal` digits and
  then no digit follow it (`_groups_digits`).
- The differential sweep (300 random rows, indexed versus naive) adds plain and full-width
  commas, 2- and 3-digit groups and a Devanagari ID.
- `test_the_sweep_reads_a_string_as_normalise_does` now skips strings whose **NFKC** form is a
  whole number, as the review asked. Its alphabet gains a full-width full stop and a Devanagari
  digit.

`tests/test_api_schema.py` (27 to 31 cases):
- `test_a_sample_value_forced_into_the_output_is_refused` gains `("segment", "1001,1002")`,
  `("segment", "[1003,1004]")` and `("balance", "１００４．００")`. Each gives 422 `leakage` with
  no echo.
- New `test_comma_lists_of_ids_do_not_repeat_a_sampled_id` uses a 300-row sample with a
  `related_csv` column (`dddd,dddd`) and a `related_json` column (`[dddd,dddd]`, compact JSON
  integers) of customer IDs, and asks for 500 rows. It answers 200, both columns are `pattern`,
  the verification passed with redraws, there are more than 1 000 tokens, and **0 tokens are
  sampled IDs**.

## 4. The review's probes re-run on the fix (in process, real route; counts only)

| Probe | Result now | At `c5b5fc2` (review) |
|---|---|---|
| `r2_wide` 600 to 1 000, 3 runs | **200 3/3, 0 list values holding a sampled ID**, 0.75-0.8 s, redraws 1 111-1 136 | 200 3/3, 110-123 per column |
| `r2_wide` 600 to 10 000, 2 runs | **200 2/2, 0**, 7.0-7.2 s | 1 149-1 226; 7.9-8.1 s |
| `r2_wide` 200 to 1 000, 3 runs | **200 3/3, 0** | 33-56 |
| `r2_wide`, every run | same-column equality 0 on all 10 personal columns | same |
| `r2_accept` 20 x 100: stub and model, NER off and on | 20/20 200 in each of the 4 modes | same |
| `r2_accept` 300 x 100: stub and model | 300/300 200 each. Total **0 refusals in 680 calls**; 0 sampled-ID tokens; 0 same-column equal; 0 sample values in the prompt | same |
| `r2_planted`, NER off and on (expectations updated) | **39/39 real-copy forms 422 `leakage`, no echo**: the review's 34 plus `1003,1004`, `[1003,1004]`, a Devanagari `1003,1004`, `1003,1004.00` and `１００４．００`. 9/9 joined forms and accidents 200 (`1003-07`, `1003/1004`, full-width `／`, `10,03,002`, `1003,25`, `1003,250.50`, and the 3 accidents). 4/4 model literals 422. | 34/34; comma lists and full-width integral 200 |
| `p13` separators | **`json_list_int` and `comma_nospace` now refused**; guard = sweep on every case; only `-`/`/` forms and accidents pass (documented) | those two passed |
| `p14` code/list columns, 1 000 to 1 000, x3 | 200 3/3, 0 tokens (redraws 942-1 035) | same |
| `p16` dense `id`, x3 | 200 3/3, 0 `id` equal to a sampled `cust_id`; 588-604 equal a sampled `id` (N-3, documented) | same |
| `p5` base, 1 000 to 10 000, x3 | 200 3/3 in 3.55-3.6 s | 3.64-3.77 s |
| `p5` phone + PAN, x3 | 200 3/3 in 4.49-4.62 s | 4.73-4.79 s |
| `p10` doomed | 422 `leakage`, no echo; 0.04 s (100 rows), 0.28 s (10 000 rows) | 0.05 / 0.34 s |
| `p6` no sample | executor hashes identical to `c5b5fc2` (= `e1f3a3a`) | — |
| Log scan (`logscan.py` over raw app stdout and stderr: 5 model calls with NER plus 53 planted calls) | **0 of 24** sampled values (IDs, names, name parts, e-mails, dates). Positive control (the probe's source): 24/24. | 0 of 24 |

## 5. Gates (lane `dps-wt-fspec`, `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 435 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success, 150 files |
| Schema files (`test_schema_profile`, `test_api_schema`, `test_schema_gen`) | **133 passed** (`tests-after-fix.log`) |
| New tests against `c5b5fc2` code | **6 failed, 90 passed** (`tests-before-fix-at-c5b5fc2.log`) |
| M1 gate `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` | 139 passed, 2 skipped (`pytest-m1-gate.log`) |
| Full suite `pytest -q` (lane `dps-db` + `azure-ocr-mock`) | **3 298 passed, 354 skipped, 5 failed** in 849 s (`pytest-full.log`). `failures.txt` is identical to the review's baseline: scanner capture x2, crawler x2, playground NER coverage. 3 298 = the review's 3 294 + the 4 new route cases. |

## 6. Docs

- `schema_profile.py`: the comma sentences now match the rule in:
  - the module docstring;
  - the `_BREAK` and `_INTEGRAL_TEXT` comments;
  - the `_words`, `_SampleValues` and `verify_against_sample` docstrings.
- `docs/technical/api-m1.md`: the sweep paragraph.
  - A comma joins only before a group of two or three digits; any other comma is a break.
  - Digits of any script count as ASCII digits.
  - `1001,1002` and `[1001,1002]` are found; `10,01,002` and `1001,25` are not.
- `docs/superpowers/build/M1-DEVIATIONS.md`:
  - the round-1 entry (its five-joiners sentence), and lines 1919-1920 and 1949 of the re-check,
    now point to fix round 2;
  - a new section, "Fix round 2 for S8-spec", covers B1, B2, the two refinements, the docs, the
    tests, the measurements and the gates.

## 7. Not changed (review non-blocking items, still owner decisions)

- N-1 (`Rs 1004.00` inside text), N-2 (P1: data-like keys that the detector does not flag reach
  the model), N-3 (a dense `id` keeps its own values) and N-4 (`-` and `/` pairs).
- The comma rule adds one documented case to N-4's class: a comma before exactly two or three
  digits stays a joiner (`[1001,25]`).

## 8. Stack

The lane stack (`dps-db`, `azure-ocr-mock`) was started for the gates and stopped afterwards with
`docker compose --profile docs stop`. No other project's containers were touched.
