// Overview — protection posture (mockup 01, Task 19): sections, order and labels from
// GET /reports/summary and /readyz; degraded mode when the metadata database is absent.
import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { html, text } from '../test/render';
import { mostUsedPolicy, OverviewView, postureBars, readinessPill } from './Overview';

const NOW = new Date('2026-10-13T10:15:00Z');

describe('OverviewView', () => {
  const out = html(<OverviewView tenant="retail-banking-apac" ready={fx.readiness()} summary={fx.summary(NOW)} degraded={null} policyLabel="gdpr-pseudonymise v3" />);
  const t = text(out);

  it('shows the crumb, readiness pill and the head with its two actions', () => {
    expect(t).toContain('Overview / Tenant: retail-banking-apac');
    expect(t).toContain('All layers ready');
    expect(t).toContain('Protection posture');
    expect(t).toContain('Last 14 days · policy pack gdpr-pseudonymise v3');
    expect(out).toMatch(/href="\/reports"[^>]*>Export evidence/);
    expect(out).toMatch(/href="\/playground"[^>]*>Open playground/);
  });

  it('renders the four KPI tiles in the mockup order', () => {
    const order = ['Values protected', 'PII findings', 'Jobs', 'Raw PII sent to LLM'].map((l) => t.indexOf(l));
    expect(order.every((i, k) => i > 0 && (k === 0 || i > order[k - 1]))).toBe(true);
    expect(t).toContain('45,791,546');
    expect(t).toContain('▲ 28% vs previous 14 days');
    expect(t).toContain('5,951,880');
    expect(t).toContain('41 entity types');
    expect(t).toContain('4 running');
    expect(t).toContain('23 completed · 0 failed · 2 awaiting review');
    expect(t).toContain('2,184 gateway calls · 3 blocked at egress · this tenant · last 14 days · placeholders only');
    expect(out).toContain('class="card kpi zero"');
  });

  it('draws the trend and the posture score with its four bars', () => {
    expect(out).toContain('aria-label="Protected values per day, last 14 days"');
    expect(t).toContain('Posture score');
    expect(t).toContain('94 of 100');
    expect(t).toContain('Review'); // 93.75 < 95
    for (const label of ['Leakage sweeps passed', 'Reversible with key held by caller', 'Columns reviewed before run', 'Policies on latest version']) expect(t).toContain(label);
  });

  it('renders the three layer cards', () => {
    expect(t).toContain('Layer 1 · API stateless 1.92M requests');
    expect(t).toContain('obfuscate 71%');
    expect(t).toContain('Layer 2 · Connectors 2 awaiting review 5.47M rows');
    expect(t).toContain('Oracle 63% · MongoDB 21% · PostgreSQL 16%');
    expect(t).toContain('1 job group keeps ids consistent across systems');
    expect(t).toContain('Layer 3 · UI scanner never submits');
    expect(t).toContain('Last scan: 38 pages, 20 personas, 0 submitted');
  });

  it('lists findings by entity type and the recent jobs with links', () => {
    expect(t).toContain('Findings by entity type');
    expect(out).toContain('<span class="chip rose">PAN_IN</span>');
    expect(out).toContain('<span class="chip amber">EMAIL</span>');
    expect(t).toContain('1,412,204');
    expect(out).toContain('href="/data-sources/jobs/j_01K7ZQ3R8D5W9Q"');
    expect(out).toContain('href="/scanner/scans/s_01K7ZRB2M4XQ"');
    expect(t).toContain('CRM.CUSTOMERS → CRM_MASKED.CUSTOMERS');
    expect(t).toContain('awaiting review');
  });

  it('never shows a key or a customer value', () => {
    expect(out).not.toMatch(/dps_[A-Za-z0-9]{12,}/);
  });
});

describe('degraded mode', () => {
  it('renders M0 tiles from /metrics and the database callout instead of erroring', () => {
    const t = text(html(<OverviewView tenant="" ready={fx.readiness()} summary={null} degraded={{ metrics: fx.METRICS_TEXT, stats: null, reason: 'db' }} policyLabel={null} />));
    expect(t).toContain('Connect the metadata database to see 14-day trends');
    expect(t).toContain('Requests served 1,920,000');
    expect(t).toContain('Raw PII sent to LLM 0');
    expect(t).toContain('Tenant: default');
    expect(t).not.toContain('Posture score');
  });
});

describe('helpers', () => {
  it('summarises readiness', () => {
    expect(readinessPill(fx.readiness())).toEqual({ label: 'All layers ready', tone: 'ok', components: expect.stringContaining('api-core · db') });
    const r = fx.readiness();
    r.components.ocr.ok = false;
    r.components.browser.ok = false;
    expect(readinessPill(r).label).toBe('2 degraded');
    expect(readinessPill(null).label).toBe('Checking readiness');
  });
  it('builds the posture bars and picks the most-used policy', () => {
    const bars = postureBars(fx.summary(NOW).posture);
    expect(bars.map((b) => b.pct)).toEqual([100, 100, 94, 81]);
    expect(bars[3].tone).toBe('amber');
    expect(mostUsedPolicy({ presets: fx.presetViews(), tenant: [{ ...fx.gdprDetail(NOW) }] })).toBe('gdpr-pseudonymise v3');
    expect(mostUsedPolicy({ presets: fx.presetViews(), tenant: [] })).toBe('pci-dss v1');
  });
});
