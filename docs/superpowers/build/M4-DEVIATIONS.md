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
