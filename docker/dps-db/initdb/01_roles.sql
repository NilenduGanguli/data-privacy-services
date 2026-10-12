-- 01_roles.sql — local-only roles and the metadata database (contract §2.3).
-- Runs once, as postgres, on an empty data directory (the postgres image's initdb hook; also
-- tools/scratch_pg.sh). Passwords are demo literals equal to the role name; production roles are
-- created by the platform and never use these.
CREATE ROLE dps_owner LOGIN PASSWORD 'dps_owner'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE dps_app_rw NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE dps_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE dps_app LOGIN PASSWORD 'dps_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE dps_worker_login LOGIN PASSWORD 'dps_worker_login'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
-- Disjoint memberships: dps_app is never a member of dps_worker (the worker_claim policy, 008).
GRANT dps_app_rw TO dps_app;
GRANT dps_app_rw, dps_worker TO dps_worker_login;
CREATE DATABASE dps OWNER dps_owner;
ALTER DATABASE dps SET timezone TO 'UTC';
REVOKE ALL ON DATABASE dps FROM PUBLIC;
GRANT CONNECT ON DATABASE dps TO dps_app_rw, dps_worker, dps_owner;
