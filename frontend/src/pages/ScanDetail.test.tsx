// Scan detail (mockup 04): parallel view, field inventory, risk flags, whole-scan inventory, captures —
// rendered from the typed demo scan.
import { describe, expect, it } from 'vitest';
import { CapturesTab } from '../components/scanner/CapturesTab';
import { FieldInventory, ScanInventory } from '../components/scanner/FieldInventory';
import { ParallelView } from '../components/scanner/ParallelView';
import { RiskFlags } from '../components/scanner/RiskFlags';
import { demoScan, inventoryRows, scanJob, summary } from '../lib/scanFixtures';
import { ApiError } from '../lib/api';
import type { ScanPageDetail } from '../lib/types';
import { render, text } from '../test/render';
import { ScanDetail, ScanDetailBody, type DetailState } from './ScanDetail';

const d = demoScan();
const pageDetail = (n: number): ScanPageDetail => ({ page: d.pages.find((p) => p.page_no === n)!, fields: d.fields[n], requests: d.requests[n] });
const state = (over: Partial<DetailState> = {}): DetailState => ({
  scanId: d.job.id, detail: d.detail, pages: d.pages, pageNo: 2, pageDetail: pageDetail(2), inventory: undefined, tab: 'parallel',
  side: 'synthesize', view: 'screens', ...over,
});
const noop = (): void => undefined;
const body = (over: Partial<DetailState> = {}): string => render(<ScanDetailBody s={state(over)} onTab={noop} onSide={noop} onView={noop} onExport={noop} />);

describe('ScanDetail page head', () => {
  const html = body();
  const t = text(html);
  it('names the scan and summarises it in one sentence', () => {
    expect(t).toContain(`Scan ${'s_01K7ZR…1C'} · demo-site:8410`);
    expect(t).toContain(`Crawl finished · ${d.summary.pages_visited} pages crawled · ${d.summary.forms} forms · ${d.summary.fields} fields · ${d.summary.personal_fields} classified as personal data · 5 risk flags · auth: storage state dps/ui/demo-onboarding`);
    expect(t).toContain('DPS never submits forms.');
  });
  it('offers exports and the evidence pack once the scan has them', () => {
    expect(t).toContain('Export inventory');
    expect(html).toMatch(/<button[^>]*>Export personas<\/button>/);
    expect(html).not.toMatch(/disabled=""[^>]*>Export personas/);
    expect(html).toContain(`href="/reports/evidence/${d.summary.evidence_id}"`);
    expect(html).toContain(`href="/scanner/new?scan=${d.job.id}"`);
  });
  it('holds exports and evidence while the scan runs', () => {
    const running = { scan: scanJob({ state: 'running' }), summary: summary({ pages_visited: 4, pages_discovered: 9 }) };
    const h = body({ detail: running });
    expect(text(h)).toContain('Export inventory so far');
    expect(h).toMatch(/disabled=""[^>]*title="The persona export is ready when the scan has finished"[^>]*>Export personas|title="The persona export is ready when the scan has finished"[^>]*disabled=""/);
    expect(h).toMatch(/aria-disabled="true"[^>]*>Evidence pack/);
    expect(text(h)).toContain('Crawl running · 4 of 9 pages');
  });
  it('has the three tabs with the current one selected', () => {
    expect(html).toMatch(/role="tab" aria-selected="true"[^>]*>Parallel view/);
    expect(html).toMatch(/role="tab" aria-selected="false"[^>]*>Inventory/);
    expect(html).toMatch(/role="tab" aria-selected="false"[^>]*>Captures/);
  });
});

