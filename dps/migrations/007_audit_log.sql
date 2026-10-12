-- 007_audit_log.sql — append-only audit log, monthly RANGE partitions created by
-- dps.db.ensure_audit_partitions (owner role, inside the migration advisory lock) and dropped only
-- by `python -m dps.migrate --maintain`. No DEFAULT partition: a missing month fails the writer's
-- batch loudly (readiness "audit") instead of silently landing in a partition that later blocks
-- CREATE TABLE … PARTITION OF.
CREATE TABLE __SCHEMA__.dps_audit_log (
    id        bigint GENERATED ALWAYS AS IDENTITY,
    ts        timestamptz NOT NULL DEFAULT now(),
    tenant_id text NOT NULL,
    key_id    text,
    trace_id  text,
    route     text NOT NULL,
    action    text NOT NULL,
    mode      text,
    policy    text,
    outcome   text NOT NULL,
    status    smallint,
    job_id    text,
    counts    jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (ts, id)
) PARTITION BY RANGE (ts);
CREATE INDEX dps_audit_tenant_ts ON __SCHEMA__.dps_audit_log (tenant_id, ts DESC);

CREATE OR REPLACE FUNCTION __SCHEMA__.dps_audit_log_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'dps_audit_log is append-only';
END$$;
CREATE TRIGGER dps_audit_log_append_only BEFORE UPDATE OR DELETE ON __SCHEMA__.dps_audit_log
    FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.dps_audit_log_guard();
