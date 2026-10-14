import { useState } from 'react';
import { CheckList } from '../../components/CheckList';
import { api, ApiError, type Auth } from '../../lib/api';
import { connectionFields, connectionParams, secretRefHelp, versionLabel, type FieldDef, type SourceEngine } from '../../lib/engines';
import { engineName, fmtClock, type WizardDraft } from '../../lib/jobs';
import type { Connection, ConnectionSpec } from '../../lib/types';
import type { WizardAction } from './state';

const NAME_RE = /^[a-z0-9][a-z0-9-]{1,62}$/;
/** A secret reference (server: `dps.secrets.SECRET_REF_PATTERN`): relative to the tenant's secret folder, or `_shared/…`. */
const SECRET_RE = /^(?:_shared\/)?[a-z0-9][a-z0-9/_.-]{0,200}$/;

function DbIcon(): JSX.Element {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M3 14h18M9 9v11" /></svg>;
}

/** The form value of a connection field (M3: Oracle's service name and the engines' options). */
function fieldValue(d: WizardDraft, key: FieldDef['key']): string {
  if (key === 'host') return d.host;
  if (key === 'port') return d.port;
  if (key === 'database') return d.database;
  if (key === 'service_name') return d.serviceName;
  return d.connOptions[key.slice('options.'.length)] ?? '';
}

function fieldPatch(d: WizardDraft, key: FieldDef['key'], value: string): Partial<WizardDraft> {
  if (key === 'host') return { host: value };
  if (key === 'port') return { port: value };
  if (key === 'database') return { database: value };
  if (key === 'service_name') return { serviceName: value };
  return { connOptions: { ...d.connOptions, [key.slice('options.'.length)]: value } };
}

function fieldValues(d: WizardDraft): Record<string, string> {
  return Object.fromEntries(connectionFields(d.engine).map((f) => [f.key, fieldValue(d, f.key)]));
}

/** Validation of a new connection's fields (value-free messages). */
function newConnectionProblem(d: WizardDraft): string | null {
  if (!NAME_RE.test(d.newName)) return 'Name: lower-case letters, digits and hyphens, 2 to 63 characters.';
  const { problem } = connectionParams(d.engine, fieldValues(d), { secretRef: d.credMode === 'secret_ref' });
  if (problem) return problem;
  if (d.credMode === 'secret_ref' && !SECRET_RE.test(d.secretRef)) return 'Secret name: lower-case letters, digits and / _ . - only, relative to this tenant\'s secret folder (or under _shared/).';
  if (d.credMode === 'inline' && (!d.username.trim() || !d.password)) return 'Username and password are required.';
  return null;
}

const ENGINES: { engine: SourceEngine; title: string; text: string; meta: string }[] = [
  { engine: 'postgres', title: 'PostgreSQL', text: '14 to 16. Reads in read-only transactions with a statement timeout; loads with COPY.', meta: 'Schemas and tables · keyset on the primary key' },
  { engine: 'oracle', title: 'Oracle Database', text: '19c and 23ai, thin-mode driver. Reads never lock; writes MERGE by key in committed batches.', meta: 'Owners and tables · key-range or ROWID partitions' },
  { engine: 'mongo', title: 'MongoDB', text: '6 and 7, documents through the JSON engine. In place and Append need a replica set.', meta: 'Databases and collections · keyset on _id' },
];