describe('Parallel view tab', () => {
  const html = body();
  const t = text(html);
  it('lists pages with forms, rendered personal data and risk flags', () => {
    expect(html).toMatch(/class="on" aria-current="true"[^>]*>.*?\/apply\/identity/);
    expect(t).toContain('Pages with forms');
    expect(t).toContain('Rendered personal data');
    expect(t).toContain('A1 autocomplete on for Aadhaar and PAN /apply/identity');
    expect(t).toContain('A3 PAN, card number and account number rendered unmasked in a table /profile');
  });
  it('shows the two screens: annotated original and the filled side', () => {
    expect(t).toContain('Parallel view · /apply/identity');
    expect(t).toContain('original · as captured');
    expect(t).toContain('filled · synthetic persona 1');
    expect((html.match(/shot-skeleton/g) ?? []).length).toBe(2);
    expect(t).toContain('Client-side validation accepted all 9 values (format evidence recorded).');
    expect(t).toContain('no button is ever pressed');
  });
  it('flips to obfuscated tokens', () => {
    const o = text(body({ side: 'obfuscate' }));
    expect(o).toContain('filled · obfuscated tokens (key held by caller)');
    expect(o).toContain('reversible token');
  });
  it('the Fields view replicates the form: originals are never shown, the filled side shows the fill values', () => {
    const h = render(<ParallelView page={d.pages[1]} fields={d.fields[2]} side="synthesize" view="fields" onSide={noop} onView={noop} origin="http://demo-site:8410" persona={1} />);
    const left = h.slice(0, h.indexOf('filled · synthetic persona 1'));
    for (const f of d.fields[2]) if (f.fill_synthesize) expect(left).not.toContain(f.fill_synthesize);
    expect(text(left)).toContain('(empty)');
    expect(h).toContain('class="f syn"');
    expect(text(h)).toContain('Meera Venkatesan');
    const o = render(<ParallelView page={d.pages[1]} fields={d.fields[2]} side="obfuscate" view="fields" onSide={noop} onView={noop} origin="http://demo-site:8410" persona={1} />);
    expect(text(o)).toContain('Tilqoe Mzvhrpkat');
    expect(o).toContain('class="f obf"');
    expect(o).toContain('class="f kept"');
  });
  it('masks pre-filled fields on the original side and says so', () => {
    const contact = d.pages.find((p) => p.page_no === 10)!;
    const h = render(<ParallelView page={contact} fields={d.fields[10]} side="synthesize" view="fields" onSide={noop} onView={noop} origin="http://demo-site:8410" persona={1} />);
    expect(text(h)).toContain('original · as captured · personal data masked');
    expect(text(h)).toContain('••• masked');
  });
  it('reports validation messages recorded on the active side', () => {
    const address = d.pages.find((p) => p.page_no === 3)!;
    const h = text(render(<ParallelView page={address} fields={d.fields[3]} side="obfuscate" view="screens" onSide={noop} onView={noop} origin="http://demo-site:8410" persona={1} />));
    expect(h).toContain('recorded 1 validation message; see the inventory');
  });
});

describe('FieldInventory', () => {
  const html = render(<FieldInventory path="/apply/identity" fields={d.fields[2]} flags={d.summary.risk_flags} side="synthesize" />);
  const t = text(html);
  it('has one row per field with input, autocomplete, entity, confidence, fill method and evidence', () => {
    expect((html.match(/<tr data-field=/g) ?? []).length).toBe(9);
    expect(t).toContain('Field inventory · /apply/identity');
    expect(t).toMatch(/3 Aadhaar number text · pattern \\d\{4\} \\d\{4\} \\d\{4\} on A1 AADHAAR 0\.98 persona\.aadhaar \/ fpe_digits label \+ pattern 9173 2206 4418/);
    expect(html).toContain('<span class="chip rose">AADHAAR</span>');
    expect(html).toContain('<span class="chip slate">GENDER</span>');
    expect(html).toContain('data-l="Entity"');
  });
  it('expands validation messages under their field', () => {
    const h = text(render(<FieldInventory path="/apply/address" fields={d.fields[3]} flags={[]} side="obfuscate" />));
    expect(h).toContain('1 message');
  });
});

