// Evidence pack (mockup 10, Task 21): synthetic section, policy version, column decisions,
// regulatory callouts by label, downloads and print.
import { describe, expect, it } from 'vitest';
import { FidelityPanel } from '../components/FidelityPanel';
import * as fx from '../dev/fixtures';
import type { ClassificationSnapshot, ColumnClassification, EvidenceOut } from '../lib/types';
import { html, occurrences, text } from '../test/render';
import { EvidenceSheet } from './EvidencePack';

const NOW = new Date('2026-10-13T10:15:00Z');
const packs = fx.evidencePacks(NOW);
const noop = async (): Promise<{ blob: Blob; filename: string }> => ({ blob: new Blob([]), filename: 'x' });

describe('EvidenceSheet', () => {
  it('offers Download JSON, Download PDF and Print', () => {
    const out = html(<EvidenceSheet ev={packs.ev_01K7ZQ9QA1} job={null} snapshot={null} onJson={noop} onPdf={noop} />);
    const t = text(out);
    expect(t).toContain('Download JSON');
    expect(t).toContain('Download PDF');
    expect(t).toContain('Print');
  });

  it('shows the policy with its version and the key custody line', () => {
    const t = text(html(<EvidenceSheet ev={packs.ev_01K7ZQ9QA1} job={null} snapshot={null} onJson={noop} onPdf={noop} />));
    expect(t).toContain('gdpr-pseudonymise v3');
    expect(t).toContain('Key custody');
    expect(t).toContain('returned to the requesting team at job creation; DPS stored no key');
  });

  it('reads type, confidence and decision from the pack columns', () => {
    const out = html(<EvidenceSheet ev={packs.ev_01K7ZQ9QA1} job={null} snapshot={null} onJson={noop} onPdf={noop} />);
    const t = text(out);
    expect(t).toContain('NUMBER(12)');
    expect(t).toContain('0.97');
    expect(occurrences(out, '<b>overridden</b>')).toBe(1);
    expect(t).toContain('pseudonymised, and pseudonymised data is still personal data');
    expect(t).not.toContain('Synthetic fidelity and privacy');
  });

  it('renders the synthetic section and the measured-not-claimed callout for a synthesize pack', () => {
    const t = text(html(<EvidenceSheet ev={packs.ev_01K7ZQS7B2} job={null} snapshot={null} onJson={noop} onPdf={noop} />));
    expect(t).toContain('6 Synthetic fidelity and privacy');
    expect(t).toContain('The synthetic report is a measurement, not a claim.');
    expect(t).not.toContain('pseudonymised data is still personal data');
  });
});

