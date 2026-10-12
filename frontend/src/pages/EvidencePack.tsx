import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api, ApiError } from '../lib/api';
import {
  engineName, errorText, fmtCount, fmtDuration, fmtUtc, methodText, packTargetText, shortId, shortSha, shortType, typeNoteShort,
  writeModeLabel, verificationText,
} from '../lib/jobs';
import type { ClassificationSnapshot, EvidenceOut, Job } from '../lib/types';

const LABEL_OBFUSCATE = 'pseudonymised — still personal data';

function apiMessage(e: unknown): string {
  return e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : e instanceof Error ? e.message : String(e);
}

const text = (v: unknown): string => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));

function Sec({ n, title, right, keep, children }: { n: number; title: string; right?: React.ReactNode; keep?: boolean; children: React.ReactNode }): JSX.Element {
  return (
    <section className={`sec${keep ? ' keep' : ''}`} aria-labelledby={`s${n}`}>
      <h2 id={`s${n}`}><span className="n">{n}</span> {title}{right && <span className="r">{right}</span>}</h2>
      {children}
    </section>
  );
}

/** The evidence pack (mockup 10): the sealed, value-free record of one job, printable as the PDF look. */
export function EvidencePack(): JSX.Element {
  const { evidenceId = '' } = useParams();
  const auth = useAuth();
  const [ev, setEv] = useState<EvidenceOut | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [snapshot, setSnapshot] = useState<ClassificationSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setEv(null); setError(null);
    api.evidence.get(auth, evidenceId).then(async (out) => {
      if (!live) return;
      setEv(out);
      api.jobs.get(auth, out.job_id).then((j) => { if (live) setJob(j); }).catch(() => undefined);
      const sid = out.pack.snapshot?.id as string | undefined;
      if (sid) api.snapshots.get(auth, sid).then((s) => { if (live) setSnapshot(s); }).catch(() => undefined);
    }).catch((e) => { if (live) setError(apiMessage(e)); });
    return () => { live = false; };
  }, [auth, evidenceId]);

  async function download(kind: 'evidence_json' | 'evidence_html'): Promise<void> {
    const art = ev?.artefacts.find((a) => a.kind === kind);
    if (!ev || !art) { setError(`The pack has no ${kind === 'evidence_json' ? 'JSON' : 'HTML'} artefact.`); return; }
    setBusy(kind);
    try { await api.artefacts.download(auth, art.id, `dps-evidence-${ev.pack.id}.${kind === 'evidence_json' ? 'json' : 'html'}`); }
    catch (e) { setError(apiMessage(e)); } finally { setBusy(null); }
  }

  if (!ev) {
    return (
      <main className="page evidence-page">
        <div className="faint sm rowx no-print"><Link to="/reports" style={{ color: 'var(--ink-2)', fontWeight: 600 }}>Reports &amp; audit</Link><span>/</span><span>Evidence pack</span></div>
        {error ? <div className="err">Could not load the evidence pack: {error}</div> : <div className="sheet"><div className="skeleton" /></div>}
      </main>
    );
  }

  const p = ev.pack;
  const v = p.verification ?? {};
  const hits = Number(v.hits ?? 0);
  const passed = !!v.passed;
  const src = p.source ?? {};
  const tgt = p.target ?? {};
  const files = (tgt.files ?? null) as Record<string, any> | null;
  const sel = (src.selection ?? {}) as Record<string, any>;
  const cons = p.consistency ?? {};
  const custody = (cons.key_custody ?? {}) as Record<string, any>;
  const synth = p.mode === 'synthesize';
  const targetText = packTargetText(tgt, src);
  const snapCols = snapshot?.columns ?? [];
  const byPath = new Map(snapCols.map((c) => [c.path, c]));
  const overridden = new Set((snapshot?.overrides ?? []).map((o) => o.path));
  const kept = snapCols.filter((c) => !p.columns.some((pc) => pc.path === c.path));
  const totalCols = snapCols.length || p.columns.length;
  const counts = p.counts ?? {};
  const parts = p.partitions ?? [];
  const retries = parts.reduce((a, x) => a + Math.max(0, Number(x.attempts ?? 1) - 1), 0);
  const resumed = parts.filter((x) => Number(x.resumed ?? 0) > 0);
  const post = (v.post_sample ?? null) as Record<string, any> | null;
  const fallbacks = (v.fallbacks ?? {}) as Record<string, number>;
  const fbTotal = Object.values(fallbacks).reduce((a, b) => a + Number(b), 0);
  const day = new Date(p.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
  const outcomeWords = p.outcome === 'succeeded' ? '' : p.outcome === 'dry_run' ? ' This was a dry run: nothing was written.' : ` The job ended ${p.outcome}.`;

  return (
    <main className="page evidence-page">
      <div className="between no-print">
        <div className="faint sm rowx"><Link to="/reports" style={{ color: 'var(--ink-2)', fontWeight: 600 }}>Reports &amp; audit</Link><span>/</span><span>Evidence pack <span className="mono">{shortId(p.job_id)}</span></span></div>
        <div className="toolbar">
          <Link className="btn btn-sm" to="/reports">Back to reports</Link>
          <button className="btn btn-sm" type="button" disabled={!!busy} onClick={() => void download('evidence_json')}>Download JSON</button>
          <button className="btn btn-sm" type="button" disabled={!!busy} onClick={() => void download('evidence_html')}>Open HTML</button>
          <button className="btn btn-sm btn-primary" type="button" onClick={() => window.print()}>Print or save as PDF</button>
        </div>
      </div>
      {error && <div className="err no-print">{error}</div>}

      <article className="sheet" aria-labelledby="doc-title">
        <header className="doc-head">
          <div style={{ minWidth: 0, flex: '1 1 420px' }}>
            <div className="eyebrow">Data Privacy Services · evidence pack</div>
            <h1 id="doc-title">{engineName(src.engine)} {text(src.object)} → {targetText}</h1>
            <div className="jid">Job {p.job_id} · {p.mode ?? p.job_kind} · purpose {p.purpose?.ticket ?? '—'}</div>
          </div>
          <div className="seal">
            {passed ? <span className="chip emerald">verified · {fmtCount(hits)} leaks</span> : <span className="chip rose">verification {verificationText(false, hits)}</span>}
            <span>Sealed {fmtUtc(p.created_at)}</span>
            <span>Pack <span className="mono">{p.id}</span> · sha256 <span className="mono">{shortSha(ev.pack_sha256)}</span></span>
            <span className={`chip ${synth ? 'violet' : 'teal'}`}>{p.label}</span>
          </div>
        </header>

        <div className="meta">
          <div><div className="l">Tenant</div><div className="v">{p.tenant_id}</div></div>
          <div><div className="l">Requested by</div><div className="v">API key <span className="mono">{p.requested_by ?? '—'}</span></div></div>
          <div><div className="l">Purpose</div><div className="v">{p.purpose?.ticket ?? '—'}</div></div>
          <div><div className="l">Legal basis</div><div className="v">{p.purpose?.legal_basis ?? '—'}</div></div>
          <div><div className="l">Job group</div><div className="v">{p.group_id ? <span className="mono">{p.group_id}</span> : 'none'}</div></div>
          <div><div className="l">Mode · write</div><div className="v">{p.mode ?? '—'} · {writeModeLabel(tgt.write_mode, tgt.table_load)}</div></div>
          <div><div className="l">Outcome</div><div className="v">{p.outcome}</div></div>
          <div><div className="l">Pack kept until</div><div className="v">{job?.expires_at ? fmtUtc(job.expires_at, false) : 'the tenant retention period'}</div></div>
        </div>

        <Sec n={1} title="Summary">
          <p className="lead">
            On {day} DPS read {fmtCount(counts.rows_read ?? 0)} rows from {engineName(src.engine)} <b>{text(src.object)}</b>, {synth ? 'replaced' : 'protected'} the personal data in {p.columns.length} of its {totalCols} columns under policy <b>{p.policy?.name || '—'}</b>, and wrote {fmtCount(counts.rows_written ?? 0)} rows to <b>{targetText}</b>{p.purpose?.ticket ? ` for change ${p.purpose.ticket}` : ''}.{' '}
            {passed ? 'The leakage sweep found no source value in the output.' : hits > 0 ? `The leakage sweep caught ${fmtCount(hits)} source ${hits === 1 ? 'value' : 'values'}; the affected batches were not written.` : 'The run failed closed before its verification counters held a hit; nothing of the failed unit was written.'}{' '}
            {synth ? 'The synthetic output has no key; DPS kept no seed.' : custody.returned ? 'The key that reverses the output was returned to the requesting team; DPS kept no copy.' : 'The run used the caller\'s key; DPS kept no copy.'}{outcomeWords}
          </p>
          <div className="stats">
            <div><div className="l">Rows written</div><div className="v">{fmtCount(counts.rows_written ?? 0)}</div><div className="s">{counts.rows_written === counts.rows_read ? '= rows read' : `of ${fmtCount(counts.rows_read ?? 0)} read`}</div></div>
            <div><div className="l">Columns transformed</div><div className="v">{p.columns.length} of {totalCols}</div><div className="s">{totalCols - p.columns.length} kept, not personal</div></div>
            <div><div className="l">Values transformed</div><div className="v">{fmtCount(counts.values_transformed ?? 0)}</div><div className="s">nulls skipped</div></div>
            <div><div className="l">Leakage sweep</div><div className={`v ${passed ? 'ok' : 'bad'}`}>{fmtCount(hits)} hits</div><div className="s">{fmtCount(Number(v.values_checked ?? 0))} values checked</div></div>
            <div><div className="l">Duration</div><div className="v">{fmtDuration(Number(p.timings?.duration_s ?? 0))}</div><div className="s">{parts.length} {parts.length === 1 ? 'partition' : 'partitions'} · {retries} {retries === 1 ? 'retry' : 'retries'}</div></div>
            <div><div className="l">Raw values to LLM</div><div className="v ok">0</div><div className="s">no LLM call in this job</div></div>
          </div>
        </Sec>

        <Sec n={2} title="Configuration hashes" right="hashes identify exactly what ran">
          <dl className="kv">
            <dt>Policy</dt><dd><b>{p.policy?.name || '—'}</b>{snapshot ? ` + ${snapshot.overrides.length} ${snapshot.overrides.length === 1 ? 'override' : 'overrides'} from the reviewed snapshot` : ''}<span className="s2">Policy hash <span className="mono">sha256:{p.policy?.hash || '—'}</span></span></dd>
            <dt>Snapshot</dt><dd><span className="mono">{text(p.snapshot?.id)}</span> · accepted by {text(p.snapshot?.accepted_by)}{p.snapshot?.accepted_at ? ` at ${fmtUtc(p.snapshot.accepted_at, false)}` : ''}<span className="s2">Content hash <span className="mono">sha256:{text(p.snapshot?.content_hash)}</span></span></dd>
            <dt>Configuration hash</dt><dd><span className="mono">sha256:{p.config_hash}</span><span className="s2">policy + reviewed snapshot + job specification, canonical JSON</span></dd>
            <dt>Engine</dt><dd>DPS engine {p.engine_version} · image <span className="mono">{p.image_digest || 'unset (local build)'}</span><span className="s2">{src.server_version ? `Source server ${src.server_version}` : ''}</span></dd>
            <dt>{synth ? 'Seed' : 'Key id'}</dt><dd>{synth ? <>seed fingerprint <span className="mono">{text(cons.seed_fingerprint)}</span></> : <>kid <span className="mono">{text(cons.kid)}</span></>} · consistency scope <b>{text(cons.scope)}</b>
              <span className="s2">{custody.returned ? 'Data key returned at job creation' : 'Key supplied by the caller'}{custody.wrapped ? ', wrapped with the caller\'s public key' : ''}; DPS stored no key. This pack holds the key id only.</span></dd>
            <dt>Source</dt><dd>{engineName(src.engine)} · connection <span className="mono">{text(src.connection_name)}</span> · <span className="mono">{text(src.object)}</span>
              <span className="s2">Selection <span className="mono">{sel.where_template || sel.query_template || 'all rows'}</span> · {fmtCount(Number(sel.binds ?? 0))} bound {Number(sel.binds ?? 0) === 1 ? 'parameter' : 'parameters'}{sel.max_rows ? ` · at most ${fmtCount(sel.max_rows)} rows` : ''}{Array.isArray(sel.columns) ? ` · ${sel.columns.length} columns` : ''}</span></dd>
            <dt>Target</dt><dd>{engineName(tgt.engine)} · connection <span className="mono">{text(tgt.connection_name)}</span> · <span className="mono">{targetText}</span>
              <span className="s2">write_mode {text(tgt.write_mode)}{tgt.table_load ? ` · ${tgt.table_load}` : ''}{tgt.created ? ' · created from the source definition' : ''}{tgt.truncated ? ' · truncated before loading' : ''}{tgt.pk_strategy ? ` · keys ${text((tgt.pk_strategy as Record<string, unknown>).kind)}` : ''}{tgt.confirm_write ? ` · write confirmed for ${fmtCount(tgt.expected_rows)} rows` : ''}{files ? ` · ${fmtCount(files.objects)} objects, manifest ${text(files.manifest_artefact_id)}` : ''}</span></dd>
          </dl>
        </Sec>

        <Sec n={3} title="Classification snapshot" right={<><span className="mono">{shortId(p.snapshot?.id)}</span>{p.snapshot?.accepted_at ? ` · accepted ${fmtUtc(p.snapshot.accepted_at, false)}` : ''}</>}>
          <div className="tbl-scroll"><table className="tbl doc stack snap c3"><thead><tr><th>Column</th><th>Type</th><th>Inferred entity</th><th className="num">Conf.</th><th>Method</th><th>Decision</th><th className="num">Values</th></tr></thead><tbody>
            {p.columns.map((c) => {
              const s = byPath.get(String(c.path));
              return (
                <tr key={String(c.path)}>
                  <td className="m" data-l="Column">{s?.name ?? String(c.path).replace(/^\$\./, '')}</td>
                  <td className="faint" data-l="Type">{s ? shortType(s.data_type) : '—'}</td>
                  <td data-l="Entity">{text(c.entity_type)}{c.text_column && c.entity_type !== 'FREE_TEXT' ? ' · free text' : ''}</td>
                  <td className="num m" data-l="Conf.">{s?.entity_type ? s.confidence.toFixed(2) : '—'}</td>
                  <td data-l="Method"><span className="m">{methodText({ method: String(c.method), params: (c.params ?? {}) as Record<string, unknown> })}</span>{!c.reversible && <span className="prm"> · one-way</span>}</td>
                  <td className="wide" data-l="Decision">{overridden.has(String(c.path)) ? <><b>overridden</b><span className="s2">{typeNoteShort(s?.type_note) ?? 'changed at review'}</span></> : 'accepted'}</td>
                  <td className="num m" data-l="Values">{fmtCount(c.values as number | null)}</td>
                </tr>
              );
            })}
            {!p.columns.length && <tr><td colSpan={7} className="faint">No column was planned.</td></tr>}
          </tbody></table></div>
          <p className="note">{kept.length ? `${kept.length} ${kept.length === 1 ? 'column' : 'columns'} kept as not personal: ${kept.map((c) => c.name).join(', ')}. ` : ''}{snapshot ? `Discovery sampled ${fmtCount(snapshot.sample_rows)} rows; examples shown to the reviewer were masked.` : 'Examples shown to the reviewer were masked.'}</p>
        </Sec>

        <Sec n={4} title="Execution" right={`${parts.length} ${parts.length === 1 ? 'partition' : 'partitions'}`}>
          <div className="timeline">
            <div><div className="t">{fmtUtc(p.timings?.started_at, true).replace(/^.*, /, '')}</div>Job started</div>
            <div className={resumed.length ? 'warn' : ''}><div className="t">{parts.length} {parts.length === 1 ? 'partition' : 'partitions'}</div>{resumed.length ? `${resumed.map((x) => `p${x.no}`).join(', ')} resumed from the last committed batch` : 'No lease was lost'}</div>
            <div><div className="t">{fmtUtc(p.timings?.finished_at, true).replace(/^.*, /, '')}</div>Last batch committed</div>
            <div className={passed ? 'ok' : 'bad'}><div className="t">{fmtCount(Number(v.values_checked ?? 0))} values</div>Verification {passed ? 'passed' : 'failed closed'}</div>
            <div className="ok"><div className="t">{fmtUtc(p.created_at, true).replace(/^.*, /, '')}</div>Pack sealed and hashed</div>
          </div>
          <div className="tbl-scroll"><table className="tbl doc stack c3" style={{ marginTop: 12 }}><thead><tr><th>Partition</th><th className="num">Rows</th><th className="num">Attempts</th><th className="num">Resumed</th><th>Result</th></tr></thead><tbody>
            {parts.map((x) => (
              <tr key={String(x.no)}><td className="m" data-l="Partition">p{x.no}</td><td className="num m" data-l="Rows">{fmtCount(x.rows)}</td><td className="num m" data-l="Attempts">{text(x.attempts)}</td><td className="num m" data-l="Resumed">{text(x.resumed)}</td>
                <td className="wide" data-l="Result">{x.state === 'done' ? 'committed' : text(x.state)}{Number(x.resumed ?? 0) > 0 && <span className="s2">worker lost its lease; resumed from the last committed batch, no row written twice</span>}</td></tr>
            ))}
            <tr style={{ fontWeight: 700 }}><td data-l="Partition">Total</td><td className="num m" data-l="Rows">{fmtCount(counts.rows_written ?? 0)}</td><td className="num m" data-l="Attempts">{parts.reduce((a, x) => a + Number(x.attempts ?? 0), 0)}</td><td className="num m" data-l="Resumed">{fmtCount(counts.resumed_partitions ?? 0)}</td>
              <td className="wide" data-l="Result">wall clock {fmtDuration(Number(p.timings?.duration_s ?? 0))}{p.timings?.rows_per_s ? ` · ${fmtCount(p.timings.rows_per_s)} rows/s` : ''} · {fmtCount(counts.batches ?? 0)} batches</td></tr>
          </tbody></table></div>
          <p className="note">Checkpoint positions stay in DPS's job tables and are not printed here, because a key position is a customer identifier.</p>
          {p.errors.length > 0 && (
            <div className="tbl-scroll"><table className="tbl doc" style={{ marginTop: 10 }}><thead><tr><th>Error</th><th>Where</th><th>Meaning</th></tr></thead><tbody>
              {p.errors.map((e, i) => <tr key={i}><td className="m">{e.code}</td><td>{e.where}</td><td>{errorText(e.code)}</td></tr>)}
            </tbody></table></div>
          )}
        </Sec>

        <Sec n={5} title="Verification" right={`mode ${text(v.mode)}`} keep>
          <div className="tbl-scroll"><table className="tbl doc stack"><thead><tr><th>Check</th><th>Result</th><th>What was checked</th><th>Status</th></tr></thead><tbody>
            <tr><td data-l="Check"><b>Leakage sweep</b></td><td className="m" data-l="Result">{fmtCount(hits)} hits</td><td className="wide" data-l="What">Every transformed value compared with the source values of its row before the batch was written ({fmtCount(Number(v.values_checked ?? 0))} values in {fmtCount(Number(v.batches ?? 0))} batches)</td><td data-l="Status"><span className={`chip ${passed ? 'emerald' : 'rose'}`}>{passed ? 'pass' : 'fail'}</span></td></tr>
            <tr><td data-l="Check"><b>Post-write sample</b></td><td className="m" data-l="Result">{post && post.rows !== undefined ? `${fmtCount(post.kept_column_hits ?? 0)} in kept columns · ${fmtCount(post.detector_hits ?? 0)} in transformed columns` : 'not run'}</td><td className="wide" data-l="What">{post && post.rows !== undefined ? `${fmtCount(post.rows)} written rows read back and rescanned with the detectors. Findings in transformed columns are expected (format-preserving tokens still look like their type); a finding in a kept column deserves a look.` : 'No written rows to sample'}</td><td data-l="Status"><span className={`chip ${!post || post.rows === undefined ? 'slate' : Number(post.kept_column_hits ?? 0) === 0 ? 'emerald' : 'amber'}`}>{!post || post.rows === undefined ? 'skipped' : Number(post.kept_column_hits ?? 0) === 0 ? 'pass' : 'review'}</span></td></tr>
            <tr><td data-l="Check"><b>Row counts</b></td><td className="m" data-l="Result">{fmtCount(counts.rows_read ?? 0)} read · {fmtCount(counts.rows_written ?? 0)} written</td><td className="wide" data-l="What">{fmtCount(counts.rows_skipped ?? 0)} rows skipped{counts.rows_estimated ? ` · ${fmtCount(counts.rows_estimated)} estimated at prepare` : ''}</td><td data-l="Status"><span className={`chip ${counts.rows_read === counts.rows_written + (counts.rows_skipped ?? 0) ? 'emerald' : 'amber'}`}>{counts.rows_read === counts.rows_written + (counts.rows_skipped ?? 0) ? 'pass' : 'review'}</span></td></tr>
            <tr><td data-l="Check"><b>FPE fallbacks</b></td><td className="m" data-l="Result">{fmtCount(fbTotal)} values</td><td className="wide" data-l="What">{fbTotal ? Object.entries(fallbacks).map(([k, n]) => `${fmtCount(n)} × ${k.replace(/^(fallback:)+/, '')}`).join(' · ') : 'Every value ran its planned method'}</td><td data-l="Status"><span className="chip slate">recorded</span></td></tr>
            {v.write_ledger_cleared !== undefined && <tr><td data-l="Check"><b>Write ledger</b></td><td className="m" data-l="Result">{v.write_ledger_cleared ? 'cleared' : 'kept'}</td><td className="wide" data-l="What">The per-batch marks DPS keeps in the target to resume without writing twice</td><td data-l="Status"><span className="chip slate">recorded</span></td></tr>}
          </tbody></table></div>
        </Sec>

        {(p.fidelity || p.privacy) && (
          <Sec n={6} title="Synthetic fidelity and privacy">
            <dl className="kv"><dt>Fidelity</dt><dd className="mono">{text(p.fidelity)}</dd><dt>Privacy</dt><dd className="mono">{text(p.privacy)}</dd></dl>
          </Sec>
        )}

        <Sec n={7} title="Regulatory note" keep>
          <div className="reg">
            {p.label === LABEL_OBFUSCATE || !synth
              ? <div className="callout"><b>The output is pseudonymised, and pseudonymised data is still personal data</b> under GDPR Art. 4(5) and India&apos;s Digital Personal Data Protection Act 2023. Hold it under the same controls as the source. Whoever holds key <span className="mono">{text(cons.kid)}</span> can re-identify every row.</div>
              : <div className="callout violet"><b>The output is synthetic — measured, not claimed anonymous.</b> Treat it as confidential until your data protection officer decides otherwise.</div>}
            <p className="note" style={{ margin: '2px 0 0' }}>DPS stores no customer values and no keys. This pack holds counts, hashes, column names, method names and key ids only. Its hash is written to the append-only audit log, so any later edit to the pack is detectable.</p>
          </div>
        </Sec>

        <Sec n={8} title="Signatures">
          <div className="sig">
            <div><div className="role">System attestation</div><div className="who">Data Privacy Services {p.engine_version}</div><div className="what">Generated and hashed at {fmtUtc(p.created_at)}.</div><div className="line"><span className="mono">sha256:{ev.pack_sha256}</span></div></div>
            <div><div className="role">Data owner</div><div className="who">{text(p.snapshot?.accepted_by)}</div><div className="what">Accepted the classification snapshot{p.snapshot?.accepted_at ? ` at ${fmtUtc(p.snapshot.accepted_at, false)}` : ''}.</div><div className="line"><span>Signature</span><span>Date</span></div></div>
            <div><div className="role">Data protection officer</div><div className="who">&nbsp;</div><div className="what">Confirms purpose, legal basis and retention.</div><div className="line"><span>Signature</span><span>Date</span></div></div>
            <div><div className="role">Change approver</div><div className="who">&nbsp;</div><div className="what">Approves the release of the output under {p.purpose?.ticket ?? 'the change'}.</div><div className="line"><span>Signature</span><span>Date</span></div></div>
          </div>
        </Sec>

        <footer className="doc-foot">
          <span>DPS evidence pack · job {p.job_id} · generated {fmtUtc(p.created_at)}</span>
          <span>Pack sha256 <span className="mono">{ev.pack_sha256}</span></span>
        </footer>
      </article>
    </main>
  );
}
