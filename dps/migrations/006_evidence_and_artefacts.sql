-- 006_evidence_and_artefacts.sql — artefact rows (objects live in the ObjectStore) and evidence packs.
-- dps_evidence is append-only except its rendering artefact ids (html now, pdf in M5).
CREATE TABLE __SCHEMA__.dps_artefact (
    id           text PRIMARY KEY,
    tenant_id    text NOT NULL,
    job_id       text REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('evidence_html','evidence_pdf','evidence_json','screenshot',
                                               'capture','export_csv','export_json','manifest','redacted_pdf')),
    store        text NOT NULL CHECK (store IN ('local','s3')),
    object_key   text NOT NULL,
    content_type text NOT NULL,
    bytes        bigint NOT NULL,
    sha256       text NOT NULL,
    meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz
);
CREATE INDEX dps_artefact_job ON __SCHEMA__.dps_artefact (job_id);

CREATE TABLE __SCHEMA__.dps_evidence (
    id               text PRIMARY KEY,
    tenant_id        text NOT NULL,
    job_id           text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    pack             jsonb NOT NULL,
    pack_sha256      text NOT NULL,
    pack_version     text NOT NULL DEFAULT 'dps.evidence.v1',
    html_artefact_id text,
    pdf_artefact_id  text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz
);
CREATE INDEX dps_evidence_feed ON __SCHEMA__.dps_evidence (tenant_id, created_at DESC, id DESC);
CREATE INDEX dps_evidence_job ON __SCHEMA__.dps_evidence (job_id);

CREATE OR REPLACE FUNCTION __SCHEMA__.dps_evidence_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.job_id IS DISTINCT FROM OLD.job_id OR NEW.pack IS DISTINCT FROM OLD.pack
       OR NEW.pack_sha256 IS DISTINCT FROM OLD.pack_sha256
       OR NEW.pack_version IS DISTINCT FROM OLD.pack_version
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'dps_evidence is append-only (only artefact ids may change)';
    END IF;
    RETURN NEW;
END$$;
CREATE TRIGGER dps_evidence_append_only BEFORE UPDATE ON __SCHEMA__.dps_evidence
    FOR EACH ROW EXECUTE FUNCTION __SCHEMA__.dps_evidence_guard();
