import { useEffect, useMemo, useState } from 'react';
import { EntityChip } from './Chip';
import { PLAN_SAMPLE, planSummary } from '../lib/policyDraft';
import { simulateFpe, type Body } from '../lib/policyEdit';
import type { EntityInfo, MethodSpec, PlanOut, Sensitivity } from '../lib/types';

function Params({ spec }: { spec: MethodSpec }): JSX.Element {
  return <>{Object.entries(spec.params ?? {}).map(([k, v]) => <span key={k} className="prm">{k} {Array.isArray(v) ? v.join(' · ') : String(v)}</span>)}</>;
}

/** Plan preview (mockup 05): `POST /policies/plan` with the draft (or active) body on a sample;
 *  the FF1 switch recomputes the planner's static fallback client-side and says "simulated". */
export function PlanPreview({ label, body, fpe, entities, runPlan, onRows }: {
  label: string; body: Body | null; fpe: boolean | null; entities: EntityInfo[]; runPlan: (data: unknown) => Promise<PlanOut>; onRows?: (n: number) => void;
}): JSX.Element {
  const [sample, setSample] = useState(PLAN_SAMPLE);
  const [plan, setPlan] = useState<PlanOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const deployment = fpe ?? true;
  const [view, setView] = useState<boolean | null>(null);
  const shown = view ?? deployment;
  const simulated = shown !== deployment;
  const rows = useMemo(() => (plan ? (simulated ? simulateFpe(plan.plan, shown, body) : plan.plan) : []), [plan, simulated, shown, body]);
  const byName = new Map(entities.map((e) => [e.name, e]));
  const tone = (n: string): Sensitivity => byName.get(n)?.sensitivity ?? 'MEDIUM';
  const summary = plan ? planSummary(rows) : null;
  async function run(): Promise<void> {
    setError(null);
    let data: unknown;
    try { data = JSON.parse(sample) as unknown; } catch (e) { setError(`The sample is not valid JSON: ${(e as Error).message}`); return; }
    setBusy(true);
    try { const p = await runPlan(data); setPlan(p); onRows?.(p.plan.length); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  // run once per policy shown (the label names it), so the table is filled as in the mockup
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (body) void run(); }, [label, !!body]);
  return (
    <section className="card" id="plan" aria-labelledby="plan-t">
      <div className="card-head"><div className="card-title" id="plan-t">Plan preview <span className="faint">· {label} on a pasted sample</span></div>
        <div className="tools">
          <div className="seg sm" role="group" aria-label="FF1">{[true, false].map((on) => (
            <button key={String(on)} type="button" className={shown === on ? 'on' : ''} aria-pressed={shown === on} onClick={() => setView(on)}>{on ? 'On' : 'Off'}{on === deployment ? ' · this deployment' : ''}</button>))}</div>
          <button className="btn btn-sm btn-primary" type="button" disabled={busy} onClick={() => void run()}>{busy ? 'Planning…' : 'Re-run plan'}</button></div></div>
      <p className="card-sub">The resolved method per finding, with the reason. Nothing is transformed and the sample is not stored.{simulated && <> <span className="chip amber">simulated</span> FF1 {shown ? 'on' : 'off'} computed in the browser from the planner's static rule.</>}</p>
      {error && <div className="err" role="alert">{error}</div>}
      <div className="plan">
        <textarea className="input" rows={18} value={sample} onChange={(e) => setSample(e.target.value)} aria-label="Plan sample (synthetic values only)" spellCheck={false} />
        <div>
          {summary && <div className="plan-sum"><span className="chip slate">{rows.length} findings</span><span className="chip slate">{rows.filter((r) => r.reversible).length} reversible</span>{summary.counts.map(([m, n]) => <span key={m} className="chip slate">{m} ×{n}</span>)}{summary.fallbacks > 0 && <span className="chip amber">{summary.fallbacks} {summary.fallbacks === 1 ? 'fallback' : 'fallbacks'}</span>}</div>}
          <div className="tbl-scroll"><table className="tbl stack"><thead><tr><th>Locator</th><th>Entity · score</th><th>Method</th><th>Reason</th><th>Reversible</th></tr></thead>
            <tbody>{plan ? rows.map((r, i) => (
              <tr key={i}><td className="loc" data-l="Locator">{r.path}{r.span ? `[${r.span[0]}:${r.span[1]}]` : ''}</td>
                <td data-l="Entity"><EntityChip type={r.entity_type} sensitivity={tone(r.entity_type)} /><span className="score">{r.score.toFixed(2)}</span></td>
                <td data-l="Method"><span className="m">{r.method}</span><Params spec={{ method: r.method, params: r.params }} /></td>
                <td className="rsn" data-l="Reason">{r.reason.split('|').map((p) => (p.startsWith('fallback:') ? <span key={p} className="fbk">{p}</span> : <span key={p}>{p} </span>))}</td>
                <td data-l="Reversible" className={`revcell${r.reversible ? '' : ' no'}`}>{r.reversible ? 'Yes' : 'No'}</td></tr>))
              : <tr><td colSpan={5} className="muted">Run the plan to see one row per finding.</td></tr>}
              {plan && rows.length === 0 && <tr><td colSpan={5} className="muted">Nothing in the sample is detected under this policy.</td></tr>}</tbody></table></div>
        </div>
      </div>
      <div className="fb-legend">
        <div><b className="mono">fallback:fpe_domain_too_small</b><p>FF1 needs at least 10⁶ free values; smaller domains run as <span className="mono">det_token</span>, still reversible.</p></div>
        <div><b className="mono">fallback:fpe_disabled</b><p>Every <span className="mono">fpe_*</span> method runs as <span className="mono">det_token</span> while <span className="mono">DPS_FPE_ENABLED=false</span>.</p></div>
      </div>
    </section>
  );
}
