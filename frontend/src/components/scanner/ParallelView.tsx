import type { ScanField, ScanMode, ScanPage } from '../../lib/types';
import { ArtefactImage } from './ArtefactImage';
import { hostOf, pathOf, plural } from './format';

export type DetailView = 'screens' | 'fields';

const filledCount = (fields: ScanField[]): number => fields.filter((f) => f.fill_status === 'filled').length;
const validationTotal = (page: ScanPage, side: ScanMode): number => Object.values(page.validation[side] ?? {}).reduce((a, b) => a + b, 0);

function keptOnObfuscate(f: ScanField): boolean { return !f.entity_type || f.obfuscate_method === 'keep' || f.fill_status === 'neutral'; }

/** The filled side of one field in the Fields view. */
function FilledField({ f, side }: { f: ScanField; side: ScanMode }): JSX.Element {
  const value = side === 'synthesize' ? f.fill_synthesize : f.fill_obfuscate;
  if (f.fill_status === 'skipped_file') return <div className="f none">file input · listed, never filled</div>;
  if (value === null) return <div className="f none">{f.input_type === 'password' ? 'typed, never stored' : f.fill_status.replace(/_/g, ' ')}</div>;
  const cls = side === 'synthesize' ? 'syn' : keptOnObfuscate(f) ? 'kept' : 'obf';
  return <div className={`f ${cls}`}>{value}</div>;
}

/**
 * Parallel view (mockup 04): the page as captured (annotated original, fields numbered) next to the same
 * page filled with the synthetic persona or with reversible tokens. "Screens" shows the worker's
 * screenshots; "Fields" rebuilds the form from the field rows. The original side never shows a value:
 * an empty field reads "(empty)", a pre-filled one "••• masked" (the worker masked it before capture).
 */
export function ParallelView({ page, fields, side, onSide, view, onView, origin, persona }: {
  page: ScanPage; fields: ScanField[]; side: ScanMode; onSide: (s: ScanMode) => void; view: DetailView; onView: (v: DetailView) => void;
  origin: string; persona: number;
}): JSX.Element {
  const path = pathOf(page.url_redacted, origin);
  const at = `${hostOf(page.url_redacted)}…${path}`;
  const masked = fields.some((f) => f.prefilled) || Object.values(page.rendered_findings).some((n) => n > 0);
  const messages = validationTotal(page, side);
  const filled = filledCount(fields);
  const filledShot = side === 'synthesize' ? page.artefacts.filled_synthesize : page.artefacts.filled_obfuscate;
  return (
    <section className="card" aria-labelledby="pvT">
      <div className="card-head"><div className="card-title" id="pvT">Parallel view · <span className="mono">{path}</span></div>
        <div className="fillwith"><span className="muted" style={{ fontSize: 12 }}>Filled with</span>
          <div className="seg" role="group" aria-label="Filled with">
            <button type="button" className={side === 'synthesize' ? 'on' : undefined} aria-pressed={side === 'synthesize'} onClick={() => onSide('synthesize')}>Synthetic persona</button>
            <button type="button" className={side === 'obfuscate' ? 'on' : undefined} aria-pressed={side === 'obfuscate'} onClick={() => onSide('obfuscate')}>Obfuscated tokens</button>
          </div>
          <div className="seg sm" role="group" aria-label="Show">
            <button type="button" className={view === 'screens' ? 'on' : undefined} aria-pressed={view === 'screens'} onClick={() => onView('screens')}>Screens</button>
            <button type="button" className={view === 'fields' ? 'on' : undefined} aria-pressed={view === 'fields'} onClick={() => onView('fields')}>Fields</button>
          </div></div></div>
      <div className="parallel">
        <div className="frame">
          <div className="bar-url"><i /><i /><i /><span className="mono">{at}</span><span className="chip amber">original · as captured{masked ? ' · personal data masked' : ''}</span></div>
          {view === 'screens'
            ? <ArtefactImage id={page.artefacts.annotated ?? page.artefacts.original} alt={`Annotated original of ${path}, fields numbered`} />
            : (
              <div className="form">
                {fields.map((f) => (
                  <div key={`${f.form_index}-${f.field_no}`} className={f.tag === 'textarea' ? 'full' : undefined}>
                    <span className="fl">{f.label ?? `field ${f.field_no}`}</span>
                    <div className="f real">{f.prefilled ? '••• masked' : '(empty)'}<span className="n">{f.field_no}</span></div>
                  </div>
                ))}
                <div className="btnrow"><span className="fakebtn">Continue →</span></div>
              </div>
            )}
        </div>
        <div className="frame">
          <div className="bar-url"><i /><i /><i /><span className="mono">{at}</span>
            <span className={`chip ${side === 'synthesize' ? 'violet' : 'teal'}`}>{side === 'synthesize' ? `filled · synthetic persona ${persona}` : 'filled · obfuscated tokens (key held by caller)'}</span></div>
          {view === 'screens'
            ? <ArtefactImage id={filledShot} alt={`${path} filled with ${side === 'synthesize' ? 'synthetic persona values' : 'reversible tokens'}`} />
            : (
              <div className="form">
                {fields.map((f) => (
                  <div key={`${f.form_index}-${f.field_no}`} className={f.tag === 'textarea' ? 'full' : undefined}>
                    <span className="fl">{f.label ?? `field ${f.field_no}`}</span><FilledField f={f} side={side} />
                  </div>
                ))}
                <div className="btnrow"><span className="fakebtn">Continue →</span></div>
              </div>
            )}
        </div>
      </div>
      <div className="legend fill-key">
        <span><i style={{ background: 'var(--amber)' }} />original value</span>
        {side === 'synthesize'
          ? <span><i style={{ background: 'var(--violet)' }} />synthetic persona value</span>
          : <><span><i style={{ background: 'var(--teal)' }} />reversible token</span><span><i style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)' }} />kept by policy</span></>}
      </div>
      <div className="callout" style={{ marginTop: 12, fontSize: 12 }}>
        Fields are filled in the real page by the browser worker and captured; no button is ever pressed and any form POST is blocked by request interception.{' '}
        {messages
          ? `Client-side validation recorded ${plural(messages, 'validation message')}; see the inventory.`
          : `Client-side validation accepted all ${filled} values (format evidence recorded).`}
      </div>
    </section>
  );
}
