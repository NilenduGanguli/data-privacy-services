# WP-I (m5): fix of the post-merge verification findings, round 1

**Result: both blocking findings are fixed. All gates pass in a venv with every extra, synth-ml included. The fix is committed as `ea4e751` on `main`.**

- Working copy: `/Users/neelu/document_intelligence/data-privacy-services`, branch `main`.
  - Start HEAD was `709f69a`, and the tree had no tracked changes.
  - The resume check found no earlier `I-m5-fix-r1.md` and no uncommitted work.
- Verify report addressed: `~/.cache/dps-build/reports/I-m5-verify-r1.md`.
- Scratch: `~/.cache/dps-build/i-m5-fr1/`.
  - `gates.sh` is the gate runner.
  - `logs/` holds every gate log.
  - The stack rebuild log is `~/.cache/dps-build/i-m5-fr1-up.log`.
- No `dps-wt-*` worktree or container was touched. I did not push or tag.

## Commit

- `ea4e751 fix(integrate): narrow the MOSTLY SDK output to one frame and pin the synth-ml extra in the tabular target test`
- Author: NilenduGanguli <nilendu.ganguli@gmail.com>. No trailer.
- Committed with an explicit pathspec covering only these 3 files:
  - `dps/engine/synth/mostly.py` (+5/-1)
  - `tests/connectors/test_targets.py` (+12/-1)
  - `tests/test_mostly.py` (+43)

## B1: mypy `union-attr` in `dps/engine/synth/mostly.py` (`_sdk_trainer`)

**Test first.** I added `tests/test_mostly.py::test_sdk_output_is_narrowed_to_one_frame`.
- It installs a stand-in `mostlyai.sdk` through `monkeypatch.setitem(sys.modules, ...)`. Its generator returns:
  - a plain `DataFrame`, which must give rows;
  - `{"rows": DataFrame}`, the SDK's per-table dict form, which must give rows;
  - `{"other": DataFrame}`, a list or `None`, each of which must raise `TabularUnavailable`.
- For the error cases it also asserts that the sentinel cell values do not appear in the exception message.
- It needs pandas, so it skips in `.venv`, which has no pandas.
- Before the fix it failed as expected in the all-extras venv, with `AttributeError: 'dict' object has no attribute 'to_dict'`.

**Fix:**

```python
generated = mostly.generate(generator, size=size, start=True, wait=True).data()
# The SDK types data() as one frame or a frame per table; this generator has one table.
frame = generated.get("rows") if isinstance(generated, dict) else generated
if not isinstance(frame, pd.DataFrame):
    raise TabularUnavailable("the generator did not return the generated table")
return [dict(r) for r in frame.to_dict(orient="records")]
```

- The fix fails closed and its message carries no values. `"rows"` is the table name this trainer passes to `mostly.train`.
- `linked.py` (`data["parent"]` / `data["child"]`) was not flagged by mypy and is unchanged.

**Results:**
- `mypy dps` gives `Success: no issues found in 128 source files` both in the all-extras venv `~/.cache/dps-build/venv-I-m5-1` and in `.venv`.
- The real-SDK `ml` test `test_real_sdk_trains_and_generates` passes with the narrowing in place.

## B2: `test_files_synthesize_group_and_tabular_rules` depended on the environment

- The test now takes `monkeypatch`.
- Refusal branch, with `mostly_available` patched to return `False`:
  - with the engine enabled, the result is `tabular_invalid`/422 (the extra is not installed);
  - with the engine not enabled, it is also 422.
- Positive branch, with `mostly_available` patched to return `True`:
  - with the engine enabled, `_check` accepts;
  - with the engine not enabled, the result is still 422.
- The patch works because `targets._check_tabular` imports `mostly_available` from the module at call time.
- The test passes in both venvs, with and without synth-ml.

## Gates

The stack was rebuilt and restarted after the change with `DPS_TOKENIZER_MOCK_PORT=8430 docker compose --env-file docker/compose.sources.env --profile full up --build -d`.
- Default ports were used, with the tokenizer mock on 8430 as in the verify run.
- `/readyz` reports `ready:true` with:
  - db up, migrations verified;
  - jobs ready;
  - fpe ff1;
  - tokenizer http;
  - ocr azure-read-v3.2;
  - keys db+static;
  - ner patterns-only, as expected.
- The stack is left running.

All gates ran in `~/.cache/dps-build/venv-I-m5-1`, which has every extra including synth-ml. The contract §12.2 `DPS_TEST_*` defaults were exported.

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 311 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success, no issues in 128 source files (also in `.venv`) |
| `DPS_SMOKE_REQUIRE_OCR=1 python tools/smoke_test.py` | all 34 checks passed |
| `pytest -q -rs` | **2072 passed, 4 skipped, 0 failed**. The verify run had 1 failed and 2070 passed; the difference is B2 now passing plus the 1 new test. Skips: tesseract binary ×2 and spaCy model ×2, the same host limitations as before |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` | 139 passed, 2 skipped (tesseract binary) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "db or pg_source or s3"` | 294 passed, 0 skipped |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "ml or slow or fpe"` | 18 passed, 0 skipped. This includes the real MOSTLY AI local engine |
| `.venv` (no synth-ml): `tests/test_mostly.py` + `tests/connectors/test_targets.py` | 37 passed, 2 skipped (pandas and mostlyai absent, as designed) |

**Not re-run:** the frontend, the browser marker, `demo_m2`, and the SEC/console probes.
- No frontend or route code changed, and these were green in `I-m5-verify-r1`.
- The change is limited to one SDK-output branch in the trainer and two tests.

## Deviations

None of substance. I added one test, `test_sdk_output_is_narrowed_to_one_frame`, beyond the two fixes I was asked for. It covers the new fail-closed branch.

## Open issues

None blocking.
- The 4 skips are host limitations: no tesseract binary and no spaCy English model.