describe('ScanInventory (whole scan)', () => {
  it('lists every field with filters and both downloads', () => {
    const rows = inventoryRows(d.pages, d.fields);
    const html = render(<ScanInventory rows={rows} flags={d.summary.risk_flags} onDownload={noop} />);
    expect((html.match(/<tr data-row=/g) ?? []).length).toBe(rows.length);
    const t = text(html);
    expect(t).toContain('Entity type');
    expect(t).toContain('Sensitivity');
    expect(t).toContain('Risk flags only');
    expect(t).toContain('Download CSV');
    expect(t).toContain('Download JSON');
  });
});

describe('RiskFlags', () => {
  it('numbers each flag and colours it by severity', () => {
    const html = render(<RiskFlags flags={d.summary.risk_flags} />);
    expect(html).toContain('<span class="chip rose">A1</span>');
    expect(html).toContain('<span class="chip amber">A2</span>');
    expect(text(html)).toContain('A2 PII in query string: ?email= /apply/review');
  });
  it('says when there is none', () => {
    expect(text(render(<RiskFlags flags={[]} />))).toContain('No risk flags');
  });
});

describe('Captures tab', () => {
  const html = render(<CapturesTab scanId={d.job.id} pages={d.pages} selected={9} mode="synthesize" origin="http://demo-site:8410" />);
  const t = text(html);
  it('shows a masked capture per page that renders personal data, with its reversible map', () => {
    expect(t).toContain('Capture · /profile · rendered personal data');
    expect(t).toContain('synthetic replacement');
    expect(t).toContain('Download reversible map');
    expect(t).toContain('Reveal with POST /api/v1/reveal and this scan’s key material');
    expect(html).toContain('id="capture-9"');
  });
  it('says when a capture was withheld', () => {
    expect(t).toContain('Withheld — a value could not be masked, so nothing was stored');
  });
  it('obfuscate scans label the capture as a reversible map', () => {
    expect(text(render(<CapturesTab scanId="s" pages={d.pages} selected={null} mode="obfuscate" origin="" />))).toContain('obfuscated · reversible map');
  });
});

describe('Inventory and captures tabs in the page', () => {
  it('renders the inventory tab from GET /inventory', () => {
    const t = text(body({ tab: 'inventory', inventory: { items: inventoryRows(d.pages, d.fields), risk_flags: d.summary.risk_flags } }));
    expect(t).toContain('Inventory · whole scan');
  });
  it('renders the captures tab', () => {
    expect(text(body({ tab: 'captures', pageNo: 9 }))).toContain('Capture · /profile');
  });
});

