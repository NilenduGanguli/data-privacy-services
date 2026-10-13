/**
 * Typed fixtures for the UI-scanner console (Vitest and the dev-only fixture mode; never imported by
 * the production bundle — `npm run build` checks it). They mirror the scans API shapes (contract §9,
 * dps/layers/scanner/models.py) for a scan of the demo onboarding site (demo/onboarding).
 *
 * Every value here is value-free (types, counts, redacted URLs and labels) or synthetic (persona
 * values and tokens invented for the fixture): there is no customer value to protect.
 */
import type {
  InventoryRow, Job, KeyMaterial, NumberedRiskFlag, PersonaPreview, RiskFlag, ScanDetail, ScanEvent, ScanField, ScanPage,
  ScanRequestCount, ScanSummary, SynthesisSeed,
} from './types';

export const DEMO_ORIGIN = 'http://demo-site:8410';
export const FIXTURE_SCAN_ID = 's_01K7ZR4M2D9Q8W6X3N5VTB7F1C';
const T0 = Date.parse('2026-10-13T09:40:00Z');
const iso = (ms: number): string => new Date(T0 + ms).toISOString();

// ---- factories: complete, value-free defaults that a test overrides in part ----------------------

export function summary(s: Partial<ScanSummary> = {}): ScanSummary {
  return {
    pages_by_status: {}, pages_visited: 0, pages_discovered: 0, pages_queued: 0, pages_classified: 0, pages_filled: 0,
    forms: 0, fields: 0, personal_fields: 0, critical_fields: 0, risk_flags: [], blocked_requests: {}, non_get_allowed: 0,
    navigations: 0, captures: 0, validation: {}, personas: 1, display_persona: 1, mode: 'synthesize', locale: 'en_IN',
    export_artefacts: {}, evidence_id: null, elapsed_s: null, ...s,
  };
}

export function scanJob(j: Partial<Job> = {}): Job {
  return {
    id: FIXTURE_SCAN_ID, tenant_id: 'default', kind: 'scan', state: 'running', phase: 'scan', mode: 'synthesize', group_id: null,
    connection_id: null, target_connection_id: null, object_ref: null,
    snapshot_id: null, spec: {
      url: `${DEMO_ORIGIN}/apply`, url_origin: DEMO_ORIGIN,
      scope: { same_origin: true, max_pages: 50, max_depth: 3, allow: [], deny: ['/logout', '/admin'], iframes: true, shadow_dom: true, spa_clicks: true },
      auth: { storage_state_ref: 'dps/ui/demo-onboarding' }, mode: 'synthesize', fill: { personas: 20, locale: 'en_IN', display_persona: 1 },
      parallel_view: true, capture: { screenshots: true, har: false }, consistency: { scope: 'request' }, policy: null,
    },
    purpose: {}, kid: 'k_fx01', progress: {
      outcome: '', stage: 'scan', rows_estimated: null, rows_read: 0, rows_written: 0, rows_skipped: 0, partitions_total: 0,
      partitions_done: 0, partitions_failed: 0, rows_per_second: null, throughput: [], throughput_mark: null, values_by_method: {},
      verification: {}, snapshot_ids: [], evidence_id: null, pages: null, fields: null, blocked_requests: null,
    },
    plan: null, error_code: null, error: null, created_by: 'k_console', created_at: iso(0), updated_at: iso(0), started_at: iso(1000),
    finished_at: null, expires_at: null, ...j,
  };
}

export function page(p: Partial<ScanPage> = {}): ScanPage {
  return {
    page_no: 1, url_redacted: `${DEMO_ORIGIN}/apply`, title_redacted: null, depth: 0, parent_page_no: null, status: 'done',
    status_reason: null, discovered_via: 'start', template: '/apply', http_status: 200, final_url_redacted: null, form_count: 0,
    field_count: 0, personal_field_count: 0, critical_field_count: 0, forms: [], artefacts: {}, rendered_findings: {},
    risk_flags: [], validation: {}, capture_status: null, timings: {}, created_at: iso(0), updated_at: iso(0), ...p,
  };
}

