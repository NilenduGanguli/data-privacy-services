import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { diffBodies } from '../dev/fixtureServer';
import {
  addDeclaredField, changedEntities, declaredSince, diffLines, parseParam, removeTreatment, setTreatment, simulateFpe, toPresidioYaml,
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

describe('diffs', () => {
  const [, v2] = fx.policyVersions();
  const changes = diffBodies(v2, v3);
  it('finds the entities a diff changes', () => {
    expect([...changedEntities(changes)].sort()).toEqual(['CVV', 'POSTAL_CODE']);
    expect([...changedEntities([{ op: 'change', path: 'treat.CVV.method', before: 'det_token', after: 'redact' }])]).toEqual(['CVV']);
  });
  it('renders YAML-like lines with nested keys, removals and additions', () => {
    const lines = diffLines(changes);
    const text = lines.map((l) => `${l.kind === 'add' ? '+' : l.kind === 'del' ? '-' : ' '}${l.text}`);
    expect(text).toContain(' detect:');
    expect(text).toContain('-  min_score: 0.55');
    expect(text).toContain('+  min_score: 0.6');
    expect(text).toContain('+    - { path: "CRM.CUSTOMERS.RISK_SCORE", entity_type: BUSINESS_SENSITIVE }');
    expect(text).toContain('     - { path: CUSTOMER_ID, entity_type: CUSTOMER_ID }');
    expect(text).toContain(' treat:');
    expect(text).toContain('-  CVV: { method: det_token }');
    expect(text).toContain('+  CVV: { method: redact }');
    expect(text).toContain('+  POSTAL_CODE: { method: generalize, keep_chars: 3 }');
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
