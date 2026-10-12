# M4 build — deviations from the plan

Plan: `docs/superpowers/plans/2026-10-11-m4-ui-scanner.md`. Contract:
`docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (incl. the 2026-10-12 lane-safe amendment).
One section per task: file, what changed, why. Contract names are preserved unless a section says
otherwise. Built in the `m4` worktree (lane ports from `.env`/`.env.lane`; the plan's
`/Users/neelu/document_intelligence/data-privacy-services` path and literal ports are superseded by
the lane rules).

## Task 1 — Scanner extra, settings, posture, logging allow-list and metrics

- `dps/config.py`: `browser_metrics_port` is bounded `0..65535` instead of the plan's blanket
  "every other int `ge=1`". Why: `0` disables an ops server, exactly as M2's `worker_metrics_port`
  (contract §10 "`worker_metrics_port = 0` disables the worker's ops server"); tests of the
  browser-worker process need the same switch. The contract §10 row records the bounds.
- `dps/observability.py`: `job_id`, `pages`, `fields`, `phase`, `kind` and `elapsed_s` were already
  allow-listed by M1/M2; only `page_no depth decision resource_type blocked step persona` are new.
  The four `dps_scan_*` counters sit after the M2 counters (the plan says "next to
  `VERIFY_FAIL`"; placement only).
- `pyproject.toml`: `scanner = ["playwright==1.55.0", "pillow>=10.4"]` as planned; the contract's
  extras paragraph (§1) now lists `pillow>=10.4` for `scanner` too (it named Playwright only).
- `HANDOFF.md`: §4 gains an "M4 settings" table and §6 the two M4 posture checks. Why: contract
  §0.6 (HANDOFF changes in the same commit as any config change); the plan leaves docs to Task 25.
- `tests/test_config_m4.py`: the plan's 7 cases plus the M4-addition defaults, more bound cases
  (`browser_concurrency` 17, `scan_max_pages` 0, `scan_max_depth` -1, `scan_page_timeout_seconds`
  0, `scan_settle_ms` -1), a clean-prod case (no M4 posture message without the two settings) and
  the four metrics' label names: 14 tests (plan: 7).

## Task 2 — URL toolkit

- `dps/layers/scanner/urls.py`: the plan's code, plus:
  - `redact_url` also turns a query **key** the classifier recognises into `<k>` (the plan only
    checked the key's characters, so `?ananya.k%40x.in=1` would have stored a customer's e-mail
    as a key). Test `test_redaction_never_keeps_a_query_key_that_is_itself_a_value`.
  - A malformed port (`http://h:99999/x`) never raises from `template`, `redact_url` or
    `redact_origin` (urllib's `.port` raises `ValueError` there; a guard that redacts arbitrary
    request URLs must not crash on one); `canonical` returns `None` and `Origin.of` raises
    `UrlError("url_invalid")` as before. Test `test_a_malformed_port_is_never_echoed_and_never_raises`.
  - `Origin.text()` (`scheme://host[:port]`, default port left out) for the redacted spec's
    `url_origin` (Task 4). Additive.
  - Docstrings on the public functions; the plan's `_segments` helper is inlined.
- `tests/scanner/test_urls.py`: the plan's 5 tests (lines wrapped to 100 characters, one extra
  `scheme` verdict assertion) plus the two above and `test_url_errors_carry_the_code_only`: 8
  tests (plan: 5).