export function field(f: Partial<ScanField> = {}): ScanField {
  return {
    page_no: 1, form_index: 1, field_no: 1, selector: 'form#start input[name=fullName]', tag: 'input', input_type: 'text',
    name_attr: 'fullName', id_attr: 'fullName', label: 'Full name', label_source: 'label_for', autocomplete: 'name',
    pattern: null, maxlength: null, required: true, options_count: null, entity_type: 'PERSON_NAME', sensitivity: 'MEDIUM',
    confidence: 0.99, detectors: ['token', 'label'], evidence: ['autocomplete=name', 'label'], fill_synthesize: 'Meera Venkatesan',
    fill_obfuscate: 'Tilqoe Mzvhrpkat', validation_messages: [], frame_index: 0, in_shadow: false, placeholder_shape: null,
    prefilled: false, persona_attr: 'name', obfuscate_method: 'fpe_text', fill_format: 'name', fill_status: 'filled', risk_codes: [],
    ...f,
  };
}

export function event(e: Partial<ScanEvent> = {}): ScanEvent {
  return { seq: 1, ts: iso(0), kind: 'navigate', method: 'GET', result: '200', page_no: 1, target: `${DEMO_ORIGIN}/apply`, note_code: 'start', counts: {}, ...e };
}

export function requestCount(r: Partial<ScanRequestCount> = {}): ScanRequestCount {
  return { page_no: 1, method: 'GET', resource_type: 'document', decision: 'allowed', reason: 'ok', host: 'demo-site', count: 1, ...r };
}

// ---- the demo scan -----------------------------------------------------------------------------

type FieldSpec = [
  label: string, tag: string, type: string | null, name: string | null, auto: string | null, entity: string | null,
  sens: ScanField['sensitivity'], conf: number | null, detectors: string[], attr: string | null, method: string | null,
  syn: string | null, obf: string | null, extra?: Partial<ScanField>,
];

/** Persona 1 of the fixture seed (synthetic, rule-valid shapes from never-issued ranges). */
const P = {
  name: 'Meera Venkatesan', dob: '29/07/1988', aadhaar: '9173 2206 4418', pan: 'MVPPV7210R', phone: '+91 90000 15627',
  email: 'meera.venkatesan@example.com', address: '22 Cunningham Road, Bengaluru 560052', line1: '22 Cunningham Road',
  line2: 'Near Exemplar Park', city: 'Bengaluru', state: 'Karnataka', postal: '560052', employer: 'Exemplar Works 214',
  ifsc: 'EXMP0A12345', account: '000412839105', employee: 'EMP-418230', username: 'user_4471', otp: null,
};
const T = {
  name: 'Tilqoe Mzvhrpkat', dob: '02/11/1987', aadhaar: '8036 1194 7721', pan: 'XKDPQ8810T', phone: '+91 93117 84062',
  email: 'vqzmra.p@example.com', address: '[[PII:STREET_ADDRESS:m4q7t2…]]', line1: '[[PII:STREET_ADDRESS:k2r9d1…]]',
  line2: '[[PII:STREET_ADDRESS:p8w3z5…]]', city: 'Bengaluru', postal: '508217', employer: 'Exemplar Works 214',
  ifsc: 'EXMP0A12345', account: '000977104238', employee: 'EMP-903311', username: 'user_7720',
};

