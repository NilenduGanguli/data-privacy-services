import { describe, expect, it } from 'vitest';
import { buildRunSpec, canConfirmWrite, draftForStorage, fmtCount, fmtDuration, heatTone, jobStatus, jobsCsv, needsDecision, progressPct, sparkPath, splitCounters, writeModeLabel } from './jobs';
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
