-- 010_job_idempotency_fp.sql — bind a durable Idempotency-Key to the request that created the job
-- (contract §9, integration of M1 and M2): the same key with another request is refused with 422
-- idempotency_key_reused, as on Layer 1, instead of replaying a different job.
--
-- idempotency_fp is a keyed fingerprint: HMAC-SHA256 under an HKDF subkey of the job KEK
-- (dps.secrets.keyed_fingerprint, info "dps.idempotency_fp", salt = the route template) over the
-- raw request body. A body can carry selection bind values or caller key material, so the
-- fingerprint is never a plain hash: without the KEK it reveals nothing. NULL for jobs admitted
-- without a KEK and for rows from before this migration; those keep the key-only replay.
ALTER TABLE __SCHEMA__.dps_job ADD COLUMN idempotency_fp text;
COMMENT ON COLUMN __SCHEMA__.dps_job.idempotency_fp IS
    'keyed fingerprint (HMAC under a KEK subkey) of the request an Idempotency-Key belongs to';
