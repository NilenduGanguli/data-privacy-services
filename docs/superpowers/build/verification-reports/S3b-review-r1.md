# S3b review r1 — Task 8B (T3 labelling in L2/L3) and Task 17 S5 (scanner SSRF guard) on `fsec`

**Verdict: FAIL — 1 blocking finding (B1: the L3 labelling prompt carries raw page values, P1).**
Everything else holds: gates green, S5 wired with no bypass found in a 117-case adversarial
corpus, D4 re-proved live (+0 on every demo counter), git hygiene clean.

Reviewer: S3b review round 1, 2026-10-15 22:00–22:35 IST. Worktree
`/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec` at `fee0041` (clean, nothing
modified by this review). Reviewed range `c450a6e..fee0041`: `5c27637` (cherry-pick of fllm
`0fd76d0`), `2e41975`, `90ed7df`, `ee31fa4`, `fee0041`. Implementer report:
`~/.cache/dps-build/reports/S3b-impl.md`. This review resumed an earlier attempt of itself (its
artefacts in `S3b-review-r1/`: ruff/mypy/full-suite logs written after `fee0041`, the SSRF corpus
and the first L3 label probe); its cut-off security/scanner run was re-run.

## 1. Gates (run by the reviewer, lane `fsec` stack: dps-db, source-pg, minio, mongo, demo-site, app, worker, browser-worker)

| Gate | Result | Log (`S3b-review-r1/`) |
|---|---|---|
| `ruff format --check dps tests tools` | 467 files already formatted | `ruff-format.log` |
| `ruff check dps tests tools` | All checks passed | `ruff-check.log` |
| `mypy dps` | no issues, 155 files | `mypy.log` |
| `pytest -q` (full lane suite, on `fee0041`, 21:43–21:59) | **3740 passed, 164 skipped, 0 failed** (skips: oracle profile, docs/OCR mock, tesseract, pandas/mostlyai, spaCy model) | `pytest-full.log` |
| `pytest -q tests/security tests/scanner tests/test_llm_label_l2_l3.py tests/connectors/test_discover_job_llm_label.py tests/test_llm_egress.py` (security + scanner integration, db + browser) | **1082 passed, 53 skipped (tesseract), 0 failed** (9 m 49 s) | `pytest-security-scanner.log` |
| `pytest -q -m browser` | **182 passed, 59 skipped, 0 failed** (11 m 24 s; skips: 53 tesseract, 6 oracle-profile console tests) | `pytest-browser.log` |
| Live D4 smoke on the running stack (image = `fee0041` code: built 20:57 after the last code commit `90ed7df` 20:53; browser-worker container verified to run `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1`): `DPS_SMOKE_SCANNER=1 DPS_SMOKE_M2=off tools/smoke_test.py` | **34/34 passed** (2 OCR skipped): 21 pages, 63 fields, 51 personal; `non_get +0, form_get +0, logout_hits +0, admin_hits +0; non_get_allowed 0`, blocked 60 POST / 1 GET; evidence submissions 0; 3/3 masked captures; persona export 0 demo values. Demo `/__received` before == after (the non-zero baseline — 16 non-GET, every per-path count even — matches the direct-POST positive controls of `test_no_submit.py`/`test_demo_site.py` from the two pytest runs before it) | `d4-smoke.log`, `d4-received-{before,after}.json` |
| Container log scan (app, worker, browser-worker, last 30 min, covers the smoke scan) | 0 of 10 demo values, raw or normalised | (counts only, not kept) |
| Git | 5 commits authored and committed by NilenduGanguli <nilendu.ganguli@gmail.com>, 0 trailers; worktree clean | — |

## 2. Acceptance

