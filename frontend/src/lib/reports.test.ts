import { describe, expect, it } from 'vitest';
import type { ApiRollup } from './types';
import * as fx from '../dev/fixtures';
import {
  auditFilters, auditOutcomeTone, evidenceRows, rollupRoute, fromSummary, failedSweepText, filtersToParams, methodsSummary, parseFilters, policyLabel, sweepColumns, sweepSeries,
  verificationChip, type EvidenceRow,
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
    // the unit that fails closed is the batch (contract §0 rule 2, §6.4): batches committed before
    // it may already be in the target, so nothing here says the target was untouched
    expect(failedSweepText(job)).toEqual({
      where: 'j_01K7F2…F2 · p3, p4',
      what: 'The leakage sweep found source values in EMPLOYER_NAME, in batches of partitions 3 and 4. Those batches were not written (fail closed); batches committed before them may already be in the target. Open the job to see what was written.',
    });
    expect(failedSweepText(apiFail).where).toBe('POST /api/v1/synthesize');
    expect(failedSweepText(apiFail).what).toContain('failed closed');
  });
  it('words a job the sweep stopped by its batch and names no column the API did not name (S2 console r3 B1)', () => {
    // what /reports/jobs sends for a run that failed closed after committing 9 batches: the pack
    // error's `where` ("partition 0") is a place, not a column
    const stopped = { where: 'job' as const, day: '2026-10-15', job_id: 'j_01M4YPD09E5MX92DEKJS9MC8TA', partitions: [0], route: null, error_code: 'leakage', paths: ['partition 0'] };
    const words = failedSweepText(stopped);
    expect(words.where).toBe('j_01M4YP…TA · p0');
    expect(words.what).toBe('The leakage sweep found a source value in a batch of partition 0. That batch was not written (fail closed); batches committed before it may already be in the target. Open the job to see what was written.');
    expect(words.what).not.toMatch(/nothing reached|discarded before writing/);
    expect(sweepColumns(stopped)).toEqual([]);
    expect(sweepColumns({ ...stopped, paths: ['prepare', 'partition 2', '$.EMAIL'] })).toEqual(['EMAIL']);
    expect(failedSweepText({ ...stopped, partitions: [1, 2, 5] }).what).toContain('in batches of partitions 1, 2 and 5. Those batches were not written');
  });
  it('words a UI scan whose own verification failed (S2 console r2 B1)', () => {
    const scan = { where: 'scan' as const, day: '2026-10-13', job_id: 's_01K7SC4NQ8ZP3M', partitions: [], route: null, error_code: 'submission_detected', paths: [] };
    const words = failedSweepText(scan);
    expect(words.where).toBe('UI scan s_01K7SC…3M');
    expect(words.what).toBe('The scan\'s request log holds a request that is not a GET and that the guard allowed. The scan was marked failed and its key purged.');
    expect(failedSweepText({ ...scan, error_code: 'other_check' }).what).toBe('The scan\'s verification failed (other_check). The scan was marked failed and its key purged.');
  });
});

