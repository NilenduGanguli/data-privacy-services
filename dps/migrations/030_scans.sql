-- 030_scans.sql — UI scanner (M4). Value-free by design: no page value, raw URL, raw label or storage
-- state is ever written here. fill_* columns hold synthetic persona values or tokens of persona
-- values only. Every table is a tenant table (RLS ENABLE + FORCE + tenant_isolation, listed in
-- dps/db.py TENANT_TABLES) and is deleted with its job (ON DELETE CASCADE).

CREATE TABLE __SCHEMA__.dps_scan_page (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id            text NOT NULL,
    job_id               text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    page_no              int NOT NULL CHECK (page_no >= 1),
    url_redacted         text NOT NULL,          -- query values -> <ENTITY>|<v>; ids -> {id}; fragment dropped
    title_redacted       text,
    depth                int NOT NULL CHECK (depth >= 0),
    parent_page_no       int,
    status               text NOT NULL CHECK (status IN ('queued','visiting','done','redirected','skipped','failed')),
    form_count           int NOT NULL DEFAULT 0,
    field_count          int NOT NULL DEFAULT 0,
    artefacts            jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {original, annotated, filled_synthesize, filled_obfuscate, masked_capture, capture_json}: artefact ids
    rendered_findings    jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {entity_type: count}
    risk_flags           jsonb NOT NULL DEFAULT '[]'::jsonb,  -- list[RiskFlag]
    validation           jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {synthesize: {checked, accepted, rejected}, obfuscate: {...}}
    created_at           timestamptz NOT NULL DEFAULT now(),
    -- M4 additions (contract §2.4 "may add")
    status_reason        text,   -- deny|other_origin|unregistered_host|not_allowed|depth|template|budget|auth_redirect|redirect|http_4xx|http_5xx|timeout|blocked|host|leakage|error
    discovered_via       text NOT NULL DEFAULT 'link' CHECK (discovered_via IN ('start','link','spa_click','redirect')),
    template             text NOT NULL,
    http_status          int,
    final_url_redacted   text,
    forms                jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{form_index, method, action_redacted, field_count, frame_index, in_shadow}]
    personal_field_count int NOT NULL DEFAULT 0,
    critical_field_count int NOT NULL DEFAULT 0,
    capture_status       text CHECK (capture_status IN ('captured','withheld','not_needed')),
    timings              jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {load_ms, extract_ms, fill_ms, capture_ms}
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, page_no)
);

CREATE TABLE __SCHEMA__.dps_scan_field (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id           text NOT NULL,
    job_id              text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    page_no             int NOT NULL,
    form_index          int NOT NULL,            -- 0 = outside any <form>
    field_no            int NOT NULL,            -- overlay number on the page's screenshots
    selector            text NOT NULL,
    tag                 text NOT NULL,
    input_type          text,
    name_attr           text,
    id_attr             text,
    label               text,                    -- detector-redacted (<ENTITY>), <= 120 chars
    autocomplete        text,
    pattern             text,
    maxlength           int,
    required            boolean NOT NULL DEFAULT false,
    options_count       int,
    entity_type         text,
    sensitivity         text,
    confidence          real,
    detectors           text[] NOT NULL DEFAULT '{}',
    evidence            jsonb NOT NULL DEFAULT '[]'::jsonb,
    fill_synthesize     text,                    -- display persona's value (never page data)
    fill_obfuscate      text,                    -- token of the display persona's value (never page data)
    validation_messages jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- M4 additions
    label_source        text NOT NULL DEFAULT 'none',
    frame_index         int NOT NULL DEFAULT 0,
    in_shadow           boolean NOT NULL DEFAULT false,
    placeholder_shape   text,                    -- digits->9, letters->x/X, <= 24 chars (contract §5.4 shapes)
    prefilled           boolean NOT NULL DEFAULT false,  -- the page arrived with a value (never stored)
    persona_attr        text,
    obfuscate_method    text,
    fill_format         text,
    fill_status         text NOT NULL DEFAULT 'not_filled' CHECK (fill_status IN
        ('filled','neutral','not_filled','skipped_file','skipped_hidden','skipped_readonly','failed')),
    risk_codes          text[] NOT NULL DEFAULT '{}',
    UNIQUE (job_id, page_no, form_index, field_no)
);

CREATE TABLE __SCHEMA__.dps_scan_request (       -- interception log: counts per page/method/decision
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     text NOT NULL,
    job_id        text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,  -- M4 adds the FK
    page_no       int,
    method        text NOT NULL,
    resource_type text,
    decision      text NOT NULL CHECK (decision IN ('allowed','blocked')),
    reason        text NOT NULL,
    host          text NOT NULL,
    count         int NOT NULL DEFAULT 1 CHECK (count >= 1)  -- a 0 or negative row would cancel others
);
CREATE UNIQUE INDEX dps_scan_request_key ON __SCHEMA__.dps_scan_request
    (job_id, (COALESCE(page_no, -1)), method, (COALESCE(resource_type, '')), decision, reason, host);

CREATE TABLE __SCHEMA__.dps_scan_event (         -- navigation log for the console (mockup 12), value-free
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       text NOT NULL,
    job_id          text NOT NULL REFERENCES __SCHEMA__.dps_job(id) ON DELETE CASCADE,
    seq             int NOT NULL,
    ts              timestamptz NOT NULL DEFAULT now(),
    kind            text NOT NULL CHECK (kind IN ('navigate','request','fill','capture','skip','spa','popup','dialog','error')),
    method          text,
    result          text NOT NULL,           -- '200' | '302' | 'blocked' | 'skipped' | 'filled' | 'captured' | 'withheld' | 'failed' | 'closed' | 'dismissed'
    page_no         int,
    target_redacted text NOT NULL,           -- redact_url / redact_origin output, or '' for page-level events
    note_code       text NOT NULL,           -- e.g. 'method', 'filled_navigation', 'deny', 'other_origin', 'persona'
    counts          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- e.g. {"fields": 9, "rejected": 0, "persona": 1}
    UNIQUE (job_id, seq)
);

ALTER TABLE __SCHEMA__.dps_scan_page    SET (fillfactor = 90);
ALTER TABLE __SCHEMA__.dps_scan_page    ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_page    FORCE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_field   ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_field   FORCE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_request FORCE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_event   ENABLE ROW LEVEL SECURITY;
ALTER TABLE __SCHEMA__.dps_scan_event   FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_scan_page
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_scan_field
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_scan_request
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY tenant_isolation ON __SCHEMA__.dps_scan_event
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- Explicit grants (never ALL TABLES); the navigation log is append-only for the runtime roles.
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_scan_page    TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_scan_field   TO dps_app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON __SCHEMA__.dps_scan_request TO dps_app_rw;
GRANT SELECT, INSERT, DELETE         ON __SCHEMA__.dps_scan_event   TO dps_app_rw;
DO $$
DECLARE
    s text;
BEGIN
    FOREACH s IN ARRAY ARRAY[pg_get_serial_sequence('__SCHEMA__.dps_scan_page', 'id'),
                             pg_get_serial_sequence('__SCHEMA__.dps_scan_field', 'id'),
                             pg_get_serial_sequence('__SCHEMA__.dps_scan_request', 'id'),
                             pg_get_serial_sequence('__SCHEMA__.dps_scan_event', 'id')] LOOP
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO dps_app_rw', s);
    END LOOP;
END$$;