| Item | Status | Evidence |
|---|---|---|
| 8B L2: leftover columns (no entity, not key, not row locator/binary/ObjectId) labelled from name, declared type, ≤3 shapes, kind, rates, table; `protected` = sampled values; cap 0.85; `min_score`; detectors `llm_label`; methods recomputed; anchor never moved; opt-in; stub no-op; ≤ `DPS_LLM_LABEL_MAX_FIELDS` per call | met | code `discovery.py` 540–680; `tests/test_llm_label_l2_l3.py`, `tests/connectors/test_discover_job_llm_label.py` (db + source-pg) pass; the prompt carries metadata keys only; L2 names that look like values are aliased by `prompt_fields` (`_PLAIN_NAME`) |
| 8B L3: one call per page, leftover fields only (not password/hidden/file), value-free prompt | **NOT met — B1** | the label element of the prompt carries raw page values (§3) |
| 8B `progress.metrics.llm` on discover and scan jobs | met (see N1 on what `blocked` misses) | `tests/scanner/test_scan_llm_metrics.py` (db), discover job tests |
| LLM tests faked at the provider seam (`_stellar`), not above `complete()` | met | every new test and both probes go through the real `complete()` (strict) |
| S5: `check_scan_url` at `POST /scans` (422, audited, nothing queued) | met | `tests/security/test_scan_ssrf.py::test_post_scans_runs_the_ssrf_guard_before_any_job_exists`; implementer's live probe |
| S5: browser-worker re-check before any browser | met | `tests/scanner/test_scan_ssrf_browser.py` (db) |
| S5: numeric encodings, IPv4-mapped IPv6, metadata IPs, DNS → link-local | met (see N3/N4) | §5 corpus |
| S5: Chromium `--host-resolver-rules` | met, deviation justified (plan's per-host EXCLUDE list breaks the proxy; measured by the implementer, flag verified in the running browser-worker) | `browser.RESOLVER_RULES`, `launch_args()` |
| D4 re-check | met | §1 smoke + browser proofs in both pytest runs |
| Docs (security review §3.25–§3.27, M5-DEVIATIONS, contract, HANDOFF §17.1) | accurate for S5 and D4; §3.26's P1 claim for L3 labels is wrong (B1) | — |

## 3. Blocking

### B1 — the L3 labelling prompt sends raw page values from attribute-sourced labels (P1: no raw value to an LLM)

`dps/layers/scanner/classify.py::leftover_field_meta` sends `label = redact_text(f.label, detector)`.
`redact_text` replaces only what a detector finds; `page_protected` (the leak guard's list) holds
digit/@ tokens of *rendered text and options* only. A field's label comes from `label[for]`,
a wrapping `label`, `aria-labelledby`, **`aria-label`**, **`placeholder`**, nearest text or
**`title`** (`dom.py` `resolveLabel`). Labels from attributes (and from `aria-labelledby` targets
that are not rendered) are in no protected list, so an identifier no detector names goes to the
model as is; the egress verifier's pattern scan only catches validated identifiers, so it passes
too. The placeholder fallback also contradicts the code's own rule "the placeholder is sent as its
shape only": with `label_source == "placeholder"` the raw placeholder is the label.

Reproduced end to end (real Chromium with `launch_args()`, `ScanRunner`, real
`LlmGateway.complete()` under `strict`, provider faked at `_stellar`, made-up values; probe
`S3b-review-r1/probes/l3_label_leak_v2.py`, output below):

```
page /a: label_sources [aria_label, placeholder, title]
  planted_in_messages_sent: aria_label_number true, placeholder_number true, title_number true,
                            aria_label_person true          (4/4 reached the provider)
  progress.metrics.llm: {calls 1, raw_values 0, blocked 0}    (the metrics report 0 raw values)
page /b: the same kind of number in a rendered <label for>  -> fallback leak_guard, 0 provider calls
```

(`<input type=checkbox aria-label="Close loan 77120034519">`, `<input placeholder="88120034519">`,
`<input title="Memo for loan 66120034519">`, `aria-label="Nominee Zebulon Quixote agrees"`; the
name case needs NER off, which is the default without presidio or without the `ner` tier.)
Banking UIs label per-record controls with record values all the time (accounts to debit, loans
to close, nominees), so this is the realistic case, not a corner. The same value in a rendered
label is caught (page /b), which also shows the opposite cost: one such label blocks the whole
page's call.

**Fix:** make the label value-free whatever its source, before the prompt is built: after
`redact_text`, replace every token holding a digit or `@` (`_VALUE_TOKEN`) by its shape (or drop
it), and send no label when `label_source == "placeholder"` (its shape is already sent). This also
stops a rendered label with a number from blocking the page's call. Names in labels stay
detector-dependent: either require NER for the L3 label (or send attribute-sourced labels only
when NER is on), or record it as a residual in §3.26/§5. Test through `complete()` with
aria-label / title / placeholder / hidden-`aria-labelledby` labels carrying a number: the
provider never receives it and the call is still made. Correct §3.26 of the security review.

## 4. Non-blocking (for the owner or a later round)

- **N1 — `metrics.llm.blocked` hides leak-guard refusals.** `blocked` counts egress-verifier blocks
  only (`LLM_STATS`), so a page whose call the module's leak guard stops records
  `{calls 0, raw_values 0, blocked 0}`, the same as "nothing to label" (page /b above). That
  matches the contract's definition but hides lost labelling. Consider counting `leak_guard` too,
  or a separate figure.
- **N2 — numeric hints collide with protected digit tokens.** A page that says
  "(max 2500 characters)" next to a `textarea maxlength=2500` loses its whole T3 call: `2500` is a
  protected token and the prompt holds `"maxlength":2500` (`probes/l3_maxlength_block.py`:
  `leak_guard`, 0 calls; without the text: 1 call). Fail closed, functional only. The leak guard
  could skip the numeric hint values, or `maxlength` could be bucketed.
- **N3 — `forbidden_ip_reason` depends on the interpreter.** The browser-worker image
  (`mcr.microsoft.com/playwright:v1.55.0-noble`, system Python **3.12.3**) has the old `ipaddress`
  semantics (an IPv4-mapped IPv6 is "reserved"), the app image (`python:3.12-slim`, 3.12.14) and the
  Mac venv (3.12.13) the new ones. Measured in the running containers: `::ffff:10.0.0.5` is refused
  on the browser-worker and allowed in the app; `::ffff:127.0.0.1` with `allow_loopback=True` is
  refused on the browser-worker, so
  `tests/security/test_netguard.py::test_loopback_follows_the_environment_on_both_ip_versions[::ffff:127.0.0.1]`
  fails under the image's interpreter; the reason text also follows set iteration order
  (`{ip, _embedded_ipv4(ip)}`). Always the safe direction (stricter at run time), no bypass. Fix:
  decide an IPv4-mapped address on its embedded IPv4 only and iterate candidates in a fixed order.
- **N4 — Azure WireServer is not in the metadata list.** `_METADATA` names Azure only for
  `169.254.169.254`; `168.63.129.16` (WireServer / HostGAPlugin `:32526`, the AKS bootstrap-token
  vector) is allowed (`ssrf-corpus.out`). A tenant admin can register it and the scanner, which
  may GET, would fetch and screenshot it on an Azure-hosted cluster (e.g. ARO). The list predates
  this round; adding `168.63.129.16/32` (and a test) is cheap.
- **N5 — `ScanGuard(allow_loopback=True)` is the default.** The only production caller (the
  crawler) passes `env == "local"`; a fail-closed default (`False`) would keep a future caller
  safe.
- **N6 (implementer's open item 1, confirmed as a residual, fllm's call):** the egress verifier
  looks for protected originals in the fixed system instructions too, so a sampled value such as
  "Personal" blocks L2 calls (5 of 11 demo tables in the implementer's measurement). Fail closed;
  it makes L2 T3 unreliable on real data and should be decided before v1.0.0.
- **N7 (informational, pre-existing):** value shapes keep combining marks (Devanagari vowel signs,
  viramas: `रामा` → `xाxा`); not a value, but more structure than "letters are x" and now sent to
  the model by L2.

## 5. S5 — what was tried and held (reviewer's adversarial corpus, `ssrf-corpus.out`)

- `forbidden_ip_reason`, 32 addresses: every metadata/link-local form refused in prod and local —
  `169.254.169.254`, `169.254.170.2`, `::ffff:169.254.169.254`, `::ffff:a9fe:a9fe`,
  `64:ff9b::a9fe:a9fe`, `2002:a9fe:a9fe::1`, `fe80::1`, `fe80::1%eth0`, `fd00:ec2::254`,
  `100.100.100.200`, `192.0.0.192`, `::127.0.0.1`, `::ffff:0:a9fe:a9fe`; unspecified, multicast,
  class E, broadcast refused; loopback (v4, v6, mapped) refused outside local. RFC 1918/ULA/CGNAT
  allowed by design.
- `check_scan_url`, 35 URLs × {prod, local}: numeric encodings (`2130706433`, `0x7f000001`,
  `0X7F000001`, `127.1`, `0177.0.0.1`, `0x7f.0x0.0x0.0x1`, `017700000001`), full-width digits and
  ideographic dots, userinfo and backslash confusions (`uat@169.254.169.254`,
  `169.254.169.254\@uat`, `uat\@169.254.169.254`, `uat%2F@…`), `ftp:`/`javascript:`, empty host,
  and registered names resolving to metadata / IPv4-mapped / 6to4 / zone-scoped / a split answer
  (one good, one metadata) / garbage → all refused; only the registered names (and loopback in
  local) pass; `uat#@169.254.169.254` passes as `uat` (the fragment — the same host Chromium uses).
- Every hop (real `ScanGuard` + scope proxy + counting server, fake DNS): 9 names over plain HTTP
  and `CONNECT` → only the allowed one dialled (200); metadata, mapped, NAT64, 6to4, split answer,
  loopback in prod → 403; unresolvable → 502; the counting server saw only the allowed path. Real
  resolver: numeric encodings at the proxy → 403/502, never dialled in prod.
- Parser differentials between `urlsplit` (accept) and WHATWG (Chromium) are backstopped: the hop
  check re-decides host and addresses on what Chromium actually requests.
- Code read: the proxy resolves once and connects to the checked addresses itself (no rebinding
  window); all-or-nothing on multi-address answers; the only `ScopeProxy`/`ScanGuard`/Chromium
  launch in `dps/` are the guarded ones; `POST /scans` resolves in a thread; error details are
  value-free.

## 6. Artefacts (`~/.cache/dps-build/reports/S3b-review-r1/`)

`ruff-*.log`, `mypy.log`, `pytest-full.log`, `pytest-security-scanner.log`, `pytest-browser.log`,
`d4-smoke.log`, `d4-received-{before,after}.json`, `ssrf-corpus.out`, `l3-attr-label-crawl.out`
(first attempt's L3 probe), `probes/` (`ssrf_corpus_probe.py`, `l3_attr_label_crawl.py`,
`l3_label_leak_v2.py`, `l3_maxlength_block.py`). Every probe uses made-up values and prints
booleans and counts only. The lane's compose stack was stopped at the end
(`docker compose --profile '*' stop`, 0 `dps-wt-fsec-*` containers running); no repository file
was modified (worktree clean).
