# S3b-impl — Task 8B (T3 labelling in L2 + L3) and Task 17 S5 (scanner netguard) on `fsec`

**Status: DONE** (resumed 2026-10-15 19:40 IST after the owner pause; finished ~21:30 IST).
Worktree `/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec`. Commits are local
(not pushed: the push is the owner's / the orchestrator's autopush).

## 0. Starting state
- `fsec` at `c450a6e`, clean except the untracked `tests/test_llm_label_l2_l3.py` (written 11:12 by
  the paused attempt, 334 lines, red at collection: it imported names that did not exist yet).
- `0fd76d0` (fllm: the egress verifier's pattern scan reads call data only) was already
  cherry-picked as `5c27637` (verified: same stat and message, author NilenduGanguli, no trailer).

## 1. Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers, explicit pathspecs)
| Commit | Content |
|---|---|
| `2e41975` | feat: opt-in T3 metadata labelling for leftover columns in discovery and leftover fields in the UI scanner (Task 8B) |
| `90ed7df` | fix(security): the scanner's SSRF guard runs at accept, on the browser-worker and on every hop (Task 17 S5 / F-03) |
| `ee31fa4` | test: discover and scan jobs record T3 labelling in progress.metrics.llm |
| `fee0041` | docs(fsec): security review §3.25–§3.27, M5-DEVIATIONS (Task 8B, S5), contract, HANDOFF §17.1 |

## 2. Task 8B — T3 labelling in L2 discovery and L3 classification
- **L2** `discovery.label_leftover_columns(columns, shape, rows, *, policy, engine, settings)`,
  called by `discover_job` after `classify_columns` and before the snapshot is stored. Leftovers =
  no entity, not key, not row locator/binary/ObjectId. Metadata only: leaf name, declared type,
  ≤3 shapes (cut to 24 chars), kind, null rate, distinct ratio, table. Protected = every sampled
  value of the leftover columns. Wide tables: calls of ≤ `DPS_LLM_LABEL_MAX_FIELDS`. A verdict ≥
  min confidence and ≥ policy `min_score` sets entity, confidence ≤ 0.85, sensitivity, detector
  `llm_label`, recomputed methods/type_note; anchor set only if none exists.
- **L3** `classify.leftover_field_meta`, `page_protected`, `label_leftover_fields`; the crawler
  labels right after `classify_fields` (before plans, risk flags, metrics, screenshots); the
  obfuscated capture carries labels to the same field (`ScanRunner._with_labels`). Prompt: name/id,
  redacted label, placeholder shape, known input type (else `text`), dictionary autocomplete token.
  Protected: field values/selected options with a value, detector findings in text, every text
  token with a digit or `@`. **Found on the demo site:** a placeholder option ("Choose…") protected
  as a value blocked the call on 3 demo pages (the egress verifier finds "choose" in the fixed
  instructions) → not protected any more (test).
- **Figures:** `llm_label.job_llm_metrics(calls)` → `progress.metrics.llm {calls, raw_values,
  blocked}` for discover jobs (each object + final) and scans (each page, finalize, `RunOutcome.llm`
  persisted for a retried finalize).
- **Tests** (all through the real `LlmGateway.complete()` under strict, faked at `_stellar` only):
  `tests/test_llm_label_l2_l3.py` (13), `tests/scanner/test_crawler_llm_label.py` (3, 2 demo-site
  crawls with D4 counters), `tests/connectors/test_discover_job_llm_label.py` (2, db+pg_source),
  `tests/scanner/test_scan_llm_metrics.py` (2, db). Red before: 13/13 (ImportError), 3/3, and the
  positive job-level tests (checked on `git archive` exports of the previous commits, PYTHONPATH).

## 3. Task 17 S5 — the scanner's SSRF guard, wired
- `POST /scans`: `netguard.check_scan_url` in a thread before `validate_scan` → 422 with the guard's
  code, audited `scan.create` refusal, nothing queued.
- Browser-worker: `scanner.check_start_url` again before any browser starts (after the
  existing-evidence branch) → failed with the code, key purged, no retry.
- **Every hop (beyond the plan):** the scope proxy dials an allowed hop only via
  `ScanGuard._dial_hop`: resolve once (`netguard.resolve_addresses`, event loop), every address
  must pass `forbidden_ip_reason`, connect to those addresses itself (no rebinding window); refusal
  403 counted `host_forbidden` (`unrouted`); unresolvable 502. `ScanGuard(allow_loopback, resolve)`;
  crawler passes `env == "local"`.
- **Chromium:** `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1` (`browser.RESOLVER_RULES`,
  `launch_args()`, used by `get_browser`). **Measured:** the plan's rule (registered hosts excluded)
  fails every scan — `MAP *` covers IP literals, so the proxy at 127.0.0.1 is unreachable
  (`ERR_PROXY_CONNECTION_FAILED`); with the proxy excluded, a proxied context reaches a registered
  name, an unproxied one nothing (`ERR_NAME_NOT_RESOLVED`).
- **Bug fixed:** `forbidden_ip_reason` treated `::1` as "reserved" before "loopback", so `localhost`
  could not be scanned (nor be a connection host, F-08) even in `env=local`.
- **Test adapted (not weakened):** M4's `test_hosts_registered_on_the_admin_page_decide_what_a_tenant_may_scan`
  registered a made-up name that does not resolve; it now resolves through a test resolver.
- **Tests:** `tests/security/test_scan_ssrf.py` (11: proxy dial control, metadata plain/IPv4-mapped/
  split/NAT64 on plain HTTP and CONNECT, loopback outside local, unresolvable and unencodable names
  → 502, real resolver, launch flag, POST /scans (db)), `tests/scanner/test_scan_ssrf_browser.py`
  (production launch; browser-worker re-check (db)), `test_netguard.py` (+5). All red on the
  previous commit (non-db 9+2+1, db 2).

## 4. D4 re-check and live proofs (security review §3.27)
- Browser proofs (no_submit, guard D-C counting servers, crawler, scan_job e2e via `get_browser`
  with the new flag): pass.
- Lane `scanner` stack rebuilt from `fsec` (`docker compose build app browser-worker`, 3m47s):
  `DPS_SMOKE_SCANNER=1 DPS_SMOKE_M2=off tools/smoke_test.py` → **34/34** (2 OCR skipped): 21 pages,
  63 fields (51 personal), `non_get +0, form_get +0, logout_hits +0, admin_hits +0;
  non_get_allowed 0`, blocked POST 60 / GET 1, evidence submissions 0, 3/3 masked captures, export 0
  demo values. Demo counters before/after: all 0 → all 0 (`S3b-impl/d4-received-*.json`).
- **Live T3 scan** (browser-worker env pointed at a fake Stellar/COIN on the host, compose override
  in scratch only; `probes/live_t3_scan.py`, output `S3b-impl/live-t3-scan.out`): 9/9 PASS —
  `progress.metrics.llm {calls 2, blocked 0, raw_values 0}`, 2 fields labelled LOCATION, demo
  counters +0, `non_get_allowed` 0, the 2 prompts the model got hold 0 demo values, metadata keys only.
- **Live S5 probe** (`probes/live_scan_ssrf.py`, `S3b-impl/live-scan-ssrf.out`): 8/8 PASS —
  numeric encodings → 422 host_forbidden; tenant admin registers 169.254.169.254 and its
  IPv4-mapped form → still 422 host_forbidden, value-free; nothing queued; hosts restored.
- Container log scan (app, worker, browser-worker, demo-site) after both scans: 0 demo values.

## 5. Gates
- `ruff format --check dps tests tools`, `ruff check dps tests tools`, `mypy dps` (155 files): clean.
- Full lane suite (dps-db + source-pg up, demo site, Chromium): run 1 (before the S5 test fix):
  3666 passed, 232 skipped, 1 failed (the registered-hosts test above, fixed). **Final run on the
  committed code: 3672 passed, 232 skipped, 0 failed (15:18).**
- Worktree clean after the docs commit; all 5 new commits authored NilenduGanguli, 0 trailers.
- Lane stack stopped (`docker compose --profile scanner --profile sources stop`); the node demo
  site and the fake Stellar are stopped too. Not pushed (autopush not run).

## 6. Open items / for the owner
1. **Egress verifier false positives block T3 (fllm owner's decision).** The verifier looks for
   protected originals in the fixed system instructions too (kept deliberately by `0fd76d0`, pinned
   by `tests/test_llm_egress.py`). Measured on the lane's seeded demo `bank` tables (11 tables, 48
   leftover columns, 500 sampled rows): **5 of 11 tables' labelling calls were stopped**, each by a
   value inside the fixed instructions (e.g. "Personal"; Faker: ≈0.4–0.7 % of first/last names,
   ≈5 % of generic words). Fail closed (no label, `blocked` counted, nothing sent). Suggested fix:
   exempt the registered constant system prompts from the protected scan (label()'s leak guard
   already checks every protected value against the call data first). Probes:
   `S3b-impl/probes/l2_t3_demo_tables.py`, `l2_t3_blocked_why.py` (counts only). Not changed here.
2. **Deploy (fos/OpenShift):** with a scan policy that opts into T3, the browser-worker needs the
   same LLM settings/secrets as the app and egress to the LLM endpoint; the app pod must resolve the
   tenants' registered UAT host names (POST /scans now resolves them). HANDOFF §17.1.
3. Not in this job: S3 review r3 R3-B1/R3-B2 (synthesize event-loop blocking, deny-list trie build
   cost) remain open on `fsec`.

## 7. Files
- Code: `dps/engine/detect/llm_label.py`, `dps/engine/service.py`, `dps/layers/connectors/discovery.py`,
  `dps/layers/scanner/{classify,crawler,runtime,__init__,browser,guard,proxy}.py`,
  `dps/layers/api/routers/scans.py`, `dps/netguard.py`.
- Tests: `tests/test_llm_label_l2_l3.py`, `tests/scanner/test_crawler_llm_label.py`,
  `tests/connectors/test_discover_job_llm_label.py`, `tests/scanner/test_scan_llm_metrics.py`,
  `tests/security/test_scan_ssrf.py`, `tests/scanner/test_scan_ssrf_browser.py`,
  `tests/security/test_netguard.py`, `tests/scanner/test_scans_api.py`.
- Docs: `docs/security/2026-10-11-m5-security-review.md` (§2 S5, F-03, §3.23–§3.27, §5, §6),
  `docs/superpowers/build/M5-DEVIATIONS.md` (Task 8B, Task 17 S5), contract (§5.4, §8.3, §9),
  `HANDOFF.md` (§17.1).
- Probes and outputs: `~/.cache/dps-build/reports/S3b-impl/` (`probes/*.py`, `d4-smoke.log`,
  `d4-received-*.json`, `live-*.out`, `live-t3/prompts.jsonl` — metadata only).
