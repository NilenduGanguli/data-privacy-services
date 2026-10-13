/** Pure logic of the Admin page (mockup 11): key KPIs and labels, who may write to a connection,
 *  quota tones, registered hosts, health tiles and the configuration export. Value-free: key
 *  prefixes and hashes only — a raw key never reaches these helpers. Tested in admin.test.ts. */
import { utcDate } from './format';
import type { ApiKeyView, Connection, PostureReport, QueueOut, Readiness, TenantView } from './types';

export const SCOPES = ['detect', 'obfuscate', 'synthesize', 'reveal', 'jobs', 'scan', 'admin'] as const;
export const PRIVILEGED = new Set(['reveal', 'admin']);
export const EXPIRY_CHOICES: [number, string][] = [[30, '30 days'], [90, '90 days'], [180, '180 days'], [365, '1 year']];
/** dps/auth_db.py KEY_NAME: 2–63 lowercase letters, digits and dashes, not starting with a dash. */
export const KEY_NAME = /^[a-z0-9][a-z0-9-]{1,62}$/;

/** Which keys are due for rotation. The posture check `keys_rotated` (dps/posture.py) names them in
 *  its detail ("<name> is <n> days old; …", "all within the rotation age" when none is): that is
 *  the server's verdict under its configured maximum age. Without it (no posture, database down)
 *  the server's default age applies. */
export type RotationRule = { names: ReadonlySet<string> } | { maxAge: number };
export const DEFAULT_MAX_KEY_AGE_DAYS = 90;
const STALE_KEY = /^([a-z0-9][a-z0-9-]*) is \d+ days old$/;

export function rotationRule(posture: PostureReport | null): RotationRule {
  const check = posture?.checks.find((c) => c.id === 'keys_rotated');
  if (!check) return { maxAge: DEFAULT_MAX_KEY_AGE_DAYS };
  if (check.ok) return { names: new Set() };
  const names = check.detail.split(';').map((part) => STALE_KEY.exec(part.trim())?.[1]).filter((n): n is string => !!n);
  return names.length ? { names: new Set(names) } : { maxAge: DEFAULT_MAX_KEY_AGE_DAYS };
}

/** An active database key, never rotated, that the rule finds due. */
export function rotationDue(k: ApiKeyView, rule: RotationRule): boolean {
  if (k.source !== 'db' || k.status !== 'active' || k.rotated_at) return false;
  return 'names' in rule ? rule.names.has(k.name) : (k.age_days ?? 0) > rule.maxAge;
}

export type KeyAction = 'rotate' | 'disable' | 'enable';

/** What dps/auth_db.py lets an admin do with a key: rotate an active key once; disable an active
 *  one; re-enable a disabled key that was never rotated and has not expired. Environment (static)
 *  keys are managed in their secret file. */
export function keyActions(k: ApiKeyView, now: Date = new Date()): KeyAction[] {
  if (k.source === 'static') return [];
  if (k.status === 'active') return k.rotated_at ? ['disable'] : ['rotate', 'disable'];
  const expired = !!k.expires_at && Date.parse(k.expires_at) <= now.getTime();
  return k.status === 'disabled' && !k.rotated_at && !expired ? ['enable'] : [];
}

export function keyKpis(keys: ApiKeyView[], rule: RotationRule): { active: number; disabled: number; due: number; dueNames: string[] } {
  const due = keys.filter((k) => rotationDue(k, rule));
  return { active: keys.filter((k) => k.status === 'active').length, disabled: keys.filter((k) => k.status === 'disabled').length, due: due.length, dueNames: due.map((k) => k.name) };
}

/** The caller's own key, matched by its 8-character prefix (the console holds the raw key it
 *  sends; only the prefix is compared, nothing is derived from the rest). */
export function callerKey(keys: ApiKeyView[], apiKey: string): ApiKeyView | null {
  if (apiKey.length <= 8) return null;
  return keys.find((k) => k.key_prefix === apiKey.slice(0, 8)) ?? null;
}

