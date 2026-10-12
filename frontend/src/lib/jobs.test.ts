import { describe, expect, it } from 'vitest';
import { buildRunSpec, canConfirmWrite, draftForStorage, filterJobs, fmtCount, fmtDuration, heatTone, jobStatus, jobsCsv, NO_FILTERS, needsDecision, ownerOf, progressPct, pushSample, sparkPath, splitCounters, statusBucket, writeModeLabel } from './jobs';
import { evidenceSoFar, methodText, partitionPct, shortType, stepperState } from './jobs';
import { draft, job, column } from './fixtures';

describe('jobs helpers', () => {
  it('labels statuses like the jobs mockup', () => {
    expect(jobStatus(job({ kind: 'discover', state: 'succeeded', progress: { snapshot_ids: ['snap_1'] } }), new Set(['snap_1']))).toEqual({ label: 'awaiting review', tone: 'amber' });
    expect(jobStatus(job({ state: 'waiting', phase: 'partitions' })).label).toBe('running');
    expect(jobStatus(job({ state: 'succeeded', progress: { outcome: 'dry_run' } })).label).toBe('dry run');
    expect(jobStatus(job({ state: 'failed' })).tone).toBe('rose');
  });
  it('formats counts, durations and write modes', () => {
    expect(fmtCount(1948210)).toBe('1,948,210');
    expect(fmtDuration(472)).toBe('7 min 52 s');
    expect(writeModeLabel('table', 'truncate_insert')).toBe('copy to table · truncate + insert');
    expect(progressPct(job({ progress: { rows_estimated: 200, rows_written: 50 } }))).toBe(25);
  });
  it('splits counters and draws sparklines', () => {
    expect(splitCounters({ det_token: 3, 'fallback:fpe_disabled': 1, 'col:email': 3 })).toEqual({ methods: { det_token: 3 }, fallbacks: { fpe_disabled: 1 }, columns: { email: 3 } });
    expect(sparkPath([['t1', 0], ['t2', 10]], 100, 20)).toBe('M0 20 L100 0');
  });
  it('flags columns that need a decision and colours the heat-map', () => {
    expect(needsDecision(column({ entity_type: 'POSTAL_CODE', confidence: 0.93, type_note: 'fpe_domain_too_small' }))).toBe(true);
    expect(needsDecision(column({ entity_type: 'EMAIL', confidence: 0.99, type_note: null }))).toBe(false);
    expect(heatTone(column({ sensitivity: 'CRITICAL' }))).toBe('crit');
    expect(heatTone(column({ entity_type: null }))).toBe('kept');
  });
  it('never stores secrets, key material or filters in the saved draft', () => {
    const saved = JSON.stringify(draftForStorage(draft({ password: 'pw-123', keyMaterialJson: '{"dek":"x"}', where: "name = 'Ananya'" })));
    expect(saved).not.toContain('pw-123');
    expect(saved).not.toContain('dek');
    expect(saved).not.toContain('Ananya');
  });
  it('builds the run spec and gates the write confirmation', () => {
    const spec = buildRunSpec(draft({ writeMode: 'in_place', typed: 'bank_restore.contacts', rows: 412560 }));
    expect(spec.target).toMatchObject({ write_mode: 'in_place', confirm_write: true, expected_rows: 412560 });
    expect(canConfirmWrite('bank_restore.contacts', 'bank_restore.contacts', false)).toBe(true);
    expect(canConfirmWrite('bank_restore.contact', 'bank_restore.contacts', false)).toBe(false);
    expect(canConfirmWrite('bank_restore.contacts', 'bank_restore.contacts', true)).toBe(false);
  });
  it('exports a value-free CSV', () => {
    const csv = jobsCsv([job({ id: 'j_1', object_ref: 'bank.customers' })]);
    expect(csv.split('\n')[0]).toBe('id,created_at,kind,state,source,target,mode,write_mode,rows_written');
  });
});

