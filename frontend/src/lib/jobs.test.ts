import { describe, expect, it } from 'vitest';
import { buildRunSpec, canConfirmWrite, draftForStorage, filterJobs, fmtCount, fmtDuration, heatTone, jobStatus, jobsCsv, NO_FILTERS, needsDecision, ownerOf, progressPct, pushSample, sparkPath, splitCounters, statusBucket, writeModeLabel } from './jobs';
import { evidenceSoFar, methodText, packTargetText, shortSha, oneWayColumns, overridesFor, partitionPct, planGroups, seedFingerprint, shortType, specPreview, stepperState } from './jobs';
import { canLeave, reachable, wizardReducer } from '../pages/wizard/state';
import type { ClassificationSnapshot, Connection, CountOut } from './types';
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

describe('wizard state (steps 1-2)', () => {
  const conn = (over: Partial<Connection> = {}): Connection => ({
    id: 'c_new', tenant_id: 'default', name: 'bank-dev', engine: 'postgres', params: { host: 'db', port: 5432, database: 'bank' },
    secret_ref: null, has_secret: true, secret_expires_at: null, roles: ['source'], tags: [], capabilities: {}, last_test: null,
    created_by: null, created_at: '2026-10-12T09:00:00Z', updated_at: '2026-10-12T09:00:00Z', ...over,
  });
  const count: CountOut = { rows: 42, checks: [], runs_as: 'SELECT …', binds: 1 };
  it('clears the selection, the check result and the discovery state when a new table is chosen', () => {
    let d = draft({ where: "country_code = 'IN'", count, rows: 42, columns: ['email'], discoverJobId: 'j_d', snapshotId: 'snap_1', snapshotAccepted: true, typed: 'bank_restore.contacts' });
    d = wizardReducer(d, { type: 'table', table: 'accounts' });
    expect(d).toMatchObject({ table: 'accounts', where: '', count: null, rows: null, columns: null, discoverJobId: null, snapshotId: null, snapshotAccepted: false, typed: '', shape: null });
    d = wizardReducer({ ...d, count }, { type: 'filter', text: 'x > 1' });
    expect(d.count).toBeNull();
  });
  it('refuses inline credentials on a production-tagged connection', () => {
    let d = wizardReducer(draft({ connMode: 'new', credMode: 'secret_ref' }), { type: 'production', on: true });
    d = wizardReducer(d, { type: 'credMode', mode: 'inline' });
    expect(d.credMode).toBe('secret_ref');
    d = wizardReducer(wizardReducer(draft({ connMode: 'new', credMode: 'inline', password: 'pw-1' }), { type: 'production', on: true }), { type: 'set', patch: {} });
    expect(d).toMatchObject({ credMode: 'secret_ref', password: '' });
  });
  it('keeps the connection id but no password once the connection is created and tested', () => {
    let d = wizardReducer(draft({ connMode: 'new', credMode: 'inline', connectionId: null }), { type: 'set', patch: { username: 'dps_reader', password: 'pw-secret-9' } });
    d = wizardReducer(d, { type: 'created', connection: conn() });
    d = wizardReducer(d, { type: 'tested', result: { ok: true, engine: 'postgres', server_version: '16', checks: [], capabilities: {}, error_code: null }, at: '2026-10-12T09:00:00Z' });
    const saved = JSON.stringify(draftForStorage(d));
    expect(saved).toContain('"connectionId":"c_new"');
    expect(saved).not.toContain('pw-secret-9');
    expect(d.password).toBe('');
    expect(canLeave(1, d)).toBe(true);
  });
  it('opens the next steps only when the earlier ones are complete', () => {
    const d = draft({ shape: null, test: null });
    expect(reachable(d)).toBe(1);
    expect(canLeave(2, draft({ shape: { object: { owner: 'bank', name: 'customers' }, columns: [], key: ['id'], key_source: 'primary_key', row_estimate: 10 }, where: 'a = 1', count: null }))).toBe(false);
  });
});

