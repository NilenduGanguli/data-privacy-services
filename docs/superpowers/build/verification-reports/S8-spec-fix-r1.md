# S8-spec fix round 1 (review `S8-spec-review-r1` at `41f30d6`): B1 and B2

**Result: both blocking findings fixed** on `fspec`, with tests written first. Commit: `c5b5fc2`
(`fix(fspec): S8-spec fix round 1 re-check ...`, parent `41f30d6`), author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailers. Not pushed.

- B1 is fixed as the reviewer prototyped it, with two refinements:
  - NFKC is applied first, so a full-width joiner still joins.
  - The break text is kept in the normalised string, so it still equals `normalise` of the whole
    value.
- B2 is fixed beyond the reviewer's prototype. The prototype (`p17`) still refused runs whenever
  the value a field gave up on was itself a swept value: 7/40 and 19/40 seeds. The committed
  version steers that value too: 0/40 and 0/40.

All evidence is in `/Users/neelu/.cache/dps-build/reports/S8-spec-fix-r1/`. That includes the
reviewer's probes re-run on the fix (`probes/`) and their outputs. No customer value, secret or
key was printed. Samples are fictitious, outputs are counts, and the new test assertions report
counts, not values.

## 1. B1: the digit rule's break set (`dps/engine/synth/schema_profile.py`)

- New `_BREAK = re.compile(r"((?:[^\w\-/.:,]|_)+)")`. A break is any run of characters that are
  neither a letter nor a digit, except the joiners `-` `/` `.` `:` `,`. That covers whitespace,
  `_ ; |`, quotes, brackets, `#`, `+`, `@`, zero-width spaces (review N-b) and others.
- `_words` now reads the value after NFKC and splits on `_BREAK`. Each piece is normalised on its
  own, breaks included (the group is captured). The cuts are the ends of the non-empty pieces.
  - The guard (`_SampleValues.__call__`) and the sweep (`found`) share `_words`, so they still
    agree on every value.
  - Why the break text is normalised instead of dropped: an exhaustive check of all code points
    (`probes/unicheck.py`) found one break character that does not normalise to nothing.
    U+0345, a combining mark, case-folds to a Greek iota. Dropping it would have changed the
    string that text originals are matched in. NFKC never changes `normalise` (0 code points).
- Docstrings corrected: the module, `_DIGIT_RUN`, `_words`, `_SampleValues`, `_tokens` and
  `verify_against_sample`.

## 2. B2: `_Steering.give_up` (`dps/engine/synth/schema_gen.py`)

- `give_up` now returns a bool, and the two callers (the redraw loop and `_skip`) respect it.
- **Field with its own `avoid_in` test:**
  - The field drops that test only (`self.own.pop`).
  - Without a guard, it also stops steering, as before.
  - With a guard, it returns whether the guard flags the value it gave up on. If it does, the
    caller draws or skips again with a fresh `MAX_SAMPLE_REDRAWS` budget around the guard
    alone. Only a value the guard still flags after that is kept. That sets `lost`, and the run
    is refused as before.
- **Field without its own test:** `lost`, unchanged.
- `sample_redraws` still counts every discarded draw.
- Docstrings corrected: `_Steering`, `give_up`, `execute_spec` and `sample_ids`.
- Prototype comparison (`b2-proto-p17-vs-new-test.txt`, `b2-fixed-vs-new-test.txt`). A 100-row
  sample with `id` 1000..1099 and k of 100 customer IDs inside that range, synthesized to 150
  rows over 40 seeds:

  | Sampled customer IDs inside the `id` range | p17 prototype | committed |
  |---|---|---|
  | 20 | 7/40 refused | 0/40 refused |
  | 50 | 19/40 refused | 0/40 refused |

## 3. Tests (failing first)

`tests-before-fix-at-41f30d6.log`: with the new tests on unchanged code, **5 failed, 3 passed**:
- the extended `LEAKS` case (`1001;1002` passed the sweep);
- the extended indexed-vs-naive sweep;
- the new B2 unit test;
- the two new route tests (B1: shared tokens; B2: 422 `leakage`).

After the fix, the three schema files pass: **129 passed** (`test_schema_profile` 65,
`test_api_schema` 27, `test_schema_gen` 37).

| File | Cases | Change |
|---|---|---|
| `tests/test_schema_profile.py` | 63 → 65 | see below |
| `tests/test_api_schema.py` | 25 → 27 | see below |

`tests/test_schema_profile.py`:
- `LEAKS` gains 12 forms: `1001;1002`, `1001|1002`, `["1001","1002"]`, `TXN_4821_1003`, `1001_1002`,
  `(1001)(1002)`, `#1001#1002`, `1001+1002`, a zero-width-space pair, `2016-09-02;2019-04-11`,
  `2016-09-02_10:15:00` and `[2016-09-02]10:15`.
- New `JOINED` list, which must pass (documented trade-offs): `1003-07`, `1003/1004`, `1001,1002`,
  `[1001,1002]` and a full-width `1001-2020`. `ACCIDENTAL` is unchanged.
- `_naive_found` follows the new break set character by character, after NFKC. The 300-row
  differential sweep now joins values with random separators, including a zero-width space, a
  full-width hyphen and U+0345.
- New `test_the_sweep_reads_a_string_as_normalise_does`: 3 000 random strings over a varied
  alphabet must satisfy `_words(s)[0] == normalise(s)`.
- New `test_a_field_that_cannot_avoid_its_own_values_still_avoids_the_swept_ones`: the dense-`id`
  sample with 50/100 customer IDs inside the range, 10 seeds, for both an `integer` and a
  skipping `sequence`. Each run must pass the sweep, write no sampled customer ID into `id`, and
  keep its own values.

