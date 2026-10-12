import { useEffect, useId, useRef, useState } from 'react';
import { canConfirmWrite, fmtCount } from '../lib/jobs';

export interface ConfirmWriteProps {
  mode: 'in_place' | 'append';
  objectFqn: string;
  rows: number | null;
  production: boolean;
  /** Whether the console's key holds `admin`; null when the console cannot tell (the server decides). */
  adminScope: boolean | null;
  blockedReason?: string | null;
  connectionName?: string;
  synthesize?: boolean;
  keyColumns?: string[];
  rewrittenColumns?: number;
  oneWay?: { name: string; method: string }[];
  partitions?: number;
  batchRows?: number | null;
  pkText?: string;
  rowEstimate?: number | null;
  onConfirm: (typed: string) => void;
  onCancel: () => void;
}

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

/**
 * The in-place / append confirmation (mockup 06): what the write does, the production note, and a
 * box where the operator types the object name. The primary button stays disabled until the typed
 * name matches and nothing blocks the write. Escape cancels; focus stays inside the dialog.
 */
export function ConfirmWriteModal(p: ConfirmWriteProps): JSX.Element {
  const [typed, setTyped] = useState('');
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const inputId = useId();
  const blocked = !!p.blockedReason || (p.production && p.adminScope === false);
  const ok = canConfirmWrite(typed, p.objectFqn, blocked);
  const rows = p.rows === null ? 'the selected' : fmtCount(p.rows);
  const key = p.keyColumns?.length ? p.keyColumns.join(', ') : 'its key';

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => opener?.focus?.();
  }, []);

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>): void {
    if (e.key === 'Escape') { e.preventDefault(); p.onCancel(); return; }
    if (e.key !== 'Tab' || !box.current) return;
    const items = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  return (
    <div className="modal-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) p.onCancel(); }}>
      <div ref={box} className={`confirm${p.mode === 'append' ? ' append' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={onKeyDown}>
        <div className="between">
          <h3 id={titleId}>{p.mode === 'in_place' ? `Confirm: overwrite ${rows} rows in ${p.objectFqn}` : `Confirm: append ${rows} rows to ${p.objectFqn}`}</h3>
          {p.production && <span className="chip amber">production</span>}
        </div>
        <p className="lead">{p.mode === 'in_place' ? 'This job changes the source table.' : 'This job adds rows to a table.'} Read what happens before you confirm.</p>
        {p.mode === 'in_place' ? (
          <ul className="conseq">
            <li className="r"><span><b>{rows} rows</b> in {p.objectFqn} are updated by {key}: {p.rewrittenColumns ?? 'the transformed'} columns each, in batches{p.batchRows ? ` of ${fmtCount(p.batchRows)}` : ''} per partition{p.partitions ? ` across ${p.partitions} partitions` : ''}.</span></li>
            <li className="a"><span>Applications and reports that read {p.objectFqn} see protected values as each batch commits.</span></li>
            {p.synthesize ? (
              <li className="r"><span>Synthetic values have no path back. <b>After the write, the original values in these columns are gone.</b></span></li>
            ) : (
              <li className="t"><span>The originals can be restored only with the key returned when you submit the job. <b>DPS keeps no copy of it</b>; if it is lost, the original values cannot be recovered.</span></li>
            )}
            {!p.synthesize && !!p.oneWay?.length && (
              <li className="a"><span>{p.oneWay.map((c) => c.name).join(', ')} {p.oneWay.length === 1 ? 'uses a one-way method' : 'use one-way methods'} ({[...new Set(p.oneWay.map((c) => c.method))].join(', ')}). {p.oneWay.length === 1 ? 'That column cannot' : 'Those columns cannot'} be restored, even with the key.</span></li>
            )}
            <li><span>If a partition fails it resumes from its last committed batch. No row is left half-written.</span></li>
          </ul>
        ) : (
          <ul className="conseq">
            <li className="a"><span>{p.objectFqn} grows by <b>{rows} rows</b>{p.rowEstimate ? <> (about {fmtCount(p.rowEstimate)} rows today)</> : null}. Existing rows are not changed.</span></li>
            {p.pkText && <li className="a"><span>{p.pkText} The evidence pack records the key range.</span></li>}
            <li><span>Reports and jobs that read {p.objectFqn} will count the new rows until you remove them.</span></li>
            {p.synthesize
              ? <li><span>The new rows are synthetic and have no path back to real customers.</span></li>
              : <li className="t"><span>The key that reverses the new rows is returned once, when you submit the job. <b>DPS keeps no copy.</b></span></li>}
          </ul>
        )}
        {p.production && (
          <div className="callout amber" style={{ marginTop: 14, fontSize: 12.5 }}>
            <b>{p.connectionName ?? 'This connection'} is tagged production</b>, so writing to it needs the admin scope.{' '}
            {p.adminScope === true && 'Your API key has it; the confirmation is written to the audit log with your key and the time.'}
            {p.adminScope === null && 'DPS checks the scope of your API key when you press Run job and refuses the job without it.'}
          </div>
        )}
        {p.production && p.adminScope === false && (
          <div className="callout rose" style={{ marginTop: 10, fontSize: 12.5 }}><b>You can&apos;t confirm with this key.</b> It lacks the admin scope that production writes need.</div>
        )}
        {p.blockedReason && <div className="callout rose" style={{ marginTop: 10, fontSize: 12.5 }}><b>You can&apos;t confirm yet.</b> {p.blockedReason}</div>}
        <div className="typebox">
          <div>
            <label className="lbl" htmlFor={inputId}>Type <span className="mono" style={{ color: 'var(--ink)' }}>{p.objectFqn}</span> to confirm</label>
            <input ref={input} id={inputId} className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false}
                   onKeyDown={(e) => { if (e.key === 'Enter' && ok) p.onConfirm(typed.trim()); }} />
          </div>
          <button type="button" className="btn" onClick={p.onCancel}>Choose another write mode</button>
          <button type="button" className="btn btn-danger" disabled={!ok} onClick={() => p.onConfirm(typed.trim())}>
            {p.mode === 'in_place' ? `Write ${rows} rows in place` : `Append ${rows} rows`}
          </button>
        </div>
      </div>
    </div>
  );
}
