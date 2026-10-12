-- 003_connections.sql — the connection registry. params never holds credentials (ConnectionParams);
-- secret_enc is the sealed InlineCredentials (AAD "dps_connection.secret:<id>"), nulled by retention
-- after secret_expires_at — hence "at most one" here, "exactly one at create" in the API.
CREATE TABLE __SCHEMA__.dps_connection (
    id                text PRIMARY KEY,
    tenant_id         text NOT NULL,
    name              text NOT NULL,
    engine            text NOT NULL CHECK (engine IN ('postgres','oracle','mongo','s3','pvc')),
    params            jsonb NOT NULL,
    secret_ref        text,
    secret_enc        bytea,
    secret_expires_at timestamptz,
    roles             text[] NOT NULL DEFAULT '{source}'
                      CHECK (cardinality(roles) > 0 AND roles <@ ARRAY['source','target']::text[]),
    tags              text[] NOT NULL DEFAULT '{}',
    capabilities      jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_test         jsonb,
    created_by        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name),
    CONSTRAINT dps_connection_one_secret CHECK (secret_ref IS NULL OR secret_enc IS NULL)
);
CREATE INDEX dps_connection_feed ON __SCHEMA__.dps_connection (tenant_id, created_at DESC, id DESC);
