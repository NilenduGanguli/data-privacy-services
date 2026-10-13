-- 040 — tenant quotas, daily usage, and the tenant directory (M5).
-- dps_tenant is GLOBAL (contract §2.4: configuration only, no RLS, no policies): the claim CTE (worker pool,
-- no GUC) reads the quota column directly, and this file creates no policy on dps_tenant.
ALTER TABLE __SCHEMA__.dps_tenant
  ADD COLUMN daily_row_quota bigint CHECK (daily_row_quota IS NULL OR daily_row_quota >= 0);
-- registered_hosts is M4's column (031_tenant_hosts.sql, which runs first once the lanes are merged);
-- the M5 admin API edits it, so a lane without 031 gets the same column here. IF NOT EXISTS keeps the
-- merged order (031 then 040) a no-op.
ALTER TABLE __SCHEMA__.dps_tenant
  ADD COLUMN IF NOT EXISTS registered_hosts text[] NOT NULL DEFAULT '{}';

CREATE TABLE __SCHEMA__.dps_tenant_usage (
  tenant_id text NOT NULL,
  day date NOT NULL,                              -- UTC day
  rows_written bigint NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day));
ALTER TABLE __SCHEMA__.dps_tenant_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_tenant_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_tenant_usage
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
-- the worker counts rows inside checkpoint() and reads usage in the claim CTE (no GUC bound there)
CREATE POLICY worker_usage ON __SCHEMA__.dps_tenant_usage TO dps_worker USING (true) WITH CHECK (true);
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_tenant_usage TO dps_app_rw;

-- Tenant directory: the only cross-tenant read of TENANT tables (RLS) open to the app role; it returns
-- ids alone. The function runs as its owner (the migration role, CURRENT_USER here); FORCE RLS still
-- applies to the owner, so the owner gets narrow SELECT-only policies on exactly the two tenant tables
-- the function reads. dps_tenant needs none: it is global and the app role already reads it.
CREATE POLICY owner_directory ON __SCHEMA__.dps_job FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY owner_directory ON __SCHEMA__.dps_connection FOR SELECT TO CURRENT_USER USING (true);
CREATE FUNCTION __SCHEMA__.dps_tenant_directory() RETURNS TABLE (tenant_id text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT t.tenant_id FROM __SCHEMA__.dps_tenant t
    UNION SELECT j.tenant_id FROM __SCHEMA__.dps_job j
    UNION SELECT c.tenant_id FROM __SCHEMA__.dps_connection c
    ORDER BY 1
  $$;
REVOKE ALL ON FUNCTION __SCHEMA__.dps_tenant_directory() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION __SCHEMA__.dps_tenant_directory() TO dps_app_rw;
