import { useState } from 'react';
import { CheckList } from '../../components/CheckList';
import { ConfirmWriteModal } from '../../components/ConfirmWriteModal';
import { api, ApiError, type Auth } from '../../lib/api';
import {
  fmtClock, fmtCount, fqn, methodFor, needsConfirmation, oneWayColumns, parseCount, sourceObject, writtenObject, type WizardDraft,
} from '../../lib/jobs';
import type { ClassificationSnapshot, Connection, ConnectionSpec, ConnectionTestResult, WriteMode } from '../../lib/types';
import { canLeave, targetReady, type WizardAction } from './state';

const BATCHES = [1000, 5000, 20000];
const STORE_NAME_RE = /^[a-z0-9][a-z0-9-]{1,62}$/;

function apiMessage(e: unknown): string {
  return e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : e instanceof Error ? e.message : String(e);
}

/** "Add a file store": an s3 (bucket, endpoint, secret) or pvc (base path) target connection. */
function AddStore({ auth, onCreated }: { auth: Auth; onCreated: (c: Connection) => void }): JSX.Element {
  const [kind, setKind] = useState<'s3' | 'pvc'>('s3');
  const [name, setName] = useState('');
  const [bucket, setBucket] = useState('');
  const [endpoint, setEndpoint] = useState('');
  const [secret, setSecret] = useState('');
  const [base, setBase] = useState('');
  const [error, setError] = useState<string | null>(null);
  async function create(): Promise<void> {
    setError(null);
    if (!STORE_NAME_RE.test(name)) { setError('Name: lower-case letters, digits and hyphens.'); return; }
    const spec: ConnectionSpec = kind === 's3'
      ? { name, engine: 's3', params: { bucket: bucket.trim(), ...(endpoint.trim() ? { endpoint_url: endpoint.trim() } : {}) }, secret_ref: secret.trim(), roles: ['target'], tags: [] }
      : { name, engine: 'pvc', params: { base_path: base.trim() }, roles: ['target'], tags: [] };
    try { onCreated(await api.connections.create(auth, spec)); } catch (e) { setError(apiMessage(e)); }
  }
  return (
    <div className="card" style={{ background: 'var(--surface-2)', marginTop: 12 }}>
      <div className="card-head"><div className="card-title">Add a file store</div>
        <div className="seg" role="group" aria-label="File store kind">
          <button type="button" className={kind === 's3' ? 'on' : ''} aria-pressed={kind === 's3'} onClick={() => setKind('s3')}>S3</button>
          <button type="button" className={kind === 'pvc' ? 'on' : ''} aria-pressed={kind === 'pvc'} onClick={() => setKind('pvc')}>PVC path</button>
        </div></div>
      <div className="fgrid">
        <div><label className="lbl" htmlFor="st-name">Store name</label><input className="input mono" id="st-name" value={name} onChange={(e) => setName(e.target.value)} spellCheck={false} /></div>
        {kind === 's3' ? (
          <>
            <div><label className="lbl" htmlFor="st-bucket">Bucket</label><input className="input mono" id="st-bucket" value={bucket} onChange={(e) => setBucket(e.target.value)} spellCheck={false} /></div>
            <div><label className="lbl" htmlFor="st-endpoint">Endpoint (optional)</label><input className="input mono" id="st-endpoint" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://s3.ap-south-1.amazonaws.com" spellCheck={false} /></div>
            <div><label className="lbl" htmlFor="st-secret">Secret name</label><input className="input mono" id="st-secret" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="dps/s3/exports" spellCheck={false} /></div>
          </>
        ) : (
          <div><label className="lbl" htmlFor="st-base">Base path</label><input className="input mono" id="st-base" value={base} onChange={(e) => setBase(e.target.value)} placeholder="exports/loans" spellCheck={false} /></div>
        )}
      </div>
      {error && <div className="err" style={{ marginTop: 10 }}>{error}</div>}
      <div className="rowx" style={{ marginTop: 12, justifyContent: 'flex-end' }}><button className="btn btn-sm btn-primary" type="button" onClick={() => void create()}>Add store</button></div>
    </div>
  );
}

