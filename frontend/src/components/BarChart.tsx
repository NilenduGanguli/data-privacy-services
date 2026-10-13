import { useMemo, useState } from 'react';
import { axisDay } from '../lib/format';

/** One stacked series; `color` is a CSS variable such as `var(--emerald)`. */
export interface Series { key: string; label: string; color: string }

/**
 * Stacked bars drawn as SVG (no chart library; mockups 01 and 09), with a "View as table" toggle
 * that shows the same numbers as an accessible table. `xKey` values are ISO days unless
 * `xFormat` says otherwise.
 */
export function BarChart({ data, series, xKey, label, height = 200, xFormat = axisDay, initialTable = false, legend = true }: {
  data: Record<string, string | number>[]; series: Series[]; xKey: string; label: string; height?: number;
  xFormat?: (x: string) => string; initialTable?: boolean; legend?: boolean;
}): JSX.Element {
  const [asTable, setAsTable] = useState(initialTable);
  const width = 640;
  const pad = { l: 40, r: 8, t: 10, b: 22 };
  const max = useMemo(() => Math.max(1, ...data.map((d) => series.reduce((s, x) => s + Number(d[x.key] ?? 0), 0))), [data, series]);
  const bw = (width - pad.l - pad.r) / Math.max(1, data.length);
  const y = (v: number): number => pad.t + (height - pad.t - pad.b) * (1 - v / max);
  const every = Math.max(1, Math.ceil(data.length / 7));
  return (
    <div className="m5chart">
      <div className="chart-tools">
        {legend ? <div className="legend">{series.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}</div> : <span />}
        <button type="button" className="btn btn-sm" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? 'View as chart' : 'View as table'}
        </button>
      </div>
      {asTable ? (
        <div className="chart-table"><table className="tbl">
          <thead><tr><th>Day</th>{series.map((s) => <th key={s.key} className="num">{s.label}</th>)}</tr></thead>
          <tbody>{[...data].reverse().map((d) => (
            <tr key={String(d[xKey])}><td>{xFormat(String(d[xKey]))}</td>
              {series.map((s) => <td key={s.key} className="num mono">{Number(d[s.key] ?? 0).toLocaleString('en-US')}</td>)}</tr>
          ))}</tbody>
        </table></div>
      ) : (
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} width="100%" height={height} preserveAspectRatio="none">
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line x1={pad.l} x2={width - pad.r} y1={y(max * f)} y2={y(max * f)} stroke={f === 0 ? 'var(--border-strong)' : 'var(--border)'} />
              <text x={pad.l - 6} y={y(max * f) + 4} textAnchor="end" fontSize="10" fill="var(--ink-3)" fontFamily="var(--mono)">{Math.round(max * f).toLocaleString('en-US')}</text>
            </g>
          ))}
          {data.map((d, i) => {
            let acc = 0;
            const x = String(d[xKey]);
            return (
              <g key={x}>
                {series.map((s) => {
                  const v = Number(d[s.key] ?? 0);
                  const top = y(acc + v);
                  const h = y(acc) - top;
                  acc += v;
                  return v > 0 ? (
                    <rect key={s.key} x={pad.l + i * bw + bw * 0.18} y={top} width={bw * 0.64} height={Math.max(h, 1.5)} fill={s.color} rx={2}>
                      <title>{`${xFormat(x)} · ${s.label}: ${v.toLocaleString('en-US')}`}</title>
                    </rect>
                  ) : null;
                })}
                {((data.length - 1 - i) % every === 0) && (
                  <text x={pad.l + i * bw + bw / 2} y={height - 6} textAnchor="middle" fontSize="10" fill="var(--ink-3)" fontFamily="var(--mono)">{xFormat(x)}</text>
                )}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