describe('refusals of the pages, page and inventory routes', () => {
  const refused = new ApiError(500, 'internal error', 'internal');
  it('a page that is not part of the scan (404) says so instead of loading forever', () => {
    const t = text(body({ pageNo: 999, pageDetail: undefined, pageError: new ApiError(404, 'not found', 'not_found') }));
    expect(t).toContain('This page is not part of the scan');
    expect(t).not.toContain('Loading the page');
  });
  it('any other refusal of the page shows the error, not a skeleton', () => {
    const h = body({ pageNo: 2, pageDetail: undefined, pageError: refused });
    expect(text(h)).toContain('The page could not be loaded: 500 internal: internal error. Retrying.');
    expect(text(h)).not.toContain('Loading the page');
    expect(h).toMatch(/class="callout amber" role="alert"[^>]*>The page could not be loaded/);
  });
  it('a refused pages list is shown in the pages card', () => {
    const t = text(body({ pages: undefined, pageNo: null, pageDetail: undefined, pagesError: new ApiError(403, 'scope missing', 'forbidden') }));
    expect(t).toContain('The pages could not be loaded: 403 forbidden: scope missing. Retrying.');
  });
  it('a refused pages list on the captures tab shows the refusal, never "no page rendered personal data"', () => {
    const h = body({ tab: 'captures', pages: undefined, pageNo: null, pageDetail: undefined, pagesError: refused });
    const t = text(h);
    expect(t).toContain('The pages could not be loaded: 500 internal: internal error. Retrying.');
    expect(h).toMatch(/class="callout amber" role="alert"[^>]*>The pages could not be loaded/);
    expect(t).not.toContain('No page of this scan rendered personal data');
  });
  it('the captures tab shows a skeleton while the pages list is loading', () => {
    const h = body({ tab: 'captures', pages: undefined, pageNo: null, pageDetail: undefined });
    expect(h).toContain('class="skeleton"');
    expect(text(h)).not.toContain('No page of this scan rendered personal data');
  });
  it('the empty captures sentence is kept for a loaded empty list, without "so far" once the scan is terminal', () => {
    const empty = d.pages.filter((p) => p.capture_status !== 'captured' && p.capture_status !== 'withheld');
    const done = text(body({ tab: 'captures', pages: empty, pageNo: null, pageDetail: undefined }));
    expect(done).toContain('No page of this scan rendered personal data that needed a masked capture.');
    const running = { scan: scanJob({ state: 'running' }), summary: summary({ pages_visited: 4, pages_discovered: 9 }) };
    expect(text(body({ detail: running, tab: 'captures', pages: empty, pageNo: null, pageDetail: undefined })))
      .toContain('No page of this scan rendered personal data that needed a masked capture so far.');
  });
  it('the pages card does not say "None so far" for rendered personal data before the pages list loads', () => {
    const t = text(body({ pages: undefined, pageNo: null, pageDetail: undefined, pagesError: refused }));
    expect(t).not.toContain('None so far');
  });
  it('a refused inventory is shown in the inventory tab', () => {
    const h = body({ tab: 'inventory', inventory: undefined, inventoryError: new ApiError(403, 'scope missing', 'forbidden') });
    expect(text(h)).toContain('The inventory could not be loaded: 403 forbidden: scope missing. Retrying.');
    expect(h).not.toContain('class="skeleton"');
  });
});

describe('ScanDetail container', () => {
  it('first frame: crumb and loading state for the route', () => {
    const t = text(render(<ScanDetail />, { path: `/scanner/scans/${d.job.id}?tab=parallel`, route: '/scanner/scans/:scanId' }));
    expect(t).toContain('UI scanner');
    expect(t).toContain('No submissions · POST interception on');
    expect(t).toContain('Loading the scan');
  });
});

describe('paths as the API returns them (NumberedRiskFlag.path and InventoryRow.path are redacted page URLs)', () => {
  const flag = { code: 'A8', path: 'http://demo-site:8410/apply/review?email=<EMAIL>&ref=<v>', flag: 'pii_in_query_string' as const, severity: 'medium' as const,
    page_no: 7, field_nos: [], entity_types: ['EMAIL'], query_keys: ['email'], where: 'url' as const };
  it('risk flags show the page path, never the origin or a placeholder', () => {
    const t = text(render(<RiskFlags flags={[flag]} />));
    expect(t).toContain('/apply/review?email=•••&ref=•••');
    expect(t).not.toContain('http://');
    expect(t).not.toContain('<EMAIL>');
  });
  it('the whole-scan inventory shows the page path', () => {
    const row = { ...inventoryRows(d.pages, d.fields)[0], path: 'http://demo-site:8410/apply/identity' };
    const t = text(render(<ScanInventory rows={[row]} flags={[]} onDownload={noop} />));
    expect(t).toContain('/apply/identity');
    expect(t).not.toContain('http://demo-site:8410');
  });
  it('the typed fixtures carry the paths in the shape the API returns', () => {
    expect(d.summary.risk_flags.every((f) => f.path.startsWith('http://'))).toBe(true);
    expect(inventoryRows(d.pages, d.fields).every((r) => r.path.startsWith('http://'))).toBe(true);
  });
});
