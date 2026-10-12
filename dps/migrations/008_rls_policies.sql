-- 008_rls_policies.sql — FORCE row-level security on every tenant table (contract §2.4), plus the
-- role-targeted worker_claim policy on the two queue tables. Permissive policies OR-combine, so
-- worker_claim widens visibility only for members of dps_worker (dps_worker_login), never dps_app.
-- ('__SCHEMA__' inside the string literals becomes '"dps"'; format('%s', …) inserts it verbatim.)
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['dps_connection','dps_job_group','dps_job','dps_job_partition',
                             'dps_snapshot','dps_artefact','dps_evidence','dps_audit_log'] LOOP
        EXECUTE format('ALTER TABLE %s.%I ENABLE ROW LEVEL SECURITY', '__SCHEMA__', t);
        EXECUTE format('ALTER TABLE %s.%I FORCE ROW LEVEL SECURITY', '__SCHEMA__', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s.%I', '__SCHEMA__', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %s.%I '
                       'USING (tenant_id = current_setting(''app.tenant_id'', true)) '
                       'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', '__SCHEMA__', t);
    END LOOP;
END$$;
DROP POLICY IF EXISTS worker_claim ON __SCHEMA__.dps_job;
CREATE POLICY worker_claim ON __SCHEMA__.dps_job TO dps_worker USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS worker_claim ON __SCHEMA__.dps_job_partition;
CREATE POLICY worker_claim ON __SCHEMA__.dps_job_partition TO dps_worker USING (true) WITH CHECK (true);
