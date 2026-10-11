-- 001_schema_and_roles.sql — schema hygiene and the two NOLOGIN group roles (belt-and-braces).
-- Runs as dps_owner. docker/dps-db/initdb/01_roles.sql (local) or the platform (prod) creates every
-- role first; this block only guarantees the groups exist so a bare database still reaches the 009
-- grant shape. If the groups are missing and dps_owner cannot CREATEROLE, it fails loudly — correct.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dps_app_rw') THEN
        CREATE ROLE dps_app_rw NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dps_worker') THEN
        CREATE ROLE dps_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END$$;
REVOKE ALL ON SCHEMA __SCHEMA__ FROM PUBLIC;
GRANT USAGE ON SCHEMA __SCHEMA__ TO dps_app_rw, dps_worker;
