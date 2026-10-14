-- 046 — a job group binds its DEK, not only its kid (S3 security review F-13).
-- The key check value is public (HKDF of the DEK, salted by the kid): it reveals nothing about the
-- DEK but lets DPS refuse a run that carries another DEK under the group's (public) kid. Groups
-- created before 046 keep NULL and the kid-only rule.
ALTER TABLE __SCHEMA__.dps_job_group ADD COLUMN IF NOT EXISTS kcv text;