const FIELDS: Record<number, FieldSpec[]> = {
  1: [
    ['Product', 'select', null, 'product', null, null, null, null, [], null, null, 'Savings', 'Savings', { options_count: 4, fill_status: 'neutral' }],
    ['Country of residence', 'select', null, 'country', null, 'NATIONALITY', 'LOW', 0.72, ['label'], 'country', 'keep', 'India', 'India', { options_count: 3 }],
    ['Full name', 'input', 'text', 'fullName', 'name', 'PERSON_NAME', 'MEDIUM', 0.99, ['token', 'label'], 'name', 'fpe_text', P.name, T.name],
    ['Email', 'input', 'email', 'email', 'email', 'EMAIL', 'MEDIUM', 0.99, ['token', 'type'], 'email', 'email', P.email, T.email],
    ['Mobile', 'input', 'tel', 'mobile', 'tel', 'PHONE', 'MEDIUM', 0.99, ['token', 'type'], 'phone', 'fpe_digits', P.phone, T.phone],
  ],
  2: [
    ['Full name', 'input', 'text', 'fullName', 'name', 'PERSON_NAME', 'MEDIUM', 0.99, ['token', 'label'], 'name', 'fpe_text', P.name, T.name],
    ['Date of birth', 'input', 'text', 'dob', 'bday', 'DATE_OF_BIRTH', 'MEDIUM', 0.99, ['token'], 'dob', 'date_shift', P.dob, T.dob, { pattern: '\\d{2}/\\d{2}/\\d{4}', placeholder_shape: 'AA/AA/AAAA', fill_format: 'date:%d/%m/%Y' }],
    ['Aadhaar number', 'input', 'text', 'aadhaar', null, 'AADHAAR', 'CRITICAL', 0.98, ['label', 'pattern'], 'aadhaar', 'fpe_digits', P.aadhaar, T.aadhaar, { pattern: '\\d{4} \\d{4} \\d{4}', risk_codes: ['A1'] }],
    ['PAN', 'input', 'text', 'pan', null, 'PAN_IN', 'CRITICAL', 0.97, ['label', 'maxlength'], 'pan', 'fpe_text', P.pan, T.pan, { maxlength: 10, pattern: '[A-Z]{5}[0-9]{4}[A-Z]', risk_codes: ['A1'] }],
    ['Mobile', 'input', 'tel', 'mobile', 'tel', 'PHONE', 'MEDIUM', 0.99, ['token', 'type'], 'phone', 'fpe_digits', P.phone, T.phone],
    ['Email', 'input', 'email', 'email', 'email', 'EMAIL', 'MEDIUM', 0.99, ['token', 'type'], 'email', 'email', P.email, T.email],
    ['Residential address', 'textarea', null, 'address', 'street-address', 'STREET_ADDRESS', 'MEDIUM', 0.96, ['token'], 'address', 'det_token', P.address, T.address],
    ['Gender', 'select', null, 'gender', 'sex', 'GENDER', 'LOW', 0.95, ['token'], 'gender', 'keep', 'Female', 'Female', { options_count: 4 }],
    ['Nationality', 'select', null, 'nationality', null, 'NATIONALITY', 'LOW', 0.81, ['label'], 'nationality', 'keep', 'Indian', 'Indian', { options_count: 6 }],
  ],
  3: [
    ['Address line 1', 'input', 'text', 'line1', 'address-line1', 'STREET_ADDRESS', 'MEDIUM', 0.98, ['token', 'label'], 'address_line1', 'det_token', P.line1, T.line1],
    ['Address line 2', 'input', 'text', 'line2', 'address-line2', 'STREET_ADDRESS', 'MEDIUM', 0.9, ['token'], 'address_line2', 'det_token', P.line2, T.line2, { label_source: 'wrapping_label' }],
    ['City', 'input', 'text', 'city', 'address-level2', 'CITY', 'LOW', 0.93, ['token', 'label'], 'city', 'keep', P.city, T.city, { label_source: 'aria_labelledby' }],
    ['State', 'select', null, 'state', 'address-level1', null, null, null, ['token'], 'region', null, P.state, P.state, { label_source: 'aria_label', options_count: 5, fill_status: 'neutral' }],
    ['PIN code', 'input', 'text', 'pin', 'postal-code', 'POSTAL_CODE', 'LOW', 0.94, ['token', 'pattern'], 'postal_code', 'fpe_digits', P.postal, T.postal, { in_shadow: true, pattern: '\\d{6}', validation_messages: [{ mode: 'obfuscate', source: 'aria', codes: ['patternMismatch'], text: 'Enter a valid PIN code' }] }],
    ['Landmark (optional)', 'input', 'text', 'landmark', null, null, null, null, [], null, null, 'Exemplar landmark', 'Exemplar landmark', { label_source: 'placeholder', fill_status: 'neutral' }],
    ['Country of residence', 'select', null, 'residence', null, 'NATIONALITY', 'LOW', 0.7, ['label'], 'country', 'keep', 'India', 'India', { label_source: 'nearby_text', options_count: 4 }],
  ],
  4: [
    ['Employer', 'input', 'text', 'employer', 'organization', 'ORG_NAME', 'LOW', 0.9, ['token'], 'organization', 'keep', P.employer, T.employer],
    ['Occupation', 'input', 'text', 'occupation', null, null, null, null, [], null, null, 'Analyst', 'Analyst', { fill_status: 'neutral' }],
    ['Annual income (INR)', 'input', 'number', 'income', null, 'AMOUNT', 'LOW', 0.66, ['label', 'type'], 'amount', 'keep', '48210', '48210'],
    ['IFSC', 'input', 'text', 'ifsc', null, 'IFSC', 'LOW', 0.95, ['label', 'pattern', 'maxlength'], 'ifsc', 'keep', P.ifsc, T.ifsc, { maxlength: 11, pattern: '[A-Z]{4}0[A-Z0-9]{6}' }],
    ['Account number', 'input', 'text', 'account', null, 'ACCOUNT_NUMBER', 'HIGH', 0.93, ['label', 'pattern'], 'account_number', 'fpe_digits', P.account, T.account, { pattern: '\\d{9,18}' }],
    ['Employee ID', 'input', 'text', 'employeeId', null, 'EMPLOYEE_ID', 'MEDIUM', 0.88, ['label', 'pattern'], 'employee_id', 'det_token', P.employee, T.employee, { pattern: 'EMP-\\d{6}' }],
  ],
  5: [
    ['Name to emboss', 'input', 'text', 'emboss', 'cc-name', 'PERSON_NAME', 'MEDIUM', 0.97, ['token'], 'name', 'fpe_text', 'MEERA VENKATESAN', 'TILQOE MZVHRPKAT'],
    ['Card delivery', 'select', null, 'delivery', null, null, null, null, [], null, null, 'Branch pickup', 'Branch pickup', { options_count: 3, fill_status: 'neutral' }],
    ['Card number', 'input', 'text', 'cardNumber', 'cc-number', 'CARD_PAN', 'CRITICAL', 0.99, ['token', 'value'], 'card_number', 'fpe_digits', '4000 0012 3456 7899', '4000 0098 1276 3341', { frame_index: 1 }],
    ['Expiry', 'input', 'text', 'expiry', 'cc-exp', 'CARD_EXPIRY', 'HIGH', 0.96, ['token'], 'card_expiry', 'keep', '09/29', '09/29', { frame_index: 1 }],
  ],
  6: [
    ['Proof of address (PDF or JPEG)', 'input', 'file', 'addressProof', null, null, null, null, [], null, null, null, null, { fill_status: 'skipped_file' }],
  ],
  8: [
    ['Username', 'input', 'text', 'username', 'username', 'USERNAME', 'MEDIUM', 0.97, ['token'], 'username', 'det_token', P.username, T.username],
    ['Password', 'input', 'password', 'password', null, 'PASSWORD', 'CRITICAL', 0.99, ['type'], 'credential', null, null, null, { fill_status: 'filled', risk_codes: ['A4'] }],
    ['One-time code', 'input', 'text', 'otp', 'one-time-code', null, null, null, ['token'], 'otp', null, null, null, { maxlength: 6, pattern: '\\d{6}', fill_status: 'filled' }],
  ],
  10: [
    ['Contact detail', 'input', 'text', 'contact1', null, 'EMAIL', 'MEDIUM', 0.92, ['value'], 'email', 'email', P.email, T.email, { prefilled: true }],
    ['Mobile', 'input', 'tel', 'mobile', 'tel', 'PHONE', 'MEDIUM', 0.99, ['token', 'type', 'value'], 'phone', 'fpe_digits', P.phone, T.phone, { prefilled: true }],
    ['Address', 'textarea', null, 'address', null, 'STREET_ADDRESS', 'MEDIUM', 0.9, ['label', 'value'], 'address', 'det_token', P.address, T.address, { prefilled: true }],
  ],
  12: [
    ['Your name', 'input', 'text', 'name', 'name', 'PERSON_NAME', 'MEDIUM', 0.99, ['token', 'label'], 'name', 'fpe_text', P.name, T.name],
    ['Email', 'input', 'email', 'email', 'email', 'EMAIL', 'MEDIUM', 0.99, ['token', 'type'], 'email', 'email', P.email, T.email],
    ['Phone', 'input', 'tel', 'phone', 'tel', 'PHONE', 'MEDIUM', 0.99, ['token', 'type'], 'phone', 'fpe_digits', P.phone, T.phone],
    ['Message', 'textarea', null, 'message', null, null, null, null, [], null, null, 'Exemplar message', 'Exemplar message', { fill_status: 'neutral' }],
  ],
  13: [
    ['Email', 'input', 'email', 'email', 'email', 'EMAIL', 'MEDIUM', 0.99, ['token', 'type'], 'email', 'email', P.email, T.email, { risk_codes: ['A5'] }],
    ['Application reference', 'input', 'text', 'ref', null, 'CASE_ID', 'LOW', 0.74, ['label', 'pattern'], 'case_id', 'keep', 'APP-2026-0412', 'APP-2026-0412', { pattern: 'APP-\\d{4}-\\d{4}' }],
  ],
};

