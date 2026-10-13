-- 020_job_group_last_check.sql (M3) — the latest cross-system consistency check per job group
-- (contract §6.5, §9 POST /job-groups/{id}/consistency-check). Value-free by construction:
-- ConsistencyCheckResult carries ids, object names, paths, entity types, method names, a params
-- hash, join forms and counts — never a customer value, token, prefix text or key.
ALTER TABLE __SCHEMA__.dps_job_group ADD COLUMN IF NOT EXISTS last_check jsonb;
ALTER TABLE __SCHEMA__.dps_job_group ADD COLUMN IF NOT EXISTS last_check_at timestamptz;
COMMENT ON COLUMN __SCHEMA__.dps_job_group.last_check IS
    'ConsistencyCheckResult (counts, codes, ids, paths) - never a customer value, token or key';
COMMENT ON COLUMN __SCHEMA__.dps_job_group.last_check_at IS
    'when the stored consistency check ran';
