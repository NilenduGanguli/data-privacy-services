// The evidence pack page for a scan (M4): the pack as dps/layers/scanner/inventory.py::build_scan_evidence
// builds it (job_kind "scan", a `scan` block, no columns, partitions or row counts).
import { describe, expect, it } from 'vitest';
import { html, render, text } from '../../test/render';
import { SCAN_PACK } from '../../test/scanPack';
import type { EvidenceItem } from '../../lib/types';
import { EvidenceTable } from '../EvidenceTable';
import { canonicalJson, isScanPack, scanPackTitle, ScanPackSections } from './ScanEvidence';

describe('the evidence pack of a scan', () => {
  it('is recognised by its job kind and scan block', () => {
    expect(isScanPack(SCAN_PACK)).toBe(true);
    expect(isScanPack({ ...SCAN_PACK, job_kind: 'run', scan: null })).toBe(false);
  });
  it('is titled by the scanned origin, not by a source table and target', () => {
    expect(scanPackTitle(SCAN_PACK)).toBe('UI scan demo-site:8410 → captures and exports');
  });
  it('shows the scan sections: submissions, non-GET requests, blocked requests, entities, risk flags, validation', () => {
    const t = text(render(<ScanPackSections p={SCAN_PACK} sha="2a16" />));
    expect(t).toContain('Submissions 0');
    expect(t).toContain('Non-GET requests allowed 0');
    expect(t).toContain('71 POST');
    expect(t).toContain('e-mail EMAIL 6');
    expect(t).toContain('A1');
    expect(t).toContain('autocomplete on for SSN');
    expect(t).toContain('/apply/us');
    expect(t).toMatch(/synthesize 61 60 1/);
    expect(t).toContain('http://demo-site:8410/apply');
    expect(t).toContain('never: /logout, /admin');
    // nothing of a table run's vocabulary, and no claim of a leakage sweep the scan finalize does not run
    expect(t).not.toMatch(/rows from|Row counts|Post-write sample|Partition|[Ll]eakage sweep/);
    expect(t).toContain('Stored fill values 37 tokens · 0 hits');
  });
  it('counts refused routes a page ran itself, and asks for review when there are any', () => {
    expect(text(render(<ScanPackSections p={SCAN_PACK} sha="2a16" />))).toMatch(/Never-visit routes 0 run .* pass/);
    const t = text(render(<ScanPackSections p={{ ...SCAN_PACK, scan: { ...SCAN_PACK.scan, scope_routes_reached: 2 } }} sha="2a16" />));
    expect(t).toMatch(/Never-visit routes 2 run .* review/);
  });
  it('says so when a non-GET request was allowed', () => {
    const t = text(render(<ScanPackSections p={{ ...SCAN_PACK, scan: { ...SCAN_PACK.scan, non_get_allowed: 2 } }} sha="2a16" />));
    expect(t).toContain('Non-GET requests allowed 2');
    expect(t).toMatch(/review/);
  });
});

describe('canonicalJson', () => {
  it('matches dps/layers/connectors/evidence.py::canonical_json (sorted keys, compact separators, UTF-8 kept)', () => {
    expect(canonicalJson({ b: 1, a: { d: [2, { z: null, y: 'é' }], c: true } })).toBe('{"a":{"c":true,"d":[2,{"y":"é","z":null}]},"b":1}');
  });
});

describe('a scan in the evidence list (Reports, mockup 09)', () => {
  // GET /reports/evidence summarises a scan pack by the host of its origin and its scan block (S2 console r1 B2)
  const item: EvidenceItem = { id: 'ev_1', job_id: 's_01SCANJOB', job_kind: 'scan', outcome: 'succeeded', created_at: '2026-10-13T18:41:21Z', pack_sha256: 'x', layer: 'ui',
    summary: { mode: 'synthesize', policy: 'gdpr-pseudonymise', policy_version: '1', ticket: null, source: 'demo-site:8410', target: null, verification_passed: true, hits: 0, pages: 21, forms: 7, personas: 3, submissions: 0 } };
  const dl = async (): Promise<{ blob: Blob; filename: string }> => ({ blob: new Blob([]), filename: 'x' });
  const table = (it: EvidenceItem): string => text(html(<EvidenceTable rows={[{ kind: 'pack', created_at: it.created_at, item: it }]} downloads={{ packJson: dl, packPdf: dl, rollup: dl }} hasOlder={false} busy={false} onOlder={() => undefined} total={1} />));
  it('names the scanned host and what the scan did: pages, forms, personas, submissions', () => {
    const t = table(item);
    expect(t).toContain('UI scan of demo-site:8410');
    expect(t).toContain('21 pages · 7 forms · 3 personas · 0 submissions');
    expect(t).not.toContain('UI scan of —');
  });
  it('reads an older server, whose summary has no forms or personas, without inventing them', () => {
    const t = table({ ...item, summary: { ...item.summary, forms: undefined, personas: undefined, pages: 1 } });
    expect(t).toContain('1 page · 0 submissions');
    expect(t).not.toMatch(/forms|personas/);
  });
});
