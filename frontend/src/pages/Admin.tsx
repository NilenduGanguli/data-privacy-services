import { useCallback, useEffect, useMemo, useState } from 'react';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { CreateKeyCard } from '../components/CreateKeyCard';
import { DownloadButton } from '../components/DownloadButton';
import { HealthGrid } from '../components/HealthGrid';
import { HostsCard } from '../components/HostsCard';
import { KeyOnceCard } from '../components/KeyOnceCard';
import { KeysTable } from '../components/KeysTable';
import { Kpi } from '../components/Kpi';
import { PostureList } from '../components/PostureList';
import { TenantsTable } from '../components/TenantsTable';
import { useAuth } from '../hooks/useAuth';
import { useSettings } from '../hooks/useSettings';
import {
  callerKey, configExport, createKeyError, healthTiles, hostRows, keyKpis, maxKeyAge, whoMayWrite, type HostRow,
} from '../lib/admin';
import { api, ApiError, errorText, type Download } from '../lib/api';
import { count, relative } from '../lib/format';
import { engineName } from '../lib/jobs';
import type {
  ApiKeyView, Connection, CreatedKey, CreateKeyBody, PostureReport, QueueOut, Readiness, RotateResult, TenantPatch, TenantSpec, TenantView,
} from '../lib/types';

/** Every Admin action goes through `api.admin` (audited server-side); the page reloads after each. */
export interface AdminActions {
  createKey: (body: CreateKeyBody) => Promise<CreatedKey>;
  rotateKey: (id: string, graceHours: number) => Promise<RotateResult>;
  disableKey: (id: string) => Promise<unknown>;
  enableKey: (id: string) => Promise<unknown>;
  createTenant: (spec: TenantSpec) => Promise<unknown>;
  patchTenant: (id: string, patch: TenantPatch) => Promise<unknown>;
  exportConfig: () => Promise<Download>;
}

export interface AdminData {
  now: Date; apiKey: string; tenant: string; ready: Readiness | null; keys: ApiKeyView[]; tenants: TenantView[]; posture: PostureReport | null;
  queue: QueueOut | null; connections: Connection[]; forbidden: boolean; error: string | null;
}

const GRACE: [number, string][] = [[0, 'now'], [24, 'in 24 hours'], [168, 'in 7 days']];
const message = (e: unknown): string => (e instanceof ApiError ? createKeyError(e.code, `${e.message}${e.code ? ` (${e.code})` : ''}`) : errorText(e));

interface TenantForm { id: string; display_name: string; retention_days: string; max_running: string; max_queued: string; daily_row_quota: string; note: string }
const intOrNull = (v: string): number | null => (v.trim() === '' ? null : Number(v));

function tenantForm(t: TenantView | null): TenantForm {
  return {
    id: t?.tenant_id ?? '', display_name: t?.display_name ?? '', retention_days: String(t?.effective.retention_days ?? 30), max_running: String(t?.effective.max_running ?? 2),
    max_queued: String(t?.effective.max_queued ?? 200), daily_row_quota: t?.effective.daily_row_quota === null || t?.effective.daily_row_quota === undefined ? '' : String(t.effective.daily_row_quota), note: t?.note ?? '',
  };
}

