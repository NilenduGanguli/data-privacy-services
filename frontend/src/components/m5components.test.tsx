// The M5 shared components (Task 18): chart with a table view, dialog, key-once card, download
// button and KPI tile.
import { describe, expect, it } from 'vitest';
import { html, text } from '../test/render';
import { BarChart } from './BarChart';
import { ConfirmDialog } from './ConfirmDialog';
import { DownloadButton } from './DownloadButton';
import { KeyOnceCard, keyFingerprint } from './KeyOnceCard';
import { Kpi } from './Kpi';

describe('BarChart', () => {
  const data = [{ day: '2026-10-10', passed: 30, failed: 0 }, { day: '2026-10-11', passed: 20, failed: 2 }];
  const series = [{ key: 'passed', label: 'Passed', color: 'var(--emerald)' }, { key: 'failed', label: 'Failed', color: 'var(--rose)' }];
  it('draws one stacked bar per day with an accessible label and a table toggle', () => {
    const out = html(<BarChart data={data} series={series} xKey="day" label="Verification sweeps per day" />);
    expect(out).toContain('role="img"');
    expect(out).toContain('aria-label="Verification sweeps per day"');
    expect((out.match(/<rect /g) ?? []).length).toBe(3); // 30 · 20 + 2 (a zero is not drawn)
    expect(out).toContain('View as table');
    expect(out).toContain('aria-pressed="false"');
    expect(text(out)).toContain('Passed');
  });
  it('renders the table view first when asked', () => {
    const out = html(<BarChart data={data} series={series} xKey="day" label="x" initialTable />);
    expect(out).toContain('<table');
    expect(text(out)).toContain('Oct 11 20 2');
    expect(out).toContain('View as chart');
  });
});

describe('Kpi', () => {
  it('renders the mockup tile markup', () => {
    expect(html(<Kpi label="Values protected" value="45.8M" delta="▲ 28%" hero />)).toBe(
      '<div class="card kpi hero"><div class="label">Values protected</div><div class="value">45.8M</div><div class="delta">▲ 28%</div></div>');
    expect(html(<Kpi label="Raw PII sent to LLM" value={0} zero />)).toContain('class="card kpi zero"');
  });
});

describe('ConfirmDialog', () => {
  it('renders nothing inside a closed dialog, and the body and actions when open', () => {
    const closed = html(<ConfirmDialog open={false} title="Disable key" body="x" confirmLabel="Disable" onConfirm={() => undefined} onCancel={() => undefined} />);
    expect(closed).toContain('<dialog');
    expect(closed).not.toContain('Disable key');
    const open = html(<ConfirmDialog open title="Discard draft" body="The draft is deleted." confirmLabel="Discard" danger requireText="retail-kyc" onConfirm={() => undefined} onCancel={() => undefined} />);
    expect(text(open)).toContain('Discard draft The draft is deleted.');
    expect(open).toMatch(/<button[^>]*disabled=""[^>]*>Discard<\/button>/); // typed confirmation still empty
    expect(text(open)).toContain('Type retail-kyc to confirm');
  });
});

describe('KeyOnceCard', () => {
  it('shows the raw key once with the secret-manager callout', () => {
    const out = html(<KeyOnceCard name="collections-voice-redaction" rawKey="dps_Tz5sEXAMPLEONLYnotarealkey0000" onStored={() => undefined} />);
    expect(text(out)).toContain('New key for collections-voice-redaction');
    expect(text(out)).toContain('dps_Tz5sEXAMPLEONLYnotarealkey0000');
    expect(text(out)).toContain('Store this key in your secret manager now, for example as dps/keys/collections-voice-redaction');
    expect(text(out)).toContain('I have stored the key');
  });
  it('renders nothing once the key is dropped and no fingerprint was taken', () => {
    expect(html(<KeyOnceCard name="x" rawKey={null} onStored={() => undefined} />)).toBe('');
  });
  it('fingerprints a key as prefix + short sha256 without keeping it', async () => {
    const fp = await keyFingerprint('dps_abcdefghijklmnop');
    expect(fp.prefix).toBe('dps_abcd');
    expect(fp.sha256).toMatch(/^[0-9a-f]{4}…[0-9a-f]{4}$/);
    expect(JSON.stringify(fp)).not.toContain('ijklmnop');
  });
});

describe('DownloadButton', () => {
  it('is a labelled button', () => {
    const out = html(<DownloadButton label="PDF" ariaLabel="Download evidence pack j_1 as PDF" fetcher={async () => ({ blob: new Blob([]), filename: 'x' })} />);
    expect(out).toContain('aria-label="Download evidence pack j_1 as PDF"');
    expect(text(out)).toBe('PDF');
  });
});
