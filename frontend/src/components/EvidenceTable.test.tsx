// Evidence packs on Reports & audit (mockup 09): what a stopped run wrote (S2 console r3 B1).
import { describe, expect, it } from 'vitest';
import type { EvidenceRow } from '../lib/reports';
import { html, text } from '../test/render';
import { EvidenceTable } from './EvidenceTable';

const dl = async (): Promise<{ blob: Blob; filename: string }> => ({ blob: new Blob([]), filename: 'x' });
const downloads = { packJson: dl, packPdf: dl, rollup: dl };

/** A copy-to-table pack of the smoke run, as `/reports/evidence` lists it (value-free summary). */
function pack(outcome: string, rows_written: number | null, verification_status: string): EvidenceRow {
  return {
    kind: 'pack', created_at: '2026-10-15T03:05:00Z',
    item: {
      id: 'ev_01M4YPDBREXBJNANPE92V6DKGM', job_id: 'j_01M4YPD09E5MX92DEKJS9MC8TA', job_kind: 'run', outcome, created_at: '2026-10-15T03:05:00Z', pack_sha256: '', layer: 'conn',
      summary: {
        mode: 'obfuscate', policy: 'gdpr-pseudonymise', ticket: 'SMOKE-0001', source: 'bank.customers', target: 'bank_uat.customers_smoke', write_mode: 'table',
        rows_written, verification_passed: false, hits: 0, verification_status,
      },
    },
  };
}

function row(r: EvidenceRow): string {
  return text(html(<EvidenceTable rows={[r]} downloads={downloads} hasOlder={false} busy={false} onOlder={() => undefined} total={null} />));
}

describe('EvidenceTable', () => {
  it('says how many rows a stopped run wrote before the sweep stopped it, and seals it failed closed', () => {
    // the run committed 9 batches, then the leakage sweep refused the 10th (contract §0 rule 2:
    // the batch is the unit that fails closed); the target was truncated and holds those rows
    const t = row(pack('failed', 9000, 'failed'));
    expect(t).toContain('SMOKE-0001 · table · stopped · 9,000 rows written before it');
    expect(t).not.toContain('nothing written');
    expect(t).toContain('failed closed');
    expect(t).not.toContain('not verified · job failed');
  });
  it('says nothing was written only when nothing was', () => {
    expect(row(pack('failed', 0, 'incomplete'))).toContain('SMOKE-0001 · table · stopped, nothing written');
    expect(row(pack('failed', null, 'incomplete'))).toContain('SMOKE-0001 · table · stopped, nothing written');
  });
  it('counts the rows a canceled run wrote', () => {
    const t = row(pack('canceled', 14000, 'incomplete'));
    expect(t).toContain('SMOKE-0001 · table · 14,000 rows · canceled');
    expect(t).not.toContain('nothing written');
    expect(t).toContain('not verified · job canceled');
  });
});