function ConnectionsCard({ connections, keys }: { connections: Connection[]; keys: ApiKeyView[] }): JSX.Element {
  return (
    <section className="card" aria-labelledby="connT">
      <div className="card-head"><div className="card-title" id="connT">Registered connections</div>
        <span className="legend"><span><i style={{ background: 'var(--amber)' }} />production · writes need admin</span></span></div>
      <div className="tbl-wrap"><table className="tbl stack"><thead><tr><th>Connection</th><th>Secret reference</th><th>Tags</th><th>Who may write in place or append</th><th>Last test</th></tr></thead><tbody>
        {connections.map((c) => {
          const who = whoMayWrite(c, keys);
          const check = c.last_test?.checks?.[0];
          return (
            <tr key={c.id}>
              <td data-l="Connection"><div className="kname">{c.name}</div><div className="sub">{engineName(c.engine)}{c.last_test?.server_version ? ` ${c.last_test.server_version}` : ''} · {c.params.service_name ?? c.params.database ?? c.params.bucket ?? c.params.base_path ?? c.params.host ?? '—'}</div></td>
              <td className="mono secref" data-l="Secret reference">{c.secret_ref ?? (c.has_secret ? `inline · expires ${relative(c.secret_expires_at)}` : '—')}</td>
              <td data-l="Tags"><div className="scopes">{c.tags.map((t) => <span key={t} className={`chip ${t === 'production' ? 'amber' : 'slate'}`}>{t}</span>)}</div></td>
              <td className="who" data-l="Who may write">{who.scope === 'files' ? 'Not applicable · files only' : <>Keys with <span className={`chip ${who.scope === 'admin' ? 'amber' : 'slate'}`} style={{ fontSize: 11 }}>{who.scope}</span> · {who.names.length ? who.names.join(', ') : 'none yet'}<div className="sub">plus a confirmation with row counts</div></>}</td>
              <td className="dt" data-l="Last test">{c.last_test ? `${relative(c.updated_at)} · ${check ? `${check.name}${check.ok ? '' : ' failed'}` : c.last_test.ok ? 'ok' : 'failed'}` : 'never'}</td>
            </tr>
          );
        })}
        {!connections.length && <tr className="empty-row"><td colSpan={5}>No connection is registered for this tenant.</td></tr>}
      </tbody></table></div>
      <div className="callout sm" style={{ marginTop: 12 }}>In place rewrites the selected rows in the source table; append adds transformed copies as new rows. On connections tagged <b>production</b> both need a key with the admin scope; elsewhere the jobs scope. The console always asks for a confirmation with row counts first.</div>
    </section>
  );
}

