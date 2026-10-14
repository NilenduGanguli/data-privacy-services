/** A UI scan's evidence pack as dps/layers/scanner/inventory.py::build_scan_evidence builds it
 *  (job_kind "scan", a `scan` block, no columns, partitions or row counts) — test data only. */
import type { EvidencePack } from '../lib/types';

export const SCAN_PACK: EvidencePack = {
  pack_version: 'dps.evidence.v1', id: 'ev_01SCANPACK', tenant_id: 'default', job_id: 's_01SCANJOB', job_kind: 'scan', group_id: null,
  outcome: 'succeeded', mode: 'synthesize', label: 'synthetic — measured, not claimed anonymous', created_at: '2026-10-13T18:41:21+00:00',
  engine_version: '0.2.0', image_digest: null, purpose: null, requested_by: 'static-abc',
  source: { kind: 'ui', url: 'http://demo-site:8410/apply', origin: 'http://demo-site:8410', auth: 'storage_state_ref',
    scope: { same_origin: true, max_pages: 50, max_depth: 3, allow: [], deny: ['/logout', '/admin'], iframes: true, shadow_dom: true, spa_clicks: true } },
  target: { kind: 'artefacts', store: 'local', exports: { csv: 'art_csv', json: 'art_json' } },
  policy: { name: 'gdpr-pseudonymise', hash: 'e98f' }, snapshot: {},
  consistency: { kid: 'k_2d0f', scope: 'key', field_scoped: false, seed_fingerprint: 'c563', key_custody: { returned: true, wrapped: false, purged_at: '2026-10-13T18:41:21+00:00' } },
  columns: [], reveal_map: [], counts: { pages: 21, forms: 14, fields: 63, personal_fields: 51 },
  verification: { hits: 0, mode: 'strict', passed: true, batches: 12, values_checked: 37 }, fidelity: null, privacy: null, utility: null,
  scan: {
    pages: 21, forms: 14, fields: 63, by_entity: { EMAIL: 6, PAN_IN: 1, PERSON_NAME: 8 }, personas: 20, display_persona: 1, submissions: 0,
    non_get_allowed: 0, scope_paths_reached: 0, blocked_requests: { GET: 1, POST: 71 }, navigations: 21,
    validation: { obfuscate: { checked: 61, accepted: 57, rejected: 4 }, synthesize: { checked: 61, accepted: 60, rejected: 1 } },
    captures: { captured: 3, withheld: 0 }, spa_routes: 1, truncated: null, submit_attempts_neutralised: 4, websockets_closed: 0,
    dialogs_dismissed: 0, popups_closed: 0, events_dropped: 0, fills_skipped: 0, scope_routes_reached: 0,
    risk_flags: [{ code: 'A1', flag: 'autocomplete_enabled_sensitive', severity: 'high', page_no: 3, path: 'http://demo-site:8410/apply/us', field_nos: [3, 11], entity_types: ['SSN'], query_keys: [], where: null }],
  },
  timings: { duration_s: 72.265, started_at: '2026-10-13T18:40:08+00:00', finished_at: '2026-10-13T18:41:21+00:00' },
  partitions: [], errors: [], config_hash: 'bc4b',
};