/** Step 1 · Connect to the source: engine, saved or new connection, target connection, test. */
export function StepConnect({ d, dispatch, auth, connections, onConnectionsChanged, onNext }: {
  d: WizardDraft; dispatch: React.Dispatch<WizardAction>; auth: Auth; connections: Connection[];
  onConnectionsChanged: () => void; onNext: () => void;
}): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [writable, setWritable] = useState(false);
  const sources = connections.filter((c) => c.engine === d.engine && c.roles.includes('source'));
  const fields = connectionFields(d.engine);
  const transactions = d.test?.capabilities?.transactions;
  const targets = connections.filter((c) => c.roles.includes('target') && c.engine === d.engine);
  const created = d.connMode === 'new' && !!d.connectionId;
  const set = (patch: Partial<WizardDraft>) => dispatch({ type: 'set', patch });

  async function test(): Promise<void> {
    setBusy(true); setError(null);
    try {
      let id = d.connectionId;
      if (d.connMode === 'new' && !id) {
        const problem = newConnectionProblem(d);
        if (problem) { setError(problem); return; }
        const { params } = connectionParams(d.engine, fieldValues(d), { secretRef: d.credMode === 'secret_ref' });
        const spec: ConnectionSpec = {
          name: d.newName, engine: d.engine, params,
          ...(d.credMode === 'secret_ref' ? { secret_ref: d.secretRef.trim() } : { credentials: { username: d.username.trim(), password: d.password } }),
          roles: writable ? ['source', 'target'] : ['source'], tags: d.production ? ['production'] : [],
        };
        const conn = await api.connections.create(auth, spec);
        dispatch({ type: 'created', connection: conn });   // the password leaves React state here
        onConnectionsChanged();
        id = conn.id;
      }
      if (!id) { setError('Choose a saved connection or describe a new one.'); return; }
      const result = await api.connections.test(auth, id, { role: 'source' });
      dispatch({ type: 'tested', result, at: new Date().toISOString() });
      onConnectionsChanged();
    } catch (e) {
      setError(e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : String(e));
    } finally { setBusy(false); }
  }

  return (
    <section className="panel" aria-labelledby="h1s">
      <div className="panel-head"><h2 id="h1s">1 · Connect to the source</h2><p>Pick the engine and how DPS reaches it. Reads always run in read-only transactions, and the job stores a secret name or a sealed credential, never a password in clear.</p></div>

      <div className="card">
        <div className="card-head"><div className="card-title">Engine</div></div>
        <div className="choices c3" role="radiogroup" aria-label="Database engine">
          {ENGINES.map((e) => (
            <label key={e.engine} className="choice"><input type="radio" name="engine" value={e.engine} aria-label={e.engine === 'oracle' ? 'Oracle' : e.title}
                checked={d.engine === e.engine} onChange={() => dispatch({ type: 'engine', engine: e.engine })} /><span className="radio" />
              <span className="ct"><span className="eng"><DbIcon /></span>{e.title}</span>
              <span className="cd">{e.text}</span>
              <span className="cm">{e.meta}</span></label>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Source connection</div>
          <div className="seg" role="group" aria-label="Saved or new connection">
            <button type="button" className={d.connMode === 'saved' ? 'on' : ''} aria-pressed={d.connMode === 'saved'} onClick={() => dispatch({ type: 'connMode', mode: 'saved' })}>Use a saved connection</button>
            <button type="button" className={d.connMode === 'new' ? 'on' : ''} aria-pressed={d.connMode === 'new'} onClick={() => dispatch({ type: 'connMode', mode: 'new' })}>New connection</button>
          </div></div>
        {d.connMode === 'saved' ? (
          <div>
            <label className="lbl" htmlFor="saved-conn">Saved connection</label>
            <select className="input" id="saved-conn" value={d.connectionId ?? ''} onChange={(e) => dispatch({ type: 'useSaved', connection: sources.find((c) => c.id === e.target.value) ?? null })}>
              <option value="">Choose a{d.engine === 'oracle' ? 'n' : ''} {engineName(d.engine)} source…</option>
              {sources.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <div className="help">{sources.length ? 'Connections registered for this tenant with the source role. The job stores the connection id; its secret stays in the registry.' : `No ${engineName(d.engine)} source is registered yet: choose New connection.`}</div>
            {d.connectionId && (
              <div className="rowx" style={{ marginTop: 10 }}>
                {d.production && <span className="chip amber">production · writes need the admin scope</span>}
                <span className={`chip ${d.connectionRoles.includes('target') ? 'teal' : 'slate'}`}>{d.connectionRoles.includes('target') ? 'source and target' : 'source only (read)'}</span>
              </div>
            )}
          </div>
        ) : created ? (
          <div className="callout emerald" style={{ fontSize: 12.5 }}>
            Connection <span className="mono">{d.connectionName}</span> is registered{d.credMode === 'inline' ? '; its password was sealed by DPS and left this page' : ` with secret ${d.secretRef}`}.{' '}
            <button type="button" className="linkbtn" onClick={() => dispatch({ type: 'connMode', mode: 'saved' })}>Use it as a saved connection</button>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="between">
              <div className="seg" role="group" aria-label="How DPS connects">
                <button type="button" className={d.credMode === 'secret_ref' ? 'on' : ''} aria-pressed={d.credMode === 'secret_ref'} onClick={() => dispatch({ type: 'credMode', mode: 'secret_ref' })}>Secret reference</button>
                <button type="button" className={d.credMode === 'inline' ? 'on' : ''} aria-pressed={d.credMode === 'inline'} disabled={d.production} onClick={() => dispatch({ type: 'credMode', mode: 'inline' })}>Inline credentials (dev only)</button>
              </div>
            </div>
            <div className="fgrid">
              <div><label className="lbl" htmlFor="nc-name">Connection name</label><input className="input mono" id="nc-name" value={d.newName} onChange={(e) => set({ newName: e.target.value })} placeholder={d.engine === 'oracle' ? 'crm-ora-ro' : d.engine === 'mongo' ? 'crm-mongo-ro' : 'bank-dev-ro'} spellCheck={false} /></div>
              {fields.map((f) => {
                const id = `nc-${f.key.replace('options.', 'opt-').replace('_', '-')}`;
                return (
                  <div key={f.key}><label className="lbl" htmlFor={id}>{f.label}{!f.required && <span className="faint"> (optional)</span>}</label>
                    <input className="input mono" id={id} value={fieldValue(d, f.key)} onChange={(e) => set(fieldPatch(d, f.key, e.target.value))}
                           placeholder={f.placeholder ?? (f.defaultValue !== undefined ? String(f.defaultValue) : undefined)} inputMode={f.key === 'port' ? 'numeric' : undefined} spellCheck={false} />
                    {f.help && <div className="help">{f.help}</div>}</div>
                );
              })}
            </div>
            {d.credMode === 'secret_ref' ? (
              <div>
                <label className="lbl" htmlFor="nc-secret">Secret name</label>
                <input className="input mono" id="nc-secret" value={d.secretRef} onChange={(e) => set({ secretRef: e.target.value })} placeholder="dps/src/bank-dev-ro" spellCheck={false} />
                <div className="help">{secretRefHelp(d.engine)} It is read from this tenant&apos;s own secret folder (<span className="mono">&lt;secret dir&gt;/&lt;tenant&gt;/{d.secretRef.trim() || 'dps/src/bank-dev-ro'}</span>), or from <span className="mono">_shared/…</span> when that secret lists this tenant; another tenant&apos;s secrets cannot be named.</div>
              </div>
            ) : (
              <div>
                <div className="fgrid">
                  <div><label className="lbl" htmlFor="nc-user">Username</label><input className="input mono" id="nc-user" value={d.username} onChange={(e) => set({ username: e.target.value })} autoComplete="off" spellCheck={false} /></div>
                  <div><label className="lbl" htmlFor="nc-pass">Password</label><input className="input mono" id="nc-pass" type="password" value={d.password} onChange={(e) => set({ password: e.target.value })} autoComplete="off" placeholder="Sent once, sealed by DPS" /></div>
                </div>
                <div className="callout amber" style={{ marginTop: 12, fontSize: 12.5 }}>Inline credentials are for dev and UAT connections. They travel over TLS, are sealed with the service key in the connection registry, and leave this page as soon as the connection is created.</div>
              </div>
            )}
            <div className="between" style={{ paddingTop: 14, borderTop: '1px solid var(--border)' }}>
              <label className="switch"><input type="checkbox" role="switch" checked={d.production} onChange={(e) => dispatch({ type: 'production', on: e.target.checked })} /><span className="track" />Tagged production</label>
              <label className="switch plain"><input type="checkbox" role="switch" checked={writable} onChange={(e) => setWritable(e.target.checked)} /><span className="track" />Writable (In place and Append)</label>
              {d.production && <span className="chip amber">production · writes need the admin scope</span>}
            </div>
            <div className="help">Reads are unaffected. Writing back to a production-tagged connection (In place or Append) needs the admin scope, and so does tagging it. A production-tagged connection always uses a secret reference.</div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Target connection</div><span className="faint sm">used by Copy to a table</span></div>
        <label className="lbl" htmlFor="target-conn-1">Target connection for Copy to a table (optional)</label>
        <select className="input" id="target-conn-1" value={d.targetConnectionId ?? ''} onChange={(e) => set({ targetConnectionId: e.target.value || null })}>
          <option value="">Choose later in step 4</option>
          {targets.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <div className="help">In place and Append write through the source connection instead, and only after a typed confirmation in step 4.</div>
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Test connection</div>
          <button className="btn btn-sm" type="button" disabled={busy || (d.connMode === 'saved' && !d.connectionId)} onClick={() => void test()}>{d.test ? 'Test again' : 'Test connection'}</button></div>
        {error && <div className="err" style={{ marginBottom: 10 }}>{error}</div>}
        {busy && <div className="skeleton" />}
        {d.test && !busy && (
          <>
            <CheckList checks={d.test.checks} title="Source" aside={<><span className="mono">{d.connectionName}</span>{d.test.server_version && <span className="faint sm">{versionLabel(d.test.engine, d.test.server_version)}</span>}</>} />
            {d.engine === 'mongo' && transactions !== undefined && (
              <div className="rowx" style={{ marginTop: 8 }}><span className={`chip ${transactions ? 'emerald' : 'amber'}`}>transactions: {transactions ? 'yes' : 'no'}</span>
                <span className="faint sm">In place and append need a replica set.</span></div>
            )}
            {!d.test.ok && <div className="err" style={{ marginTop: 8 }}>The connection test failed{d.test.error_code ? ` (${d.test.error_code})` : ''}. Fix the connection or its secret and test again.</div>}
            <div className="faint sm" style={{ marginTop: 8 }}>Tested at {fmtClock(d.testedAt)} · no rows were read</div>
          </>
        )}
        {!d.test && !busy && <p className="muted" style={{ margin: 0, fontSize: 13 }}>{d.connMode === 'new' ? 'Testing registers the connection first, then checks the login and its privileges.' : 'Checks the login, TLS, the session tag and that the account cannot write the source.'}</p>}
      </div>

      <div className="wfoot"><span className="hint">Step 1 of 5</span><button className="btn btn-primary" type="button" disabled={!(d.connectionId && d.test?.ok)} onClick={onNext}>Next: Select data</button></div>
    </section>
  );
}
