import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { diffBodies, flatten } from '../dev/fixtureServer';
import {
  addDeclaredField, changedEntities, isTenantPolicy, planPolicy, declaredSince, diffLines, parseParam, removeTreatment, setTreatment, simulateFpe, splitPath, toPresidioYaml,
  upsertRecognizer,
} from './policyEdit';
import type { PlanRow } from './types';

const v3 = fx.policyVersions()[2] as unknown as Record<string, any>;

describe('policyEdit (pure, no mutation)', () => {
  it('sets and removes a treatment', () => {
    const next = setTreatment(v3, 'ORG_NAME', { method: 'det_token', params: {} });
    expect(next.treat.ORG_NAME).toEqual({ method: 'det_token', params: {} });
    expect(v3.treat.ORG_NAME).toEqual({ method: 'keep', params: {} });
    const gone = removeTreatment(next, 'ORG_NAME');
    expect('ORG_NAME' in gone.treat).toBe(false);
    expect('ORG_NAME' in next.treat).toBe(true);
  });
  it('declares a field once and upserts a recognizer by name', () => {
    const a = addDeclaredField(v3, { path: 'LOANS.APPLICANTS.EMPLOYER_NAME', entity_type: 'ORG_NAME' });
    const b = addDeclaredField(a, { path: 'LOANS.APPLICANTS.EMPLOYER_NAME', entity_type: 'PERSON_NAME' });
    expect(b.detect.declared_fields.filter((d: { path: string }) => d.path === 'LOANS.APPLICANTS.EMPLOYER_NAME')).toEqual([{ path: 'LOANS.APPLICANTS.EMPLOYER_NAME', entity_type: 'PERSON_NAME' }]);
    expect(v3.detect.declared_fields).toHaveLength(4);
    const rec = { name: 'employee-id', supported_entity: 'EMPLOYEE_ID', patterns: [{ name: 'employee-id', regex: '\\bEMP-\\d{6}\\b', score: 0.85 }] };
    const r1 = upsertRecognizer(v3, rec);
    const r2 = upsertRecognizer(r1, { ...rec, patterns: [{ ...rec.patterns[0], score: 0.9 }] });
    expect(r2.detect.custom_recognizers).toHaveLength(1);
    expect(r2.detect.custom_recognizers[0].patterns[0].score).toBe(0.9);
  });
  it('writes a recognizer as Presidio YAML', () => {
    const yaml = toPresidioYaml({ name: 'employee-id', supported_entity: 'EMPLOYEE_ID', patterns: [{ name: 'employee-id', regex: '\\bEMP-\\d{6}\\b', score: 0.85 }], context: ['staff id'], deny_list: [] });
    expect(yaml).toContain('recognizers:');
    expect(yaml).toContain('supported_entity: EMPLOYEE_ID');
    expect(yaml).toContain('regex: "\\\\bEMP-\\\\d{6}\\\\b"');
    expect(yaml).toContain('- "staff id"');
    expect(yaml).not.toContain('deny_list');
  });
  it('parses parameter values typed in the inline editor', () => {
    expect(parseParam('4')).toBe(4);
    expect(parseParam('true')).toBe(true);
    expect(parseParam('[0, 300, 600]')).toEqual([0, 300, 600]);
    expect(parseParam('keep')).toBe('keep');
  });
});

describe('fixture diff = backend flatten_policy / diff_policies (M5 plan Task 15, r1 B3)', () => {
  it('keys list items by name or path (test_flatten_keys_lists_by_name_or_path)', () => {
    const flat = flatten({ detect: { min_score: 0.6, custom_recognizers: [{ name: 'emp', patterns: [{ name: 'p', regex: 'x', score: 0.9 }] }], declared_fields: [{ path: '$.a', entity_type: 'X' }] } });
    expect(flat['detect.min_score']).toBe(0.6);
    expect(flat['detect.custom_recognizers[emp].patterns[p].score']).toBe(0.9);
    expect(flat['detect.declared_fields[$.a].entity_type']).toBe('X');
    expect(flat['detect.custom_recognizers[emp].name']).toBe('emp');
  });
  it('falls back to the index, keeps empty and scalar lists as leaves, drops empty mappings', () => {
    const flat = flatten({ a: [{ x: 1 }, { name: '', path: 'q', x: 2 }], b: [], c: [1, 2], d: {}, e: { method: 'keep', params: {} } });
    expect(flat).toEqual({ 'a[0].x': 1, 'a[q].name': '', 'a[q].path': 'q', 'a[q].x': 2, b: [], c: [1, 2], 'e.method': 'keep' });
  });
  it('reports add, remove and change and ignores name and version (test_diff_reports_add_remove_change_and_ignores_name_version)', () => {
    const v1 = fx.gdprBody(1) as unknown as Record<string, any>;
    const treat = { ...v1.treat };
    delete treat.CVV;
    const v2 = { ...v1, name: 'other', version: 2, detect: { ...v1.detect, min_score: 0.6 }, treat: { ...treat, BUSINESS_SENSITIVE: { method: 'bucket', params: { ranges: [0, 300, 600, 900] } } } };
    const changes = new Set(diffBodies(v1, v2).map((c) => `${c.op} ${c.path}`));
    expect(changes.has('change detect.min_score')).toBe(true);
    expect(changes.has('remove treat.CVV.method')).toBe(true);
    expect(changes.has('add treat.BUSINESS_SENSITIVE.method')).toBe(true);
    expect(changes.has('add treat.BUSINESS_SENSITIVE.params.ranges')).toBe(true);
    expect([...changes].some((c) => c.endsWith(' name') || c.endsWith(' version'))).toBe(false);
  });
});

