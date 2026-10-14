import { count, percent } from '../lib/format';
import type { FidelityBlock, PrivacyBlock, TabularBlock, UtilityBlock } from '../lib/types';

const ENGINE: Record<string, string> = { copula: 'built-in Gaussian copula', mostly: 'MOSTLY AI engine (optional synth-ml extra)' };
const col = (path: string): string => path.replace(/^\$\./, '').replace(/^\$/, '');
const num = (v: number | undefined | null, d = 3): string => (v === undefined || v === null || Number.isNaN(v) ? '—' : v.toFixed(d));

/** A null-rate change in percentage points: "+0.1 pp", "−0.2 pp", "0.0 pp". */
export function pp(delta: number): string {
  const v = Math.round(delta * 1000) / 10;
  if (v === 0) return '0.0 pp';
  return `${v > 0 ? '+' : '−'}${Math.abs(v).toFixed(1)} pp`;
}

function Fidelity({ f, tabular }: { f: FidelityBlock; tabular: TabularBlock | null }): JSX.Element {
  const limit = f.limits.column;
  const within = f.columns.filter((c) => c.within_limit).length;
  const meanCol = f.columns.length ? f.columns.reduce((a, c) => a + c.distance, 0) / f.columns.length : 0;
  const names = f.corr_matrix.columns.map(col);
  const m = f.corr_matrix.delta;
  const n = names.length;
  let max = { v: 0, a: '', b: '' };
  for (let i = 1; i < n; i += 1) for (let j = 0; j < i; j += 1) if ((m[i]?.[j] ?? 0) > max.v) max = { v: m[i][j], a: names[i], b: names[j] };
  const pairs = (n * (n - 1)) / 2;
  const corrLimit = f.limits.corr;
  const shade = (v: number): number => Math.round(Math.min(45, (Math.abs(v) / (corrLimit || 1)) * 45));
  return (
    <div className="two">
      <div>
        <div className="tbl-scroll"><table className="tbl doc stack c3 fid"><thead><tr><th>Column</th><th>Metric</th><th className="num">Distance</th><th style={{ width: '28%' }}>vs {num(limit, 2)} limit</th><th className="num">Null rate Δ</th></tr></thead><tbody>
          {f.columns.map((c) => {
            const w = Math.min(100, Math.round((c.distance / (limit || 1)) * 100));
            return (
              <tr key={c.path}>
                <td className="m" data-l="Column">{col(c.path)}</td>
                <td data-l="Metric">{c.metric.toUpperCase()}</td>
                <td className="num m" data-l="Distance">{num(c.distance)}</td>
                <td data-l="vs limit"><div className="dist" title={`${num(c.distance)} of the ${num(limit, 2)} limit`}><span className="track"><i className={c.within_limit ? '' : 'over'} style={{ width: `${w}%` }} /><b style={{ left: '100%' }} /></span></div></td>
                <td className="num m" data-l="Null rate Δ">{pp(c.null_rate_delta)}</td>
              </tr>
            );
          })}
        </tbody></table></div>
        <p className="note">KS = Kolmogorov–Smirnov statistic for numbers and dates; TVD = total-variation distance for categories.{' '}
          {within === f.columns.length ? `All ${f.columns.length} columns are within the ${num(limit, 2)} limit.` : `${within} of ${f.columns.length} columns are within the ${num(limit, 2)} limit.`}{' '}
          Summary score <b>{num(f.score, 2)}</b> = 1 − mean column distance ({num(meanCol)}) − mean correlation delta ({num(f.corr_delta)}).</p>
        {tabular && tabular.excluded.length > 0 && (
          <p className="note">Not modelled (kept out of the tabular model): {tabular.excluded.map((e) => `${col(e.path)} (${e.reason})`).join(' · ')}.</p>
        )}
      </div>
      <div>
        <div className="synth-h" style={{ marginTop: 0 }}>Correlation-matrix delta, |synthetic − source|</div>
        {n > 1 ? (
          <>
            <div className="cmx" role="table" aria-label="Absolute change in correlation between synthetic and source, lower triangle"
              style={{ gridTemplateColumns: `112px repeat(${n - 1}, minmax(0, 1fr))` }}>
              <div role="presentation" />
              {names.slice(0, n - 1).map((name) => <div key={`h-${name}`} className="hc" role="columnheader" title={name}>{name}</div>)}
              {names.slice(1).map((row, k) => {
                const i = k + 1;
                return [
                  <div key={`r-${row}`} className="hl2" role="rowheader" title={row}>{row}</div>,
                  ...names.slice(0, n - 1).map((cname, j) => (j < i
                    ? <div key={`${row}-${cname}`} className="c" role="cell" style={{ background: `color-mix(in srgb, var(--ink-2) ${shade(m[i][j])}%, var(--surface))` }} title={`${row} × ${cname}: |Δ| ${num(m[i][j])}`}>{num(m[i][j])}</div>
                    : <div key={`${row}-${cname}`} className="c blank" role="cell" />)),
                ];
              })}
            </div>
            <div className="ramp"><span>0.00</span><span className="g" /><span>{num(corrLimit, 2)} limit</span></div>
            <p className="note">Largest change {num(max.v)}{max.a ? ` for ${max.a} × ${max.b}` : ''}. Mean {num(f.corr_delta)} across {pairs} {pairs === 1 ? 'pair' : 'pairs'}.</p>
          </>
        ) : <p className="note">Fewer than two numeric columns were modelled, so there is no correlation to compare.</p>}
      </div>
    </div>
  );
}

