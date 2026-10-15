// The live-crawl components of mockup 12, rendered from typed fixtures.
import { describe, expect, it } from 'vitest';
import { ApiError } from '../../lib/api';
import { demoScan, event, page, scanJob, summary } from '../../lib/scanFixtures';
import type { ScanEvent, ScanPage } from '../../lib/types';
import { LiveCrawl } from '../../pages/ScanNew';
import { render, text } from '../../test/render';
import { CrawlMap } from './CrawlMap';
import { CrawlProgress } from './CrawlProgress';
import { NavLog } from './NavLog';
import { NoSubmitBanner } from './NoSubmitBanner';
import { PersonaChips } from './PersonaPreview';

const d = demoScan();

describe('NoSubmitBanner', () => {
  it('states that DPS never submits, with the blocked-POST count and a constant 0 submissions', () => {
    const html = render(<NoSubmitBanner blocked={7} />);
    const t = text(html);
    expect(t).toContain('DPS never submits forms.');
    expect(t).toContain('7 POST requests blocked in this scan');
    expect(t).toContain('0 forms submitted, in any scan');
    expect(html).toContain('decision D4');
  });
});

describe('CrawlProgress', () => {
  it('shows a running crawl: clock, visited of discovered, queue, budget, pipe and stats', () => {
    const job = scanJob({ state: 'running', started_at: '2026-10-13T09:40:01.000Z' });
    const s = summary({ pages_visited: 31, pages_discovered: 38, pages_queued: 7, pages_classified: 24, pages_filled: 9, forms: 10, fields: 180, personal_fields: 81, critical_fields: 5, risk_flags: d.summary.risk_flags.slice(0, 3) });
    const html = render(<CrawlProgress scan={job} summary={s} now={Date.parse('2026-10-13T09:44:13.000Z')} />);
    const t = text(html);
    expect(t).toContain('crawling · 4:12');
    expect(t).toContain('31 of 38 discovered pages visited · 7 queued');
    expect(t).toContain('budget 50 pages · depth ≤ 3');
    expect(t).toContain('24 pages classified');
    expect(t).toContain('9 filled and captured');
    expect(t).toContain('81 personal data fields · 5 critical');
    expect(t).toContain('3 risk flags so far');
    expect(html).toContain('aria-valuenow="31"');
    expect(html).toContain('live-dot');
  });
  it('says finished, stopped or failed once the scan is terminal', () => {
    expect(text(render(<CrawlProgress scan={d.job} summary={d.summary} />))).toContain('finished · 4:12');
    expect(text(render(<CrawlProgress scan={scanJob({ state: 'canceled' })} summary={summary({ pages_visited: 6 })} />))).toContain('stopped · 6 pages kept');
    expect(text(render(<CrawlProgress scan={scanJob({ state: 'failed' })} summary={summary()} />))).toContain('failed');
  });
});

describe('CrawlMap', () => {
  const html = render(<CrawlMap scanId={d.job.id} pages={d.pages} events={d.events} flags={d.summary.risk_flags} origin="http://demo-site:8410" />);
  const t = text(html);
  it('nests pages under their parent with status, depth and counts', () => {
    expect(t).toContain('/apply start 1 form · 5 fields');
    expect(t).toMatch(/\/apply\/identity d1 1 form · 9 fields PII 7 2 crit\. A1 2 POST blocked/);
    expect(html).toContain('class="kids"');
    expect(html).toContain('aria-expanded="true"');
  });
  it('links pages with forms to the parallel view and rendered-PII pages to their capture', () => {
    expect(html).toContain(`href="/scanner/scans/${d.job.id}?tab=parallel&amp;page=2"`);
    expect(html).toContain(`href="/scanner/scans/${d.job.id}?tab=captures&amp;page=9"`);
  });
  it('lists skipped paths with their reason, other origins with their origin', () => {
    expect(t).toContain('/logout deny list');
    expect(t).toContain('https://cdn.bank-assets.invalid other origin');
    expect(t).toContain('template limit, links not followed');
    expect(html).toContain('node skipped');
  });
  it('explains the risk codes in the footer', () => {
    expect(t).toContain('A1 autocomplete on for Aadhaar and PAN');
    expect(t).toContain('A2 PII in query string: ?email=');
  });
  it('marks a page in progress and queued pages', () => {
    const live = text(render(<CrawlMap scanId="s_1" pages={[page({ page_no: 1 }), page({ page_no: 2, parent_page_no: 1, status: 'visiting', url_redacted: 'http://demo-site:8410/apply/card', depth: 1 }), page({ page_no: 3, parent_page_no: 1, status: 'queued', url_redacted: 'http://demo-site:8410/apply/documents', depth: 1 })]} events={[]} flags={[]} origin="http://demo-site:8410" />));
    expect(live).toContain('/apply/card d1 in progress');
    expect(live).toContain('/apply/documents d1 queued');
  });
});