describe('review overrides (step 3)', () => {
  const snap = (): ClassificationSnapshot => ({
    id: 'snap_1', tenant_id: 'default', job_id: 'j_d', connection_id: 'c_src', engine: 'postgres', object_ref: 'bank.customers',
    status: 'proposed', policy_name: 'gdpr-pseudonymise', policy_hash: 'h', sample_rows: 2000, content_hash: 'c',
    created_at: '2026-10-12T09:00:00Z', accepted_at: null, accepted_by: null, overrides: [{ path: '$.dob', anchor: true }],
    shape: { object: { owner: 'bank', name: 'customers' }, columns: [], key: ['customer_id'], key_source: 'primary_key', row_estimate: 10 },
    columns: [
      column({ path: '$.customer_id', name: 'customer_id', entity_type: 'CUSTOMER_ID', is_key: true, anchor: false }),
      column({ path: '$.dob', name: 'dob', entity_type: 'DATE_OF_BIRTH', anchor: true }),
      column({ path: '$.email', name: 'email' }),
      column({ path: '$.segment', name: 'segment', entity_type: null, sensitivity: null, suggested: { method: 'keep', params: {} }, method: { method: 'keep', params: {} } }),
    ],
  });
  it('sends only the fields that changed', () => {
    const out = overridesFor(snap(), {
      '$.email': { entity_type: 'EMAIL', method: { method: 'det_token', params: {} }, text_column: false },
      '$.segment': { method: { method: 'keep', params: {} } },
    });
    expect(out).toEqual([{ path: '$.email', method: { method: 'det_token', params: {} } }]);
  });
  it('setting an anchor clears the anchor of every other column', () => {
    const out = overridesFor(snap(), { '$.customer_id': { anchor: true } });
    expect(out).toEqual([{ path: '$.customer_id', anchor: true }, { path: '$.dob', anchor: false }]);
  });
  it('reclassifies a kept column and turns the text pipeline on', () => {
    const out = overridesFor(snap(), { '$.segment': { entity_type: 'ORG_NAME' }, '$.email': { text_column: true } });
    expect(out).toEqual([{ path: '$.segment', entity_type: 'ORG_NAME' }, { path: '$.email', text_column: true }]);
  });
});

