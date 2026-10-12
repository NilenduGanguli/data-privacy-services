import { describe, expect, it } from 'vitest';
import { buildRunSpec, confirmationKey, countKey, draftForStorage, fqn, isWriteConfirmed, writtenObject, type WizardDraft } from '../../lib/jobs';
import { draft } from '../../lib/fixtures';
import type { ColumnInfo, TableShape } from '../../lib/types';
import { canLeave, isSubmitted, reachable, wizardReducer, type WizardAction } from './state';

// The D3 write confirmation (spec §7, contract §14): In place and Append are confirmed with the row count
// the selection matches, and a confirmation covers exactly what the operator read in the dialog.

const col = (name: string, over: Partial<ColumnInfo> = {}): ColumnInfo => ({
  path: `$.${name}`, name, data_type: 'text', kind: 'text', length: null, precision: null, scale: null, ts_precision: null,
  nullable: true, is_key: false, has_default: false, is_identity: false, ...over,
});
const shape: TableShape = {
  object: { owner: 'bank', name: 'customers' }, key: ['customer_id'], key_source: 'primary_key', row_estimate: 1948210,
  columns: [col('customer_id', { kind: 'integer', data_type: 'bigint', is_key: true, nullable: false }), col('email'), col('phone'), col('segment')],
};

function base(over: Partial<WizardDraft> = {}): WizardDraft {
  return draft({ owner: 'bank', table: 'customers', shape, writeMode: 'in_place', ...over });
}
const run = (d: WizardDraft, ...actions: WizardAction[]): WizardDraft => actions.reduce(wizardReducer, d);
/** The `POST …/count` answer for the draft's current selection. */
const counted = (d: WizardDraft, rows: number): WizardDraft =>
  wizardReducer(d, { type: 'counted', count: { rows, checks: [], runs_as: null, binds: 0 }, at: '2026-10-12T09:00:00Z', seconds: 0.2, key: countKey(d) });
/** The operator types the written object's name in the dialog rendered for this draft. */
const confirm = (d: WizardDraft): WizardDraft =>
  wizardReducer(d, { type: 'confirm', typed: fqn(writtenObject(d)), at: '2026-10-12T09:01:00Z', key: confirmationKey(d) });

describe('write confirmation needs the row count (B1)', () => {
  it('case A: an unfiltered in-place selection cannot be confirmed before it is counted', () => {
    const d = confirm(base());
    expect(d.rows).toBeNull();
    expect(isWriteConfirmed(d)).toBe(false);
    expect(canLeave(4, d)).toBe(false);
    expect(d.typed).toBe('');
    expect(buildRunSpec(d).target).toMatchObject({ write_mode: 'in_place', confirm_write: false });
  });
  it('an unfiltered selection, once counted, is confirmed with the count and sends expected_rows', () => {
    const d = confirm(counted(base(), 1948210));
    expect(canLeave(4, d)).toBe(true);
    expect(buildRunSpec(d).target).toEqual({ write_mode: 'in_place', confirm_write: true, expected_rows: 1948210 });
  });
  it('append is gated the same way', () => {
    const d = base({ writeMode: 'append', appendTarget: 'same', pkKind: 'offset', pkOffset: '900000000' });
    expect(canLeave(4, confirm(d))).toBe(false);
    const ok = confirm(counted(d, 12));
    expect(canLeave(4, ok)).toBe(true);
    expect(buildRunSpec(ok).target).toMatchObject({ write_mode: 'append', confirm_write: true, expected_rows: 12 });
  });
  it('never sends confirm_write: true without expected_rows', () => {
    // a draft whose typed name matches but which never went through the dialog is not confirmed
    const forged = base({ typed: 'bank.customers', confirmedAt: '2026-10-12T09:01:00Z', rows: null });
    expect(buildRunSpec(forged).target).toMatchObject({ confirm_write: false, expected_rows: null });
    expect(canLeave(4, forged)).toBe(false);
  });
  it('ignores a count answer for a selection that has changed since it was asked for', () => {
    const asked = run(base(), { type: 'filter', text: "country_code = 'IN'" });
    const key = countKey(asked);
    const now = run(asked, { type: 'filter', text: '' });
    const d = wizardReducer(now, { type: 'counted', count: { rows: 12069, checks: [], runs_as: null, binds: 1 }, at: '2026-10-12T09:00:00Z', seconds: 0.2, key });
    expect(d.rows).toBeNull();
    expect(d.count).toBeNull();
  });
  it('drops a count when the row cap changes', () => {
    const d = run(counted(base(), 1948210), { type: 'set', patch: { maxRows: '1000' } });
    expect(d.rows).toBeNull();
  });
  it('a restored draft keeps no row count (its filter is not stored)', () => {
    const d = counted(run(base(), { type: 'filter', text: "country_code = 'IN'" }), 12069);
    const restored = wizardReducer(draft({ owner: '', table: '' }), { type: 'restore', draft: JSON.parse(JSON.stringify(draftForStorage(d))) });
    expect(restored.rows).toBeNull();
    expect(canLeave(4, confirm(restored))).toBe(false);
  });
  it('refuses a confirmation given in a dialog drawn for another draft', () => {
    const d = counted(base(), 1948210);
    const stale = confirmationKey(run(d, { type: 'set', patch: { mode: 'synthesize' } }));
    const out = wizardReducer(d, { type: 'confirm', typed: 'bank.customers', at: '2026-10-12T09:01:00Z', key: stale });
    expect(isWriteConfirmed(out)).toBe(false);
  });
});

