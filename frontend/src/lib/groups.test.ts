import { describe, expect, it } from 'vitest';
import { column, job } from './fixtures';
import {
  ENGINE_BADGE, formLabel, groupKeyFiles, JOIN_PREFERENCE, joinsText, memberFields, pairLabel, pickGroup, problemText,
  sharedEntities, targetOf,
} from './groups';
import type { ClassificationSnapshot, ColumnClassification, ConsistencyCheckResult, JobGroup, MemberResult } from './types';

function snap(columns: ColumnClassification[], key: string[] = []): ClassificationSnapshot {
  return {
    id: 'snap_x', tenant_id: 't1', job_id: null, connection_id: 'c_1', engine: 'postgres', object_ref: 'x.y', status: 'accepted',
    policy_name: 'gdpr-pseudonymise', policy_hash: 'h', columns, sample_rows: 100, overrides: [], content_hash: 'c',
    created_at: '2026-10-12T09:00:00+00:00', accepted_at: null, accepted_by: null,
    shape: { object: { owner: null, name: 'y' }, columns: [], key, key_source: key.length ? 'primary_key' : 'none', row_estimate: 1 },
  };
}

const col = (path: string, entity: string | null, method = 'det_token', extra: Partial<ColumnClassification> = {}): ColumnClassification =>
  column({ path, name: path.replace(/^\$\./, '').split(/[.[]/)[0], entity_type: entity, method: { method, params: {} }, ...extra });

const ORA = snap([
  col('$.CUSTOMER_ID', 'CUSTOMER_ID', 'fpe_digits', { is_key: true }),
  col('$.FULL_NAME', 'PERSON_NAME'),
  col('$.EMAIL', 'EMAIL', 'email'),
  col('$.NOTES', 'FREE_TEXT', 'det_token', { text_column: true }),
  col('$.SEGMENT', null, 'keep'),
], ['CUSTOMER_ID']);
const MDB = snap([
  col('$._id', null, 'keep', { is_key: true }),
  col('$.customerId', 'CUSTOMER_ID', 'fpe_digits'),
  col('$.profile.fullName', 'PERSON_NAME'),
  col('$.contact.email', 'EMAIL', 'email'),
  col('$.accounts[*].iban', 'IBAN'),
  col('$.notes', 'FREE_TEXT', 'det_token', { text_column: true }),
]);
const PG = snap([
  col('$.customer_id', 'CUSTOMER_ID', 'fpe_digits', { is_key: true }),
  col('$.full_name', 'PERSON_NAME'),
  col('$.email', 'EMAIL', 'email'),
  col('$.notes', 'FREE_TEXT', 'det_token', { text_column: true }),
], ['customer_id']);

function member(m: Partial<MemberResult> = {}): MemberResult {
  return {
    job_id: 'j_1', engine: 'oracle', connection_id: 'c_1', object: 'CRM_MASKED.CUSTOMERS', path: '$.CUSTOMER_ID',
    entity_type: 'CUSTOMER_ID', method: 'fpe_digits', params_hash: 'abc', form: 'numeric', width: null, prefix_len: null,
    found: 500, anchor: false, ...m,
  };
}

function group(g: Partial<JobGroup> = {}): JobGroup {
  return { id: 'g_1', tenant_id: 't1', name: 'crm-uat-refresh', kid: 'k_1', seed_fingerprint: 'f', created_by: null, created_at: '2026-10-10T09:00:00+00:00', closed_at: null, ...g };
}

describe('sharedEntities', () => {
  it('lists the entities mapped in at least two members, join preference first', () => {
    expect(sharedEntities([ORA, MDB, PG])).toEqual(['CUSTOMER_ID', 'EMAIL', 'PERSON_NAME']);
    expect(JOIN_PREFERENCE[0]).toBe('CUSTOMER_ID');
  });
  it('is empty when only one member maps an entity', () => {
    expect(sharedEntities([MDB, null])).toEqual([]);
    expect(sharedEntities([snap([col('$.a', 'IBAN')]), snap([col('$.b', 'EMAIL')])])).toEqual([]);
  });
  it('never counts keep columns or free text', () => {
    const a = snap([col('$.a', 'EMAIL', 'keep'), col('$.n', 'FREE_TEXT', 'det_token', { text_column: true })]);
    const b = snap([col('$.b', 'EMAIL'), col('$.m', 'FREE_TEXT', 'det_token', { text_column: true })]);
    expect(sharedEntities([a, b])).toEqual([]);
    expect(sharedEntities([ORA, MDB, PG])).not.toContain('FREE_TEXT');
  });
});

describe('memberFields', () => {
  it('shows document paths without $. and [*] as []', () => {
    expect(memberFields(MDB, ['CUSTOMER_ID', 'PERSON_NAME'])).toEqual(['customerId', 'profile.fullName']);
    const arr = snap([col('$.accounts[*].iban', 'IBAN')]);
    expect(memberFields(arr, ['IBAN'])).toEqual(['accounts[].iban']);
  });
  it('keeps the shared order and skips entities the member does not map', () => {
    expect(memberFields(ORA, ['CUSTOMER_ID', 'EMAIL', 'PERSON_NAME', 'IBAN'])).toEqual(['CUSTOMER_ID', 'EMAIL', 'FULL_NAME']);
    expect(memberFields(null, ['CUSTOMER_ID'])).toEqual([]);
  });
});

describe('formLabel', () => {
  it('names each join form', () => {
    expect(formLabel(member({ form: 'numeric' }))).toBe('number');
    expect(formLabel(member({ form: 'prefixed_digits', prefix_len: 5, width: 8 }))).toBe('prefixed · 5-char prefix · 8 digits');
    expect(formLabel(member({ form: 'prefixed_digits', prefix_len: 5, width: null }))).toBe('prefixed · 5-char prefix');
    expect(formLabel(member({ form: 'exact' }))).toBe('text');
    expect(formLabel(member({ form: 'unknown' }))).toBe('unknown');
  });
});

describe('pairLabel and badges', () => {
  it('names the engines of a pair', () => {
    expect(pairLabel(member({ engine: 'oracle' }), member({ engine: 'mongo' }))).toBe('Oracle ↔ MongoDB');
    expect(ENGINE_BADGE.postgres).toBe('PG');
  });
});

describe('problemText', () => {
  it('explains a code in words and names the member object', () => {
    const members = [member(), member({ object: 'profiles_drift', engine: 'mongo' })];
    expect(problemText({ code: 'method_mismatch', member: null, pair: null }, members)).toBe(
      'The join columns are not protected with the same method and parameters in every member.',
    );
    expect(problemText({ code: 'join_form_mismatch', member: 1, pair: null }, members)).toMatch(/^profiles_drift: /);
    expect(problemText({ code: 'something_new', member: null, pair: null }, members)).toBe('something_new');
  });
});

describe('targetOf', () => {
  it('reads the target, falling back to the source for in-place specs', () => {
    const table = job({ spec: { source: { connection_id: 'c_src', object: { owner: 'CRM', name: 'CUSTOMERS' } },
      target: { write_mode: 'table', connection_id: 'c_tgt', object: { owner: 'CRM_MASKED', name: 'CUSTOMERS' } } } });
    expect(targetOf(table)).toEqual({ connectionId: 'c_tgt', object: 'CRM_MASKED.CUSTOMERS' });
    const inPlace = job({ spec: { source: { connection_id: 'c_src', object: { owner: null, name: 'profiles' } }, target: { write_mode: 'in_place' } } });
    expect(targetOf(inPlace)).toEqual({ connectionId: 'c_src', object: 'profiles' });
  });
});

describe('pickGroup', () => {
  const groups = [group({ id: 'g_new', created_at: '2026-10-12T09:00:00+00:00' }), group({ id: 'g_old', created_at: '2026-10-01T09:00:00+00:00' })];
  it('prefers the requested group, then the most recent with members, then the most recent', () => {
    const jobs = [job({ group_id: 'g_old' })];
    expect(pickGroup(groups, jobs, 'g_old')).toBe('g_old');
    expect(pickGroup(groups, jobs, 'g_missing')).toBe('g_old');
    expect(pickGroup(groups, jobs, null)).toBe('g_old');
    expect(pickGroup(groups, [], null)).toBe('g_new');
    expect(pickGroup([], [], 'g_x')).toBeNull();
  });
});

describe('joinsText', () => {
  it('summarises the last check', () => {
    const check = { consistent_pairs: 3, total_pairs: 3, passed: true } as ConsistencyCheckResult;
    expect(joinsText(group({ last_check: check }))).toEqual({ text: '3 / 3 consistent', tone: 'ok' });
    expect(joinsText(group({ last_check: { ...check, consistent_pairs: 1, passed: false } }))).toEqual({ text: '1 / 3 consistent', tone: 'bad' });
    expect(joinsText(group())).toEqual({ text: 'not checked', tone: 'none' });
    expect(joinsText(null)).toEqual({ text: 'not checked', tone: 'none' });
  });
});

describe('groupKeyFiles', () => {
  it('builds the key and seed files the wizard loads, with no other secret', () => {
    const made = {
      group: group(), key_material: { alg: 'aes', kid: 'k_1', dek: 'DEKSECRET' } as unknown as Record<string, unknown>,
      synthesis_seed: { alg: 'x', seed: 'SEEDSECRET' },
    };
    const files = groupKeyFiles(made);
    expect(files.key.name).toBe('crm-uat-refresh.dpskey');
    expect(files.seed.name).toBe('crm-uat-refresh.dpsseed');
    const key = JSON.parse(files.key.text) as Record<string, any>;
    const seed = JSON.parse(files.seed.text) as Record<string, any>;
    expect(key.key_material.kid).toBe('k_1');
    expect(key.group).toEqual({ id: 'g_1', name: 'crm-uat-refresh', kid: 'k_1' });
    expect(files.key.text).not.toContain('SEEDSECRET');
    expect(files.seed.text).not.toContain('DEKSECRET');
    expect(seed.synthesis_seed.seed).toBe('SEEDSECRET');
  });
});
