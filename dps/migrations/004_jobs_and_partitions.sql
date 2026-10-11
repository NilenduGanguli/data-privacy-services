-- 004_jobs_and_partitions.sql — job groups, jobs, partitions, the worker registry, and two helpers.
-- Additions to the contract DDL (all value-free): dps_job.plan (the RunPlan written at prepare:
-- column plans, resolved target summary), dps_job_partition.meta (files: per-part object keys, rows,
-- sha256), indexes dps_part_job / dps_job_expires / dps_group_feed, and dps_jsonb_add +
-- dps_jsonb_sum for server-side counter maps (values_by_method).
CREATE TABLE __SCHEMA__.dps_job_group (
    id               text PRIMARY KEY,
    tenant_id        text NOT NULL,
    name             text NOT NULL,
    kid              text NOT NULL,
    seed_fingerprint text NOT NULL,
    created_by       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz,
    UNIQUE (tenant_id, name)
);
CREATE INDEX dps_group_feed ON __SCHEMA__.dps_job_group (tenant_id, created_at DESC, id DESC);

CREATE TABLE __SCHEMA__.dps_job (
    id                   text PRIMARY KEY,
    tenant_id            text NOT NULL,
    kind                 text NOT NULL CHECK (kind IN ('discover','run','scan','retention')),
    state                text NOT NULL DEFAULT 'queued' CHECK (state IN
                         ('queued','running','waiting','paused','succeeded','failed','dead','canceled')),
    phase                text NOT NULL CHECK (phase IN
                         ('discover','prepare','partitions','finalize','scan','retention')),
    mode                 text CHECK (mode IN ('obfuscate','synthesize')),
    group_id             text REFERENCES __SCHEMA__.dps_job_group(id),
    connection_id        text,
    target_connection_id text,
    object_ref           text,
    snapshot_id          text,
    spec                 jsonb NOT NULL,
    purpose              jsonb NOT NULL DEFAULT '{}'::jsonb,
    key_enc              bytea,
    spec_enc             bytea,
    kid                  text,
    progress             jsonb NOT NULL DEFAULT '{}'::jsonb,
    plan                 jsonb,
    priority             smallint NOT NULL DEFAULT 100,
    attempts             int NOT NULL DEFAULT 0,
    max_attempts         int NOT NULL DEFAULT 3,
    run_after            timestamptz NOT NULL DEFAULT now(),
    lease_expires_at     timestamptz,
    locked_by            text,
    error_code           text,
    error                text,
    idempotency_key      text,
    created_by           text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    finished_at          timestamptz,
    expires_at           timestamptz
) WITH (fillfactor = 70, autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);
CREATE UNIQUE INDEX dps_job_idem ON __SCHEMA__.dps_job (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX dps_job_claim ON __SCHEMA__.dps_job (kind, priority, run_after, created_at, id)
    WHERE state = 'queued';
CREATE INDEX dps_job_lease ON __SCHEMA__.dps_job (lease_expires_at) WHERE state = 'running';
CREATE INDEX dps_job_feed ON __SCHEMA__.dps_job (tenant_id, created_at DESC, id DESC);
CREATE INDEX dps_job_expires ON __SCHEMA__.dps_job (expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE __SCHEMA__.dps_job_partition (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id           text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    tenant_id        text NOT NULL,
    partition_no     int NOT NULL,
    lower_key        jsonb,
    upper_key        jsonb,
    state            text NOT NULL DEFAULT 'queued'
                     CHECK (state IN ('queued','running','done','failed','dead','canceled')),
    attempts         int NOT NULL DEFAULT 0,
    max_attempts     int NOT NULL DEFAULT 5,
    run_after        timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz,
    locked_by        text,
    last_key         jsonb,
    batch_no         int NOT NULL DEFAULT 0,
    rows_read        bigint NOT NULL DEFAULT 0,
    rows_written     bigint NOT NULL DEFAULT 0,
    rows_skipped     bigint NOT NULL DEFAULT 0,
    values_by_method jsonb NOT NULL DEFAULT '{}'::jsonb,
    verify_checked   bigint NOT NULL DEFAULT 0,
    resumed          int NOT NULL DEFAULT 0,
    meta             jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code       text,
    error            text,
    started_at       timestamptz,
    finished_at      timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, partition_no)
) WITH (fillfactor = 70, autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);
CREATE INDEX dps_part_claim ON __SCHEMA__.dps_job_partition (run_after, job_id, partition_no)
    WHERE state = 'queued';
CREATE INDEX dps_part_lease ON __SCHEMA__.dps_job_partition (lease_expires_at) WHERE state = 'running';
CREATE INDEX dps_part_job ON __SCHEMA__.dps_job_partition (job_id, state);

CREATE TABLE __SCHEMA__.dps_worker (
    worker_id  text PRIMARY KEY,
    role       text NOT NULL CHECK (role IN ('worker','browser-worker')),
    version    text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),
    draining   boolean NOT NULL DEFAULT false,
    busy       int NOT NULL DEFAULT 0
);

-- Sum two {"name": number} maps key by key (missing keys count as 0).
CREATE OR REPLACE FUNCTION __SCHEMA__.dps_jsonb_add(a jsonb, b jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
    SELECT COALESCE(jsonb_object_agg(k, COALESCE((a ->> k)::numeric, 0) + COALESCE((b ->> k)::numeric, 0)),
                    '{}'::jsonb)
    FROM (SELECT jsonb_object_keys(COALESCE(a, '{}'::jsonb)) AS k
          UNION SELECT jsonb_object_keys(COALESCE(b, '{}'::jsonb))) AS keys
$$;
CREATE OR REPLACE AGGREGATE __SCHEMA__.dps_jsonb_sum(jsonb) (
    SFUNC = __SCHEMA__.dps_jsonb_add, STYPE = jsonb, INITCOND = '{}'
);