describe('diffs', () => {
  const [v1, v2] = fx.policyVersions();
  const show = (lines: { kind: string; text: string }[]): string[] => lines.map((l) => `${l.kind === 'add' ? '+' : l.kind === 'del' ? '-' : ' '}${l.text}`);
  it('splits paths at dots outside brackets (labels keep their dots and JSONPath brackets)', () => {
    expect(splitPath('detect.declared_fields[LOANS.APPLICANTS.EMPLOYER_NAME].entity_type')).toEqual([
      { key: 'detect' }, { key: 'declared_fields', label: 'LOANS.APPLICANTS.EMPLOYER_NAME' }, { key: 'entity_type' }]);
    expect(splitPath('detect.declared_fields[$.items[*].ssn].path')).toEqual([{ key: 'detect' }, { key: 'declared_fields', label: '$.items[*].ssn' }, { key: 'path' }]);
    expect(splitPath('detect.custom_recognizers[emp].patterns[p].score')).toEqual([
      { key: 'detect' }, { key: 'custom_recognizers', label: 'emp' }, { key: 'patterns', label: 'p' }, { key: 'score' }]);
    expect(splitPath('treat.CVV.method')).toEqual([{ key: 'treat' }, { key: 'CVV' }, { key: 'method' }]);
  });
  it('finds the entities a diff changes', () => {
    expect([...changedEntities(diffBodies(v2, v3))].sort()).toEqual(['CVV', 'POSTAL_CODE']);
    expect([...changedEntities([{ op: 'change', path: 'treat.CVV.method', before: 'det_token', after: 'redact' }])]).toEqual(['CVV']);
  });
  it('renders the review probe paths as list items, never as keys split at label dots', () => {
    const text = show(diffLines([
      { op: 'add', path: 'detect.declared_fields[LOANS.APPLICANTS.EMPLOYER_NAME].entity_type', after: 'ORG_NAME' },
      { op: 'add', path: 'detect.declared_fields[LOANS.APPLICANTS.EMPLOYER_NAME].path', after: 'LOANS.APPLICANTS.EMPLOYER_NAME' },
      { op: 'remove', path: 'treat.CVV.method', before: 'redact' },
      { op: 'add', path: 'detect.custom_recognizers[employee-id].patterns[p].regex', after: '\\bEMP-\\d{6}\\b' },
    ]));
    expect(text).toEqual([
      ' detect:',
      '   custom_recognizers:',
      '     - [employee-id]',
      '       patterns:',
      '         - [p]',
      '+          regex: "\\\\bEMP-\\\\d{6}\\\\b"',
      '   declared_fields:',
      '+    - { path: "LOANS.APPLICANTS.EMPLOYER_NAME", entity_type: ORG_NAME }',
      ' treat:',
      '-  CVV: { method: redact }',
    ]);
    expect(text.join('\n')).not.toContain('LOANS:');
  });
  it('renders v1 → v2 like mockup 05 (new list items and a new treatment on one line)', () => {
    expect(show(diffLines(diffBodies(v1, v2)))).toEqual([
      ' detect:',
      '   declared_fields:',
      '+    - { path: "$.loan.internal_score", entity_type: BUSINESS_SENSITIVE }',
      '+    - { path: CUSTOMER_ID, entity_type: CUSTOMER_ID }',
      '+    - { path: "LOANS.APPLICANTS.CIBIL_SCORE", entity_type: BUSINESS_SENSITIVE }',
      ' treat:',
      '+  BUSINESS_SENSITIVE: { method: bucket, ranges: [0, 300, 600, 900] }',
    ]);
  });
  it('renders v2 → v3 with changed leaves under their keys', () => {
    expect(show(diffLines(diffBodies(v2, v3)))).toEqual([
      ' detect:',
      '   declared_fields:',
      '+    - { path: "CRM.CUSTOMERS.RISK_SCORE", entity_type: BUSINESS_SENSITIVE }',
      '-  min_score: 0.55',
      '+  min_score: 0.6',
      ' treat:',
      '   CVV:',
      '-    method: det_token',
      '+    method: redact',
      '   POSTAL_CODE:',
      '-    method: fpe_digits',
      '+    method: generalize',
      '     params:',
      '+      keep_chars: 3',
    ]);
  });
  it('renders v3 → draft v4 with the new recognizer as one item (nested list items included)', () => {
    const text = show(diffLines(diffBodies(v3, fx.gdprDraft())));
    expect(text).toContain('+    - { name: employee-id, allow_list: [EMP-000000], context: ["staff id", employee], deny_list: [], patterns: [{ name: employee-id, regex: "\\\\bEMP-\\\\d{6}\\\\b", score: 0.9 }], supported_entity: EMPLOYEE_ID }');
    expect(text).toContain('+    - { path: "LOANS.APPLICANTS.EMPLOYER_NAME", entity_type: ORG_NAME }');
    expect(text).toContain('-    method: keep');
    expect(text).toContain('+    method: det_token');
    expect(text.some((l) => l.includes('custom_recognizers: []'))).toBe(false);
  });
  it('does not fold a partly changed item or treatment into one line (unchanged fields are not in the diff)', () => {
    const text = show(diffLines([
      { op: 'add', path: 'detect.declared_fields[CUSTOMER_ID].api', after: 'keep' },
      { op: 'add', path: 'treat.SSN.params.keep_prefix', after: 2 },
      { op: 'add', path: 'detect.declared_fields[0].entity_type', after: 'X' },
    ]));
    expect(text).toEqual([
      ' detect:',
      '   declared_fields:',
      '     - [0]',
      '+      entity_type: X',
      '     - [CUSTOMER_ID]',
      '+      api: keep',
      ' treat:',
      '   SSN:',
      '     params:',
      '+      keep_prefix: 2',
    ]);
  });
  it('still renders a list of scalars with kept items as context', () => {
    expect(show(diffLines([{ op: 'change', path: 'detect.tiers', before: ['structural', 'patterns'], after: ['structural', 'patterns', 'ner'] }]))).toEqual([
      ' detect:', '   tiers:', '     - structural', '     - patterns', '+    - ner',
    ]);
  });
});