describe('jobs page filters', () => {
  const review = job({ id: 'j_review', kind: 'discover', state: 'succeeded', phase: 'discover', object_ref: 'loans.applicants', spec: {}, progress: { snapshot_ids: ['snap_p'] }, purpose: {} });
  const running = job({ id: 'j_run', state: 'waiting', phase: 'partitions', connection_id: 'c_pg', object_ref: 'bank.customers', purpose: { ticket: 'CHG-48213' } });
  const failed = job({ id: 'j_fail', state: 'failed', connection_id: 'c_ora', object_ref: 'CRM.CUSTOMERS', mode: 'synthesize', spec: { target: { write_mode: 'files', files: { format: 'parquet', prefix: 'crm/' } } } });
  const ctx = { proposed: new Set(['snap_p']), engines: { c_pg: 'postgres', c_ora: 'oracle' } as Record<string, string> };
  const all = [review, running, failed];
  it('puts every job in one status bucket', () => {
    expect([review, running, failed].map((j) => statusBucket(j, ctx.proposed))).toEqual(['review', 'running', 'failed']);
    expect(statusBucket(job({ state: 'canceled' }), ctx.proposed)).toBe('canceled');
    expect(statusBucket(job({ state: 'succeeded' }), ctx.proposed)).toBe('completed');
  });
  it('filters by status, search, engine, mode, write mode and owner', () => {
    expect(filterJobs(all, NO_FILTERS, ctx)).toHaveLength(3);
    expect(filterJobs(all, { ...NO_FILTERS, status: 'review' }, ctx).map((j) => j.id)).toEqual(['j_review']);
    expect(filterJobs(all, { ...NO_FILTERS, q: 'chg-48213' }, ctx).map((j) => j.id)).toEqual(['j_run']);
    expect(filterJobs(all, { ...NO_FILTERS, q: 'applicants' }, ctx).map((j) => j.id)).toEqual(['j_review']);
    expect(filterJobs(all, { ...NO_FILTERS, engine: 'oracle' }, ctx).map((j) => j.id)).toEqual(['j_fail']);
    expect(filterJobs(all, { ...NO_FILTERS, mode: 'synthesize' }, ctx).map((j) => j.id)).toEqual(['j_fail']);
    expect(filterJobs(all, { ...NO_FILTERS, writeMode: 'files' }, ctx).map((j) => j.id)).toEqual(['j_fail']);
    expect(filterJobs(all, { ...NO_FILTERS, owner: 'bank' }, ctx).map((j) => j.id)).toEqual(['j_run']);
    expect(ownerOf(failed)).toBe('CRM');
  });
  it('keeps the last samples of a client-side ring buffer', () => {
    let buf: [string, number][] = [];
    for (let i = 0; i < 65; i++) buf = pushSample(buf, [`t${i}`, i], 60);
    expect(buf).toHaveLength(60);
    expect(buf[0]).toEqual(['t5', 5]);
    expect(buf[59]).toEqual(['t64', 64]);
  });
});

describe('job detail helpers', () => {
  it('highlights the stepper step of the job phase', () => {
    expect(stepperState(job({ state: 'running', phase: 'prepare' }))).toBe('target');
    expect(stepperState(job({ state: 'waiting', phase: 'partitions' }))).toBe('run');
    expect(stepperState(job({ state: 'queued', phase: 'finalize' }))).toBe('evidence');
    expect(stepperState(job({ state: 'succeeded', phase: 'finalize' }))).toBe('done');
    expect(stepperState(job({ kind: 'discover', state: 'running', phase: 'discover' }))).toBe('discover');
    expect(stepperState(job({ kind: 'discover', state: 'succeeded', phase: 'discover' }))).toBe('review');
  });
  it('sizes partition bars from the per-partition share of the estimate', () => {
    const j = job({ progress: { rows_estimated: 1000, partitions_total: 4 } });
    const p = { partition_no: 0, state: 'running' as const, rows_read: 0, rows_written: 125, rows_skipped: 0, batch_no: 1, attempts: 1, resumed: 0, checkpoint_fp: null, started_at: null, finished_at: null, updated_at: '', error_code: null, rows_per_second: null };
    expect(partitionPct(p, j)).toBe(50);
    expect(partitionPct({ ...p, rows_written: 900 }, j)).toBe(100);
    expect(partitionPct({ ...p, state: 'done', rows_written: 10 }, j)).toBe(100);
    expect(partitionPct(p, job())).toBe(0);
  });
  it('shortens catalogue types and method params for the snapshot table', () => {
    expect(shortType('character varying(120)')).toBe('varchar(120)');
    expect(shortType('timestamp with time zone')).toBe('timestamptz');
    expect(shortType('bigint')).toBe('bigint');
    expect(methodText({ method: 'phone', params: { keep_country_code: true, leading_zero: 'preserve' } })).toBe('phone · keep_country_code');
  });
  it('sums the evidence-so-far tiles from value-free counters', () => {
    const e = evidenceSoFar(job({ progress: { rows_written: 40, partitions_total: 4, partitions_done: 3, partitions_failed: 1,
      values_by_method: { det_token: 30, keep: 50, fpe_digits: 10, 'fallback:fpe_domain_too_small': 4, 'fallback:fpe_disabled': 1, 'col:email': 30, 'verify:hits': 0 },
      verification: { checked: 40, hits: 0 } } }));
    expect(e).toMatchObject({ rows: 40, values: 40, hits: 0, fallbacks: 5, dominantFallback: 'fpe_domain_too_small', done: 3, total: 4, failed: 1 });
  });
});
