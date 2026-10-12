-- 002_tenancy_and_keys.sql — per-tenant overrides and the API-key table (M5 uses it).
-- Both tables are GLOBAL (no RLS): caps are read by the cross-tenant claim query (dps/jobs.py) and
-- keys are looked up before a tenant is known. A missing dps_tenant row means "settings defaults";
-- the API inserts an all-NULL row the first time a tenant creates a connection or a job, so the
-- retention job can enumerate tenants. The CHECK admits a leading "_" for DPS pseudo-tenants
-- (_system owns retention job rows, contract §3.6); API callers can never select one (§8.1).
CREATE TABLE __SCHEMA__.dps_tenant (
    tenant_id      text PRIMARY KEY CHECK (tenant_id ~ '^[a-z0-9_][a-z0-9_-]{0,62}$'),
    display_name   text,
    retention_days int CHECK (retention_days BETWEEN 1 AND 3650),
    max_running    int CHECK (max_running > 0),
    max_queued     int CHECK (max_queued > 0),
    note           text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE __SCHEMA__.dps_api_key (
    id           text PRIMARY KEY,
    key_hash     text NOT NULL UNIQUE,
    name         text NOT NULL,
    tenant_ids   text[] NOT NULL DEFAULT '{}',
    scopes       text[] NOT NULL DEFAULT '{}',
    created_by   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,
    last_used_at timestamptz,
    disabled_at  timestamptz,
    rotated_from text
);