describe('run spec per write mode (steps 4-5)', () => {
  const key = JSON.stringify({ alg: 'A256SIV', kid: 'k_group', dek: 'AAAA', kcv: null, wrapped_dek: null, wrap: null, consistency_scope: 'key', created_at: '2026-10-12T09:00:00Z' });
  it('copies to a table without a primary-key strategy', () => {
    const t = buildRunSpec(draft({ writeMode: 'table', tableLoad: 'upsert' })).target;
    expect(t).toMatchObject({ write_mode: 'table', connection_id: 'c_tgt', object: { owner: 'bank_uat', name: 'contacts' }, table_load: 'upsert', create_if_missing: true });
    expect(t).not.toHaveProperty('pk_strategy');
  });
  it('appends to the same table without an object and with the key strategy', () => {
    const t = buildRunSpec(draft({ writeMode: 'append', appendTarget: 'same', pkKind: 'offset', pkOffset: '900,000,000', typed: 'bank_restore.contacts', rows: 10 })).target;
    expect(t).not.toHaveProperty('object');
    expect(t).toMatchObject({ write_mode: 'append', pk_strategy: { kind: 'offset', offset: 900000000 }, confirm_write: true, expected_rows: 10 });
    const named = buildRunSpec(draft({ writeMode: 'append', appendTarget: 'named', targetOwner: 'bank_perf', targetTable: 'contacts_x', pkKind: 'generate', typed: 'bank_perf.contacts_x' })).target;
    expect(named).toMatchObject({ object: { owner: 'bank_perf', name: 'contacts_x' }, pk_strategy: { kind: 'generate' }, confirm_write: true });
  });
  it('exports files without an object', () => {
    const t = buildRunSpec(draft({ writeMode: 'files', filesConnectionId: 'c_s3', filesFormat: 'csv', filesPrefix: 'exports/contacts', filesCompression: 'gzip' })).target;
    expect(t).toEqual({ write_mode: 'files', connection_id: 'c_s3', files: { format: 'csv', prefix: 'exports/contacts', compression: 'gzip' } });
  });
  it('reuses a job group key from the loaded file', () => {
    const spec = buildRunSpec(draft({ keyChoice: 'group', groupId: 'grp_1', keyMaterialJson: JSON.stringify({ group: {}, key_material: JSON.parse(key), synthesis_seed: { alg: 'x', seed: 'c2VlZA==' } }) }));
    expect(spec.group_id).toBe('grp_1');
    expect(spec.consistency.key_material).toMatchObject({ kid: 'k_group' });
    expect(spec.consistency).not.toHaveProperty('synthesis_seed');
    expect(specPreview(draft({ keyChoice: 'group', groupId: 'grp_1', keyMaterialJson: key })).consistency).toEqual({ scope: 'key', group: 'grp_1', kid: 'k_group' });
    expect(JSON.stringify(specPreview(draft({ keyChoice: 'group', groupId: 'grp_1', keyMaterialJson: key })))).not.toContain('AAAA');
  });
  it('never sends key material in synthesize mode', () => {
    const spec = buildRunSpec(draft({ mode: 'synthesize', keyChoice: 'group', groupId: 'grp_1', keyMaterialJson: JSON.stringify({ key_material: JSON.parse(key), synthesis_seed: { alg: 'x', seed: 'c2VlZA==' } }) }));
    expect(spec.consistency).not.toHaveProperty('key_material');
    expect(spec.consistency.synthesis_seed).toMatchObject({ seed: 'c2VlZA==' });
    expect(buildRunSpec(draft({ mode: 'synthesize' })).consistency).toEqual({ scope: 'key' });
  });
  it('lists one-way columns and groups the plan by method', () => {
    const snap = { columns: [column({ name: 'pin', method: { method: 'generalize', params: {} } }), column({ name: 'email' }), column({ name: 'seg', entity_type: null, method: { method: 'keep', params: {} } })] };
    expect(oneWayColumns(snap, 'obfuscate')).toEqual([{ name: 'pin', method: 'generalize' }]);
    expect(planGroups(snap, 'obfuscate')).toEqual({ groups: [['email', ['email']], ['generalize', ['pin']]], kept: ['seg'] });
  });
});

describe('seed fingerprint', () => {
  it('matches the server (first 16 hex of sha256 of the raw seed)', async () => {
    // sha256(b'seed') = 19b25856e1c150ca834cffc8b59b23adbd0ec0389e58eb22b3b64768098d002b
    expect(await seedFingerprint('c2VlZA==')).toBe('19b25856e1c150ca');
    expect(await seedFingerprint('c2VlZA')).toBe('19b25856e1c150ca');
  });
});

describe('evidence pack helpers', () => {
  it('shortens the pack hash and names the target like the pack', () => {
    expect(shortSha('6ec5e6ddf67837f2b91df0fea306b9db0fcd146c58a7d233c79d6bae6ab7d73b')).toBe('6ec5e6dd…d73b');
    expect(packTargetText({ write_mode: 'table', object: 'bank_uat.customers' }, { object: 'bank.customers' })).toBe('bank_uat.customers');
    expect(packTargetText({ write_mode: 'in_place', object: 'bank_restore.contacts' }, { object: 'bank_restore.contacts' })).toBe('bank_restore.contacts (in place)');
    expect(packTargetText({ write_mode: 'files', object: null, files: { format: 'parquet', prefix: 'exports/x' } }, {})).toBe('Parquet files · exports/x');
  });
});