describe('plan simulation and declared-since', () => {
  const plan: PlanRow[] = [
    { path: '$.tax_ids.us_ssn', span: null, entity_type: 'SSN', score: 0.95, method: 'fpe_digits', params: { keep_suffix: 4 }, reason: 'policy:SSN', reversible: true },
    { path: '$.email', span: null, entity_type: 'EMAIL', score: 0.99, method: 'email', params: {}, reason: 'policy:EMAIL', reversible: true },
  ];
  it('recomputes the FF1 fallback client-side in both directions', () => {
    const off = simulateFpe(plan, false, v3);
    expect(off[0]).toMatchObject({ method: 'det_token', reason: 'policy:SSN|fallback:fpe_disabled', reversible: true, params: {} });
    expect(off[1]).toEqual(plan[1]);
    const on = simulateFpe(off, true, v3);
    expect(on[0]).toMatchObject({ method: 'fpe_digits', reason: 'policy:SSN', params: { keep_suffix: 4 } });
  });
  it('names the version a declaration first appeared in', () => {
    const bodies = Object.fromEntries(fx.policyVersions().map((b) => [b.version, b as unknown as Record<string, any>]));
    const since = declaredSince(bodies, fx.gdprDraft() as unknown as Record<string, any>, 4);
    expect(since['CUSTOMER_ID']).toBe('v2');
    expect(since['CRM.CUSTOMERS.RISK_SCORE']).toBe('v3');
    expect(since['LOANS.APPLICANTS.EMPLOYER_NAME']).toBe('draft v4');
  });
});

describe('wiring to the policy store as built (dps/layers/api/routers/policy_store.py)', () => {
  const tenant = [{ name: 'retail-banking-apac', base_preset: 'gdpr-pseudonymise', active_version: 2, active_hash: 'h', draft: false, draft_changes: 0, published_at: null, published_by: null, jobs: 0 }];
  it('asks GET /policies/{name} only for a tenant policy (a preset answers 404 policy_not_found)', () => {
    expect(isTenantPolicy('retail-banking-apac', true, tenant)).toBe(true);
    expect(isTenantPolicy('gdpr-pseudonymise', true, tenant)).toBe(false);
    expect(isTenantPolicy('retail-banking-apac', false, tenant)).toBe(false);
  });
  it('plans with the shown body, else @name for a tenant policy (dps/layers/api/policy_refs.py), else the preset name', () => {
    const body = { name: 'retail-banking-apac', version: 2 };
    expect(planPolicy('retail-banking-apac', body, true)).toBe(body);
    expect(planPolicy('retail-banking-apac', null, true)).toBe('@retail-banking-apac');
    expect(planPolicy('gdpr-pseudonymise', null, false)).toBe('gdpr-pseudonymise');
  });
});
