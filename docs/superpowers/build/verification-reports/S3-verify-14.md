# S3-verify-14 — custom entity names as unbounded /metrics labels + global taxonomy growth

Branch fsec @ 64c46e3, worktree /Users/neelu/document_intelligence/dps-wt-fsec. No repo files modified.
Probes: /Users/neelu/.cache/dps-build/reports/S3-verify-14/ (p07, p09 copied from S3-secrets-logs; p07b/p09b prod-posture variants).

Verdict: REAL (could not refute).

Code facts
- dps/engine/service.py:307 `FINDINGS.labels(f.entity_type, ...)` — entity_type is the caller's `supported_entity`
  (only validated by `_check_entity_name` for shape, no allow-list); batch.py:898 does the same in jobs.
- dps/engine/detect/custom.py:162 -> taxonomy.register_entity writes into process-wide TAXONOMY when
  `sensitivity` is set; nothing ever removes entries.
- DetectConfig.custom_recognizers (policy.py:102) is an uncapped list; RecognizerSpec has no length caps.
- /metrics (dps/layers/api/routers/ops.py:26) has no auth dependency; docs/technical/api-m0.md:37 documents it as keyless.
  No production-posture rule (posture.py) gates /metrics or inline custom recognizers; no deploy manifests/NetworkPolicy on this branch.

Reproduction (counts/booleans only)
- p07 env=dev, 10 req x 1000 recognizers, detect-only key: statuses {200:10}; anon /metrics 200, 1,566,481 bytes;
  dps_findings_total series 0 -> 10000; taxonomy 58 -> 10058; max RSS 194 -> 211 MB.
- p09 env=dev: tenant A /detect 200; tenant B GET /entities lists name: False; anon /metrics lists it: True.
- p09b env=prod (posture-compliant: no stub LLM, egress TLS required, verify_warn_allowed false): same — anon /metrics 200, lists it: True.
- p07b env=prod, 3 x 500: statuses {200:3}; series 0 -> 1500; taxonomy 58 -> 1558; anon /metrics 234,418 bytes.

Side note: the audit/log line `findings_by_type` also carries every custom type name (1000 keys per line) — log-volume amplification of the same input.

Caveat on severity: anonymous /metrics is a documented design choice and is usually network-restricted in real deployments,
which limits (b); (a) unbounded series/taxonomy growth from any detect-scope key holds regardless of network placement.