/** What the rotate dialog says when the key being rotated is the one this console calls with:
 *  with grace "now" the console loses access the moment the rotation completes (r1 B1). */
export function rotateWarning(key: ApiKeyView, graceHours: number, keys: ApiKeyView[], apiKey: string): { tone: 'rose' | 'amber'; text: string } | null {
  if (callerKey(keys, apiKey)?.id !== key.id) return null;
  if (graceHours === 0) {
    return { tone: 'rose', text: 'This is the key this console uses. It stops working as soon as the rotation completes: the new key is shown once below, '
      + 'store it, then paste it in the API key field above to keep using Admin.' };
  }
  return { tone: 'amber', text: 'This is the key this console uses. Store the new key, then paste it in the API key field above before the old one stops working.' };
}

/** "dps_9Fq2…", plus "· new" for a key created today or "· disabled 02 Sep 2026". */
export function keyMeta(k: ApiKeyView, now: Date = new Date()): string {
  if (k.source === 'static') return 'environment key';
  const parts = [`${k.key_prefix}…`];
  if (k.status === 'disabled') parts.push(`${k.rotated_at ? 'rotated, ' : ''}disabled ${utcDate(k.disabled_at)}`);
  else if (k.status === 'expired') parts.push(k.rotated_at ? 'rotated, expired' : 'expired');
  else if (k.rotated_at) parts.push(`rotated · stops ${utcDate(k.expires_at)}`);
  else if (k.created_at && k.created_at.slice(0, 10) === now.toISOString().slice(0, 10)) parts.push('new');
  return parts.join(' · ');
}

/** Scope chips of a key; `*` (every scope, dps/auth.py) reads "all scopes" and is privileged. */
export function scopeChips(scopes: string[]): { scope: string; privileged: boolean }[] {
  if (scopes.includes('*')) return [{ scope: 'all scopes', privileged: true }];
  return scopes.map((scope) => ({ scope, privileged: PRIVILEGED.has(scope) }));
}

export function tenantsLabel(ids: string[]): { main: string; more: string | null } {
  if (ids.includes('*')) return { main: 'all tenants (*)', more: null };
  return { main: ids[0] ?? '—', more: ids.length > 1 ? `+ ${ids.slice(1).join(', ')}` : null };
}

const covers = (k: ApiKeyView, tenant: string): boolean => k.tenant_ids.includes('*') || k.tenant_ids.includes(tenant);

/** Who may run an in-place or append write against a connection (contract §5.3): production →
 *  keys with `admin` for its tenant; otherwise keys with `jobs`; files-only engines → nobody. */
export function whoMayWrite(c: Connection, keys: ApiKeyView[]): { scope: 'admin' | 'jobs' | 'files'; names: string[] } {
  if (c.engine === 's3' || c.engine === 'pvc') return { scope: 'files', names: [] };
  const scope = c.tags.includes('production') ? 'admin' : 'jobs';
  const names = keys.filter((k) => k.status === 'active' && (k.scopes.includes(scope) || k.scopes.includes('*')) && covers(k, c.tenant_id)).map((k) => k.name);
  return { scope, names };
}

/** Amber from 80 % of a quota (mockup 11), neutral otherwise or without a quota. */
export function quotaTone(used: number, quota: number | null | undefined): 'amber' | 'neu' {
  return quota && quota > 0 && used / quota >= 0.8 ? 'amber' : 'neu';
}

/** Words for the codes dps/auth_db.py (GrantError) and dps/layers/api/routers/admin.py raise. */
const CREATE_ERRORS: Record<string, string> = {
  invalid_name: 'Use 2–63 lowercase letters, digits and dashes, starting with a letter or digit.',
  invalid_expiry: 'Choose an expiry between 1 day and the longest the server allows (1 year by default).',
  invalid_grant: 'A key needs at least one tenant and one scope.',
  invalid_tenant: 'Tenant ids are lowercase letters, digits, dashes and underscores, or * for every tenant.',
  invalid_scope: 'One of the scopes is not a DPS scope.',
  scope_escalation: 'You cannot grant a scope you do not hold yourself.',
  tenant_escalation: 'You cannot grant a tenant your key does not cover.',
  key_rotated: 'This key was already rotated. Rotate its successor instead.',
  key_expired: 'This key has expired and cannot be re-enabled. Create a new key.',
  key_not_found: 'This key no longer exists or is outside your tenants. Reload the page.',
  invalid_grace: 'The grace period is longer than the server allows.',
};

