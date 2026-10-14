// Reports & audit (mockup 09, Task 20).
import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { evidenceRows } from '../lib/reports';
import { html, text } from '../test/render';
import { ReportsView, type ReportsViewProps } from './Reports';

const NOW = new Date('2026-10-13T10:15:00Z');
const filters = { days: 14 as const, layer: 'all' as const, outcome: 'all' as const, q: '', scope: 'tenant' as const };
const noop = (): void => undefined;
const dl = async (): Promise<{ blob: Blob; filename: string }> => ({ blob: new Blob([]), filename: 'x' });

function props(p: Partial<ReportsViewProps> = {}): ReportsViewProps {
  return {
    tenant: 'retail-banking-apac', readableTenants: 5, ready: fx.readiness(), filters, now: NOW, jobs: fx.reportsJobs(NOW, 14),
    rows: evidenceRows(fx.evidenceItems(NOW), fx.apiRollups(NOW, 14), filters), packsCursor: 'next', audit: { rows: fx.auditRows(NOW), cursor: '9', forbidden: false },
    error: null, busy: null, onFilters: noop, onOlderPacks: noop, onOlderAudit: noop, onScope: noop,
    download: { packJson: dl, packPdf: dl, rollup: dl, auditCsv: dl }, ...p,
  };
}

describe('ReportsView', () => {
  it('shows the crumb with readable tenants, the audit-writer pill and the CSV export', () => {
    const t = text(html(<ReportsView {...props()} />));
    expect(t).toContain('Reports & audit / Tenant: retail-banking-apac + 4 tenants you can read');
    expect(t).toContain('Audit writer healthy · append-only');
    expect(t).toContain('Evidence and audit');
    expect(t).toContain('Export 24.6M audit rows as CSV');
    expect(t).not.toContain('Schedule a weekly export');
  });

  it('renders the filters, KPIs, sweeps chart and failed sweeps', () => {
    const out = html(<ReportsView {...props()} />);
    const t = text(out);
    expect(out).toContain('role="search"');
    for (const s of ['All', 'API', 'Connectors', 'UI scanner', 'OK', 'Failed', 'Blocked', 'Clear']) expect(t).toContain(s);
    expect(t).toContain('Evidence packs 62 48 jobs · 9 scans · 5 API roll-ups');
    expect(t).toMatch(/Verification sweeps [\d,]+ [\d,]+ passed · 3 failed, all failed closed/);
    expect(t).toContain('Audit rows 24.6M append-only · monthly partitions');
    expect(t).toContain('Customer values stored 0 counts, hashes and key names only');
    expect(out).toContain('aria-label="Verification sweeps per day');
    expect(t).toContain('Failed sweeps 2 in 14 days');
    expect(t).toContain('j_01K7F2…F2 · p3, p4');
    expect(out).toContain('href="/data-sources/jobs/j_01K7F2W8Q4LMF2"');
  });

  it('lists evidence packs and roll-ups with downloads and links', () => {
    const out = html(<ReportsView {...props()} />);
    const t = text(out);
    expect(out).toContain('href="/reports/evidence/ev_01K7ZQ9QA1"');
    expect(t).toContain('CRM.CUSTOMERS → CRM_MASKED.CUSTOMERS');
    expect(t).toContain('CHG-48213 · table · 2,706,118 rows');
    expect(t).toContain('gdpr-pseudonymise v3');
    expect(t).toContain('passed · 0 exact rows');
    expect(t).toContain('failed · 2 leaks caught');
    expect(t).toContain('POST /api/v1/obfuscate · daily roll-up');
    // a scan is named by its host, with what it did (mockup 09; S2 console r1 B2)
    expect(t).toContain('UI scan of onboarding-uat.bank.internal');
    expect(t).toContain('CHG-48102 · 38 pages · 12 forms · 20 personas · 0 submissions');
    expect(out).toContain('aria-label="Download evidence pack j_01K7ZQ…9Q as PDF"');
    expect(t).toContain('Show older packs');
  });

  it('renders the audit log for admin keys', () => {
    const out = html(<ReportsView {...props()} />);
    const t = text(out);
    expect(t).toContain('Append-only. Rows cannot be edited or deleted; partitions expire under the retention policy.');
    expect(t).toContain('This tenant');
    expect(t).toContain('All tenants I can read');
    expect(t).toContain('POST /api/v1/reveal');
    expect(t).toContain('fpe_digits +4');
    expect(t).toContain('unknown key');
    expect(t).toContain('Load 50 older rows');
    expect(out).toContain('<span class="chip rose">leakage</span>');
  });

  it('replaces the audit log and the CSV export with a callout without admin', () => {
    const t = text(html(<ReportsView {...props({ readableTenants: null, audit: { rows: [], cursor: null, forbidden: true } })} />));
    expect(t).toContain('The audit log needs a key with the admin scope');
    expect(t).not.toContain('as CSV');
    expect(t).toContain('Tenant: retail-banking-apac');
    expect(t).not.toContain('tenants you can read');
  });

  it('shows empty states with the mockup wording', () => {
    const t = text(html(<ReportsView {...props({ rows: [], packsCursor: null, audit: { rows: [], cursor: null, forbidden: false } })} />));
    expect(t).toContain('No evidence pack matches these filters. Clear the filters, or widen the date range to 30 days.');
    expect(t).toContain('No audit rows match these filters. Clear the search or pick another layer.');
  });
});
