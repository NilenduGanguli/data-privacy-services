-- 044 — per-tenant LLM figures in the daily roll-ups (M5 Task 16B, spec P1/§10). Counts only:
-- calls sent to the LLM gateway, raw sensitive values found in a sent prompt (must stay 0; only a
-- local warn-mode deployment can send one) and prompts the egress verifier blocked.
ALTER TABLE __SCHEMA__.dps_report_daily
  ADD COLUMN llm_calls bigint NOT NULL DEFAULT 0,
  ADD COLUMN llm_raw_values bigint NOT NULL DEFAULT 0,
  ADD COLUMN llm_blocked bigint NOT NULL DEFAULT 0;