function Privacy({ p, rowsWritten }: { p: PrivacyBlock; rowsWritten: number }): JSX.Element {
  const nn = p.nndr;
  const qi = p.qi_uniqueness;
  const mia = p.membership_inference;
  const redraws = p.exact_row_resamples_run ?? p.exact_row_resamples ?? 0;
  const matches = p.exact_row_matches ?? 0;
  const qiOk = qi ? qi.synthetic <= qi.source && qi.unique_overlap === 0 : false;
  return (
    <div className="pm">
      <div>
        <div className="top"><span className="l">Exact-row matches</span>{matches > 0
          ? <span className="chip rose">{count(matches)} found</span>
          : <span className={`chip ${p.enforced ? 'emerald' : 'amber'}`}>{p.enforced ? 'enforced' : 'measured'}</span>}</div>
        <div className="v">{count(matches)} of {count(rowsWritten)}</div>
        <p>{matches > 0
          ? `${count(matches)} synthetic ${matches === 1 ? 'row equals' : 'rows equal'} a source row on all modelled columns.`
          : 'No synthetic row equals a source row on all modelled columns.'} The engine rejected and redrew {count(redraws)} {redraws === 1 ? 'row' : 'rows'} before writing.</p>
      </div>
      <div>
        <div className="top"><span className="l">Nearest-neighbour distance ratio</span>
          {nn && nn.measured ? <span className={`chip ${nn.passed ? 'emerald' : 'amber'}`}>{nn.passed ? `pass · ≥ ${num(nn.target, 2)}` : `below target ${num(nn.target, 2)}`}</span> : <span className="chip slate">not measured</span>}</div>
        <div className="v">{nn && nn.measured ? num(nn.ratio, 2) : '—'}</div>
        <p>{nn && nn.measured
          ? `Median distance synthetic → source ${num(nn.synth_to_real_median)}, against ${num(nn.real_to_real_median)} between real rows${nn.holdout ? ' in the holdout' : ''}. Synthetic rows are ${nn.passed ? 'not closer to real people than real people are to each other' : 'closer to real rows than the target allows'}.`
          : 'Too few rows to compare distances.'}</p>
      </div>
      <div>
        <div className="top"><span className="l">Quasi-identifier uniqueness</span>{qi ? <span className={`chip ${qiOk ? 'emerald' : 'amber'}`}>{qiOk ? 'pass' : 'review'}</span> : <span className="chip slate">not measured</span>}</div>
        <div className="v">{qi ? <>{percent(qi.synthetic, true, 1)} <small>vs {percent(qi.source, true, 1)} in source</small></> : '—'}</div>
        <p>{qi ? `Rows unique on ${qi.columns.map(col).join(', ') || 'the quasi-identifiers'}. ${count(qi.unique_overlap)} of the unique synthetic rows ${qi.unique_overlap === 1 ? 'matches' : 'match'} a unique source row.` : 'The policy names no quasi-identifiers.'}</p>
      </div>
      <div>
        <div className="top"><span className="l">Membership-inference proxy</span>
          {mia?.run ? <span className={`chip ${mia.passed ? 'emerald' : 'amber'}`}>{mia.passed ? `pass · AUC ≤ ${num(mia.target, 2)}` : `above target ${num(mia.target, 2)}`}</span> : <span className="chip slate">not run</span>}</div>
        <div className="v">{mia?.run ? num(mia.auc, 2) : '—'}</div>
        <p>{mia?.run
          ? `Distance-to-closest-record AUC over ${count(mia.members ?? 0)} training rows and ${count(mia.non_members ?? 0)} held-out rows; 0.50 means an attacker cannot tell them apart.`
          : mia?.reason ?? 'Not measured for this engine.'}</p>
      </div>
    </div>
  );
}

