// Wizard step 4: an Append's key offset or prefix is checked where it is typed (H2 verification,
// programme plan F4 polish). The server needs exactly one of a whole-number offset above 0 or a
// 1-16 character prefix of letters, digits, "_" and "-" (PkOffset); before this check step 4 let
// "0" or "abc" through and step 5 showed the raw validation message; a fraction such as "0.5"
// passed too and was sent as offset 0 (S7 review r1 N3).
import { describe, expect, it } from 'vitest';
import type { WizardDraft } from '../../lib/jobs';
import { draft } from '../../lib/fixtures';
import type { ColumnInfo, TableShape } from '../../lib/types';
import { offsetProblem, targetReady } from './state';

const col = (name: string, over: Partial<ColumnInfo> = {}): ColumnInfo => ({
  path: `$.${name}`, name, data_type: 'text', kind: 'text', length: null, precision: null, scale: null, ts_precision: null,
  nullable: true, is_key: false, has_default: false, is_identity: false, ...over,
});
const numeric: TableShape = {
  object: { owner: 'bank', name: 'transactions' }, key: ['txn_id'], key_source: 'primary_key', row_estimate: 1000,
  columns: [col('txn_id', { kind: 'integer', data_type: 'bigint', is_key: true, nullable: false }), col('note')],
};
const textual: TableShape = { ...numeric, key: ['doc_ref'], columns: [col('doc_ref', { is_key: true, nullable: false }), col('note')] };
const append = (over: Partial<WizardDraft> = {}): WizardDraft =>
  draft({ owner: 'bank', table: 'transactions', shape: numeric, writeMode: 'append', appendTarget: 'same', pkKind: 'offset', ...over });

describe('offsetProblem', () => {
  it('asks for an offset, and refuses one that is not a whole number above 0', () => {
    expect(offsetProblem(append({ pkOffset: '' }))).toMatch(/Enter the key offset/);
    for (const bad of ['0', 'abc', '-5', '  ', '0.5', '1.5', '1e3', '0x10']) expect(offsetProblem(append({ pkOffset: bad })), bad).not.toBeNull();
    for (const good of ['900000000', '900,000,000', '1 000']) expect(offsetProblem(append({ pkOffset: good })), good).toBeNull();
  });
  it('checks a text key prefix like the server does', () => {
    const t = (pkPrefix: string): string | null => offsetProblem(append({ shape: textual, pkPrefix }));
    expect(t('')).toMatch(/Enter the key prefix/);
    expect(t('SYN-')).toBeNull();
    expect(t('bad prefix!')).toMatch(/1 to 16/);
    expect(t('X'.repeat(17))).toMatch(/1 to 16/);
  });
  it('says nothing for other strategies and write modes', () => {
    expect(offsetProblem(append({ pkKind: 'generate', pkOffset: '' }))).toBeNull();
    expect(offsetProblem(append({ writeMode: 'table', pkOffset: '' }))).toBeNull();
  });
  it('keeps step 4 closed until the offset is valid', () => {
    expect(targetReady(append({ pkOffset: 'abc' }))).toBe(false);
    expect(targetReady(append({ pkOffset: '900000000' }))).toBe(true);
    expect(targetReady(append({ pkKind: 'generate', pkOffset: '' }))).toBe(true);
  });
});
