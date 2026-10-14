-- 031_tenant_hosts.sql — hosts a tenant may scan (M4 reads, M5 admin edits). Lower-case host names,
-- no scheme or port. dps_tenant stays GLOBAL (no RLS, contract §2.4): host names are configuration,
-- never customer data. An empty list means "no registered hosts" (local/dev fall back to
-- DPS_SCAN_REGISTERED_HOSTS; production scans nothing).
-- IF NOT EXISTS: main's databases applied M5's 040 (which adds the same column IF NOT EXISTS) before
-- this file shipped; the runner applies pending files out of number order.
ALTER TABLE __SCHEMA__.dps_tenant ADD COLUMN IF NOT EXISTS registered_hosts text[] NOT NULL DEFAULT '{}';
