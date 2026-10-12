-- 005_snapshots.sql — classification snapshots (contract §5.4). An accepted snapshot is immutable;
-- accepting another for the same (connection, object) supersedes it (dps/snapshots.py).
CREATE TABLE __SCHEMA__.dps_snapshot (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL,
    job_id        text REFERENCES __SCHEMA__.dps_job(id) ON DELETE SET NULL,
    connection_id text NOT NULL,
    engine        text NOT NULL,
    object_ref    text NOT NULL,
    status        text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','superseded')),
    policy_name   text NOT NULL,
    policy_hash   text NOT NULL,
    shape         jsonb NOT NULL,
    columns       jsonb NOT NULL,
    sample_rows   int NOT NULL,
    overrides     jsonb NOT NULL DEFAULT '[]'::jsonb,
    content_hash  text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    accepted_at   timestamptz,
    accepted_by   text
);
CREATE UNIQUE INDEX dps_snapshot_one_accepted ON __SCHEMA__.dps_snapshot (tenant_id, connection_id, object_ref)
    WHERE status = 'accepted';
CREATE INDEX dps_snapshot_feed ON __SCHEMA__.dps_snapshot (tenant_id, connection_id, object_ref, created_at DESC);
