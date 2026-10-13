/** Pure logic of the Admin page (mockup 11): key KPIs and labels, who may write to a connection,
 *  quota tones, registered hosts, health tiles and the configuration export. Value-free: key
 *  prefixes and hashes only — a raw key never reaches these helpers. Tested in admin.test.ts. */
import { utcDate } from './format';
import type { ApiKeyView, Connection, PostureReport, QueueOut, Readiness, TenantView } from './types';

export const SCOPES = ['detect', 'obfuscate', 'synthesize', 'reveal', 'jobs', 'scan', 'admin'] as const;
export const PRIVILEGED = new Set(['reveal', 'admin']);
export const EXPIRY_CHOICES: [number, string][] = [[30, '30 days'], [90, '90 days'], [180, '180 days'], [365, '1 year']];
export const KEY_NAME = /^[a-z0-9][a-z0-9-]{2,62}$/;

/** The rotation age the posture check uses ("Keys rotated within 90 days"), default 90. */
export function maxKeyAge(posture: PostureReport | null): number {
  const name = posture?.checks.find((c) => c.id === 'keys_rotated')?.name ?? '';
  const m = /(\d+)\s*days/.exec(name);
  return m ? Number(m[1]) : 90;
}

/** An active database key past the rotation age and not yet rotated. */
export function rotationDue(k: ApiKeyView, maxAge: number): boolean {
  return k.source === 'db' && k.status === 'active' && !k.rotated_at && (k.age_days ?? 0) > maxAge;
}

export function keyKpis(keys: ApiKeyView[], maxAge: number): { active: number; disabled: number; due: number; dueNames: string[] } {
  const due = keys.filter((k) => rotationDue(k, maxAge));
  return { active: keys.filter((k) => k.status === 'active').length, disabled: keys.filter((k) => k.status === 'disabled').length, due: due.length, dueNames: due.map((k) => k.name) };
}

/** The caller's own key, matched by its 8-character prefix (the console holds the raw key it
 *  sends; only the prefix is compared, nothing is derived from the rest). */
export function callerKey(keys: ApiKeyView[], apiKey: string): ApiKeyView | null {
  if (apiKey.length <= 8) return null;
  return keys.find((k) => k.key_prefix === apiKey.slice(0, 8)) ?? null;
}

/** "dps_9Fq2…", plus "· new" for a key created today or "· disabled 02 Sep 2026". */
export function keyMeta(k: ApiKeyView, now: Date = new Date()): string {
  const parts = [`${k.key_prefix}…`];
  if (k.status === 'disabled') parts.push(`disabled ${utcDate(k.disabled_at)}`);
  else if (k.status === 'expired') parts.push(k.rotated_at ? 'rotated, expired' : 'expired');
  else if (k.created_at && k.created_at.slice(0, 10) === now.toISOString().slice(0, 10)) parts.push('new');
  else if (k.rotated_at) parts.push(`rotated · stops ${utcDate(k.expires_at)}`);
  return parts.join(' · ');
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
  const names = keys.filter((k) => k.status === 'active' && k.scopes.includes(scope) && covers(k, c.tenant_id)).map((k) => k.name);
  return { scope, names };
}

/** Amber from 80 % of a quota (mockup 11), neutral otherwise or without a quota. */
export function quotaTone(used: number, quota: number | null | undefined): 'amber' | 'neu' {
  return quota && quota > 0 && used / quota >= 0.8 ? 'amber' : 'neu';
}

const CREATE_ERRORS: Record<string, string> = {
  invalid_name: 'Use 3–63 lowercase letters, digits and dashes.',
  key_name_exists: 'A key with this name exists. Rotate it instead, or pick another name.',
  scope_escalation: 'You cannot grant a scope you do not hold yourself.',
  tenant_escalation: 'You cannot grant a tenant your key does not cover.',
  invalid_expiry: 'Choose an expiry between 1 day and 1 year.',
  invalid_scopes: 'Choose at least one scope.',
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

export interface HealthTile { name: string; ok: boolean; label: string; detail: string }

/** One tile per readiness component, plus worker and browser-worker from the queue heartbeats. */
export function healthTiles(ready: Readiness | null, queue: QueueOut | null): HealthTile[] {
  const tiles: HealthTile[] = Object.entries(ready?.components ?? {}).map(([name, c]) => ({ name, ok: c.ok, label: c.ok ? 'ready' : 'degraded', detail: c.detail }));
  for (const role of ['worker', 'browser-worker']) {
    const ws = (queue?.workers ?? []).filter((w) => w.role === role && w.online);
    if (!queue) continue;
    const busy = ws.filter((w) => w.busy).length;
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
