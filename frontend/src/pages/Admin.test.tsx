// Admin (mockup 11, Task 22).
import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { html, occurrences, text } from '../test/render';
import { AdminView, type AdminActions } from './Admin';

const NOW = new Date('2026-10-13T10:15:00Z');
const never = (): Promise<never> => new Promise(() => undefined);
const actions: AdminActions = {
  createKey: never, rotateKey: never, disableKey: never, enableKey: never, createTenant: never, patchTenant: never,
  exportConfig: never,
};
const data = {
  now: NOW, apiKey: 'dps_9Fq2fictional', tenant: 'retail-banking-apac', ready: fx.readiness(), keys: fx.apiKeys(NOW), tenants: fx.tenants(NOW),
  posture: fx.posture(NOW), queue: fx.queue(NOW), connections: fx.connections(NOW), forbidden: false, error: null,
};

describe('AdminView', () => {
  const out = html(<AdminView {...data} actions={actions} />);
  const t = text(out);
  it('shows the environment, posture pill and the caller', () => {
    expect(t).toContain('Admin / production');
    expect(t).toContain('Posture · 10 of 11 checks pass');
    expect(t).toContain('platform-admin · admin');
    expect(t).toContain('Export configuration');
    expect(t).toContain('Create key');
  });
  it('renders the KPI row', () => {
    expect(t).toContain('Active API keys 10 1 disabled · 1 due for rotation');
    expect(t).toContain('Tenants 5 6 jobs running of 15 allowed');
    expect(t).toContain('Posture checks 10 of 11 pass 1 to fix: Keys rotated within 90 days');
    expect(t).toContain('Keys stored in clear 0 SHA-256 hashes only · each key shown once');
  });
  it('lists keys with prefixes, scopes, rotation chips and actions', () => {
    expect(t).toContain('platform-admin dps_9Fq2…');
    expect(t).toContain('all tenants (*)');
    expect(out).toContain('<span class="chip amber">admin</span>');
    expect(out).toContain('<span class="chip amber">reveal</span>');
    expect(t).toContain('rotate · 113 days');
    expect(t).toContain('Re-enable');
    expect(occurrences(out, '>Rotate<')).toBe(9);
    expect(t).toContain('ops-bootstrap');
    expect(t).toContain('env');
    expect(t).toContain('11 keys · prefixes only; DPS cannot show a full key after creation');
    expect(t).not.toMatch(/dps_[A-Za-z0-9]{12,}/);
  });
  it('offers the create-key form with privileged scopes and expiry', () => {
    expect(out).toContain('id="createCard"');
    expect(t).toContain('Lowercase letters, digits and dashes. Name the pipeline, not a person.');
    expect(out).toMatch(/class="check priv"><input type="checkbox"[^>]*value="admin"/);
    for (const e of ['30 days', '90 days', '180 days', '1 year']) expect(t).toContain(e);
    expect(t).toContain('DPS keeps only a SHA-256 hash of the key.');
  });
  it('shows tenants with quota bars and the near-cap callout', () => {
    expect(t).toContain('Tenants and quotas');
    expect(t).toContain('17.2M of 20M · 86%');
    expect(t).toContain('cards-in has used 86% of its daily row quota. New jobs for this tenant queue until 00:00 UTC once it is reached.');
    expect(t).toContain('Add tenant');
  });
  it('lists registered connections with who may write', () => {
    expect(t).toContain('Registered connections');
    expect(t).toContain('loans-prod');
    expect(t).toContain('Keys with admin · platform-admin');
    expect(t).toContain('Not applicable · files only');
    expect(t).toContain('plus a confirmation with row counts');
  });
  it('lists scanner hosts, service health and posture checks', () => {
    expect(t).toContain('UI-scanner hosts 4 registered');
    expect(t).toContain('https://onboarding-uat.bank.internal');
    expect(t).toContain('none · public pages only');
    expect(t).toContain('A scan whose URL is outside this list is refused before the browser starts.');
    expect(t).toContain('Service health');
    expect(t).toContain('6 of 7 workers busy · 1 draining');
    expect(t).toContain('Posture checks 1 to fix');
    expect(t).toContain('Rotate ledger-recon-batch');
    expect(t).toContain('Staging and production refuse to boot when');
  });
});

describe('AdminView without the admin scope', () => {
  it('says what is needed and still shows service health', () => {
    const t = text(html(<AdminView {...data} keys={[]} tenants={[]} posture={null} queue={null} connections={[]} forbidden actions={actions} />));
    expect(t).toContain('Admin needs a key with the admin scope');
    expect(t).toContain('Service health');
    expect(t).not.toContain('Create key');
  });
});
