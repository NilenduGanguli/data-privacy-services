import { useState } from 'react';
import { CheckList } from '../../components/CheckList';
import { api, ApiError, type Auth } from '../../lib/api';
import { fmtClock, type WizardDraft } from '../../lib/jobs';
import type { Connection, ConnectionSpec } from '../../lib/types';
import type { WizardAction } from './state';

const NAME_RE = /^[a-z0-9][a-z0-9-]{1,62}$/;
const SECRET_RE = /^[a-z0-9][a-z0-9/_.-]{0,200}$/;

function DbIcon(): JSX.Element {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M3 14h18M9 9v11" /></svg>;
}

/** Validation of a new connection's fields (value-free messages). */
function newConnectionProblem(d: WizardDraft): string | null {
  if (!NAME_RE.test(d.newName)) return 'Name: lower-case letters, digits and hyphens, 2 to 63 characters.';
  if (!d.host.trim() || !d.database.trim()) return 'Host and database are required.';
  const port = Number(d.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return 'Port must be a number from 1 to 65535.';
  if (d.credMode === 'secret_ref' && !SECRET_RE.test(d.secretRef)) return 'Secret name: lower-case letters, digits and / _ . - only.';
  if (d.credMode === 'inline' && (!d.username.trim() || !d.password)) return 'Username and password are required.';
  return null;
}

/** Step 1 · Connect to the source: engine, saved or new connection, target connection, test. */
export function StepConnect({ d, dispatch, auth, connections, onConnectionsChanged, onNext }: {
  d: WizardDraft; dispatch: React.Dispatch<WizardAction>; auth: Auth; connections: Connection[];
  onConnectionsChanged: () => void; onNext: () => void;
}): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [writable, setWritable] = useState(false);
  const sources = connections.filter((c) => c.engine === 'postgres' && c.roles.includes('source'));
  const targets = connections.filter((c) => c.roles.includes('target') && ['postgres', 'oracle', 'mongo'].includes(c.engine));
  const created = d.connMode === 'new' && !!d.connectionId;
  const set = (patch: Partial<WizardDraft>) => dispatch({ type: 'set', patch });

  async function test(): Promise<void> {
    setBusy(true); setError(null);
    try {
      let id = d.connectionId;
      if (d.connMode === 'new' && !id) {
        const problem = newConnectionProblem(d);
        if (problem) { setError(problem); return; }
        const spec: ConnectionSpec = {
          name: d.newName, engine: 'postgres', params: { host: d.host.trim(), port: Number(d.port), database: d.database.trim() },
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
          <label className="choice"><input type="radio" name="engine" value="postgres" checked={d.engine === 'postgres'} onChange={() => set({ engine: 'postgres' })} /><span className="radio" />
            <span className="ct"><span className="eng"><DbIcon /></span>PostgreSQL</span>
            <span className="cd">14 to 16. Reads in read-only transactions with a statement timeout; loads with COPY.</span>
            <span className="cm">Schemas and tables · keyset on the primary key</span></label>
          <label className="choice"><input type="radio" name="engine" value="oracle" disabled checked={false} onChange={() => undefined} /><span className="radio" />
            <span className="ct"><span className="eng"><DbIcon /></span>Oracle Database <span className="chip slate">M3</span></span>
            <span className="cd">19c and 23ai, thin-mode driver. Arrives with milestone M3.</span>
            <span className="cm">Owners and tables · key-range or ROWID partitions</span></label>
          <label className="choice"><input type="radio" name="engine" value="mongo" disabled checked={false} onChange={() => undefined} /><span className="radio" />
            <span className="ct"><span className="eng"><DbIcon /></span>MongoDB <span className="chip slate">M3</span></span>
            <span className="cd">6 and 7, documents through the JSON engine. Arrives with milestone M3.</span>
            <span className="cm">Databases and collections · keyset on _id</span></label>
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
              <option value="">Choose a PostgreSQL source…</option>
              {sources.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <div className="help">{sources.length ? 'Connections registered for this tenant with the source role. The job stores the connection id; its secret stays in the registry.' : 'No PostgreSQL source is registered yet: choose New connection.'}</div>
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
              <div><label className="lbl" htmlFor="nc-name">Connection name</label><input className="input mono" id="nc-name" value={d.newName} onChange={(e) => set({ newName: e.target.value })} placeholder="bank-dev-ro" spellCheck={false} /></div>
              <div><label className="lbl" htmlFor="nc-db">Database</label><input className="input mono" id="nc-db" value={d.database} onChange={(e) => set({ database: e.target.value })} spellCheck={false} /></div>
              <div><label className="lbl" htmlFor="nc-host">Host</label><input className="input mono" id="nc-host" value={d.host} onChange={(e) => set({ host: e.target.value })} spellCheck={false} /></div>
              <div><label className="lbl" htmlFor="nc-port">Port</label><input className="input mono" id="nc-port" value={d.port} onChange={(e) => set({ port: e.target.value })} inputMode="numeric" /></div>
            </div>
            {d.credMode === 'secret_ref' ? (
              <div>
                <label className="lbl" htmlFor="nc-secret">Secret name</label>
                <input className="input mono" id="nc-secret" value={d.secretRef} onChange={(e) => set({ secretRef: e.target.value })} placeholder="dps/src/bank-dev-ro" spellCheck={false} />
                <div className="help">The job stores only this name. Workers read the secret from the mounted store when they start; it never appears in the console, the logs or the evidence pack.</div>
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
            <CheckList checks={d.test.checks} title="Source" aside={<><span className="mono">{d.connectionName}</span>{d.test.server_version && <span className="faint sm">PostgreSQL {d.test.server_version}</span>}</>} />
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
