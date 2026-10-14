import { describe, expect, it } from 'vitest';
import { buildRunSpec, canConfirmWrite, draftForStorage, filterJobs, fmtCount, fmtDuration, heatTone, jobStatus, jobsCsv, NO_FILTERS, needsDecision, ownerOf, progressPct, pushSample, sparkPath, splitCounters, statusBucket, writeModeLabel } from './jobs';
import { evidenceSoFar, methodText, packSelection, packTargetText, recordNoun, shortSha, verificationText, oneWayColumns, overridesFor, partitionPct, planGroups, seedFingerprint, shortType, specPreview, stepperState } from './jobs';
import { confirmationKey, countKey, fqn, keyMethodsOf, runMethodFor, writtenObject, type WizardDraft } from './jobs';
import { decisionReason, keptHitText, keptWithHits, verificationTone } from './jobs';
import { canLeave, reachable, targetReady, transactionsMissing, wizardReducer } from '../pages/wizard/state';
import type { ClassificationSnapshot, Connection, CountOut } from './types';
import { draft, job, column } from './fixtures';

/** The draft after its rows were counted and the write was confirmed in the dialog (as the wizard does). */
function confirmed(d: WizardDraft, rows: number): WizardDraft {
  const c = wizardReducer(d, { type: 'counted', count: { rows, checks: [], runs_as: null, binds: 0 }, at: '2026-10-12T09:00:00Z', seconds: 0.1, key: countKey(d) });
  return wizardReducer(c, { type: 'confirm', typed: fqn(writtenObject(c)), at: '2026-10-12T09:01:00Z', key: confirmationKey(c) });
}

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
  it('puts mixed columns and kept columns with detector hits in front of the reviewer', () => {
    const mixed = column({ entity_type: 'MIXED_PII', confidence: 0.95, type_note: 'mixed_entities', value_entities: ['EMAIL', 'PHONE'] });
    expect(needsDecision(mixed)).toBe(true);
    expect(decisionReason(mixed)).toBe('Personal data of several types in one column (EMAIL, PHONE): every value is replaced whole.');
    const kept = column({ entity_type: null, confidence: 0.36, type_note: 'kept_with_hits', value_entities: ['EMAIL'], value_hit_rate: 0.2 });
    expect(needsDecision(kept) && keptWithHits(kept)).toBe(true);
    expect(decisionReason(kept)).toContain('found EMAIL in 20 % of sampled rows');
    expect(keptHitText(kept)).toBe('20 % hits');
    const clean = column({ entity_type: null, type_note: null, value_entities: [] });
    expect(keptWithHits(clean) || needsDecision(clean)).toBe(false);
    expect(keptHitText(clean)).toBe('—');
  });
  it('never stores secrets, key material or filters in the saved draft', () => {
    const saved = JSON.stringify(draftForStorage(draft({ password: 'pw-123', keyMaterialJson: '{"dek":"x"}', where: "name = 'Ananya'" })));
    expect(saved).not.toContain('pw-123');
    expect(saved).not.toContain('dek');
    expect(saved).not.toContain('Ananya');
  });
  it('builds the run spec and gates the write confirmation', () => {
    const spec = buildRunSpec(confirmed(draft({ writeMode: 'in_place' }), 412560));
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
    expect(e).toMatchObject({ rows: 40, values: 40, hits: 0, keptHits: 0, fallbacks: 5, dominantFallback: 'fpe_domain_too_small', done: 3, total: 4, failed: 1 });
    expect(evidenceSoFar(job({ progress: { verification: { checked: 40, hits: 0, kept_hits: 186 } } })).keptHits).toBe(186);
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
    const t = buildRunSpec(confirmed(draft({ writeMode: 'append', appendTarget: 'same', pkKind: 'offset', pkOffset: '900,000,000' }), 10)).target;
    expect(t).not.toHaveProperty('object');
    expect(t).toMatchObject({ write_mode: 'append', pk_strategy: { kind: 'offset', offset: 900000000 }, confirm_write: true, expected_rows: 10 });
    const named = buildRunSpec(confirmed(draft({ writeMode: 'append', appendTarget: 'named', targetOwner: 'bank_perf', targetTable: 'contacts_x', pkKind: 'generate' }), 7)).target;
    expect(named).toMatchObject({ object: { owner: 'bank_perf', name: 'contacts_x' }, pk_strategy: { kind: 'generate' }, confirm_write: true, expected_rows: 7 });
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
  it("reads the key columns' run methods as the server does (shape.key; synthesize falls back to the suggestion, then surrogate)", () => {
    const snap = {
      shape: { object: { owner: 'bank', name: 'customers' }, columns: [], key: ['customer_id'], key_source: 'primary_key' as const, row_estimate: 10 },
      columns: [
        column({ name: 'customer_id', is_key: true, method: { method: 'fpe_digits', params: {} }, synthesize_method: null, synthesize_suggested: null }),
        column({ name: 'account_number', is_key: true, method: { method: 'det_token', params: {} } }),
        column({ name: 'email' }),
      ],
    };
    expect(keyMethodsOf(snap)).toEqual([{ name: 'customer_id', obfuscate: 'fpe_digits', synthesize: 'surrogate' }]);
    expect(runMethodFor(column({ synthesize_method: null, synthesize_suggested: { method: 'keep', params: {} } }), 'synthesize').method).toBe('keep');
    expect(runMethodFor(column({ method: { method: 'keep', params: {} } }), 'obfuscate').method).toBe('keep');
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
  it('states the selection a pack read: SQL template and binds, or the MongoDB filter template and its literals', () => {
    expect(recordNoun('mongo', 2)).toBe('documents');
    expect(recordNoun('mongo', 1)).toBe('document');
    expect(recordNoun('oracle', 2)).toBe('rows');
    expect(recordNoun('postgres', 1)).toBe('row');
    expect(packSelection({ where_template: 'region = :r', binds: 1, max_rows: 1000, columns: ['a', 'b'] }, 'postgres'))
      .toEqual({ scope: 'region = :r', detail: ' · 1 bound parameter · at most 1,000 rows · 2 columns' });
    expect(packSelection({ query_template: 'select * from t where x = :x', binds: 2 }, 'oracle'))
      .toEqual({ scope: 'select * from t where x = :x', detail: ' · 2 bound parameters' });
    expect(packSelection({ binds: 0 }, 'postgres')).toEqual({ scope: 'all rows', detail: ' · 0 bound parameters' });
    // MongoDB: the '?' template, never "all rows", and every '?' counted as a bound literal
    const one = packSelection({ where_template: null, query_template: null, mongo_filter_template: { segment: '?' }, binds: 0 }, 'mongo');
    expect(one).toEqual({ scope: '{"segment":"?"}', detail: ' · 1 bound literal' });
    expect(one.scope).not.toContain('all rows');
    expect(packSelection({ mongo_filter_template: { $or: [{ 'profile.tier': { $in: ['?', '?'] } }, { age: { $gte: '?' } }] }, binds: 0, max_rows: 50, columns: ['profile.tier'] }, 'mongo'))
      .toEqual({ scope: '{"$or":[{"profile.tier":{"$in":["?","?"]}},{"age":{"$gte":"?"}}]}', detail: ' · 3 bound literals · at most 50 documents · 1 field' });
    expect(packSelection({ mongo_filter_template: null, binds: 0 }, 'mongo')).toEqual({ scope: 'all documents', detail: ' · 0 bound literals' });
    expect(packSelection({ mongo_filter_template: {}, binds: 0 }, 'mongo')).toEqual({ scope: 'all documents', detail: ' · 0 bound literals' });
  });
  it('words the verification outcome without claiming leaks it did not count', () => {
    expect(verificationText(true, 0)).toBe('passed · 0 leaks');
    expect(verificationText(false, 2)).toBe('failed · 2 leaks caught');
    expect(verificationText(false, 1)).toBe('failed · 1 leak caught');
    // a run stopped before its counters held a hit (a preflight leak, a fail-closed check)
    expect(verificationText(false, 0)).toBe('failed closed');
  });
  it('never words a review-required pack as passed (integrated verify r1, B1)', () => {
    expect(verificationText(false, 0, true)).toBe('review required');
    expect(verificationTone(false, 0, true)).toBe('amber');
    expect(verificationText(false, 3, true)).toBe('failed · 3 leaks caught');
    expect(verificationTone(false, 3, true)).toBe('rose');
    expect(verificationTone(true, 0)).toBe('emerald');
    expect(verificationTone(false, 0)).toBe('rose');
  });
});

describe('secret reference codes (SEC-2)', () => {
  it('explains the tenant-scoped secret_ref refusals in words', async () => {
    const { errorText } = await import('./jobs');
    expect(errorText('secret_ref_foreign_tenant')).toMatch(/another tenant/);
    expect(errorText('secret_ref_not_shared')).toMatch(/does not list this tenant/);
    expect(errorText('secret_ref_invalid')).toMatch(/not a valid relative path/);
  });
});

describe('connection change codes (SEC-3)', () => {
  it('explains the endpoint-change refusal in words', async () => {
    const { errorText } = await import('./jobs');
    expect(errorText('connection_endpoint_change_requires_credentials')).toMatch(/credentials again/);
    expect(errorText('admin_scope_required')).toMatch(/changing a production-tagged connection/);
  });
});

describe('the real outcome of a run that did not finish (N3)', () => {
  it('never words a failed or canceled run as passed', () => {
    expect(verificationText(false, 0, false, 'failed', 'length_overflow')).toBe('not verified · job failed (length_overflow)');
    // a pack stored before the fix said passed: the outcome decides
    expect(verificationText(true, 0, false, 'failed', 'length_overflow')).toBe('not verified · job failed (length_overflow)');
    expect(verificationTone(true, 0, false, 'failed')).toBe('rose');
    expect(verificationText(false, 0, false, 'canceled')).toBe('not verified · job canceled');
    expect(verificationTone(false, 0, false, 'canceled')).toBe('slate');
    // a leak caught by the preflight is still "failed closed", and hits still count first
    expect(verificationText(false, 0, false, 'failed', 'leakage')).toBe('failed closed');
    expect(verificationText(false, 2, false, 'failed', 'leakage')).toBe('failed · 2 leaks caught');
    expect(verificationText(true, 0, false, 'succeeded')).toBe('passed · 0 leaks');
    expect(verificationText(true, 0, false, 'dry_run')).toBe('passed · 0 leaks');
    expect(verificationTone(true, 0, false, 'succeeded')).toBe('emerald');
  });
});

describe('bounded columns at discovery (M2 verify r2, issue 2)', () => {
  it('puts a method that cannot fit its column under Needs a decision, with the reason', async () => {
    const { typeNoteShort } = await import('./jobs');
    const overflow = column({ name: 'national_id', data_type: 'character varying(18)', entity_type: 'NATIONAL_ID', confidence: 0.95, type_note: 'length_overflow' });
    expect(needsDecision(overflow)).toBe(true);
    expect(decisionReason(overflow)).toMatch(/character varying\(18\)/);
    expect(decisionReason(overflow)).toMatch(/In place/);
    const kept = column({ name: 'national_id', data_type: 'character varying(18)', entity_type: 'NATIONAL_ID', confidence: 0.95, type_note: 'length_kept' });
    expect(needsDecision(kept)).toBe(true);
    expect(decisionReason(kept)).toMatch(/keeps the length/);
    expect(typeNoteShort('length_overflow')).toBe('output too long for the column');
  });
});

describe('length_overflow in words', () => {
  it('explains the refusal on the jobs page and the evidence errors table', async () => {
    const { errorText } = await import('./jobs');
    expect(errorText('length_overflow')).toMatch(/longer than the column/);
  });
});

describe('Oracle and MongoDB drafts (M3 Task 18b)', () => {
  const mongo = (over: Partial<WizardDraft> = {}): WizardDraft => draft({
    engine: 'mongo', owner: '', table: 'profiles', selMode: 'mongo', targetOwner: '', targetTable: 'profiles_console', ...over,
  });
  it('sends a MongoDB filter as mongo_filter with no owner, WHERE or query', () => {
    const spec = buildRunSpec(mongo({ mongoFilterText: '{"segment": "premium"}' }));
    expect(spec.source.object).toEqual({ owner: null, name: 'profiles' });
    expect(spec.source.selection.mongo_filter).toEqual({ segment: 'premium' });
    expect(spec.source.selection).not.toHaveProperty('where');
    expect(spec.source.selection).not.toHaveProperty('query');
    expect(spec.target.object).toEqual({ owner: null, name: 'profiles_console' });
    expect(buildRunSpec(mongo({ mongoFilterText: '' })).source.selection).toEqual({});
  });
  it('keeps the Oracle owner exactly as listed', () => {
    const spec = buildRunSpec(draft({ engine: 'oracle', owner: 'CRM', table: 'CUSTOMERS', where: "SEGMENT = 'RETAIL'", targetOwner: 'CRM_MASKED', targetTable: 'CUSTOMERS_CONSOLE' }));
    expect(spec.source.object).toEqual({ owner: 'CRM', name: 'CUSTOMERS' });
    expect(spec.source.selection.where).toBe("SEGMENT = 'RETAIL'");
    expect(spec.target.object).toEqual({ owner: 'CRM_MASKED', name: 'CUSTOMERS_CONSOLE' });
  });
  it('never stores the MongoDB filter and shows its literals as <bound>', () => {
    const d = mongo({ mongoFilterText: '{"email": "a@x.in"}' });
    expect(JSON.stringify(draftForStorage(d))).not.toContain('a@x.in');
    const preview = JSON.stringify(specPreview(d));
    expect(preview).not.toContain('a@x.in');
    expect((specPreview(d).source as Record<string, any>).selection.mongo_filter).toEqual({ email: '<bound>' });
  });
  it('counts a MongoDB filter: a new filter drops the count, a new engine clears the source', () => {
    const count: CountOut = { rows: 9, checks: [], runs_as: null, binds: 0 };
    let d = wizardReducer(mongo({ mongoFilterText: '{"a": 1}' }), { type: 'counted', count, at: 'x', seconds: 0.1, key: countKey(mongo({ mongoFilterText: '{"a": 1}' })) });
    expect(d.count).toEqual(count);
    expect(canLeave(2, { ...d, shape: { object: { owner: null, name: 'profiles' }, columns: [], key: ['_id'], key_source: 'primary_key', row_estimate: 10 } })).toBe(true);
    d = wizardReducer(d, { type: 'filter', text: '{"a": 2}' });
    expect(d).toMatchObject({ mongoFilterText: '{"a": 2}', count: null });
    d = wizardReducer(d, { type: 'engine', engine: 'oracle' });
    expect(d).toMatchObject({ engine: 'oracle', connectionId: null, table: '', mongoFilterText: '', selMode: 'where', port: '1521' });
    expect(wizardReducer(d, { type: 'engine', engine: 'mongo' })).toMatchObject({ selMode: 'mongo', port: '27017' });
  });
  it('follows the engine of a saved connection', () => {
    const conn = { id: 'c_m', tenant_id: 'default', name: 'crm-mongo', engine: 'mongo' as const, params: { database: 'crm' }, secret_ref: 'x', has_secret: true,
      secret_expires_at: null, roles: ['source' as const], tags: [], capabilities: { transactions: true }, last_test: null, created_by: null,
      created_at: 'x', updated_at: 'x' };
    const d = wizardReducer(draft({ engine: 'mongo', connectionId: null }), { type: 'useSaved', connection: conn });
    expect(d).toMatchObject({ engine: 'mongo', selMode: 'mongo', connectionId: 'c_m' });
  });
});

describe('MongoDB write modes need transactions (M3 Task 18b)', () => {
  it('blocks In place and Append on a MongoDB connection without transactions', () => {
    const noTx = { ok: true, engine: 'mongo' as const, server_version: '7.0', checks: [], capabilities: { transactions: false }, error_code: null };
    const tx = { ...noTx, capabilities: { transactions: true } };
    const base = { engine: 'mongo' as const, owner: '', table: 'profiles', selMode: 'mongo' as const, connectionRoles: ['source', 'target'] as ('source' | 'target')[] };
    expect(targetReady(draft({ ...base, test: noTx, writeMode: 'in_place' }))).toBe(false);
    // an append needs a complete key strategy as well (offsetProblem); `generate` isolates the transactions rule
    expect(targetReady(draft({ ...base, test: noTx, writeMode: 'append', appendTarget: 'same', pkKind: 'generate' }))).toBe(false);
    expect(targetReady(draft({ ...base, test: tx, writeMode: 'append', appendTarget: 'same', pkKind: 'generate' }))).toBe(true);
    expect(targetReady(draft({ ...base, test: noTx, writeMode: 'table', targetTable: 'profiles_uat' }))).toBe(true);
    expect(transactionsMissing(draft({ ...base, test: noTx, writeMode: 'in_place' }))).toBe(true);
    expect(transactionsMissing(draft({ test: noTx, writeMode: 'in_place' }))).toBe(false);   // PostgreSQL: not a MongoDB rule
  });
});
