// The New scan page (mockup 12): first frame of /scanner and /scanner/new?scan=… (effects do not run
// in a static render, so nothing is fetched; the live column's data views are tested in liveCrawl.test).
import { describe, expect, it } from 'vitest';
import { render, text } from '../test/render';
import { ScanNew } from './ScanNew';

describe('ScanNew', () => {
  const html = render(<ScanNew />, { path: '/scanner' });
  const t = text(html);

  it('lays out the setup column in the mockup order', () => {
    const order = ['Target', 'Scope', 'Authentication', 'Fill mode', 'Request'].map((h) => t.indexOf(h));
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html).toContain('class="page scanner-page"');
    expect(html).toContain('class="setup"');
  });

  it('opens with the no-submission statement and stepper', () => {
    expect(t).toContain('DPS never submits forms.');
    expect(t).toContain('No submissions · POST interception on');
    expect(t).toMatch(/Setup .*Crawl .*Classify .*Parallel view .*Export/);
  });

  it('starts from safe defaults: demo start URL, deny /logout and /admin, session, synthesize, en_IN', () => {
    expect(html).toContain('value="http://demo-site:8410/apply"');
    expect(t).toContain('/logout');
    expect(t).toContain('/admin');
    expect(html).toContain('value="dps/ui/demo-onboarding"');
    expect(html).toMatch(/<button[^>]*class="on"[^>]*aria-pressed="true"[^>]*>Synthesize persona fill/);
    expect(html).toContain('<option value="en_IN" selected="">');
  });

  it('has switches with role=switch and a disabled HAR switch', () => {
    expect((html.match(/role="switch"/g) ?? []).length).toBe(5);
    expect(html).toMatch(/aria-label="HAR file"[^>]*aria-disabled="true"|aria-disabled="true"[^>]*aria-label="HAR file"/);
    expect(t).toContain('Not supported: a HAR holds response bodies with personal data');
  });

  it('previews the exact request body, which has no submit field and no seed', () => {
    const pane = /<pre[^>]*class="pane reqpane"[^>]*>([\s\S]*?)<\/pre>/.exec(html)?.[1] ?? '';
    const body = JSON.parse(text(pane)) as Record<string, unknown>;
    expect(body).toMatchObject({ url: 'http://demo-site:8410/apply', mode: 'synthesize', parallel_view: true, capture: { screenshots: true, har: false } });
    expect(body).not.toHaveProperty('consistency');
    expect(JSON.stringify(body)).not.toMatch(/submit/i);
    expect(t).toContain('Start crawl');
    expect(t).toContain('Copy request');
  });

  it('shows an empty live column before a crawl exists', () => {
    expect(t).toContain('Start a crawl to see pages appear here.');
    expect(html).toMatch(/aria-disabled="true"[^>]*>Open parallel view/);
  });

  it('with ?scan= it shows the scan id and waits for the first poll', () => {
    const live = text(render(<ScanNew />, { path: '/scanner/new?scan=s_01K7ZR4M2D9Q8W6X3N5VTB7F1C' }));
    expect(live).toContain('New scan · s_01K7ZR…1C');
    expect(live).toContain('Restart crawl with these settings');
    expect(live).not.toContain('Start a crawl to see pages appear here.');
  });
});
