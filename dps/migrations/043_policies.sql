-- 043 — tenant policies with immutable published versions (M5 Task 15, mockup 05).
-- A policy is configuration, never customer data. Versions are a governance record: the app role
-- can insert them but never update or delete them, and a trigger refuses both for every role.
-- Policies and versions are never deleted; retention does not touch them.
CREATE TABLE __SCHEMA__.dps_policy (
  tenant_id text NOT NULL,
  name text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9-]{1,62}$'
                            AND name NOT IN ('presets', 'validate', 'plan', 'test-recognizer')),
  base_preset text,
  active_version int,
  draft jsonb,
  draft_by text,
  draft_updated_at timestamptz,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, name));
CREATE TABLE __SCHEMA__.dps_policy_version (
  tenant_id text NOT NULL,
  name text NOT NULL,
  version int NOT NULL CHECK (version >= 1),
  body jsonb NOT NULL,                                      -- the resolved Policy (configuration)
  policy_hash text NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
  change_ticket text CHECK (change_ticket IS NULL OR length(change_ticket) BETWEEN 3 AND 64),
  note text CHECK (note IS NULL OR length(note) <= 1000),
  published_by text,
  published_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, name, version),
  FOREIGN KEY (tenant_id, name) REFERENCES __SCHEMA__.dps_policy (tenant_id, name) ON DELETE RESTRICT);
ALTER TABLE __SCHEMA__.dps_policy ADD CONSTRAINT dps_policy_active_fk
  FOREIGN KEY (tenant_id, name, active_version)
  REFERENCES __SCHEMA__.dps_policy_version (tenant_id, name, version)
  DEFERRABLE INITIALLY DEFERRED;
CREATE FUNCTION __SCHEMA__.dps_policy_version_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'dps_policy_version is immutable';
END $$;
CREATE TRIGGER dps_policy_version_immutable BEFORE UPDATE OR DELETE ON __SCHEMA__.dps_policy_version
  FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.dps_policy_version_immutable();
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['dps_policy', 'dps_policy_version'] LOOP
    EXECUTE format('ALTER TABLE %s.%I ENABLE ROW LEVEL SECURITY', '__SCHEMA__', t);
    EXECUTE format('ALTER TABLE %s.%I FORCE ROW LEVEL SECURITY', '__SCHEMA__', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s.%I '
                   'USING (tenant_id = current_setting(''app.tenant_id'', true)) '
                   'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', '__SCHEMA__', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON __SCHEMA__.dps_policy TO dps_app_rw;
GRANT SELECT, INSERT ON __SCHEMA__.dps_policy_version TO dps_app_rw;