const SELECTORS: Record<number, string> = { 1: 'form#start', 2: 'form#identity', 3: 'form#address', 4: 'form#employment', 5: 'form#card', 6: 'form#documents', 8: 'form#login', 10: 'form#contact', 12: 'form#contact-us', 13: 'form#track' };

function buildFields(pageNo: number): ScanField[] {
  return (FIELDS[pageNo] ?? []).map(([label, tag, type, name, auto, entity, sens, conf, detectors, attr, method, syn, obf, extra], i) => field({
    page_no: pageNo, form_index: 1, field_no: i + 1, selector: `${SELECTORS[pageNo]} ${tag}${name ? `[name=${name}]` : ''}`, tag, input_type: type,
    name_attr: name, id_attr: name, label, label_source: 'label_for', autocomplete: auto, pattern: null, maxlength: null, required: false,
    options_count: null, entity_type: entity, sensitivity: sens, confidence: conf, detectors, evidence: detectors.map((d) => d),
    fill_synthesize: syn, fill_obfuscate: obf, persona_attr: attr, obfuscate_method: method, fill_format: null,
    fill_status: syn === null && !extra?.fill_status ? 'not_filled' : 'filled', risk_codes: [], ...extra,
  }));
}

const RISKS: NumberedRiskFlag[] = [
  { code: 'A1', path: '/apply/identity', flag: 'autocomplete_enabled_sensitive', severity: 'high', page_no: 2, field_nos: [3, 4], entity_types: ['AADHAAR', 'PAN_IN'], query_keys: [], where: 'form' },
  { code: 'A2', path: '/apply/review', flag: 'pii_in_query_string', severity: 'medium', page_no: 7, field_nos: [], entity_types: ['EMAIL'], query_keys: ['email'], where: 'url' },
  { code: 'A3', path: '/profile', flag: 'pii_rendered_unmasked', severity: 'medium', page_no: 9, field_nos: [], entity_types: ['PAN_IN', 'CARD_PAN', 'ACCOUNT_NUMBER'], query_keys: [], where: 'table' },
  { code: 'A4', path: '/login', flag: 'password_without_autocomplete_token', severity: 'medium', page_no: 8, field_nos: [2], entity_types: ['PASSWORD'], query_keys: [], where: 'form' },
  { code: 'A5', path: '/help/track', flag: 'sensitive_form_uses_get', severity: 'high', page_no: 13, field_nos: [1], entity_types: ['EMAIL'], query_keys: [], where: 'form' },
];
const riskOf = (pageNo: number): RiskFlag[] => RISKS.filter((r) => r.page_no === pageNo).map(({ code: _c, path: _p, ...r }) => r);