describe('roll-up routes', () => {
  it('names the route an audit action of Layer 1 was called on (dps/layers/api/routers)', () => {
    expect(rollupRoute('obfuscate')).toBe('POST /api/v1/obfuscate');
    expect(rollupRoute('reveal')).toBe('POST /api/v1/reveal');
    expect(rollupRoute('document.detect')).toBe('POST /api/v1/documents/detect');
    expect(rollupRoute('schema.synthesize')).toBe('POST /api/v1/schema/synthesize');
    expect(rollupRoute('schema.generator-spec')).toBe('POST /api/v1/schema/generator-spec');
    expect(rollupRoute('plan')).toBe('POST /api/v1/policies/plan');
    expect(rollupRoute('plan:synthesize')).toBe('POST /api/v1/policies/plan');
    // API-layer actions that are not a POST of their own name (dps/layers/api/routers/artefacts.py, common.py)
    expect(rollupRoute('artefact.read')).toBe('GET /api/v1/artefacts/{id}');
    expect(rollupRoute('external.detokenize')).toBe('POST /api/v1/reveal · bank detokenizer');
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
  it("dates today's roll-up now and marks it open (the API refreshes it live; it seals at 00:05 UTC tomorrow)", () => {
    const now = new Date('2026-10-13T15:56:00Z');
    const today: ApiRollup = { day: '2026-10-13', action: 'detect', calls: 1, findings: 8, values: 0, verify_failed: 0 };
    const yesterday: ApiRollup = { ...today, day: '2026-10-12' };
    const rows = evidenceRows([], [today, yesterday], { days: 14, layer: 'all', outcome: 'all', q: '', scope: 'tenant' }, now);
    expect(rows.map((r) => [r.created_at, r.kind === 'rollup' && r.open])).toEqual([
      ['2026-10-13T15:56:00.000Z', true], ['2026-10-13T00:05:00.000Z', false],
    ]);
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
    expect(verificationChip({ kind: 'rollup', rollup: rollups[2], created_at: '', open: false })).toEqual({ tone: 'rose', text: '1 failed · fell back' });
  });
});

describe('verification of runs that did not finish (r1 B2, main N3)', () => {
  const pack = (outcome: string, summary: Record<string, unknown>, layer = 'conn', job_kind = 'run'): EvidenceRow => ({
    kind: 'pack', created_at: '2026-10-13T00:00:00Z',
    item: { id: 'e', job_id: 'j', job_kind, outcome, created_at: '2026-10-13T00:00:00Z', pack_sha256: '', layer, summary },
  });
  for (const outcome of ['canceled', 'dead', 'failed']) {
    for (const vp of [undefined, null, true]) {
      it(`${outcome} with verification_passed=${String(vp)} and no hit never reads as passed`, () => {
        const chip = verificationChip(pack(outcome, { mode: 'obfuscate', verification_passed: vp, hits: 0 }));
        expect(chip.text).toBe(`not verified · job ${outcome}`);
        expect(chip.tone).toBe(outcome === 'canceled' ? 'slate' : 'rose');
      });
    }
  }
  it('a run with hits reads as caught, whatever its outcome', () => {
    expect(verificationChip(pack('canceled', { verification_passed: false, hits: 3 }))).toEqual({ tone: 'rose', text: 'failed · 3 leaks caught' });
    expect(verificationChip(pack('failed', { verification_passed: false, hits: 1 }))).toEqual({ tone: 'rose', text: 'failed · 1 leak caught' });
  });
  it('passes only a finished run whose verification_passed is true', () => {
    expect(verificationChip(pack('succeeded', { verification_passed: true, hits: 0 }))).toEqual({ tone: 'emerald', text: 'passed · 0 leaks' });
    expect(verificationChip(pack('dry_run', { verification_passed: true, hits: 0 }))).toEqual({ tone: 'emerald', text: 'passed · 0 leaks' });
    expect(verificationChip(pack('succeeded', { verification_passed: null, hits: 0 }))).toEqual({ tone: 'slate', text: 'not verified · no verification recorded' });
    expect(verificationChip(pack('succeeded', {}))).toEqual({ tone: 'slate', text: 'not verified · no verification recorded' });
    // a finished run whose verification failed before a hit was counted (a preflight leak)
    expect(verificationChip(pack('succeeded', { verification_passed: false, hits: 0 }))).toEqual({ tone: 'rose', text: 'failed closed' });
  });
  it('a finished run that needs a reviewer reads as review required (main B1, M5 D-A)', () => {
    expect(verificationChip(pack('succeeded', { mode: 'synthesize', verification_passed: false, review_required: true, hits: 0 }))).toEqual({ tone: 'amber', text: 'review required' });
    expect(verificationChip(pack('dry_run', { verification_passed: false, review_required: true, hits: 0 }))).toEqual({ tone: 'amber', text: 'review required' });
    // a hit or an unfinished run outranks the review
    expect(verificationChip(pack('succeeded', { verification_passed: false, review_required: true, hits: 2 }))).toEqual({ tone: 'rose', text: 'failed · 2 leaks caught' });
    expect(verificationChip(pack('failed', { verification_passed: false, review_required: true, hits: 0 })).text).toBe('not verified · job failed');
  });
  it('a run the leakage sweep stopped reads as failed closed, as its own pack seals it (S2 console r3 B1)', () => {
    // a leakage refusal discards its batch's counters, so hits stays 0; the pack's status is failed
    expect(verificationChip(pack('failed', { verification_passed: false, hits: 0, verification_status: 'failed', rows_written: 9000 }))).toEqual({ tone: 'rose', text: 'failed closed' });
    expect(verificationChip(pack('failed', { verification_passed: false, hits: 0, verification_status: 'incomplete' })).text).toBe('not verified · job failed');
    expect(verificationChip(pack('canceled', { verification_passed: false, hits: 0, verification_status: 'incomplete', rows_written: 14000 })).text).toBe('not verified · job canceled');
  });
  it('a scan passes only when it succeeded (no dry run for scans)', () => {
    expect(verificationChip(pack('succeeded', { verification_passed: true, submissions: 0 }, 'ui', 'scan'))).toEqual({ tone: 'emerald', text: 'passed · 0 POSTs left' });
    expect(verificationChip(pack('dry_run', { verification_passed: true, submissions: 0 }, 'ui', 'scan')).tone).not.toBe('emerald');
    expect(verificationChip(pack('canceled', { verification_passed: true, submissions: 0 }, 'ui', 'scan'))).toEqual({ tone: 'slate', text: 'not verified · scan canceled' });
  });
});

describe('audit helpers', () => {
  it('tones outcomes and summarises methods and policies', () => {
    expect(auditOutcomeTone('ok')).toBe('emerald');
    expect(auditOutcomeTone('leakage')).toBe('rose');
    expect(auditOutcomeTone('rejected')).toBe('amber');
    expect(auditOutcomeTone('blocked')).toBe('slate');
    // job.finalize rows carry the job's outcome (dps/layers/connectors/jobs_run.py), grouped as
    // dps/audit_query.py OUTCOME_GROUPS does: ok, failed, blocked
    expect(auditOutcomeTone('succeeded')).toBe('emerald');
    expect(auditOutcomeTone('dry_run')).toBe('emerald');
    expect(auditOutcomeTone('dead')).toBe('rose');
    expect(auditOutcomeTone('canceled')).toBe('slate');
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
  it('maps a UI scan\'s M2 summary to the UI layer (M4: scan ids are s_…)', () => {
    const item = fromSummary({ id: 'ev_3', job_id: 's_1', created_at: '2026-10-12T00:00:00Z', outcome: 'succeeded', mode: 'synthesize', label: 'x', source_object: null,
      target: null, write_mode: null, policy: null, ticket: null, passed: true, hits: 0, rows_written: 0, html_artefact_id: null });
    expect(item).toMatchObject({ layer: 'ui', job_kind: 'scan' });
  });
  it('carries main\'s review_required into the evidence item', () => {
    const item = fromSummary({ id: 'ev_2', job_id: 'j_2', created_at: '2026-10-12T00:00:00Z', outcome: 'succeeded', mode: 'obfuscate', label: 'x', source_object: 'bank.contacts',
      target: 'bank_uat.contacts', write_mode: 'table', policy: 'gdpr-pseudonymise', ticket: null, passed: false, review_required: true, hits: 0, rows_written: 10, html_artefact_id: null });
    expect(item.summary.review_required).toBe(true);
    expect(verificationChip({ kind: 'pack', item, created_at: '' })).toEqual({ tone: 'amber', text: 'review required' });
  });
});
