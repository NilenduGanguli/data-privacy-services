import { heatTone, keptHitText } from '../lib/jobs';
import type { ColumnClassification } from '../lib/types';

/** Why a column is kept, in a few words (no values). */
function keptNote(c: ColumnClassification): string {
  if (c.value_entities?.length) return `kept · ${c.value_entities.join(', ')} found`;
  if (c.is_key) return 'kept · key';
  if (['int', 'decimal', 'float'].includes(c.kind)) return 'kept · numeric';
  if (['date', 'timestamp'].includes(c.kind)) return 'kept · date';
  return 'kept';
}

/** Column heat-map (mockup 06 step 3): one tile per column, coloured by sensitivity. */
export function HeatMap({ columns }: { columns: ColumnClassification[] }): JSX.Element {
  return (
    <div className="hm" role="list" aria-label="Column heat-map">
      {columns.map((c) => {
        const tone = heatTone(c);
        const entity = c.entity_type ? `${c.entity_type}${c.text_column ? ' · free text' : ''}${c.is_key ? ' · key' : ''}` : keptNote(c);
        return (
          <div key={c.path} role="listitem" className={`cell ${tone}`} title={`${c.name} · ${c.data_type}`}>
            <div className="cn">{c.name}</div>
            <div className="ce"><span>{entity}</span><span>{c.entity_type ? c.confidence.toFixed(2) : keptHitText(c)}</span></div>
          </div>
        );
      })}
    </div>
  );
}

/** The four-colour legend of the heat-map and the snapshot tables. */
export function HeatLegend(): JSX.Element {
  return (
    <div className="legend">
      <span><i style={{ background: 'var(--rose)' }} />critical</span>
      <span><i style={{ background: 'var(--amber)' }} />sensitive</span>
      <span><i style={{ background: 'var(--teal)' }} />declared</span>
      <span><i style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)' }} />kept</span>
    </div>
  );
}