type PageSpec = [no: number, path: string, depth: number, parent: number | null, via: ScanPage['discovered_via'], status: ScanPage['status'], reason: string | null, extra?: Partial<ScanPage>];
const PAGES: PageSpec[] = [
  [1, '/apply', 0, null, 'start', 'done', null, { title_redacted: 'Apply for an account' }],
  [2, '/apply/identity', 1, 1, 'link', 'done', null, { title_redacted: 'Identity details' }],
  [3, '/apply/address', 1, 1, 'link', 'done', null, { title_redacted: 'Address', validation: { synthesize: {}, obfuscate: { patternMismatch: 1 } } }],
  [4, '/apply/employment', 1, 1, 'link', 'done', null, { title_redacted: 'Employment' }],
  [5, '/apply/card', 1, 1, 'link', 'done', null, { title_redacted: 'Debit card' }],
  [6, '/apply/documents', 1, 1, 'link', 'done', null, { title_redacted: 'Documents' }],
  [7, '/apply/review?email=<EMAIL>', 1, 2, 'link', 'done', null, { title_redacted: 'Review your application', template: '/apply/review?email=<EMAIL>', rendered_findings: { PERSON_NAME: 1, EMAIL: 1, PHONE: 1, PAN_IN: 1 }, capture_status: 'captured' }],
  [8, '/login', 1, 1, 'link', 'done', null, { title_redacted: 'Sign in' }],
  [9, '/profile', 1, 1, 'link', 'done', null, { title_redacted: 'My profile', rendered_findings: { PERSON_NAME: 1, DATE_OF_BIRTH: 1, CUSTOMER_ID: 1, AADHAAR: 1, PAN_IN: 1, ACCOUNT_NUMBER: 1, CARD_PAN: 1, EMAIL: 1, PHONE: 1, STREET_ADDRESS: 1 }, capture_status: 'captured' }],
  [10, '/profile/contact', 2, 9, 'link', 'done', null, { title_redacted: 'Contact details', rendered_findings: {}, capture_status: 'withheld' }],
  [11, '/help', 1, 1, 'link', 'done', null, { title_redacted: 'Help' }],
  [12, '/help/contact-us', 2, 11, 'link', 'done', null, { title_redacted: 'Contact us' }],
  [13, '/help/track', 2, 11, 'link', 'done', null, { title_redacted: 'Track an application' }],
  [14, '/help/faq/{n}', 2, 11, 'link', 'done', null, { title_redacted: 'FAQ', template: '/help/faq/{n}' }],
  [15, '/help/faq/{n}', 2, 11, 'link', 'skipped', 'template', { template: '/help/faq/{n}', http_status: null }],
  [16, '/app/nominee', 2, 1, 'spa_click', 'done', null, { title_redacted: 'Nominee centre' }],
  [17, '/logout', 1, 1, 'link', 'skipped', 'deny', { http_status: null }],
  [18, '/admin', 1, 1, 'link', 'skipped', 'deny', { http_status: null }],
  [19, '', 1, 1, 'link', 'skipped', 'other_origin', { http_status: null }],
];
const OTHER_ORIGIN = 'https://cdn.bank-assets.invalid';

