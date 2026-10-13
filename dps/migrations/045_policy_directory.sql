-- 045 — the tenant directory also lists tenants that only have policies (M5 review G4 B1).
-- dps_tenant_directory() (040) unioned dps_tenant, dps_job and dps_connection, so a tenant with
-- policies but no tenant row, job or connection was invisible to the runtime posture report's
-- leakage_strict check (and to every other directory reader). The function still returns ids only.
-- FORCE RLS applies to the owner, so the owner gets the same narrow SELECT-only policy on dps_policy
-- that 040 gave it on dps_job and dps_connection.
CREATE POLICY owner_directory ON __SCHEMA__.dps_policy FOR SELECT TO CURRENT_USER USING (true);
CREATE OR REPLACE FUNCTION __SCHEMA__.dps_tenant_directory() RETURNS TABLE (tenant_id text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT t.tenant_id FROM __SCHEMA__.dps_tenant t
    UNION SELECT j.tenant_id FROM __SCHEMA__.dps_job j
    UNION SELECT c.tenant_id FROM __SCHEMA__.dps_connection c
    UNION SELECT p.tenant_id FROM __SCHEMA__.dps_policy p
    ORDER BY 1
  $$;
REVOKE ALL ON FUNCTION __SCHEMA__.dps_tenant_directory() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION __SCHEMA__.dps_tenant_directory() TO dps_app_rw;