function Utility({ u }: { u: UtilityBlock }): JSX.Element {
  return (
    <div className="pm" style={{ marginTop: 12 }}>
      <div>
        <div className="top"><span className="l">Utility · train on synthetic, test on real</span>{u.run ? <span className="chip slate">{u.model ?? 'model'} · {u.metric}</span> : <span className="chip slate">not run</span>}</div>
        <div className="v">{u.run ? num(u.ratio, 2) : '—'}{u.run && <small> synthetic / real</small>}</div>
        <p>{u.run
          ? `Predicting ${u.label ?? 'the nominated label'} (${u.task}) on ${count(u.test_rows ?? 0)} held-out real rows: ${u.metric} ${num(u.train_on_synthetic)} when trained on synthetic rows, ${num(u.train_on_real)} when trained on real rows.`
          : u.reason ?? 'No utility label was nominated.'}</p>
      </div>
    </div>
  );
}

/** Section 6 of a synthesize pack (mockup 10 #synthetic): fidelity, correlation delta, privacy,
 *  utility — figures measured on the deterministic preview; numbers and column names only. */
export function FidelityPanel({ fidelity, privacy, utility, tabular, rowsWritten }: {
  fidelity: FidelityBlock | null; privacy: PrivacyBlock | null; utility: UtilityBlock | null; tabular: TabularBlock | null; rowsWritten: number;
}): JSX.Element {
  if (privacy && privacy.engine === 'none') {
    return <div className="callout amber">Privacy not measured — non-sensitive columns were copied from the source (tabular synthesis off). The sensitive columns were replaced by surrogates; the pack makes no fidelity or privacy claim.</div>;
  }
  const engine = tabular?.engine ?? fidelity?.engine ?? privacy?.engine ?? 'copula';
  return (
    <>
      <p className="lead">
        DPS fitted the {ENGINE[engine] ?? engine}{tabular ? ` on ${count(tabular.fit_rows)} source rows, held ${count(tabular.holdout_rows)} out to measure privacy, and measured a deterministic preview of ${count(tabular.preview_rows)} rows exactly as the job writes them` : ''}.
        {' '}The synthesis seed went back to the caller and is not in this pack.
      </p>
      {fidelity && <><div className="synth-h">Fidelity · how closely the synthetic columns follow the source</div><Fidelity f={fidelity} tabular={tabular} /></>}
      {privacy && privacy.measured && <><div className="synth-h">Privacy · how far the synthetic rows are from real customers</div><Privacy p={privacy} rowsWritten={rowsWritten} /></>}
      {privacy && !privacy.measured && <div className="callout amber">Privacy not measured: {privacy.reason ?? 'no preview was measured'}.</div>}
      {utility && <Utility u={utility} />}
    </>
  );
}