const personal = (fs: ScanField[]): number => fs.filter((f) => f.entity_type && f.sensitivity !== 'LOW').length;
const critical = (fs: ScanField[]): number => fs.filter((f) => f.sensitivity === 'CRITICAL').length;

function buildPage([no, path, depth, parent, via, status, reason, extra = {}]: PageSpec): ScanPage {
  const fs = buildFields(no);
  const url = no === 19 ? OTHER_ORIGIN : `${DEMO_ORIGIN}${path}`;
  const hasForm = fs.length > 0;
  const artefacts: ScanPage['artefacts'] = status !== 'done' ? {} : hasForm
    ? { original: `art_${no}_original`, annotated: `art_${no}_annotated`, filled_synthesize: `art_${no}_filled_synthesize`, filled_obfuscate: `art_${no}_filled_obfuscate` }
    : { original: `art_${no}_original` };
  if (extra.capture_status === 'captured') Object.assign(artefacts, { masked_capture: `art_${no}_masked_capture`, capture_json: `art_${no}_capture_json` });
  return page({
    page_no: no, url_redacted: url, depth, parent_page_no: parent, status, status_reason: reason, discovered_via: via,
    template: path || '/', http_status: status === 'done' ? 200 : null, form_count: hasForm ? 1 : 0, field_count: fs.length,
    personal_field_count: personal(fs), critical_field_count: critical(fs),
    forms: hasForm ? [{ form_index: 1, method: no === 13 ? 'get' : 'post', field_count: fs.length, frame_index: 0, in_shadow: false }] : [],
    artefacts, risk_flags: riskOf(no), validation: hasForm ? { synthesize: {}, obfuscate: {} } : {},
    capture_status: hasForm || extra.capture_status ? (extra.capture_status ?? 'not_needed') : null,
    timings: status === 'done' ? { load_ms: 180 + no * 7, classify_ms: 22, fill_ms: hasForm ? 640 : 0 } : {},
    created_at: iso(no * 2000), updated_at: iso(no * 2000 + 1500), ...extra,
  });
}

/** Blocked requests per page (the demo site's autosave, beacon and form posts). */
const BLOCKED: [pageNo: number, method: string, reason: string, type: string, count: number][] = [
  [2, 'POST', 'method', 'fetch', 1], [2, 'POST', 'method', 'beacon', 1], [3, 'POST', 'method', 'fetch', 2],
  [5, 'POST', 'method', 'fetch', 1], [5, 'GET', 'filled_navigation', 'document', 1], [10, 'POST', 'method', 'fetch', 1],
  [1, 'GET', 'host', 'image', 1],
];

function buildRequests(pageNo: number): ScanRequestCount[] {
  const allowed = requestCount({ page_no: pageNo, count: 6 + (pageNo % 4) });
  const blocked = BLOCKED.filter((b) => b[0] === pageNo).map(([p, method, reason, type, count]) => requestCount({
    page_no: p, method, resource_type: type, decision: 'blocked', reason, host: reason === 'host' ? 'tracker.invalid' : 'demo-site', count,
  }));
  return [allowed, ...blocked];
}