/** Words for a create/rotate refusal by `code`; the server's value-free detail otherwise. */
export function createKeyError(code: string | undefined, detail: string): string {
  return (code && CREATE_ERRORS[code]) || detail;
}

export interface HostRow { host: string; tenant: string; storageRef: string | null; added: string | null }

/** Registered UI-scanner hosts per tenant; the storage-state reference is the `secret_ref` of the
 *  tenant's connection tagged `host:<host>` (or whose `params.host` is the host), if any. */
export function hostRows(tenants: TenantView[], connections: Connection[]): HostRow[] {
  return tenants.flatMap((t) => t.effective.registered_hosts.map((host) => {
    const conn = connections.find((c) => c.tenant_id === t.tenant_id && (c.tags.includes(`host:${host}`) || (c.tags.includes('ui-scanner') && c.params.host === host)));
    return { host, tenant: t.tenant_id, storageRef: conn?.secret_ref ?? null, added: t.updated_at };
  }));
}

/** Why the server would refuse a host to register (dps/netguard.py normalize_host: a URL or a
 *  bare host name or IP; no credentials, no wildcard), or null. The server stays the judge: its
 *  422 is shown as is. Checked here so an obvious slip costs no refused request. */
export function hostInputError(value: string): string | null {
  const v = value.trim();
  if (!v) return 'Enter a host such as https://host.bank.internal.';
  if (v.includes('*')) return 'A registered host cannot be a wildcard. Register each host.';
  const rest = v.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '');
  if (/^[^/?#]*@/.test(rest)) return 'A registered host must not carry credentials.';
  const host = rest.split(/[/?#]/)[0].replace(/:\d*$/, '');
  return /^[a-z0-9.-]+$/i.test(host) || /^\[[0-9a-f:.]+\]$/i.test(host) ? null : 'This is not a valid host. Enter a DNS name or an IP address such as host.bank.internal.';
}

export interface HealthTile { name: string; ok: boolean; label: string; detail: string }

/** One tile per readiness component, plus worker and browser-worker from the queue heartbeats. */
export function healthTiles(ready: Readiness | null, queue: QueueOut | null): HealthTile[] {
  const tiles: HealthTile[] = Object.entries(ready?.components ?? {}).map(([name, c]) => ({ name, ok: c.ok, label: c.ok ? 'ready' : 'degraded', detail: c.detail }));
  for (const role of ['worker', 'browser-worker']) {
    const ws = (queue?.workers ?? []).filter((w) => w.role === role && w.online);
    if (!queue) continue;
    const busy = ws.filter((w) => w.busy > 0).length;
    const draining = ws.filter((w) => w.draining).length;
    tiles.push({ name: role, ok: ws.length > 0, label: ws.length ? 'online' : 'none online',
      detail: ws.length ? `${busy} of ${ws.length} ${role === 'worker' ? 'workers' : 'browser workers'} busy${draining ? ` · ${draining} draining` : ''}` : 'no heartbeat in the last minute' });
  }
  return tiles;
}

/** The configuration export: tenants, key metadata, connection metadata and the posture report.
 *  None of these responses carries a secret, so nothing has to be removed. */
export function configExport(d: { tenants: TenantView[]; keys: ApiKeyView[]; connections: Connection[]; posture: PostureReport | null }, now: Date = new Date()): {
  generated_at: string; tenants: TenantView[]; keys: ApiKeyView[]; connections: Connection[]; posture: PostureReport | null;
} {
  return { generated_at: now.toISOString(), tenants: d.tenants, keys: d.keys, connections: d.connections, posture: d.posture };
}