/** Admin (mockup 11) as a view: data and actions in; dialogs and one-time keys in local state. */
export function AdminView({ actions, ...d }: AdminData & { actions: AdminActions }): JSX.Element {
  const maxAge = maxKeyAge(d.posture);
  const kpis = keyKpis(d.keys, maxAge);
  const me = callerKey(d.keys, d.apiKey);
  const tenantIds = useMemo(() => [...new Set([...d.tenants.map((t) => t.tenant_id), ...(d.tenant ? [d.tenant] : [])])], [d.tenants, d.tenant]);
  const hosts = useMemo(() => hostRows(d.tenants, d.connections), [d.tenants, d.connections]);
  const tiles = healthTiles(d.ready, d.queue);
  const running = d.tenants.reduce((a, t) => a + t.usage.running, 0);
  const allowed = d.tenants.reduce((a, t) => a + t.effective.max_running, 0);
  const failing = d.posture?.checks.filter((c) => !c.ok) ?? [];

  const [rotate, setRotate] = useState<{ key: ApiKeyView; grace: number; busy: boolean; error: string | null } | null>(null);
  const [rotated, setRotated] = useState<{ n: number; name: string; key: string | null } | null>(null);
  const [toggle, setToggle] = useState<{ key: ApiKeyView; busy: boolean; error: string | null } | null>(null);
  const [tenantDlg, setTenantDlg] = useState<{ edit: TenantView | null; form: TenantForm; busy: boolean; error: string | null } | null>(null);
  const [removeHost, setRemoveHost] = useState<{ row: HostRow; busy: boolean; error: string | null } | null>(null);

  async function doRotate(): Promise<void> {
    if (!rotate) return;
    setRotate({ ...rotate, busy: true, error: null });
    try {
      const out = await actions.rotateKey(rotate.key.id, rotate.grace);
      setRotated((r) => ({ n: (r?.n ?? 0) + 1, name: out.new.record.name, key: out.new.key }));
      setRotate(null);
    } catch (e) { setRotate({ ...rotate, busy: false, error: message(e) }); }
  }
  async function doToggle(): Promise<void> {
    if (!toggle) return;
    setToggle({ ...toggle, busy: true, error: null });
    try { await (toggle.key.status === 'disabled' ? actions.enableKey(toggle.key.id) : actions.disableKey(toggle.key.id)); setToggle(null); }
    catch (e) { setToggle({ ...toggle, busy: false, error: message(e) }); }
  }
  async function doTenant(): Promise<void> {
    if (!tenantDlg) return;
    const f = tenantDlg.form;
    setTenantDlg({ ...tenantDlg, busy: true, error: null });
    const patch: TenantPatch = {
      display_name: f.display_name.trim() || null, retention_days: intOrNull(f.retention_days), max_running: intOrNull(f.max_running),
      max_queued: intOrNull(f.max_queued), daily_row_quota: intOrNull(f.daily_row_quota), note: f.note.trim() || null,
    };
    try {
      if (tenantDlg.edit) await actions.patchTenant(tenantDlg.edit.tenant_id, patch);
      else await actions.createTenant({ tenant_id: f.id.trim(), ...patch });
      setTenantDlg(null);
    } catch (e) { setTenantDlg({ ...tenantDlg, busy: false, error: message(e) }); }
  }
  async function doRemoveHost(): Promise<void> {
    if (!removeHost) return;
    const t = d.tenants.find((x) => x.tenant_id === removeHost.row.tenant);
    setRemoveHost({ ...removeHost, busy: true, error: null });
    try {
      await actions.patchTenant(removeHost.row.tenant, { registered_hosts: (t?.effective.registered_hosts ?? []).filter((h) => h !== removeHost.row.host) });
      setRemoveHost(null);
    } catch (e) { setRemoveHost({ ...removeHost, busy: false, error: message(e) }); }
  }
  async function addHost(tenant: string, host: string): Promise<void> {
    const t = d.tenants.find((x) => x.tenant_id === tenant);
    await actions.patchTenant(tenant, { registered_hosts: [...(t?.effective.registered_hosts ?? []), host] });
  }
  const setField = (k: keyof TenantForm, v: string): void => setTenantDlg((s) => (s ? { ...s, form: { ...s.form, [k]: v } } : s));
  const field = (k: keyof TenantForm, label: string, numeric = true): JSX.Element => (
    <div className="frow"><label htmlFor={`tf-${k}`}>{label}</label>
      <input id={`tf-${k}`} className="input" inputMode={numeric ? 'numeric' : undefined} value={tenantDlg?.form[k] ?? ''} onChange={(e) => setField(k, e.target.value)} /></div>
  );

  return (
    <main className="page m5-page admin-page">
      <div className="crumbbar">
        <span className="crumb">Admin</span><span className="sep">/</span><span className="muted">{d.posture?.env ?? 'deployment'}</span>
        <span className="spacer" />
        {d.posture && <span className="pill"><span className={`dot${failing.length ? ' amber' : ''}`} />Posture · {d.posture.passed} of {d.posture.total} checks pass</span>}
        {me && <span className="pill mono">{me.name} · {me.scopes.join(', ')}</span>}
      </div>
      <div className="page-head">
        <div><h1>Admin</h1><p>Keys, tenants, connections and health for this deployment{d.posture ? <> (<b>{d.posture.env}</b>)</> : null}. Every change on this page is written to the audit log.</p></div>
        {!d.forbidden && <div className="head-actions">
          <DownloadButton className="btn" label="Export configuration" ariaLabel="Export configuration as JSON (no secrets)" fetcher={actions.exportConfig} />
          <button className="btn btn-primary" type="button" onClick={() => document.getElementById('createCard')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Create key</button>
        </div>}
      </div>
      {d.error && <div className="err" role="alert">{d.error}</div>}

      {d.forbidden ? (
        <>
          <div className="callout amber">Admin needs a key with the admin scope. Paste one in the API key field above; health below is public.</div>
          <HealthGrid tiles={tiles} />
        </>
      ) : (
        <>
          <div className="grid cols-4">
            <Kpi label="Active API keys" value={count(kpis.active)} delta={`${count(kpis.disabled)} disabled · ${count(kpis.due)} due for rotation`} />
            <Kpi label="Tenants" value={count(d.tenants.length)} delta={`${count(running)} jobs running of ${count(allowed)} allowed`} />
            <Kpi label="Posture checks" value={d.posture ? <>{d.posture.passed} <span className="faint" style={{ fontSize: 16, fontWeight: 600 }}>of {d.posture.total} pass</span></> : '—'}
              delta={failing.length ? `${failing.length} to fix: ${failing[0].name}` : 'nothing to fix'} />
            <Kpi zero label="Keys stored in clear" value={0} delta="SHA-256 hashes only · each key shown once" />
          </div>

          <KeysTable keys={d.keys} now={d.now} maxAge={maxAge} onRotate={(key) => setRotate({ key, grace: 24, busy: false, error: null })}
            onDisable={(key) => setToggle({ key, busy: false, error: null })} onEnable={(key) => setToggle({ key, busy: false, error: null })} />
          {rotated && <KeyOnceCard key={rotated.n} name={rotated.name} rawKey={rotated.key} onStored={() => setRotated((r) => (r ? { ...r, key: null } : r))} />}

          <div className="row-c">
            <CreateKeyCard tenants={tenantIds} defaultTenant={d.tenant} onCreate={actions.createKey} />
            <TenantsTable tenants={d.tenants} onEdit={(t) => setTenantDlg({ edit: t, form: tenantForm(t), busy: false, error: null })} onAdd={() => setTenantDlg({ edit: null, form: tenantForm(null), busy: false, error: null })} />
          </div>
          <ConnectionsCard connections={d.connections} keys={d.keys} />
          <HostsCard rows={hosts} tenants={tenantIds} defaultTenant={d.tenant} onAdd={addHost} onRemove={(row) => setRemoveHost({ row, busy: false, error: null })} />
          <div className="row-e">
            <HealthGrid tiles={tiles} />
            {d.posture && <PostureList posture={d.posture} now={d.now} dueKeys={kpis.dueNames} onRotate={(name) => { const key = d.keys.find((k) => k.name === name); if (key) setRotate({ key, grace: 24, busy: false, error: null }); }} />}
          </div>
        </>
      )}

      <ConfirmDialog open={!!rotate} title={rotate ? `Rotate ${rotate.key.name}` : 'Rotate'} confirmLabel="Rotate key" busy={rotate?.busy} error={rotate?.error}
        body={<>DPS creates a new key with the same name, tenants and scopes and shows it once. Store it, update <span className="mono">dps/keys/{rotate?.key.name}</span>, then let the old key lapse.</>}
        onConfirm={() => void doRotate()} onCancel={() => setRotate(null)}>
        <div className="sm" style={{ fontWeight: 600, color: 'var(--ink-2)', marginBottom: 6 }}>Old key stops working</div>
        <div className="seg" role="group" aria-label="Old key stops working">{GRACE.map(([h, label]) => (
          <button key={h} type="button" className={rotate?.grace === h ? 'on' : ''} aria-pressed={rotate?.grace === h} onClick={() => setRotate((r) => (r ? { ...r, grace: h } : r))}>{label}</button>))}</div>
      </ConfirmDialog>
      <ConfirmDialog open={!!toggle} title={toggle ? `${toggle.key.status === 'disabled' ? 'Re-enable' : 'Disable'} ${toggle.key.name}` : ''} danger={toggle?.key.status !== 'disabled'}
        confirmLabel={toggle?.key.status === 'disabled' ? 'Re-enable' : 'Disable'} busy={toggle?.busy} error={toggle?.error}
        body={toggle?.key.status === 'disabled' ? 'Calls with this key are accepted again within a few seconds.' : 'Calls with this key are refused within a few seconds. Running jobs it started keep running; re-enable it at any time.'}
        onConfirm={() => void doToggle()} onCancel={() => setToggle(null)} />
      <ConfirmDialog open={!!tenantDlg} title={tenantDlg?.edit ? `Edit ${tenantDlg.edit.tenant_id}` : 'Add tenant'} confirmLabel={tenantDlg?.edit ? 'Save' : 'Add tenant'} busy={tenantDlg?.busy} error={tenantDlg?.error}
        body="Empty fields fall back to the service defaults." onConfirm={() => void doTenant()} onCancel={() => setTenantDlg(null)}>
        {!tenantDlg?.edit && field('id', 'Tenant id', false)}
        {field('display_name', 'Display name', false)}
        {field('retention_days', 'Retention days')}
        {field('max_running', 'Max running')}
        {field('max_queued', 'Max queued')}
        {field('daily_row_quota', 'Daily row quota')}
        {field('note', 'Note', false)}
      </ConfirmDialog>
      <ConfirmDialog open={!!removeHost} title={removeHost ? `Remove ${removeHost.row.host}` : ''} danger confirmLabel="Remove host" busy={removeHost?.busy} error={removeHost?.error}
        body={<>New scans of <span className="mono">{removeHost?.row.host}</span> for {removeHost?.row.tenant} are refused once it is removed. Finished scans and their evidence stay.</>}
        onConfirm={() => void doRemoveHost()} onCancel={() => setRemoveHost(null)} />
    </main>
  );
}

/** Admin (mockup 11): GET /admin/keys, /admin/tenants, /admin/posture, /admin/queue, M2
 *  /connections and /readyz; every action through `api.admin`, then a reload. */
export function Admin(): JSX.Element {
  const auth = useAuth();
  const { apiKey, tenant } = useSettings();
  const [ready, setReady] = useState<Readiness | null>(null);
  const [keys, setKeys] = useState<ApiKeyView[]>([]);
  const [tenants, setTenants] = useState<TenantView[]>([]);
  const [posture, setPosture] = useState<PostureReport | null>(null);
  const [queue, setQueue] = useState<QueueOut | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    api.readyz().then(setReady).catch(() => setReady(null));
    try {
      const [k, t] = await Promise.all([api.admin.keys(auth), api.admin.tenants(auth)]);
      setKeys(k.items); setTenants(t.items); setForbidden(false); setError(null);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) { setForbidden(true); return; }
      setError(errorText(e));
    }
    api.admin.posture(auth).then(setPosture).catch(() => setPosture(null));
    api.admin.queue(auth).then(setQueue).catch(() => setQueue(null));
    api.connections.list(auth).then((r) => setConnections(r.items)).catch(() => setConnections([]));
  }, [auth]);
  useEffect(() => { void load(); }, [load]);

  const after = <T,>(p: Promise<T>): Promise<T> => p.then(async (v) => { await load(); return v; });
  const actions: AdminActions = {
    createKey: (body) => after(api.admin.createKey(auth, body)),
    rotateKey: (id, grace) => after(api.admin.rotateKey(auth, id, grace)),
    disableKey: (id) => after(api.admin.disableKey(auth, id)),
    enableKey: (id) => after(api.admin.enableKey(auth, id)),
    createTenant: (spec) => after(api.admin.createTenant(auth, spec)),
    patchTenant: (id, patch) => after(api.admin.patchTenant(auth, id, patch)),
    exportConfig: async () => ({
      blob: new Blob([JSON.stringify(configExport({ tenants, keys, connections, posture }), null, 2)], { type: 'application/json' }),
      filename: `dps-config-${new Date().toISOString().slice(0, 10)}.json`,
    }),
  };
  return <AdminView now={new Date()} apiKey={apiKey} tenant={tenant} ready={ready} keys={keys} tenants={tenants} posture={posture} queue={queue}
    connections={connections} forbidden={forbidden} error={error} actions={actions} />;
}