`tests/test_api_schema.py`:
- `test_code_and_list_columns_do_not_repeat_a_sampled_id`: a 300-row sample with `TXN_dddd_dddd`
  and `["dddd","dddd"]` columns, 500 rows. It answers 200, both columns are `pattern`, the
  verification passed with redraws, and 0 generated tokens equal a sampled customer ID.
- `test_a_dense_id_next_to_a_swept_id_is_not_refused`: 3 calls of 300 rows. Each answers 200 with
  verification passed, and 0 generated `id`s equal a sampled customer ID.

## 4. The reviewer's probes re-run on the fix (in process, real route; counts only)

| Probe | Result now | At `41f30d6` (review) |
|---|---|---|
| `p13` separator forms (sweep, guard, M0) | **all 17 separator forms refused, guard = sweep**. Newly refused (12): `;`, `\|`, a JSON list of strings, an `_` code, an `_` pair, parentheses, `#`, `+`, zero-width, a date before `_time`, `[date]time`, `date;date`. Already refused: `a=…&b=…`, NBSP, narrow NBSP, newline, French thousands with NBSP. | 12 of them passed |
| `p13` joined and accidents | `[1001,1002]`, `1001,1002`, `1001-1002`, `1003/1004`, `1003-07`, ID in date, ID in amount, ID as integer part: not refused (documented) | same |
| `p12` phone forms | all refused as before; `1003/1004` and `1003-07` not (documented) | same |
| `p14` 1 000-row sample with `TXN_dddd_dddd`, JSON list and `date;date` columns, to 1 000 rows, 3 runs | **200 3/3, 0 values holding a sampled-ID token** (redraws 949-1 005) | 200 3/3 with 176-208 per column |
| `p16` dense `id` 1000..1999 next to 1 000 random 4-digit `cust_id`, 1 000 rows, 3 runs | **200 3/3, 0 `id` equal to a sampled `cust_id`**; about 600 equal a sampled `id` (N-d, documented) | 422 3/3 |
| `p1` LIVE and MINE, 20 calls each, patterns and NER | 200: 20/20, 20/20, 20/20, 20/20 | same |
| `p1` LIVE and MINE at scale | 200: 300/300 and 300/300 (patterns), 100/100 and 100/100 (NER). Total **0 refusals in 880 calls**. Same-column equality 0. | same |
| `p1b` sampled-ID raw tokens (MINE, 300 calls) | only as the integer part of a MINE balance, as `<ID>.99` (13) and `<ID>.9` (2), by design | same class (13 + 1) |
| `p2` planted copies, patterns and NER | **22/23 refused with no echo**; `1003-07` 200 (documented); the 4 accidents 200 | same. One reference column differs: on `city_exact_exempt`, the M0 comparison varies with the call's random seed. |
| `p4` DDL bounds | identical output | — |
| `p5` 1 000-row sample to 10 000 rows | 200 3/3 in 3.56-3.62 s | 3.3 s; prototype 3.5-3.6 s |
| `p5` with mobile and PAN | 200 3/3 in 4.74-4.78 s | prototype 4.6-4.8 s |
| `p5` 200-row sample to 1 000 rows | 200 5/5, 0.32-0.34 s | — |
| `p6` no sample | executor output byte-identical to `e1f3a3a` (hash files equal) | same |
| `p8` determinism | same seed gives the same rows; an idempotent replay is identical; a spec-only replay differs exactly in `sample_redraws` rows | same |
| `p10` doomed sample | 422 `leakage` with no echo, 0.08 s (100 rows) and 0.39 s (10 000 rows) | 0.04 and 0.29 s. Run alongside 11 other probes; a field with its own test now spends one extra `MAX_SAMPLE_REDRAWS`. |
| `p18` P1 (LLM faked at `LlmGateway._stellar`, real `complete()` and egress verifier) | 0 sample values in the prompt (0/27, 0/48), the profile or the response metadata, patterns and NER | same |

## 5. Gates (lane `dps-wt-fspec`, `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 435 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success, 150 files |
| Full suite (`pytest -q`, lane `dps-db` + `azure-ocr-mock`) | **3 294 passed, 354 skipped, 5 failed** in 951 s (`pytest-full.log`). The 5 are exactly the pre-existing failures in the review's baseline (`failures.txt` equals the review's list): scanner capture x2, crawler x2, playground NER coverage. The review had 3 290 passed; the 4 new tests account for the rest. |

## 6. Docs

- `docs/technical/api-m1.md`: the sweep paragraph now states the break set, with examples, and
  that a field that cannot avoid its own values is still steered around the swept ones.
- `docs/superpowers/build/M1-DEVIATIONS.md`:
  - The round-1 joiner sentence and the N3 give-up sentence are corrected in place. They name what
    `ec74985` really did.
  - A new section, "Fix round 1 for S8-spec, re-check", covers B1, B2, the docs, the tests,
    measurements and gates.

## 7. Not changed (owner decisions, as the review recorded them)

- **N-a.** A comma without a space, and `-` or `/` pairs, still join (`1001,1002`, `[1001,1002]`,
  `1003-07`, `1003/1004`). These are documented trade-offs.
- **N-b.** Zero-width and other format characters are now breaks, so this is covered by B1.
- **N-c.** P1, pre-existing since `0fd76d0`: data-like keys the detector does not flag still reach
  the model. It is the one open P1 item for the owner.
- **N-d.** A field that keeps its own sampled values is acceptable as documented. Widening such a
  range is an option.
- **N-e.** N1, N4 and N5 remain as recorded.

## 8. Lane

The lane stack (`dps-db`, `azure-ocr-mock`) was started for the full suite and stopped afterwards
with `docker compose --profile docs stop`. No other project's containers were touched.