function buildEvents(pages: ScanPage[], fields: Record<number, ScanField[]>, displayPersona: number): ScanEvent[] {
  const out: ScanEvent[] = [];
  const push = (e: Omit<ScanEvent, 'seq' | 'ts'>, atMs: number): void => { out.push({ seq: out.length + 1, ts: iso(atMs), ...e }); };
  for (const p of pages) {
    const at = p.page_no * 2000;
    if (p.status === 'skipped') {
      push({ kind: 'skip', method: 'GET', result: 'skipped', page_no: p.page_no, target: p.url_redacted, note_code: p.status_reason ?? 'skipped', counts: {} }, at);
      continue;
    }
    push({ kind: 'navigate', method: 'GET', result: String(p.http_status ?? 200), page_no: p.page_no, target: p.url_redacted, note_code: p.discovered_via, counts: {} }, at);
    if (p.discovered_via === 'spa_click') push({ kind: 'spa', method: null, result: 'queued', page_no: p.page_no, target: p.url_redacted, note_code: 'spa_click', counts: {} }, at + 100);
    for (const [pn, method, reason, , count] of BLOCKED) {
      if (pn !== p.page_no || reason === 'host') continue;
      for (let i = 0; i < count; i += 1) push({ kind: 'request', method, result: 'blocked', page_no: pn, target: `${DEMO_ORIGIN}/api/${p.template.split('/')[1] ?? 'x'}/autosave`, note_code: reason, counts: {} }, at + 900 + i * 50);
    }
    const fs = fields[p.page_no] ?? [];
    if (fs.length) {
      const filled = fs.filter((f) => f.fill_status === 'filled').length;
      for (const mode of ['synthesize', 'obfuscate'] as const) {
        const rejected = Object.values(p.validation[mode] ?? {}).reduce((a, b) => a + b, 0);
        push({ kind: 'fill', method: null, result: 'captured', page_no: p.page_no, target: p.url_redacted, note_code: mode, counts: { fields: filled, rejected, persona: displayPersona } }, at + 1200);
      }
    }
    if (p.capture_status === 'captured' || p.capture_status === 'withheld') {
      push({ kind: 'capture', method: null, result: p.capture_status, page_no: p.page_no, target: p.url_redacted, note_code: p.capture_status === 'withheld' ? 'residual' : 'synthesize', counts: {} }, at + 1600);
    }
  }
  return out;
}

/** The scan summary of a set of pages, fields and events (what GET /scans/{id} computes in SQL). */
export function summarize(pages: ScanPage[], fields: Record<number, ScanField[]>, events: ScanEvent[], base: Partial<ScanSummary> = {}): ScanSummary {
  const byStatus: Record<string, number> = {};
  for (const p of pages) byStatus[p.status] = (byStatus[p.status] ?? 0) + 1;
  const done = pages.filter((p) => p.status === 'done' || p.status === 'redirected');
  const allFields = done.flatMap((p) => fields[p.page_no] ?? []);
  const blocked: Record<string, number> = {};
  for (const e of events) if (e.kind === 'request' && e.result === 'blocked' && e.method) blocked[e.method] = (blocked[e.method] ?? 0) + 1;
  const validation: Record<string, Record<string, number>> = {};
  for (const p of done) {
    for (const [mode, codes] of Object.entries(p.validation)) {
      for (const [code, n] of Object.entries(codes)) { validation[mode] = validation[mode] ?? {}; validation[mode][code] = (validation[mode][code] ?? 0) + n; }
    }
  }
  const visibleNos = new Set(done.map((p) => p.page_no));
  return summary({
    pages_by_status: byStatus, pages_visited: done.length, pages_discovered: pages.length, pages_queued: byStatus.queued ?? 0,
    pages_classified: done.length, pages_filled: done.filter((p) => p.form_count > 0 && p.artefacts.annotated).length,
    forms: done.reduce((a, p) => a + p.form_count, 0), fields: allFields.length, personal_fields: personal(allFields),
    critical_fields: critical(allFields), risk_flags: RISKS.filter((r) => visibleNos.has(r.page_no)), blocked_requests: blocked,
    non_get_allowed: 0, navigations: events.filter((e) => e.kind === 'navigate').length,
    captures: done.filter((p) => p.capture_status === 'captured').length, validation, personas: 20, display_persona: 1,
    mode: 'synthesize', locale: 'en_IN', ...base,
  });
}

export function inventoryRows(pages: ScanPage[], fields: Record<number, ScanField[]>): InventoryRow[] {
  return pages.flatMap((p) => (fields[p.page_no] ?? []).map((f) => ({
    page_no: p.page_no, path: p.template, form_index: f.form_index, field_no: f.field_no, label: f.label, tag: f.tag, input_type: f.input_type,
    name_attr: f.name_attr, autocomplete: f.autocomplete, pattern: f.pattern, maxlength: f.maxlength, required: f.required,
    entity_type: f.entity_type, sensitivity: f.sensitivity, confidence: f.confidence, detectors: f.detectors, risk_codes: f.risk_codes,
    fill_status: f.fill_status, persona_attr: f.persona_attr, obfuscate_method: f.obfuscate_method,
  })));
}

