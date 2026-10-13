import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import {
  auditFilters, auditOutcomeTone, evidenceRows, fromSummary, failedSweepText, filtersToParams, methodsSummary, parseFilters, policyLabel, sweepSeries,
  verificationChip,
} from './reports';

const NOW = new Date('2026-10-13T10:15:00Z');

describe('report filters', () => {
  it('round-trips the URL query with defaults', () => {
    expect(parseFilters(new URLSearchParams(''))).toEqual({ days: 14, layer: 'all', outcome: 'all', q: '', scope: 'tenant' });
    const f = parseFilters(new URLSearchParams('days=30&layer=conn&outcome=failed&q=CHG-1&scope=all'));
    expect(f).toEqual({ days: 30, layer: 'conn', outcome: 'failed', q: 'CHG-1', scope: 'all' });
    expect(filtersToParams(f).toString()).toBe('days=30&layer=conn&outcome=failed&q=CHG-1&scope=all');
    expect(filtersToParams(parseFilters(new URLSearchParams('days=99&layer=x'))).toString()).toBe('');
  });
  it('maps filters to audit query parameters', () => {
    expect(auditFilters({ days: 7, layer: 'api', outcome: 'blocked', q: ' key ', scope: 'all' }, NOW)).toEqual({
      from: '2026-10-06T10:15:00.000Z', layer: 'api', outcome: 'blocked', q: 'key', tenants: '*', limit: 50,
    });
    expect(auditFilters({ days: 14, layer: 'all', outcome: 'all', q: '', scope: 'tenant' }, NOW)).toEqual({ from: '2026-09-29T10:15:00.000Z', limit: 50 });
  });
});

describe('sweeps', () => {
  const report = fx.reportsJobs(NOW, 14);
  it('sums the layers per day and honours the outcome filter', () => {
    const all = sweepSeries(report.sweeps, 'all', 'all');
    expect(all).toHaveLength(14);
    const failed = all.reduce((a, d) => a + d.failed, 0);
    expect(failed).toBe(3);
    expect(sweepSeries(report.sweeps, 'conn', 'all').reduce((a, d) => a + d.failed, 0)).toBe(2);
    expect(sweepSeries(report.sweeps, 'all', 'ok').every((d) => d.failed === 0)).toBe(true);
    expect(sweepSeries(report.sweeps, 'all', 'blocked').every((d) => d.failed === 0 && d.passed === 0)).toBe(true);
  });
  it('describes a failed sweep in words, value-free', () => {
    const [job, apiFail] = report.failed_sweeps;
    expect(failedSweepText(job)).toEqual({ where: 'j_01K7F2…F2 · p3, p4', what: 'The leakage sweep found source values in EMPLOYER_NAME. The partitions were discarded before writing; nothing reached the target.' });
    expect(failedSweepText(apiFail).where).toBe('POST /api/v1/synthesize');
    expect(failedSweepText(apiFail).what).toContain('failed closed');
  });
});

describe('evidence rows', () => {
  const items = fx.evidenceItems(NOW);
  const rollups = fx.apiRollups(NOW, 14);
  it('merges packs and daily API roll-ups, newest first', () => {
    const rows = evidenceRows(items, rollups, { days: 14, layer: 'all', outcome: 'all', q: '', scope: 'tenant' });
    expect(rows.length).toBe(items.length + rollups.length);
    const times = rows.map((r) => new Date(r.created_at).getTime());
    expect([...times].sort((a, b) => b - a)).toEqual(times);
    expect(rows.filter((r) => r.kind === 'rollup')[0].created_at).toBe('2026-10-13T00:05:00.000Z');
  });
  it('filters roll-ups by layer, outcome and search', () => {
    expect(evidenceRows(items, rollups, { days: 14, layer: 'conn', outcome: 'all', q: '', scope: 'tenant' }).some((r) => r.kind === 'rollup')).toBe(false);
    expect(evidenceRows([], rollups, { days: 14, layer: 'api', outcome: 'failed', q: '', scope: 'tenant' })).toHaveLength(1);
    expect(evidenceRows([], rollups, { days: 14, layer: 'all', outcome: 'all', q: 'obfus', scope: 'tenant' })).toHaveLength(1);
  });
  it('labels verification per kind of pack', () => {
    const byId = Object.fromEntries(items.map((i) => [i.id, i]));
    expect(verificationChip({ kind: 'pack', item: byId.ev_01K7ZQ9QA1, created_at: '' })).toEqual({ tone: 'emerald', text: 'passed · 0 leaks' });
    expect(verificationChip({ kind: 'pack', item: byId.ev_01K7ZQS7B2, created_at: '' })).toEqual({ tone: 'emerald', text: 'passed · 0 exact rows' });
    expect(verificationChip({ kind: 'pack', item: byId.ev_01K6MXSC88, created_at: '' })).toEqual({ tone: 'emerald', text: 'passed · 0 POSTs left' });
    expect(verificationChip({ kind: 'pack', item: byId.ev_01K7F2E5D5, created_at: '' })).toEqual({ tone: 'rose', text: 'failed · 2 leaks caught' });
    expect(verificationChip({ kind: 'rollup', rollup: rollups[2], created_at: '' })).toEqual({ tone: 'rose', text: '1 failed · fell back' });
  });
});

describe('audit helpers', () => {
  it('tones outcomes and summarises methods and policies', () => {
    expect(auditOutcomeTone('ok')).toBe('emerald');
    expect(auditOutcomeTone('leakage')).toBe('rose');
    expect(auditOutcomeTone('rejected')).toBe('amber');
    expect(auditOutcomeTone('blocked')).toBe('slate');
    expect(methodsSummary({ keep: 1, fpe_digits: 5, email: 2 })).toEqual({ first: 'fpe_digits', more: 2 });
    expect(methodsSummary({})).toBeNull();
    expect(policyLabel('gdpr-pseudonymise@v3')).toBe('gdpr-pseudonymise v3');
    expect(policyLabel(null)).toBeNull();
  });
});

describe('M2 fallback', () => {
  it('maps an M2 evidence summary to an evidence item', () => {
    const item = fromSummary({ id: 'ev_1', job_id: 'j_1', created_at: '2026-10-12T00:00:00Z', outcome: 'succeeded', mode: 'obfuscate', label: 'x', source_object: 'bank.customers',
      target: 'bank_uat.customers', write_mode: 'table', policy: 'gdpr-pseudonymise', ticket: 'CHG-1', passed: true, hits: 0, rows_written: 10, html_artefact_id: null });
    expect(item).toMatchObject({ layer: 'conn', summary: { policy: 'gdpr-pseudonymise', policy_version: null, rows_written: 10, verification_passed: true } });
  });
});
