import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import {
  callerKey, configExport, hostInputError, keyActions, rotateWarning, createKeyError, healthTiles, hostRows, KEY_NAME, keyKpis, keyMeta, quotaTone, rotationDue, scopeChips, rotationRule, tenantsLabel,
  whoMayWrite,
} from './admin';
import type { PostureReport } from './types';

const NOW = new Date('2026-10-13T10:15:00Z');
const keys = fx.apiKeys(NOW);

describe('admin helpers', () => {
  it('takes the keys due for rotation from the posture check as built (dps/posture.py) and counts keys', () => {
    const rule = rotationRule(fx.posture(NOW));
    expect(rule).toEqual({ names: new Set(['ledger-recon-batch']) });
    expect(keyKpis(keys, rule)).toEqual({ active: 10, disabled: 1, due: 1, dueNames: ['ledger-recon-batch'] });
    // the check names no age: "<name> is <n> days old; …" in its detail is the server's verdict
    const check = (ok: boolean, detail: string): PostureReport => ({ ...fx.posture(NOW), checks: [{ id: 'keys_rotated', name: 'API keys rotated within the maximum age', ok, detail, enforced_at_boot: false }] });
    expect(rotationRule(check(false, 'a-key is 120 days old; b-key is 91 days old'))).toEqual({ names: new Set(['a-key', 'b-key']) });
    expect(rotationRule(check(true, 'all within the rotation age'))).toEqual({ names: new Set() });
    // no verdict (no posture, or the database is down): fall back to the server's default age
    expect(rotationRule(check(false, 'database unavailable'))).toEqual({ maxAge: 90 });
    expect(rotationRule(null)).toEqual({ maxAge: 90 });
    const old = { ...keys[0], source: 'db' as const, status: 'active' as const, rotated_at: null, age_days: 200 };
    expect(rotationDue(old, { maxAge: 90 })).toBe(true);
    expect(rotationDue(old, { names: new Set() })).toBe(false);
    expect(rotationDue({ ...old, source: 'static' }, { maxAge: 90 })).toBe(false);
  });
  it('accepts the key names the server accepts (dps/auth_db.py KEY_NAME: 2-63 characters)', () => {
    expect(KEY_NAME.test('ab')).toBe(true);
    expect(KEY_NAME.test('a')).toBe(false);
    expect(KEY_NAME.test(`a${'b'.repeat(62)}`)).toBe(true);
    expect(KEY_NAME.test(`a${'b'.repeat(63)}`)).toBe(false);
    expect(KEY_NAME.test('-ab')).toBe(false);
    expect(KEY_NAME.test('Ab')).toBe(false);
  });
  it('offers only the key actions dps/auth_db.py accepts', () => {
    const base = { ...keys[0], source: 'db' as const, status: 'active' as const, rotated_at: null, disabled_at: null, expires_at: '2027-01-01T00:00:00Z' };
    expect(keyActions(base, NOW)).toEqual(['rotate', 'disable']);
    // rotated with a grace period: still active, but a key is rotated once (409 key_rotated)
    expect(keyActions({ ...base, rotated_at: '2026-10-13T09:00:00Z', expires_at: '2026-10-14T09:00:00Z' }, NOW)).toEqual(['disable']);
    // rotated with grace "now": disabled, and a rotated key is never re-enabled (409 key_rotated)
    expect(keyActions({ ...base, status: 'disabled', rotated_at: '2026-10-13T09:00:00Z', disabled_at: '2026-10-13T09:00:00Z' }, NOW)).toEqual([]);
    expect(keyActions({ ...base, status: 'disabled', disabled_at: '2026-10-12T09:00:00Z' }, NOW)).toEqual(['enable']);
    // disabled and past its expiry: 409 key_expired
    expect(keyActions({ ...base, status: 'disabled', disabled_at: '2026-10-01T09:00:00Z', expires_at: '2026-10-10T00:00:00Z' }, NOW)).toEqual([]);
    expect(keyActions({ ...base, status: 'expired', expires_at: '2026-10-10T00:00:00Z' }, NOW)).toEqual([]);
    expect(keyActions({ ...base, source: 'static' }, NOW)).toEqual([]);
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
    // a static key with every scope ("*", dps/auth.py Principal.has_scope) may write too
    const bootstrap = { ...keys[0], id: 'static-bootstrap', name: 'bootstrap', key_prefix: 'static', source: 'static' as const, tenant_ids: ['*'], scopes: ['*'] };
    expect(whoMayWrite(conns['loans-prod'], [bootstrap])).toEqual({ scope: 'admin', names: ['bootstrap'] });
    expect(scopeChips(['*'])).toEqual([{ scope: 'all scopes', privileged: true }]);
    expect(scopeChips(['detect', 'reveal'])).toEqual([{ scope: 'detect', privileged: false }, { scope: 'reveal', privileged: true }]);
    expect(keyMeta(bootstrap, NOW)).toBe('environment key');
    // rotated today with a grace period: the rotation is the news, not the creation
    const graced = { ...keys[0], source: 'db' as const, status: 'active' as const, key_prefix: 'dps_1jDa', created_at: '2026-10-13T08:00:00Z', rotated_at: '2026-10-13T09:00:00Z', expires_at: '2026-10-14T09:00:00Z' };
    expect(keyMeta(graced, NOW)).toBe('dps_1jDa… · rotated · stops 14 Oct 2026');
  });
  it('tones quotas and maps create-key refusals to words', () => {
    expect(quotaTone(17_200_000, 20_000_000)).toBe('amber');
    expect(quotaTone(5, 10)).toBe('neu');
    expect(quotaTone(5, null)).toBe('neu');
    expect(createKeyError('scope_escalation', 'x')).toContain('scope you do not hold');
    expect(createKeyError('weird', 'server said so')).toBe('server said so');
    // the codes dps/auth_db.py raises (GrantError) each have words
    for (const code of ['invalid_name', 'invalid_expiry', 'invalid_grant', 'invalid_tenant', 'invalid_scope', 'scope_escalation', 'tenant_escalation', 'key_rotated', 'key_expired', 'key_not_found', 'invalid_grace']) {
      expect(createKeyError(code, 'detail')).not.toBe('detail');
    }
    expect(createKeyError('invalid_name', 'x')).toContain('2–63');
  });
  it('lists registered hosts with their storage-state reference', () => {
    const rows = hostRows(fx.tenants(NOW), fx.connections(NOW));
    expect(rows).toHaveLength(4);
    expect(rows[0]).toMatchObject({ host: 'onboarding-uat.bank.internal', tenant: 'retail-banking-apac', storageRef: 'retail-banking-apac/dps/ui/onboarding-uat' });
    expect(rows.find((r) => r.host === 'cards-apply-uat.bank.internal')?.storageRef).toBeNull();
  });
  it('refuses a host the server would refuse before sending it (dps/netguard.py normalize_host)', () => {
    expect(hostInputError('https://Payments-UAT.bank.internal:8443/start')).toBeNull();
    expect(hostInputError('payments-uat.bank.internal')).toBeNull();
    expect(hostInputError('10.1.2.3')).toBeNull();
    expect(hostInputError('')).toMatch(/Enter a host/);
    expect(hostInputError('not a host')).toMatch(/not a valid host/);
    expect(hostInputError('*.bank.internal')).toMatch(/wildcard/);
    expect(hostInputError('https://user:pw@host.bank.internal')).toMatch(/credentials/);
  });
  it('builds health tiles from readiness and the queue', () => {
    const tiles = healthTiles(fx.readiness(), fx.queue(NOW));
    expect(tiles.map((t) => t.name)).toEqual(expect.arrayContaining(['api-core', 'db', 'worker', 'browser-worker', 'tokenizer']));
    expect(tiles.find((t) => t.name === 'worker')?.detail).toBe('6 of 7 workers busy · 1 draining');
    // dps/layers/api/routers/admin.py sends `busy` as the number of claimed slots (an int)
    const q = { queues: [], workers: [{ worker_id: 'w1', role: 'worker', version: '0', online: true, busy: 3, draining: false, last_seen: null }, { worker_id: 'w2', role: 'worker', version: '0', online: true, busy: 0, draining: false, last_seen: null }] };
    expect(healthTiles(null, q).find((t) => t.name === 'worker')?.detail).toBe('1 of 2 workers busy');
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
