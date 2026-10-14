// The evidence pack page for a scan (M4): the pack as dps/layers/scanner/inventory.py::build_scan_evidence
// builds it (job_kind "scan", a `scan` block, no columns, partitions or row counts).
import { describe, expect, it } from 'vitest';
import { render, text } from '../../test/render';
import { SCAN_PACK } from '../../test/scanPack';
import { canonicalJson, isScanJobId, isScanPack, scanEvidenceRow, scanPackTitle, ScanPackSections } from './ScanEvidence';

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

describe('a scan in the evidence list', () => {
  const row = { id: 'ev_1', job_id: 's_01SCANJOB', created_at: '2026-10-13T18:41:21Z', outcome: 'succeeded', mode: 'synthesize', label: 'x',
    source_object: null, target: null, write_mode: null, policy: 'gdpr-pseudonymise', ticket: null, passed: true, hits: 0, rows_written: 0, html_artefact_id: 'art_h' };
  it('is told apart by its job id prefix (dps/ids.py)', () => {
    expect(isScanJobId('s_01SCANJOB')).toBe(true);
    expect(isScanJobId('j_01RUNJOB')).toBe(false);
    expect(isScanJobId('snap_01X')).toBe(false);
  });
  it('links to the scan, names the UI scanner layer and claims no rows or leak count', () => {
    expect(scanEvidenceRow(row)).toEqual({ href: '/scanner/scans/s_01SCANJOB', what: 'UI scan → captures and exports', sub: 'nothing written to the scanned site', layer: 'UI scanner', verification: 'passed' });
    expect(scanEvidenceRow({ ...row, outcome: 'canceled', passed: false, ticket: 'CHG-1' }).sub).toBe('CHG-1 · nothing written to the scanned site · canceled');
  });
});