export const FIXTURE_SEED: SynthesisSeed = { alg: 'hmac-sha256', seed: 'Rml4dHVyZSBzZWVkIC0gbm90IGEgcmVhbCBzZWVkIQ==', created_at: iso(0) };
export const FIXTURE_KEY: KeyMaterial = {
  alg: 'aes-256-siv', kid: 'k_fx01', dek: 'RklYVFVSRS1PTkxZLU5PVC1BLUtFWS1GSVhUVVJFLU9OTFk=', kcv: 'fx01', wrapped_dek: null, wrap: null,
  consistency_scope: 'request', created_at: iso(0),
};

/** Persona 1 per locale as the setup preview shows it (synthetic values). */
export const PREVIEW_PERSONAS: Record<string, { values: Record<string, string>; notes: string[] }> = {
  en_IN: { values: { name: P.name, dob: P.dob, aadhaar: P.aadhaar, pan: P.pan, phone: P.phone, email: P.email },
    notes: ['Aadhaar Verhoeff-valid outside the issued space', 'PAN format valid', 'one persona per record, so name, gender, e-mail and birth date agree'] },
  en_US: { values: { name: 'Jordan Ellis', dob: '04/11/1986', ssn: '900-41-2297', phone: '(555) 010-4471', email: 'jordan.ellis@example.com' },
    notes: ['SSN in the never-issued area 900', 'phone in the 555-01xx fiction range'] },
  en_GB: { values: { name: 'Olivia Hughes', dob: '18/02/1990', nino: 'QQ 12 34 56 C', phone: '07700 900418', email: 'olivia.hughes@example.org' },
    notes: ['NINO with the reserved QQ prefix', 'Ofcom drama number range'] },
  en_CA: { values: { name: 'Liam Tremblay', dob: '03/09/1984', sin: '046 454 286', phone: '(555) 010-2234', email: 'liam.tremblay@example.ca' },
    notes: ['SIN from the test range', 'phone in the 555-01xx fiction range'] },
  es_MX: { values: { name: 'Ximena Ortega Ruiz', dob: '12/05/1990', curp: 'OERX900512MDFRZM08', rfc: 'OERX900512KQ3', email: 'ximena.ortega@example.net' },
    notes: ['CURP and RFC built from the persona name and birth date'] },
};

export function personaPreview(locale: string, seed: SynthesisSeed | null = null): PersonaPreview {
  const p = PREVIEW_PERSONAS[locale] ?? PREVIEW_PERSONAS.en_US;
  return { synthesis_seed: seed ?? FIXTURE_SEED, personas: [{ persona: 1, values: p.values }], notes: p.notes };
}

export interface DemoScan {
  job: Job; pages: ScanPage[]; fields: Record<number, ScanField[]>; requests: Record<number, ScanRequestCount[]>;
  events: ScanEvent[]; summary: ScanSummary; detail: ScanDetail;
}

/** The finished demo scan (succeeded, exports and evidence ready). */
export function demoScan(id: string = FIXTURE_SCAN_ID): DemoScan {
  const pages = PAGES.map(buildPage);
  const fields: Record<number, ScanField[]> = {};
  const requests: Record<number, ScanRequestCount[]> = {};
  for (const p of pages) { fields[p.page_no] = buildFields(p.page_no); requests[p.page_no] = p.status === 'done' ? buildRequests(p.page_no) : []; }
  const events = buildEvents(pages, fields, 1);
  const s = summarize(pages, fields, events, {
    export_artefacts: { json: `art_${id}_export_json`, csv: `art_${id}_export_csv` }, evidence_id: 'ev_01K7ZR9F2B6Q4W8N3X5VTD1M7C', elapsed_s: 252,
  });
  const job = scanJob({
    id, state: 'succeeded', phase: 'scan', finished_at: iso(253000), updated_at: iso(253000),
    progress: { ...scanJob().progress, outcome: 'succeeded', evidence_id: s.evidence_id, pages: s.pages_visited, fields: s.fields, blocked_requests: Object.values(s.blocked_requests).reduce((a, b) => a + b, 0) },
  });
  return { job, pages, fields, requests, events, summary: s, detail: { scan: job, summary: s } };
}