const MODES: { mode: WriteMode; title: string; chip: string; tone: string }[] = [
  { mode: 'in_place', title: 'In place', chip: 'changes source rows', tone: 'rose' },
  { mode: 'append', title: 'Append new rows', chip: 'adds rows to source', tone: 'amber' },
  { mode: 'table', title: 'Copy to a table', chip: 'source unchanged', tone: 'slate' },
  { mode: 'files', title: 'Export files', chip: 'source unchanged', tone: 'slate' },
];

/** Step 4 · Target and write mode: where protected rows go; in place / append need a typed confirmation. */
export function StepTarget({ d, dispatch, auth, connections, snapshot, onConnectionsChanged, onNext, onBack }: {
  d: WizardDraft; dispatch: React.Dispatch<WizardAction>; auth: Auth; connections: Connection[]; snapshot: ClassificationSnapshot | null;
  onConnectionsChanged: () => void; onNext: () => void; onBack: () => void;
}): JSX.Element {
  const [modal, setModal] = useState(false);
  const [checks, setChecks] = useState<{ mode: WriteMode; result: ConnectionTestResult } | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addStore, setAddStore] = useState(false);
  const set = (patch: Partial<WizardDraft>) => dispatch({ type: 'set', patch });
  const src = fqn(sourceObject(d));
  const rows = d.rows ?? d.shape?.row_estimate ?? null;
  const key = snapshot?.shape.key ?? d.shape?.key ?? [];
  const keyInfo = key.map((k) => d.shape?.columns.find((c) => c.name === k));
  const dbTargets = connections.filter((c) => c.roles.includes('target') && ['postgres', 'oracle', 'mongo'].includes(c.engine));
  const stores = connections.filter((c) => c.roles.includes('target') && (c.engine === 's3' || c.engine === 'pvc'));
  const writable = d.connectionRoles.includes('target');
  const sameTable = d.writeMode === 'append' && d.appendTarget === 'same';
  const textKey = keyInfo.some((c) => c && (c.kind === 'text' || c.kind === 'char'));
  const rewritten = snapshot ? snapshot.columns.filter((c) => !c.is_key && methodFor(c, d.mode).method !== 'keep').length : undefined;
  const confirmed = needsConfirmation(d.writeMode) && !!d.typed;
  const written = fqn(writtenObject(d));

  function choose(mode: WriteMode): void {
    dispatch({ type: 'writeMode', mode });
    setChecks(null); setError(null);
    if (mode === 'table') {
      const patch: Partial<WizardDraft> = {};
      if (!d.targetOwner) patch.targetOwner = d.owner ? `${d.owner}_uat` : '';
      if (!d.targetTable) patch.targetTable = d.table;
      if (Object.keys(patch).length) set(patch);
    }
    if (mode === 'files' && !d.filesPrefix) set({ filesPrefix: `${d.owner ? `${d.owner}/` : ''}${d.table}/${new Date().toISOString().slice(0, 10)}` });
  }

  async function check(): Promise<void> {
    if (!d.writeMode) return;
    setChecking(true); setError(null);
    try {
      let id: string | null = null;
      let body: { role: 'target'; object?: string; write_mode?: WriteMode } = { role: 'target' };
      if (d.writeMode === 'table') { id = d.targetConnectionId; body = { role: 'target', object: `${d.targetOwner.trim()}.${d.targetTable.trim()}`, write_mode: 'table' }; }
      if (d.writeMode === 'files') { id = d.filesConnectionId; body = { role: 'target' }; }
      if (d.writeMode === 'in_place') { id = d.connectionId; body = { role: 'target', object: src, write_mode: 'in_place' }; }
      if (d.writeMode === 'append') { id = d.connectionId; body = { role: 'target', object: written, write_mode: 'append' }; }
      if (!id) { setError('Choose the connection first.'); return; }
      setChecks({ mode: d.writeMode, result: await api.connections.test(auth, id, body) });
    } catch (e) { setError(apiMessage(e)); } finally { setChecking(false); }
  }

  const pkText = d.pkKind === 'generate' ? 'New rows get keys from the table\'s identity or sequence.'
    : d.pkKind === 'token' ? 'New rows get tokenized keys; the collision pre-check stops the job before any write if a token already exists.'
      : d.pkPrefix.trim() ? `New rows get ${key.join(', ')} = "${d.pkPrefix.trim()}" + original.`
        : `New rows get ${key.join(', ')} = original + ${fmtCount(parseCount(d.pkOffset))}.`;

  return (
    <section className="panel" aria-labelledby="h4s">
      <div className="panel-head"><h2 id="h4s">4 · Target and write mode</h2><p>Choose where protected rows go. Copy to a table and Export files leave the source untouched. In place and Append change {src} and ask you to confirm first.</p></div>

      <div className="choices c2" role="radiogroup" aria-label="Write mode">
        {MODES.map((m) => (
          <label key={m.mode} className="choice"><input type="radio" name="wm" value={m.mode} checked={d.writeMode === m.mode} onChange={() => choose(m.mode)} /><span className="radio" />
            <span className="ct">{m.title} <span className={`chip ${m.tone}`}>{m.chip}</span></span>
            <span className="cd">{{
              in_place: `Updates the ${rows === null ? 'selected' : fmtCount(rows)} selected rows in ${src} by primary key, column by column, in committed batches.`,
              append: `Inserts transformed copies of the selected rows into ${src} or a table you name. Existing rows are not changed.`,
              table: 'Loads protected rows into a table in another schema, created from the source definition if it is missing.',
              files: 'Writes CSV, Parquet or JSONL to S3 or a PVC path, with a manifest of files, row counts and checksums.',
            }[m.mode]}</span>
            <span className="cm">{{ in_place: 'Meant for a restored copy. On a production connection it also needs the admin scope.', append: 'Needs a primary-key strategy', table: 'Upsert on the primary key, or truncate and insert', files: 'For other loaders and analytics' }[m.mode]}</span></label>
        ))}
      </div>

      {d.writeMode === 'table' && (
        <div className="opts">
          <div className="card-head"><div className="card-title">Copy to a table</div></div>
          <div className="fgrid">
            <div className="full"><label className="lbl" htmlFor="tg-conn">Target connection</label>
              <select className="input" id="tg-conn" value={d.targetConnectionId ?? ''} onChange={(e) => { set({ targetConnectionId: e.target.value || null }); setChecks(null); }}>
                <option value="">Choose a target connection…</option>
                {dbTargets.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select></div>
            <div><label className="lbl" htmlFor="tg-schema">Destination schema</label><input className="input mono" id="tg-schema" value={d.targetOwner} onChange={(e) => set({ targetOwner: e.target.value })} spellCheck={false} /></div>
            <div><label className="lbl" htmlFor="tg-table">Destination table</label><input className="input mono" id="tg-table" value={d.targetTable} onChange={(e) => set({ targetTable: e.target.value })} spellCheck={false} /></div>
            <div><span className="lbl">Load</span><div className="seg" role="group" aria-label="Load method">
              <button type="button" className={d.tableLoad === 'truncate_insert' ? 'on' : ''} aria-pressed={d.tableLoad === 'truncate_insert'} onClick={() => set({ tableLoad: 'truncate_insert' })}>Truncate and insert</button>
              <button type="button" className={d.tableLoad === 'upsert' ? 'on' : ''} aria-pressed={d.tableLoad === 'upsert'} onClick={() => set({ tableLoad: 'upsert' })}>Upsert on {key.join(', ') || 'the key'}</button></div></div>
            <div style={{ alignSelf: 'end' }}><label className="switch plain"><input type="checkbox" role="switch" checked={d.createIfMissing} onChange={(e) => set({ createIfMissing: e.target.checked })} /><span className="track" />Create the table if it is missing</label></div>
          </div>
          <div className="checks" style={{ marginTop: 12 }}>
            {d.tableLoad === 'upsert'
              ? <div className="ck ok"><span className="ic">✓</span><span><b>Upsert keeps rows that are not in this selection</b><span className="d">Rows with a matching {key.join(', ') || 'key'} are replaced; every other row stays as it is</span></span><span className="meta">ok</span></div>
              : <div className="ck warn"><span className="ic">!</span><span><b>Truncate empties the destination table before loading</b><span className="d">Every row in it is removed first, including rows from earlier loads. Choose Upsert to keep them.</span></span><span className="meta">check</span></div>}
          </div>
        </div>
      )}

      {d.writeMode === 'files' && (
        <div className="opts">
          <div className="card-head"><div className="card-title">Export files</div></div>
          <div className="fgrid">
            <div><span className="lbl">Format</span><div className="seg" role="group" aria-label="File format">
              {(['csv', 'parquet', 'jsonl'] as const).map((f) => <button key={f} type="button" className={d.filesFormat === f ? 'on' : ''} aria-pressed={d.filesFormat === f} onClick={() => set({ filesFormat: f, filesCompression: f === 'parquet' ? 'snappy' : d.filesCompression === 'snappy' ? 'gzip' : d.filesCompression })}>{f === 'parquet' ? 'Parquet' : f.toUpperCase()}</button>)}
            </div></div>
            <div><label className="lbl" htmlFor="fl-comp">Compression</label>
              <select className="input" id="fl-comp" value={d.filesCompression} onChange={(e) => set({ filesCompression: e.target.value as WizardDraft['filesCompression'] })}>
                {(d.filesFormat === 'parquet' ? ['snappy', 'zstd', 'gzip', 'none'] : ['gzip', 'none']).map((c) => <option key={c} value={c}>{c}</option>)}
              </select></div>
            <div><label className="lbl" htmlFor="fl-conn">Destination connection</label>
              <select className="input" id="fl-conn" value={d.filesConnectionId ?? ''} onChange={(e) => { set({ filesConnectionId: e.target.value || null }); setChecks(null); }}>
                <option value="">Choose a file store…</option>
                {stores.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button type="button" className="linkbtn" style={{ marginTop: 6 }} onClick={() => setAddStore((v) => !v)}>{addStore ? 'Close' : 'Add a file store'}</button></div>
            <div><label className="lbl" htmlFor="fl-prefix">Prefix</label><input className="input mono" id="fl-prefix" value={d.filesPrefix} onChange={(e) => set({ filesPrefix: e.target.value })} spellCheck={false} />
              <div className="help">One file per partition ({d.parallelism}), under this prefix.</div></div>
          </div>
          {addStore && <AddStore auth={auth} onCreated={(c) => { setAddStore(false); onConnectionsChanged(); set({ filesConnectionId: c.id }); }} />}
          <div className="checks" style={{ marginTop: 12 }}>
            <div className="ck ok"><span className="ic">✓</span><span><b>manifest.json lists each file with its row count and SHA-256</b><span className="d">Written last, so a loader that sees the manifest sees complete files</span></span><span className="meta">ok</span></div>
          </div>
        </div>
      )}

      {d.writeMode === 'in_place' && (
        <div className="opts">
          <div className="card-head"><div className="card-title">In place</div></div>
          <div className="fgrid">
            <div><label className="lbl" htmlFor="ip-conn">Write connection</label><input className="input mono" id="ip-conn" value={d.connectionName} readOnly />
              <div className="help">The source connection. Reads still run in read-only transactions.</div></div>
            <div><label className="lbl" htmlFor="ip-batch">Commit every</label>
              <select className="input" id="ip-batch" value={d.batchRows ?? ''} onChange={(e) => set({ batchRows: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Service default rows per batch</option>
                {BATCHES.map((n) => <option key={n} value={n}>{fmtCount(n)} rows per partition</option>)}
              </select></div>
          </div>
          {!writable && <div className="callout amber" style={{ marginTop: 12, fontSize: 12.5 }}><b>{d.connectionName} has the source role only.</b> In place writes through the source connection, so it needs the target role too (an UPDATE-capable login). Register a writable connection for the restored copy, or add the role to this one (the admin scope is needed when it is tagged production), then come back.</div>}
          <div className="checks" style={{ marginTop: 12 }}>
            <div className="ck ok"><span className="ic">✓</span><span><b>{key.join(', ') || 'The key'} is the update key and is not changed</b><span className="d">{rewritten ?? 'The transformed'} columns are rewritten; foreign keys that point at the key keep working</span></span><span className="meta">ok</span></div>
          </div>
        </div>
      )}

      {d.writeMode === 'append' && (
        <div className="opts">
          <div className="card-head"><div className="card-title">Append new rows</div></div>
          <div className="fgrid">
            <div><span className="lbl">Insert into</span><div className="seg" role="group" aria-label="Append destination">
              <button type="button" className={d.appendTarget === 'same' ? 'on' : ''} aria-pressed={d.appendTarget === 'same'} onClick={() => { set({ appendTarget: 'same', ...(d.pkKind === 'token' ? { pkKind: 'offset' as const } : {}) }); dispatch({ type: 'unconfirm' }); }}>Same table · {src}</button>
              <button type="button" className={d.appendTarget === 'named' ? 'on' : ''} aria-pressed={d.appendTarget === 'named'} onClick={() => { set({ appendTarget: 'named', targetOwner: d.targetOwner || d.owner }); dispatch({ type: 'unconfirm' }); }}>Named table</button></div></div>
            {d.appendTarget === 'named' && (
              <div className="fgrid" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 12 }}>
                <div><label className="lbl" htmlFor="ap-schema">Destination schema</label><input className="input mono" id="ap-schema" value={d.targetOwner} onChange={(e) => { set({ targetOwner: e.target.value }); dispatch({ type: 'unconfirm' }); }} spellCheck={false} /></div>
                <div><label className="lbl" htmlFor="ap-table">Destination table</label><input className="input mono" id="ap-table" value={d.targetTable} onChange={(e) => { set({ targetTable: e.target.value }); dispatch({ type: 'unconfirm' }); }} spellCheck={false} /></div>
              </div>
            )}
          </div>
          <div className="lbl" style={{ marginTop: 14 }}>Primary-key strategy</div>
          <div className="choices c3" role="radiogroup" aria-label="Primary-key strategy">
            <label className="choice sm"><input type="radio" name="pk" value="generate" checked={d.pkKind === 'generate'} onChange={() => set({ pkKind: 'generate' })} /><span className="radio" /><span className="ct">Database-generated</span><span className="cd">The table's identity or owned sequence assigns new keys.</span></label>
            <label className="choice sm"><input type="radio" name="pk" value="offset" checked={d.pkKind === 'offset'} onChange={() => set({ pkKind: 'offset' })} /><span className="radio" /><span className="ct">Offset</span><span className="cd">{textKey ? 'New key = prefix + original key.' : 'New key = original + offset, above the current maximum.'}</span></label>
            <label className="choice sm"><input type="radio" name="pk" value="token" disabled={sameTable || d.mode === 'synthesize'} checked={d.pkKind === 'token'} onChange={() => set({ pkKind: 'token' })} /><span className="radio" /><span className="ct">Token</span>
              <span className="cd">{sameTable ? 'Not for the same table: tokens could land on existing keys.' : d.mode === 'synthesize' ? 'Not in synthesize mode: tokens need the key.' : 'Obfuscate the key for the new rows; checked for clashes before any write.'}</span></label>
          </div>
          {d.pkKind === 'offset' && (
            <div style={{ marginTop: 12, maxWidth: 320 }}>
              {textKey
                ? <><label className="lbl" htmlFor="ap-prefix">Key prefix</label><input className="input mono" id="ap-prefix" value={d.pkPrefix} onChange={(e) => set({ pkPrefix: e.target.value, pkOffset: '' })} placeholder="SYN-" spellCheck={false} /></>
                : <><label className="lbl" htmlFor="ap-offset">Key offset</label><input className="input mono" id="ap-offset" inputMode="numeric" value={d.pkOffset} onChange={(e) => set({ pkOffset: e.target.value, pkPrefix: '' })} placeholder="900000000" /></>}
            </div>
          )}
          <div className="help">The collision pre-check runs when the job prepares and stops it before any write.</div>
        </div>
      )}

      {d.writeMode && (
        <div className="card">
          <div className="card-head"><div className="card-title">Target checks</div>
            <button className="btn btn-sm" type="button" disabled={checking || (d.writeMode === 'in_place' && !writable)} onClick={() => void check()}>Check target</button></div>
          {error && <div className="err">{error}</div>}
          {checks && checks.mode === d.writeMode ? <CheckList checks={checks.result.checks} title="Target" aside={checks.result.server_version ? <span className="faint sm">{checks.result.server_version}</span> : undefined} />
            : <p className="muted" style={{ margin: 0, fontSize: 13 }}>{d.writeMode === 'files' ? 'Writes and deletes a probe object in the store.' : 'Checks the privileges the write needs, without writing a row.'}</p>}
        </div>
      )}

      {needsConfirmation(d.writeMode) && (
        <div className="card">
          {confirmed ? (
            <div className="callout emerald" style={{ fontSize: 12.5 }}><b>Confirmed at {fmtClock(d.confirmedAt, false)}.</b> Nothing is written yet. The job writes to {written} only after you press Run job in step 5.{' '}
              <button className="linkbtn" type="button" onClick={() => dispatch({ type: 'unconfirm' })}>Undo</button></div>
          ) : (
            <div className="between"><span className="muted" style={{ fontSize: 13 }}>{d.writeMode === 'in_place' ? `In place changes ${src}.` : `Append adds rows to ${written || 'the table'}.`} Read what happens and type the table name to confirm.</span>
              <button className="btn btn-warn" type="button" disabled={!written} onClick={() => setModal(true)}>Confirm</button></div>
          )}
        </div>
      )}

      {modal && (d.writeMode === 'in_place' || d.writeMode === 'append') && (
        <ConfirmWriteModal mode={d.writeMode} objectFqn={written} rows={d.rows} production={d.production} adminScope={null}
          connectionName={d.connectionName} synthesize={d.mode === 'synthesize'} keyColumns={key} rewrittenColumns={rewritten}
          oneWay={snapshot ? oneWayColumns(snapshot, d.mode) : []} partitions={d.parallelism} batchRows={d.batchRows}
          pkText={d.writeMode === 'append' ? pkText : undefined} rowEstimate={d.writeMode === 'append' && sameTable ? d.shape?.row_estimate : null}
          blockedReason={d.writeMode === 'in_place' && !writable ? `${d.connectionName} has no target role, so DPS cannot write through it.` : null}
          onCancel={() => setModal(false)} onConfirm={(typed) => { dispatch({ type: 'confirm', typed, at: new Date().toISOString() }); setModal(false); }} />
      )}

      <div className="wfoot"><button className="btn" type="button" onClick={onBack}>Back</button>
        <span className="rowx">{!canLeave(4, d) && d.writeMode && <span className="hint">{!targetReady(d) ? (d.writeMode === 'in_place' && !writable ? 'In place needs a writable connection' : 'Complete the target to continue') : 'Confirm the write to continue'}</span>}
          <button className="btn btn-primary" type="button" disabled={!canLeave(4, d)} onClick={onNext}>Next: Review and run</button></span></div>
    </section>
  );
}
