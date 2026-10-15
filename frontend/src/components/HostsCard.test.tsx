// Admin › UI-scanner hosts (mockup 11) says what the deployment really does with a tenant that has
// no registered host (S2 console r1 B3): a local or dev deployment falls back to
// DPS_SCAN_REGISTERED_HOSTS (POST /scans accepts demo-site there), staging and production refuse.
import { describe, expect, it } from 'vitest';
import type { HostRow } from '../lib/admin';
import { html, text } from '../test/render';
import { fallbackApplies, HostsCard } from './HostsCard';

const noop = (): void => undefined;
const add = async (): Promise<void> => undefined;
const card = (p: { rows?: HostRow[]; env?: string | null; fallbackHosts?: string[] | null }): string =>
  text(html(<HostsCard rows={p.rows ?? []} tenants={['default']} defaultTenant="default" onAdd={add} onRemove={noop} env={p.env} fallbackHosts={p.fallbackHosts} />));

describe('HostsCard', () => {
  it('on a local or dev deployment lists the service default a tenant without hosts may scan', () => {
    for (const env of ['local', 'dev']) {
      const t = card({ env, fallbackHosts: ['localhost', '127.0.0.1', 'demo-site'] });
      expect(t).toContain('0 registered');
      expect(t).toContain('No tenant has a registered host.');
      expect(t).toContain(`This ${env} deployment lets every tenant without one scan the service default below (DPS_SCAN_REGISTERED_HOSTS)`);
      expect(t).toContain('staging and production refuse scans until a host is registered');
      expect(t).toMatch(/demo-site every tenant without a host — service default/);
      expect(t).not.toContain('Scans are refused until one is');
    }
  });
  it('on staging or production says scans are refused until a host is registered', () => {
    for (const env of ['staging', 'prod']) {
      const t = card({ env, fallbackHosts: [] });
      expect(t).toContain('No host is registered. Scans are refused until one is.');
      expect(t).not.toContain('service default');
      expect(t).not.toContain('demo-site');
    }
  });
  it('names the default list when an older server does not report the fallback, and never claims a refusal it cannot know', () => {
    expect(card({ env: 'local', fallbackHosts: null })).toContain('A local deployment falls back to DPS_SCAN_REGISTERED_HOSTS (by default localhost, 127.0.0.1 and demo-site)');
    const unknown = card({ env: null });
    expect(unknown).toContain('Staging and production refuse scans until one is; a local or dev deployment falls back to DPS_SCAN_REGISTERED_HOSTS');
    expect(unknown).not.toContain('Scans are refused until one is.');
  });
  it('says so when the local fallback list is empty', () => {
    expect(card({ env: 'dev', fallbackHosts: [] })).toContain('DPS_SCAN_REGISTERED_HOSTS is empty: this dev deployment refuses scans until a host is registered.');
  });
  it('keeps registered rows and notes the fallback for the other tenants on local', () => {
    const rows: HostRow[] = [{ tenant: 'default', host: 'uat.bank.example', storageRef: null, added: '2026-10-13T10:15:00Z' }];
    const t = card({ rows, env: 'local', fallbackHosts: ['localhost', '127.0.0.1', 'demo-site'] });
    expect(t).toContain('https://uat.bank.example');
    expect(t).toContain('on this local deployment a tenant without a host of its own may scan the service default: localhost, 127.0.0.1 and demo-site');
    expect(t).not.toMatch(/No host is registered|No tenant has a registered host/);
    expect(card({ rows, env: 'prod', fallbackHosts: [] })).not.toContain('service default');
  });
  it('applies the fallback only where POST /scans does', () => {
    expect(['local', 'dev', 'staging', 'prod', null].map(fallbackApplies)).toEqual([true, true, false, false, false]);
  });
});