describe('a confirmation covers only what it confirmed (B2)', () => {
  const filtered = (): WizardDraft => confirm(counted(run(base(), { type: 'filter', text: "country_code = 'IN'" }), 12069));

  it('case B: clearing the filter after confirming 12,069 rows withdraws the confirmation', () => {
    const before = filtered();
    expect(canLeave(4, before)).toBe(true);
    const d = run(before, { type: 'filter', text: '' });
    expect(d.typed).toBe('');
    expect(d.confirmLapsed).toBe(true);
    expect(canLeave(4, d)).toBe(false);
    expect(buildRunSpec(d).target).toMatchObject({ confirm_write: false });
    // a recount of the whole table does not revive it: the operator confirms the new count
    const recounted = counted(d, 1948210);
    expect(canLeave(4, recounted)).toBe(false);
    expect(buildRunSpec(confirm(recounted)).target).toEqual({ write_mode: 'in_place', confirm_write: true, expected_rows: 1948210 });
  });
  it('editing the filter or switching to a SELECT withdraws it', () => {
    expect(canLeave(4, run(filtered(), { type: 'filter', text: "country_code = 'GB'" }))).toBe(false);
    expect(canLeave(4, run(filtered(), { type: 'selMode', mode: 'query' }))).toBe(false);
  });
  it('case C: switching an obfuscate in-place write to synthesize withdraws it', () => {
    const d = run(filtered(), { type: 'set', patch: { mode: 'synthesize' } });
    expect(canLeave(4, d)).toBe(false);
    expect(buildRunSpec(d)).toMatchObject({ mode: 'synthesize', target: { confirm_write: false } });
    expect(canLeave(4, confirm(d))).toBe(true);
  });
  it('case D: a different column subset withdraws it', () => {
    expect(canLeave(4, run(filtered(), { type: 'columns', columns: ['customer_id', 'email'] }))).toBe(false);
    const subset = confirm(counted(run(base(), { type: 'columns', columns: ['customer_id', 'email'] }), 5));
    expect(canLeave(4, run(subset, { type: 'columns', columns: null }))).toBe(false);
  });
  it('a different batch size, partition count or row cap withdraws an in-place confirmation', () => {
    expect(canLeave(4, run(filtered(), { type: 'set', patch: { batchRows: 5000 } }))).toBe(false);
    expect(canLeave(4, run(filtered(), { type: 'set', patch: { parallelism: 8 } }))).toBe(false);
    expect(canLeave(4, run(filtered(), { type: 'set', patch: { maxRows: '100' } }))).toBe(false);
  });
  it('a different primary-key strategy or destination withdraws an append confirmation', () => {
    const app = confirm(counted(base({ writeMode: 'append', appendTarget: 'same', pkKind: 'offset', pkOffset: '900000000' }), 12));
    expect(canLeave(4, app)).toBe(true);
    expect(canLeave(4, run(app, { type: 'set', patch: { pkOffset: '800000000' } }))).toBe(false);
    expect(canLeave(4, run(app, { type: 'set', patch: { pkKind: 'generate' } }))).toBe(false);
    expect(canLeave(4, run(app, { type: 'set', patch: { appendTarget: 'named', targetOwner: 'bank', targetTable: 'customers_x' } }))).toBe(false);
  });
  it('keeps the confirmation through changes it does not cover', () => {
    const d = run(filtered(), { type: 'step', step: 5 }, { type: 'set', patch: { ticket: 'CHG-48190', purposeNote: 'UAT', keyChoice: 'new', policy: 'gdpr-strict' } });
    expect(isWriteConfirmed(d)).toBe(true);
    expect(d.confirmLapsed).toBe(false);
  });
  it('Undo withdraws it without the lapse note', () => {
    const d = run(filtered(), { type: 'unconfirm' });
    expect(d).toMatchObject({ typed: '', confirmedAt: null, confirmedKey: null, confirmLapsed: false });
  });
  it('the saved draft holds neither the confirmation nor the filter behind it', () => {
    const saved = JSON.stringify(draftForStorage(filtered()));
    expect(saved).not.toContain("'IN'");
    expect(saved).not.toContain('confirmedKey');
    expect(saved).not.toContain('countedKey');
  });
});

