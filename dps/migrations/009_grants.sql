-- 009_grants.sql — explicit per-table grants to the group roles; nothing to PUBLIC, never
-- "ALL TABLES IN SCHEMA" (that would reach audit partitions directly and bypass the parent's RLS).
-- dps_migration_ledger is created by the runner before 001, so the grant below always finds it.
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_tenant TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_api_key TO dps_app_rw;
GRANT SELECT ON __SCHEMA__.dps_migration_ledger TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    __SCHEMA__.dps_connection, __SCHEMA__.dps_job_group, __SCHEMA__.dps_job,
    __SCHEMA__.dps_job_partition, __SCHEMA__.dps_snapshot, __SCHEMA__.dps_artefact
TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_evidence TO dps_app_rw;       -- UPDATE: artefact ids only (trigger)
GRANT SELECT, INSERT ON __SCHEMA__.dps_audit_log TO dps_app_rw;
GRANT SELECT ON __SCHEMA__.dps_worker TO dps_app_rw;                         -- /jobs/stats
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_worker TO dps_worker; -- heartbeats, pruning
GRANT EXECUTE ON FUNCTION __SCHEMA__.dps_jsonb_add(jsonb, jsonb) TO dps_app_rw, dps_worker;
GRANT EXECUTE ON FUNCTION __SCHEMA__.dps_jsonb_sum(jsonb) TO dps_app_rw, dps_worker;
DO $$
DECLARE
    s text;
BEGIN
    FOREACH s IN ARRAY ARRAY[pg_get_serial_sequence('__SCHEMA__.dps_job_partition', 'id'),
                             pg_get_serial_sequence('__SCHEMA__.dps_audit_log', 'id')] LOOP
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO dps_app_rw', s);
    END LOOP;
END$$;
