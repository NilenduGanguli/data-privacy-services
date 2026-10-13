import { useMemo, useState } from 'react';
import type { InventoryRow, NumberedRiskFlag, ScanField, ScanMode, Sensitivity } from '../../lib/types';
import { autocompleteText, entityChip, evidenceLabel, fillMethod, flagTone, inputText, plural } from './format';

function EntityCell({ type, sensitivity }: { type: string | null; sensitivity: Sensitivity | null }): JSX.Element {
  return type ? <span className={`chip ${entityChip(sensitivity)}`}>{type}</span> : <span className="faint">—</span>;
}

function Codes({ codes, flags }: { codes: string[]; flags: NumberedRiskFlag[] }): JSX.Element | null {
  if (!codes.length) return null;
  return <>{codes.map((c) => { const f = flags.find((r) => r.code === c); return <span key={c} className={`chip ${f ? flagTone(f) : 'amber'}`} style={{ marginLeft: 6 }}>{c}</span>; })}</>;
}

const conf = (c: number | null): string => (c === null ? '—' : c.toFixed(2));

/**
 * Field inventory of one page (mockup 04) with the filled value of the active side; the page's own
 * validation messages (detector-redacted by the worker) expand under their field.
 */
export function FieldInventory({ path, fields, flags, side }: { path: string; fields: ScanField[]; flags: NumberedRiskFlag[]; side: ScanMode }): JSX.Element {
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set());
  const toggle = (n: number): void => setOpen((s) => { const x = new Set(s); if (x.has(n)) x.delete(n); else x.add(n); return x; });
  return (
    <section className="card" aria-labelledby="fiT">
      <div className="card-head"><div className="card-title" id="fiT">Field inventory · <span className="mono">{path}</span></div>
        <div className="legend"><span><i style={{ background: 'var(--rose)' }} />critical</span><span><i style={{ background: 'var(--amber)' }} />sensitive</span>
          <span><i style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)' }} />kept</span></div></div>
      <div className="tbl-scroll"><table className="tbl stack inv"><thead><tr>
        <th>#</th><th>Label</th><th>Input</th><th>autocomplete</th><th>Entity</th><th>Conf.</th><th>Fill method</th><th>Evidence</th><th>Filled value</th>
      </tr></thead><tbody>
        {fields.map((f) => {
          const msgs = f.validation_messages.filter((m) => m.mode === side);
          const value = side === 'synthesize' ? f.fill_synthesize : f.fill_obfuscate;
          return [
            <tr key={`r${f.field_no}`} data-field={f.field_no}>
              <td data-l="#">{f.field_no}</td>
              <td data-l="Label">{f.label ?? <span className="faint">(no label)</span>}{f.in_shadow && <span className="faint"> · shadow DOM</span>}{f.frame_index > 0 && <span className="faint"> · iframe</span>}</td>
              <td data-l="Input" className="mono">{inputText(f)}</td>
              <td data-l="autocomplete" className="mono">{autocompleteText(f, flags)}<Codes codes={f.risk_codes} flags={flags} /></td>
              <td data-l="Entity"><EntityCell type={f.entity_type} sensitivity={f.sensitivity} /></td>
              <td data-l="Conf." className="mono">{conf(f.confidence)}</td>
              <td data-l="Fill method" className="mono">{fillMethod(f)}</td>
              <td data-l="Evidence" className="faint">{evidenceLabel(f.detectors)}</td>
              <td data-l="Filled value" className="mono">{value ?? <span className="faint">{f.fill_status.replace(/_/g, ' ')}</span>}
                {msgs.length > 0 && <> <button type="button" className="linkish" aria-expanded={open.has(f.field_no)} onClick={() => toggle(f.field_no)}>{plural(msgs.length, 'message')}</button></>}</td>
            </tr>,
            msgs.length > 0 && open.has(f.field_no) ? (
              <tr key={`v${f.field_no}`} className="vrow"><td colSpan={9}>
                {msgs.map((m, i) => <div key={i} className="vmsg"><span className="chip slate">{m.source}</span><span className="mono">{m.codes.join(', ')}</span><span>{m.text}</span></div>)}
              </td></tr>
            ) : null,
          ];
        })}
      </tbody></table></div>
    </section>
  );
}

const SENS: Sensitivity[] = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];

/** Inventory tab: every field of the scan, filterable, downloadable as CSV or JSON (GET /inventory). */
export function ScanInventory({ rows, flags, onDownload }: { rows: InventoryRow[]; flags: NumberedRiskFlag[]; onDownload: (format: 'csv' | 'json') => void }): JSX.Element {
  const [entity, setEntity] = useState('');
  const [sens, setSens] = useState('');
  const [riskOnly, setRiskOnly] = useState(false);
  const entities = useMemo(() => [...new Set(rows.map((r) => r.entity_type).filter((e): e is string => !!e))].sort(), [rows]);
  const shown = rows.filter((r) => (!entity || r.entity_type === entity) && (!sens || r.sensitivity === sens) && (!riskOnly || r.risk_codes.length > 0));
  return (
    <section className="card" aria-labelledby="invT">
      <div className="card-head"><div className="card-title" id="invT">Inventory · whole scan</div>
        <div className="rowx"><button type="button" className="btn btn-sm" onClick={() => onDownload('csv')}>Download CSV</button>
          <button type="button" className="btn btn-sm" onClick={() => onDownload('json')}>Download JSON</button></div></div>
      <div className="filters">
        <label>Entity type<select className="input" value={entity} onChange={(e) => setEntity(e.target.value)}>
          <option value="">all</option>{entities.map((e) => <option key={e} value={e}>{e}</option>)}</select></label>
        <label>Sensitivity<select className="input" value={sens} onChange={(e) => setSens(e.target.value)}>
          <option value="">all</option>{SENS.map((s) => <option key={s} value={s}>{s.toLowerCase()}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={riskOnly} onChange={(e) => setRiskOnly(e.target.checked)} />Risk flags only</label>
        <span className="faint" style={{ fontSize: 12 }}>{shown.length} of {plural(rows.length, 'field')}</span>
      </div>
      <div className="tbl-scroll"><table className="tbl stack inv"><thead><tr>
        <th>Page</th><th>#</th><th>Label</th><th>Input</th><th>autocomplete</th><th>Entity</th><th>Conf.</th><th>Fill method</th><th>Evidence</th>
      </tr></thead><tbody>
        {shown.map((r) => (
          <tr key={`${r.page_no}-${r.form_index}-${r.field_no}`} data-row={`${r.page_no}-${r.field_no}`}>
            <td data-l="Page" className="mono">{r.path}</td>
            <td data-l="#">{r.field_no}</td>
            <td data-l="Label">{r.label ?? <span className="faint">(no label)</span>}</td>
            <td data-l="Input" className="mono">{inputText(r)}</td>
            <td data-l="autocomplete" className="mono">{autocompleteText(r, flags)}<Codes codes={r.risk_codes} flags={flags} /></td>
            <td data-l="Entity"><EntityCell type={r.entity_type} sensitivity={r.sensitivity} /></td>
            <td data-l="Conf." className="mono">{conf(r.confidence)}</td>
            <td data-l="Fill method" className="mono">{fillMethod(r)}</td>
            <td data-l="Evidence" className="faint">{evidenceLabel(r.detectors)}</td>
          </tr>
        ))}
        {!shown.length && <tr><td colSpan={9} className="faint">No field matches these filters.</td></tr>}
      </tbody></table></div>
    </section>
  );
}