describe('a submitted run closes the draft (B3)', () => {
  /** In place on the whole table, counted, confirmed, purpose entered, on step 5. */
  const ready = (): WizardDraft => run(confirm(counted(base(), 40000)), { type: 'step', step: 5 }, { type: 'set', patch: { ticket: 'CHG-48190' } });
  const submitted = (): WizardDraft => wizardReducer(ready(), { type: 'submitted', jobId: 'j_01K7ZQ3R8D5W2N6X4H1VTB7F9Q' });

  it('withdraws the confirmation it used and closes every step', () => {
    expect(isWriteConfirmed(ready())).toBe(true);
    const d = submitted();
    expect(isSubmitted(d)).toBe(true);
    expect(d.submittedJobId).toBe('j_01K7ZQ3R8D5W2N6X4H1VTB7F9Q');
    expect(d.step).toBe(5);
    expect(isWriteConfirmed(d)).toBe(false);
    expect(d).toMatchObject({ typed: '', confirmedAt: null, confirmedKey: null, confirmLapsed: false, keyMaterialJson: '' });
    for (const s of [1, 2, 3, 4] as const) expect(canLeave(s, d)).toBe(false);
    expect(buildRunSpec(d).target).toMatchObject({ write_mode: 'in_place', confirm_write: false });
  });
  it('closes a Copy to a table run too (its key card is as once-only)', () => {
    const d = wizardReducer(base({ writeMode: 'table', step: 5 }), { type: 'submitted', jobId: 'j_2' });
    expect(canLeave(4, d)).toBe(false);
    expect(isSubmitted(d)).toBe(true);
  });
  it('refuses every action except Start a new job: no step change, no re-confirmation, no unlock', () => {
    const d = submitted();
    const again: WizardAction[] = [
      { type: 'step', step: 4 },
      { type: 'step', step: 5 },
      { type: 'confirm', typed: fqn(writtenObject(d)), at: '2026-10-12T09:05:00Z', key: confirmationKey(d) },
      { type: 'set', patch: { submittedJobId: null } },
      { type: 'set', patch: { typed: 'bank.customers', confirmedAt: '2026-10-12T09:05:00Z', confirmedKey: confirmationKey(d) } },
      { type: 'counted', count: { rows: 40000, checks: [], runs_as: null, binds: 0 }, at: '2026-10-12T09:05:00Z', seconds: 0.1, key: countKey(d) },
      { type: 'writeMode', mode: 'append' },
      { type: 'restore', draft: JSON.parse(JSON.stringify(draftForStorage(ready()))) },
      { type: 'submitted', jobId: 'j_other' },
    ];
    for (const a of again) expect(wizardReducer(d, a)).toBe(d);
  });
  it('a second run starts from a new draft and needs a new confirmation', () => {
    const fresh = run(submitted(), { type: 'reset', draftId: 'd_00000002' });
    expect(isSubmitted(fresh)).toBe(false);
    expect(fresh).toMatchObject({ draftId: 'd_00000002', step: 1, rows: null, typed: '', confirmedKey: null });
    // the same in-place target rebuilt from the saved fields and counted again is not confirmed
    const rebuilt = counted(run(fresh, { type: 'set', patch: JSON.parse(JSON.stringify(draftForStorage(ready()))) }), 40000);
    expect(isWriteConfirmed(rebuilt)).toBe(false);
    expect(canLeave(4, rebuilt)).toBe(false);
    expect(reachable(rebuilt)).toBe(4);
    expect(buildRunSpec(rebuilt).target).toMatchObject({ confirm_write: false });
    expect(canLeave(4, confirm(rebuilt))).toBe(true);
  });
  it('the saved draft never holds the submitted job, and a restored one is open', () => {
    expect(JSON.stringify(draftForStorage(submitted()))).not.toContain('submittedJobId');
    const restored = wizardReducer(draft({ owner: '', table: '' }), { type: 'restore', draft: { submittedJobId: 'j_forged' } as Partial<WizardDraft> });
    expect(isSubmitted(restored)).toBe(false);
  });
});
