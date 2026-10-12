import { useEffect, useMemo, useState } from 'react';
import { CheckList } from '../../components/CheckList';
import { api, ApiError, type Auth } from '../../lib/api';
import { fmtClock, fmtCount, fqn, selectionOf, shortType, sourceObject, type WizardDraft } from '../../lib/jobs';
import type { Check, ObjectRef } from '../../lib/types';
import { canLeave, type WizardAction } from './state';

const PARALLELISM = [2, 4, 8, 16];

/** Step 2 · Select data: owner and table, the rows to read (checked server-side), columns, limits. */
export function StepSelect({ d, dispatch, auth, onNext, onBack }: {
  d: WizardDraft; dispatch: React.Dispatch<WizardAction>; auth: Auth; onNext: () => void; onBack: () => void;
}): JSX.Element {
  const [objects, setObjects] = useState<ObjectRef[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [refusal, setRefusal] = useState<{ message: string; code?: string; checks: Check[] } | null>(null);
  const set = (patch: Partial<WizardDraft>) => dispatch({ type: 'set', patch });
  const connectionId = d.connectionId ?? '';

  useEffect(() => {
    if (!connectionId) return;
    api.connections.objects(auth, connectionId).then((p) => { setObjects(p.items); setLoadError(null); })
      .catch((e) => setLoadError(e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : String(e)));
  }, [auth, connectionId]);

  // describe the chosen table (also after a restored draft)
  useEffect(() => {
    if (!connectionId || !d.table || d.shape) return;
    let live = true;
    api.connections.describe(auth, connectionId, fqn(sourceObject(d)))
      .then((shape) => { if (live) dispatch({ type: 'shape', shape }); })
      .catch((e) => { if (live) setLoadError(e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : String(e)); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, connectionId, d.owner, d.table, d.shape]);

  const owners = useMemo(() => [...new Set((objects ?? []).map((o) => o.owner ?? ''))].sort(), [objects]);
  const tables = useMemo(() => (objects ?? []).filter((o) => (o.owner ?? '') === d.owner).map((o) => o.name).sort(), [objects, d.owner]);
  const shape = d.shape;
  const keyCols = shape ? shape.key.map((k) => shape.columns.find((c) => c.name === k)).filter(Boolean) : [];
  const chosen = new Set(d.columns ?? shape?.columns.map((c) => c.name) ?? []);
  const filterText = d.selMode === 'where' ? d.where : d.query;
  const objectText = d.table ? fqn(sourceObject(d)) : '';

  async function check(): Promise<void> {
    if (!connectionId || !d.table) return;
    setChecking(true); setRefusal(null);
    const started = performance.now();
    try {
      const count = await api.connections.count(auth, connectionId, sourceObject(d), selectionOf(d));
      dispatch({ type: 'counted', count, at: new Date().toISOString(), seconds: (performance.now() - started) / 1000 });
    } catch (e) {
      if (e instanceof ApiError) setRefusal({ message: e.message, code: e.code, checks: Array.isArray(e.extra.checks) ? (e.extra.checks as Check[]) : [] });
      else setRefusal({ message: String(e), checks: [] });
    } finally { setChecking(false); }
  }

  function toggle(name: string, on: boolean): void {
    if (!shape) return;
    const next = new Set(chosen);
    if (on) next.add(name); else next.delete(name);
    for (const k of shape.key) next.add(k);
    dispatch({ type: 'columns', columns: next.size >= shape.columns.length ? null : shape.columns.map((c) => c.name).filter((n) => next.has(n)) });
  }

  return (
    <section className="panel" aria-labelledby="h2s">
      <div className="panel-head"><h2 id="h2s">2 · Select data</h2><p>Choose the table and the rows to read. What you type is parsed and checked before it reaches the database; literals are sent as bound parameters.</p></div>
      {loadError && <div className="err">{loadError}</div>}

      <div className="card">
        <div className="fgrid">
          <div><label className="lbl" htmlFor="sel-owner">Owner</label>
            <select className="input" id="sel-owner" value={d.owner} onChange={(e) => dispatch({ type: 'owner', owner: e.target.value })}>
              <option value="">{objects ? 'Choose a schema…' : 'Loading…'}</option>
              {owners.map((o) => <option key={o} value={o}>{o || '(no schema)'}</option>)}
            </select></div>
          <div><label className="lbl" htmlFor="sel-table">Table</label>
            <select className="input" id="sel-table" value={d.table} disabled={!d.owner && owners.length > 1} onChange={(e) => dispatch({ type: 'table', table: e.target.value })}>
              <option value="">Choose a table…</option>
              {tables.map((t) => <option key={t} value={t}>{t}</option>)}
            </select></div>
          <div className="full kv">
            {shape ? <>{fmtCount(shape.row_estimate)} rows (estimate) · {shape.columns.length} columns · {keyCols.length
              ? <>{shape.key_source === 'primary_key' ? 'primary key' : 'unique key'} <b className="mono">{keyCols.map((c) => `${c!.name} ${shortType(c!.data_type)}`).join(', ')}</b></>
              : <span style={{ color: 'var(--rose)' }}>no primary key or unique index: DPS cannot page this table</span>}</>
              : d.table ? 'Reading the table definition…' : 'The definition (columns, types, key) is read without reading any row.'}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Which rows</div>
          <div className="seg" role="group" aria-label="Selection type">
            <button type="button" className={d.selMode === 'where' ? 'on' : ''} aria-pressed={d.selMode === 'where'} onClick={() => { setRefusal(null); dispatch({ type: 'selMode', mode: 'where' }); }}>WHERE expression</button>
            <button type="button" className={d.selMode === 'query' ? 'on' : ''} aria-pressed={d.selMode === 'query'} onClick={() => { setRefusal(null); dispatch({ type: 'selMode', mode: 'query' }); }}>Read-only SELECT</button>
          </div></div>
        <label className="lbl" htmlFor="sel-filter">{d.selMode === 'where' ? `WHERE expression${objectText ? ` on ${objectText}` : ''}` : 'Read-only SELECT'}</label>
        <textarea className="input" id="sel-filter" rows={d.selMode === 'where' ? 3 : 7} spellCheck={false} value={filterText}
                  placeholder={d.selMode === 'where' ? 'Empty = every row' : `SELECT … FROM ${objectText || 'schema.name'} WHERE …`}
                  onChange={(e) => { setRefusal(null); dispatch({ type: 'filter', text: e.target.value }); }} />
        <div className="help">The filter text is never saved with the draft; the job keeps it sealed and stores only its template.</div>
        <div className="between" style={{ marginTop: 12 }}>
          {d.count ? <span className="chip emerald">Valid</span>
            : refusal ? <span className="chip rose">Not accepted{refusal.code ? ` · ${refusal.code}` : ''}</span>
              : <span className="faint sm">{filterText.trim() ? 'Check the filter before you continue.' : 'No filter: every row is read.'}</span>}
          <button className="btn btn-sm" type="button" disabled={checking || !d.table} onClick={() => void check()}>Check</button>
        </div>
        {checking && <div className="skeleton" style={{ marginTop: 10 }} />}
        {refusal && (
          <>
            <div className="callout rose" style={{ marginTop: 10, fontSize: 12.5 }}><b>{refusal.message}</b> Change the {d.selMode === 'where' ? 'expression' : 'statement'} and check again.</div>
            {refusal.checks.length > 0 && <div style={{ marginTop: 6 }}><CheckList checks={refusal.checks} title="Selection checks" /></div>}
          </>
        )}
        {d.count && !checking && (
          <>
            <div style={{ marginTop: 6 }}><CheckList checks={d.count.checks} title="Selection checks" /></div>
            {d.count.runs_as && <><div className="lbl" style={{ marginTop: 12 }}>Runs as</div><div className="pane">{d.count.runs_as}</div></>}
            <div className="kv" style={{ marginTop: 10 }}><b>{fmtCount(d.count.rows)} rows match</b> · {d.count.binds} bound {d.count.binds === 1 ? 'parameter' : 'parameters'} <span className="faint">· counted at {fmtClock(d.countedAt, false)} in {(d.countSeconds ?? 0).toFixed(1)} s</span></div>
          </>
        )}
      </div>

      {shape && (
        <div className="card">
          <div className="card-head"><div className="card-title">Columns <span className="faint">· {chosen.size} of {shape.columns.length}</span></div>
            <div className="rowx"><button className="btn btn-sm" type="button" onClick={() => dispatch({ type: 'columns', columns: null })}>Select all</button>
              <button className="btn btn-sm btn-ghost" type="button" onClick={() => dispatch({ type: 'columns', columns: [...shape.key] })}>Clear</button></div></div>
          <div className="cols">
            {shape.columns.map((c) => (
              <label key={c.name} className="colpick" title={c.data_type}>
                <input type="checkbox" checked={c.is_key || chosen.has(c.name)} disabled={shape.key.includes(c.name)} onChange={(e) => toggle(c.name, e.target.checked)} />
                <span className="mono">{c.name}</span><span className="ty">{shape.key.includes(c.name) ? 'key · always read' : shortType(c.data_type)}</span>
              </label>
            ))}
          </div>
          <div className="help">Columns you leave out are not read. For In place, columns you leave out keep their current values.</div>
        </div>
      )}

      <div className="card">
        <div className="card-head"><div className="card-title">Limits</div></div>
        <div className="fgrid c3">
          <div><label className="lbl" htmlFor="sel-max">Max rows</label><input className="input mono" id="sel-max" placeholder="No limit" inputMode="numeric" value={d.maxRows} onChange={(e) => set({ maxRows: e.target.value })} />
            <div className="help">Leave empty to read {d.count ? `all ${fmtCount(d.count.rows)} matching rows` : 'every matching row'}.</div></div>
          <div><label className="lbl" htmlFor="sel-sample">Discovery sample</label><input className="input mono" id="sel-sample" inputMode="numeric" value={d.sampleRows} onChange={(e) => set({ sampleRows: e.target.value })} />
            <div className="help">50 to 50,000 rows. Values stay inside the worker.</div></div>
          <div><label className="lbl" htmlFor="sel-par">Parallelism</label>
            <select className="input" id="sel-par" value={d.parallelism} onChange={(e) => set({ parallelism: Number(e.target.value) })}>
              {PARALLELISM.map((n) => <option key={n} value={n}>{n} partitions by {shape?.key.join(', ') || 'key'} range</option>)}
            </select>
            <div className="help">Checkpoint after every committed batch.</div></div>
        </div>
      </div>

      <div className="wfoot"><button className="btn" type="button" onClick={onBack}>Back</button>
        <span className="rowx">{!canLeave(2, d) && d.table && filterText.trim() && !d.count && <span className="hint">Check the filter to continue</span>}
          <button className="btn btn-primary" type="button" disabled={!canLeave(2, d)} onClick={onNext}>Next: Discover</button></span></div>
    </section>
  );
}