describe('NavLog', () => {
  it('shows the 13 newest entries, newest first, with value-free notes', () => {
    const html = render(<NavLog scanId={d.job.id} events={d.events} pages={d.pages} origin="http://demo-site:8410" />);
    const t = text(html);
    expect(t).toContain(`Showing 13 of ${d.events.length} entries`);
    expect((html.match(/class="lr( fresh)?"/g) ?? []).length).toBe(13);
    const newest = d.events[d.events.length - 1];
    expect(html.indexOf(`data-seq="${newest.seq}"`)).toBeLessThan(html.indexOf(`data-seq="${newest.seq - 1}"`));
    expect(t).toContain('Download log as JSONL');
    expect(html).toContain('aria-live="polite"');
  });
  it('writes the mockup sentences for blocked requests and fills', () => {
    const pages = [page({ page_no: 3, url_redacted: 'http://demo-site:8410/apply/address', form_count: 1, field_count: 7 })];
    const t = text(render(<NavLog scanId="s_1" pages={pages} origin="http://demo-site:8410" events={[
      event({ seq: 1, kind: 'fill', method: null, result: 'captured', page_no: 3, target: 'http://demo-site:8410/apply/address', note_code: 'synthesize', counts: { fields: 7, rejected: 2, persona: 1 } }),
      event({ seq: 2, kind: 'request', method: 'POST', result: 'blocked', page_no: 3, target: 'http://demo-site:8410/api/apply/autosave', note_code: 'method' }),
    ]} />));
    expect(t).toContain('POST blocked /api/apply/autosave from filled page /apply/address · interception');
    expect(t).toContain('fill captured /apply/address persona 1 · 7 fields · 2 validation messages recorded');
    expect(t).toContain('Showing 2 of 2 entries');
  });
  it('says so when nothing has happened yet', () => {
    expect(text(render(<NavLog scanId="s_1" events={[]} pages={[]} origin="" />))).toContain('No navigation yet');
  });
});

describe('refused or loading lists on the live crawl page', () => {
  const refused = new ApiError(500, 'internal error', 'internal');
  it('a refused pages list shows the refusal in the crawl map, not "No pages discovered yet"', () => {
    const html = render(<CrawlMap scanId="s_1" pages={[]} events={[]} flags={[]} origin="" error={refused} />);
    expect(text(html)).toContain('The pages could not be loaded: 500 internal: internal error. Retrying.');
    expect(html).toMatch(/class="callout amber" role="alert"[^>]*>The pages could not be loaded/);
    expect(text(html)).not.toContain('No pages discovered yet');
  });
  it('the crawl map shows a skeleton while the pages list is loading', () => {
    const html = render(<CrawlMap scanId="s_1" pages={[]} events={[]} flags={[]} origin="" loaded={false} />);
    expect(html).toContain('class="skeleton"');
    expect(text(html)).not.toContain('No pages discovered yet');
  });
  it('a crawl map with stale pages keeps them under the refusal', () => {
    const t = text(render(<CrawlMap scanId="s_1" pages={[page({ page_no: 1 })]} events={[]} flags={[]} origin="http://demo-site:8410" error={refused} />));
    expect(t).toContain('The pages could not be loaded');
    expect(t).toContain('/apply start');
  });
  it('a refused event feed shows the refusal in the navigation log, not "No navigation yet"', () => {
    const html = render(<NavLog scanId="s_1" events={[]} pages={[]} origin="" error={refused} />);
    expect(text(html)).toContain('The navigation log could not be loaded: 500 internal: internal error. Retrying.');
    expect(text(html)).not.toContain('No navigation yet');
    expect(text(html)).not.toContain('Showing 0 of 0 entries');
  });
  it('the navigation log shows a skeleton while the event feed is loading', () => {
    const html = render(<NavLog scanId="s_1" events={[]} pages={[]} origin="" loaded={false} />);
    expect(html).toContain('class="skeleton"');
    expect(text(html)).not.toContain('No navigation yet');
  });
  it('the live crawl column passes pages and events refusals through (ScanNew wiring)', () => {
    const live = (pages: { data?: ScanPage[]; error?: Error }, events: { data?: ScanEvent[]; error?: Error }): string =>
      text(render(<LiveCrawl scanId="s_1" scan={d.job} summary={d.summary} pages={pages} events={events} origin="http://demo-site:8410" />));
    const p500 = live({ error: refused }, { data: d.events });
    expect(p500).toContain('The pages could not be loaded: 500 internal: internal error. Retrying.');
    expect(p500).not.toContain('No pages discovered yet');
    const e500 = live({ data: d.pages }, { error: refused });
    expect(e500).toContain('The navigation log could not be loaded: 500 internal: internal error. Retrying.');
    expect(e500).not.toContain('No navigation yet');
    const loading = live({}, {});
    expect(loading).not.toContain('No pages discovered yet');
    expect(loading).not.toContain('No navigation yet');
    const empty = live({ data: [] }, { data: [] });
    expect(empty).toContain('No pages discovered yet');
    expect(empty).toContain('No navigation yet');
  });
});

describe('PersonaChips', () => {
  it('shows persona 1 as synthetic tokens with the notes', () => {
    const html = render(<PersonaChips preview={{ synthesis_seed: { alg: 'a', seed: 's', created_at: '' }, personas: [{ persona: 1, values: { name: 'Jordan Ellis', ssn: '900-41-2297' } }], notes: ['SSN in the never-issued area 900'] }} />);
    expect(html).toContain('class="tok syn"');
    expect(text(html)).toContain('Jordan Ellis SSN 900-41-2297');
    expect(text(html)).toContain('SSN in the never-issued area 900');
    expect(html).not.toContain('"s"');
  });
});
