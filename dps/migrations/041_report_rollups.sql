-- 041 — daily, value-free aggregates behind the Overview and Reports pages (M5).
-- Rows hold an action name, a mode, an outcome, an entity type and numbers — never a value.
CREATE TABLE __SCHEMA__.dps_report_daily (
  tenant_id text NOT NULL,
  day date NOT NULL,                                          -- UTC
  layer text NOT NULL CHECK (layer IN ('api', 'conn', 'ui', 'admin')),
  action text NOT NULL,                                       -- audit action, or job.<kind> / job.partition
  mode text NOT NULL DEFAULT '-',
  outcome text NOT NULL,                                      -- audit outcome or job/partition state
  calls bigint NOT NULL DEFAULT 0,
  findings bigint NOT NULL DEFAULT 0,
  values_transformed bigint NOT NULL DEFAULT 0,
  verify_passed bigint NOT NULL DEFAULT 0,
  verify_failed bigint NOT NULL DEFAULT 0,
  rows_written bigint NOT NULL DEFAULT 0,
  pages bigint NOT NULL DEFAULT 0,
  fields bigint NOT NULL DEFAULT 0,
  blocked_requests bigint NOT NULL DEFAULT 0,
  refreshed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day, layer, action, mode, outcome));
CREATE TABLE __SCHEMA__.dps_report_entity_daily (
  tenant_id text NOT NULL, day date NOT NULL,
  layer text NOT NULL CHECK (layer IN ('api', 'conn', 'ui')),
  entity_type text NOT NULL CHECK (entity_type ~ '^[A-Z][A-Z0-9_]{0,63}$'),
  findings bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, day, layer, entity_type));
CREATE TABLE __SCHEMA__.dps_report_refresh (                 -- which days were recomputed (also empty ones)
  tenant_id text NOT NULL, day date NOT NULL, refreshed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, day));
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['dps_report_daily', 'dps_report_entity_daily', 'dps_report_refresh'] LOOP
    EXECUTE format('ALTER TABLE %s.%I ENABLE ROW LEVEL SECURITY', '__SCHEMA__', t);
    EXECUTE format('ALTER TABLE %s.%I FORCE ROW LEVEL SECURITY', '__SCHEMA__', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s.%I '
                   'USING (tenant_id = current_setting(''app.tenant_id'', true)) '
                   'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', '__SCHEMA__', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s.%I TO dps_app_rw', '__SCHEMA__', t);
  END LOOP;
END $$;
-- Roll-ups scan one tenant's day of audit rows through 007's dps_audit_tenant_ts (tenant_id, ts DESC).