// S1-M3 r1 (privacy-console B1): every MongoDB leaf shares its top-level field's name, so the
// sheet labels a column by its path — the kept note names the leaves really kept, not "contact".
describe('EvidenceSheet for a MongoDB pack', () => {
  const keep = { method: 'keep', params: {} };
  const leaf = (path: string, entity: string | null, text_column = false): ColumnClassification => ({
    path, name: path.replace(/^\$\./, '').split(/[.[]/)[0], data_type: 'string', kind: 'text', is_key: path === '$._id',
    entity_type: entity, confidence: entity ? 0.97 : 0, sensitivity: null, detectors: [], value_hit_rate: entity ? 0.97 : 0,
    null_rate: 0, distinct_ratio: 1, shapes: [], text_column, anchor: false, suggested: keep, method: keep, override: false,
    synthesize_suggested: null, synthesize_method: null, type_note: null,
  });
  const base = packs.ev_01K7ZQ9QA1;
  const mdb: EvidenceOut = {
    ...base,
    pack: {
      ...base.pack,
      source: { ...base.pack.source, engine: 'mongo', object: 'crm.profiles' },
      target: { ...base.pack.target, engine: 'mongo', object: 'crm_uat.profiles' },
      columns: [
        { path: '$.contact.email', entity_type: 'EMAIL', method: 'email', params: {}, reversible: true, values: 3469, confidence: 0.99, override: false },
        { path: '$.contact.mobile', entity_type: 'PHONE', method: 'fpe_digits', params: {}, reversible: true, values: 3469, confidence: 0.98, override: false },
        { path: '$.customerId', entity_type: 'CUSTOMER_ID', method: 'fpe_digits', params: {}, reversible: true, values: 3469, confidence: 1, override: false },
      ],
    },
  };
  const snapshot = {
    id: 'snap_mdb', tenant_id: 't1', job_id: null, connection_id: 'c_mdb', engine: 'mongo', object_ref: 'crm.profiles', status: 'accepted',
    policy_name: 'gdpr-pseudonymise', policy_hash: 'h', shape: { object: { owner: null, name: 'profiles' }, columns: [], key: ['_id'], key_source: 'primary_key', row_estimate: null },
    columns: [
      leaf('$._id', null), leaf('$.contact.email', 'EMAIL'), leaf('$.contact.mobile', 'PHONE'), leaf('$.contact.addresses[*].type', null),
      leaf('$.customerId', 'CUSTOMER_ID'), leaf('$.kyc.verifiedAt', null), leaf('$.segment', null),
    ],
    sample_rows: 500, overrides: [], content_hash: 'c', created_at: NOW.toISOString(), accepted_at: NOW.toISOString(), accepted_by: 'jobs',
  } as unknown as ClassificationSnapshot;
  const t = text(html(<EvidenceSheet ev={mdb} job={null} snapshot={snapshot} onJson={noop} onPdf={noop} />));

  it('labels each protected leaf by its path, never by the shared field name', () => {
    expect(t).toContain('contact.email');
    expect(t).toContain('contact.mobile');
    expect(t).toContain('customerId');
  });

  it('names the leaves actually kept in the kept note', () => {
    const note = t.slice(t.indexOf('columns kept, no entity:'), t.indexOf('Discovery sampled'));
    expect(note).toContain('contact.addresses[].type');
    expect(note).toContain('kyc.verifiedAt');
    expect(note).toContain('_id');
    // a bare field name ends at a comma or the sentence's full stop, never at a path dot
    expect(note).not.toMatch(/(?:^|[:,] )contact(?:,|\.(?:\s|$))/);
    expect(note).not.toMatch(/(?:^|[:,] )kyc(?:,|\.(?:\s|$))/);
  });
});

describe('FidelityPanel', () => {
  const p = packs.ev_01K7ZQS7B2.pack;
  const out = html(<FidelityPanel fidelity={p.fidelity ?? null} privacy={p.privacy ?? null} utility={p.utility ?? null} tabular={p.tabular ?? null} rowsWritten={500_000} />);
  const t = text(out);
  it('leads with the engine and the seed label', () => {
    expect(t).toContain('Gaussian copula');
    expect(t).toContain('The synthesis seed went back to the caller and is not in this pack.');
  });
  it('tabulates fidelity per column against the limit', () => {
    expect(t).toContain('RISK_SCORE KS 0.021');
    expect(t).toContain('SEGMENT TVD 0.009');
    expect(t).toContain('−0.2 pp');
    expect(out).toContain('style="width:42%"');
    expect(t).toContain('All 10 columns are within the 0.05 limit');
    expect(t).toContain('Summary score 0.96');
    expect(t).toContain('ACCOUNT_OPENED_BRANCH');
  });
  it('draws the lower-triangle correlation delta grid with a ramp and note', () => {
    expect(occurrences(out, 'role="cell"')).toBe(25); // 5 × 5 lower triangle incl. blanks
    expect(out).toContain('title="AVG_BALANCE_90D × CREDIT_LIMIT: |Δ| 0.058"');
    expect(out).toContain('color-mix(in srgb, var(--ink-2) 26%, var(--surface))');
    expect(t).toContain('0.00');
    expect(t).toContain('0.10 limit');
    expect(t).toContain('Largest change 0.058 for AVG_BALANCE_90D × CREDIT_LIMIT. Mean 0.019 across 15 pairs.');
  });
  it('shows the privacy and utility cards', () => {
    expect(t).toContain('Exact-row matches enforced 0 of 500,000');
    expect(t).toContain('Nearest-neighbour distance ratio pass · ≥ 1.00 1.04');
    expect(t).toContain('0.8% vs 2.9% in source');
    expect(t).toContain('Membership-inference proxy pass · AUC ≤ 0.60 0.52');
    expect(t).toContain('Utility');
    expect(t).toContain('0.95');
    expect(t).toContain('SEGMENT');
  });
  it('says privacy was not measured when tabular synthesis was off', () => {
    const none = packs.ev_01K6KNON99.pack;
    const t2 = text(html(<FidelityPanel fidelity={null} privacy={none.privacy ?? null} utility={null} tabular={null} rowsWritten={12_400} />));
    expect(t2).toContain('Privacy not measured — non-sensitive columns were copied from the source (tabular synthesis off)');
    expect(t2).not.toContain('Exact-row matches');
  });
  it('reports a membership-inference proxy that did not run, with its reason', () => {
    const priv = { ...p.privacy!, membership_inference: { run: false, reason: 'holdout too small: 40 rows' } };
    const t3 = text(html(<FidelityPanel fidelity={p.fidelity ?? null} privacy={priv} utility={{ run: false, reason: 'no utility label nominated' }} tabular={p.tabular ?? null} rowsWritten={1} />));
    expect(t3).toContain('Membership-inference proxy not run');
    expect(t3).toContain('holdout too small: 40 rows');
    expect(t3).toContain('no utility label nominated');
  });
});
