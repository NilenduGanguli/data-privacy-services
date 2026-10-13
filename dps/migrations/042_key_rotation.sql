-- 042 — DB-backed API keys (M5, contract §8.3). dps_api_key is GLOBAL (no RLS): a key is looked up
-- before any tenant is known. Only the sha256 of a key and its first 8 characters are stored.
ALTER TABLE __SCHEMA__.dps_api_key
  ADD COLUMN key_prefix text NOT NULL DEFAULT '' CHECK (key_prefix ~ '^(dps_[A-Za-z0-9_-]{0,4})?$'),
  ADD COLUMN disabled_by text,
  ADD COLUMN rotated_at timestamptz;
ALTER TABLE __SCHEMA__.dps_api_key
  ADD CONSTRAINT dps_api_key_name_ck CHECK (name ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  ADD CONSTRAINT dps_api_key_hash_ck CHECK (key_hash ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT dps_api_key_scopes_ck CHECK (
    scopes <@ ARRAY['detect','obfuscate','synthesize','reveal','jobs','scan','admin','*']::text[]),
  ADD CONSTRAINT dps_api_key_tenants_ck CHECK (cardinality(tenant_ids) >= 1);
-- one successor per key: a concurrent second rotation fails instead of minting two live keys
CREATE UNIQUE INDEX dps_api_key_one_successor ON __SCHEMA__.dps_api_key (rotated_from)
  WHERE rotated_from IS NOT NULL;
CREATE INDEX dps_api_key_active ON __SCHEMA__.dps_api_key (expires_at) WHERE disabled_at IS NULL;
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_api_key TO dps_app_rw;
REVOKE DELETE ON __SCHEMA__.dps_api_key FROM dps_app_rw;   -- keys are disabled, never deleted (audit)
