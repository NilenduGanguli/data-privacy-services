import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import {
  callerKey, configExport, rotateWarning, createKeyError, healthTiles, hostRows, keyKpis, keyMeta, maxKeyAge, quotaTone, tenantsLabel, whoMayWrite,
} from './admin';

const NOW = new Date('2026-10-13T10:15:00Z');
const keys = fx.apiKeys(NOW);

describe('admin helpers', () => {
  it('reads the rotation age from the posture check and counts keys', () => {
    expect(maxKeyAge(fx.posture(NOW))).toBe(90);
    expect(maxKeyAge(null)).toBe(90);
    expect(keyKpis(keys, 90)).toEqual({ active: 10, disabled: 1, due: 1, dueNames: ['ledger-recon-batch'] });
  });
  it('finds the caller by key prefix only', () => {
    expect(callerKey(keys, 'dps_9Fq2remainder-of-a-fictional-key')?.name).toBe('platform-admin');
    expect(callerKey(keys, '')).toBeNull();
    expect(callerKey(keys, 'dps_9F')).toBeNull();
  });
  it('describes keys and their tenants', () => {
    const byName = Object.fromEntries(keys.map((k) => [k.name, k]));
    expect(keyMeta(byName['collections-voice-redaction'], NOW)).toBe('dps_Tz5s… · new');
    expect(keyMeta(byName['legacy-etl-poc'], NOW)).toMatch(/^dps_a1Nn… · disabled \d\d [A-Z][a-z]{2} 2026$/);
    expect(tenantsLabel(['*'])).toEqual({ main: 'all tenants (*)', more: null });
    expect(tenantsLabel(['retail-banking-apac', 'cards-in'])).toEqual({ main: 'retail-banking-apac', more: '+ cards-in' });
  });
  it('says who may write in place or append to a connection', () => {
    const conns = Object.fromEntries(fx.connections(NOW).map((c) => [c.name, c]));
    expect(whoMayWrite(conns['loans-prod'], keys)).toEqual({ scope: 'admin', names: ['platform-admin'] });
    expect(whoMayWrite(conns['crm-uat'], keys)).toEqual({ scope: 'jobs', names: ['crm-uat-provisioner', 'ledger-recon-batch'] });
    expect(whoMayWrite(conns['exports-aps1'], keys)).toEqual({ scope: 'files', names: [] });
  });
  it('tones quotas and maps create-key refusals to words', () => {
    expect(quotaTone(17_200_000, 20_000_000)).toBe('amber');
    expect(quotaTone(5, 10)).toBe('neu');
    expect(quotaTone(5, null)).toBe('neu');
    expect(createKeyError('scope_escalation', 'x')).toContain('scope you do not hold');
    expect(createKeyError('weird', 'server said so')).toBe('server said so');
  });
  it('lists registered hosts with their storage-state reference', () => {
    const rows = hostRows(fx.tenants(NOW), fx.connections(NOW));
    expect(rows).toHaveLength(4);
    expect(rows[0]).toMatchObject({ host: 'onboarding-uat.bank.internal', tenant: 'retail-banking-apac', storageRef: 'retail-banking-apac/dps/ui/onboarding-uat' });
    expect(rows.find((r) => r.host === 'cards-apply-uat.bank.internal')?.storageRef).toBeNull();
  });
  it('builds health tiles from readiness and the queue', () => {
    const tiles = healthTiles(fx.readiness(), fx.queue(NOW));
    expect(tiles.map((t) => t.name)).toEqual(expect.arrayContaining(['api-core', 'db', 'worker', 'browser-worker', 'tokenizer']));
    expect(tiles.find((t) => t.name === 'worker')?.detail).toBe('6 of 7 workers busy · 1 draining');
    expect(healthTiles(null, null)).toEqual([]);
  });
  it('exports configuration without any secret', () => {
    const cfg = configExport({ tenants: fx.tenants(NOW), keys, connections: fx.connections(NOW), posture: fx.posture(NOW) }, NOW);
    const json = JSON.stringify(cfg);
    expect(cfg.keys[0]).not.toHaveProperty('key');
    expect(json).not.toMatch(/password|credentials/);
    expect(cfg.generated_at).toBe('2026-10-13T10:15:00.000Z');
  });
});

describe('rotateWarning (r1 B1)', () => {
  const NOW2 = new Date('2026-10-13T10:15:00Z');
  const keys = fx.apiKeys(NOW2);
  const me = keys.find((k) => k.name === 'platform-admin')!;
  const other = keys.find((k) => k.name !== 'platform-admin' && k.status === 'active')!;
  const own = 'dps_9Fq2remainder-of-a-fictional-key';
  it('warns hard when the console rotates its own key with grace now', () => {
    const w = rotateWarning(me, 0, keys, own);
    expect(w?.tone).toBe('rose');
    expect(w?.text).toContain('This is the key this console uses');
    expect(w?.text).toContain('stops working as soon as the rotation completes');
  });
  it('notes its own key with a grace period, and says nothing for another key', () => {
    expect(rotateWarning(me, 24, keys, own)?.tone).toBe('amber');
    expect(rotateWarning(other, 0, keys, own)).toBeNull();
    expect(rotateWarning(me, 0, keys, '')).toBeNull();
  });
});
