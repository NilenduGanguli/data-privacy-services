// Evidence pack (mockup 10, Task 21): synthetic section, policy version, column decisions,
// regulatory callouts by label, downloads and print.
import { describe, expect, it } from 'vitest';
import { FidelityPanel } from '../components/FidelityPanel';
import * as fx from '../dev/fixtures';
import { html, occurrences, text } from '../test/render';
import { SCAN_PACK } from '../test/scanPack';
import type { EvidenceOut } from '../lib/types';
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

describe('EvidenceSheet for a UI scan (M4 merged into the M5 sheet)', () => {
  const scanEv = { id: 'ev_01SCANPACK', job_id: 's_01SCANJOB', pack: SCAN_PACK, pack_sha256: '2a16', artefacts: [] } as unknown as EvidenceOut;
  it('titles the scanned origin and claims no table run', () => {
    const t = text(html(<EvidenceSheet ev={scanEv} job={null} snapshot={null} onJson={noop} onPdf={noop} />));
    expect(t).toContain('UI scan demo-site:8410 → captures and exports');
    expect(t).toContain('0 submissions · 0 non-GET allowed');
    expect(t).toContain('artefacts only, nothing written to the scanned site');
    expect(t).toContain('Owns the scanned application and its registered host.');
    expect(t).not.toContain('Classification snapshot');
    expect(t).not.toContain('Leakage sweep');
  });
});
