import { Link } from 'react-router-dom';
import type { CheckError, GroupMember, GroupView } from '../../hooks/useJobGroup';
import { memberFields, targetOf } from '../../lib/groups';
import { engineBadge, engineName, fmtCount, isTerminal, jobTargetText, progressPct, shortId } from '../../lib/jobs';
import { ConsistencyVerdict } from './ConsistencyVerdict';
import { CoverageTable } from './CoverageTable';

function KeyIcon(): JSX.Element {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true"><circle cx="8" cy="15" r="4" /><path d="M11 12l9-9M17 6l3 3M15 8l2 2" /></svg>;
}

/** "running · 72 %" · "completed" · "failed" … for a member card. */
function memberState(m: GroupMember): { text: string; tone: string } {
  const j = m.job;
  if (j.state === 'succeeded') return j.progress.outcome === 'dry_run' ? { text: 'dry run', tone: 'violet' } : { text: 'completed', tone: 'emerald' };
  if (j.state === 'failed' || j.state === 'dead') return { text: 'failed', tone: 'rose' };
  if (j.state === 'canceled') return { text: 'canceled', tone: 'slate' };
  if (j.state === 'paused') return { text: 'paused', tone: 'amber' };
  if (j.state === 'queued') return { text: 'queued', tone: 'slate' };
  const pct = progressPct(j);
  return { text: `running${pct === null ? '' : ` · ${pct} %`}`, tone: 'teal' };
}

function SystemCard({ m, shared }: { m: GroupMember; shared: string[] }): JSX.Element {
  const st = memberState(m);
  const engine = m.connection?.engine ?? m.target?.engine;
  const unit = engine === 'mongo' ? 'documents' : 'rows';
  const target = targetOf(m.job).object;
  const wm = m.job.spec?.target?.write_mode as string | undefined;
  const rows = m.job.progress.rows_written;
  const fields = memberFields(m.snapshot, shared);
  return (
    <div className="sys" data-testid="group-member">
      <div className="hd"><span className="rowx"><span className="eb">{engineBadge(engine)}</span><b>{engineName(engine)} · {m.connection?.name ?? shortId(m.job.connection_id)}</b></span><span className={`chip ${st.tone}`}>{st.text}</span></div>
      <div className="obj">{m.job.object_ref ?? '—'}</div>
      <div className="to">{wm === 'in_place' || wm === 'append' ? jobTargetText(m.job) : `→ ${target}`}</div>
      <div className="faint sm"><Link to={`/data-sources/jobs/${m.job.id}`} className="mono" title={m.job.id}>{shortId(m.job.id)}</Link> · {isTerminal(m.job) || m.job.progress.rows_estimated === null
        ? `${fmtCount(rows)} ${unit}` : `${fmtCount(rows)} of ${fmtCount(m.job.progress.rows_estimated)} ${unit}`}</div>
      {fields.length > 0 ? <div className="flds">{fields.map((f) => <span key={f} className="fl">{f}</span>)}</div>
        : <div className="faint sm">{m.snapshot ? 'No column shared with another member' : 'Reading the snapshot…'}</div>}
    </div>
  );
}

/**
 * One job group as mockup 07 draws it: header with the purpose, Add a job and Run consistency check;
 * the key node (the group key's id only); wires (exactly three members); one card per member; the
 * verdict and the join coverage.
 */
export function JobGroupCard({ view, checking, checkError, onCheck }: {
  view: GroupView; checking: boolean; checkError: CheckError | null; onCheck: () => void;
}): JSX.Element {
  const { group, members, shared } = view;
  const first = members[members.length - 1]?.job;   // members are newest first; the purpose of the first run
  const purpose = first?.purpose?.ticket ? `Purpose ${first.purpose.ticket}${first.purpose.legal_basis ? ` · ${first.purpose.legal_basis}` : ''}.` : '';
  const systems = members.length === 1 ? 'One system' : `${members.length === 2 ? 'Two' : members.length === 3 ? 'Three' : members.length} systems`;
  return (
    <div className="jgroup">
      <div className="ghead">
        <div style={{ flex: '1 1 520px' }}><h2 id="hg">Job group · <span className="mono">{group.name}</span></h2>
          <p>{members.length ? `${systems} protected with one consistency key, so the same customer gets the same token everywhere.` : 'No job has run in this group yet.'} {purpose}</p></div>
        <div className="rowx">
          <Link className="btn btn-sm" to={`/data-sources/jobs/new?group=${encodeURIComponent(group.id)}`}>Add a job to this group</Link>
          <button className="btn btn-sm btn-primary" type="button" disabled={checking || members.length < 2} onClick={onCheck}>{checking ? 'Checking…' : 'Run consistency check'}</button>
        </div>
      </div>
      {checkError && <div className="callout rose" style={{ fontSize: 12.5, marginBottom: 12 }} role="alert"><b>The check did not run.</b> {checkError.message}{checkError.code && <span className="mono faint"> · {checkError.code}</span>}</div>}

      <div className="keynode">
        <span className="ki"><KeyIcon /></span>
        <span><span className="kt">Key <span className="mono">{group.kid}</span></span><br /><span className="ks">Held by the requesting team since {new Date(group.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}. DPS keeps no copy.</span></span>
        {shared.length > 0 && <span className="chip teal">entity-scoped: {shared.join(' · ')}</span>}
      </div>
      {members.length === 3 && (
        <svg className="wires" viewBox="0 0 300 26" preserveAspectRatio="none" aria-hidden="true">
          <path d="M150 0 V13 M49 26 V13 H251 V26 M150 13 V26" fill="none" stroke="var(--teal-border)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        </svg>
      )}
      {members.length > 0 ? (
        <div className={`sys3${members.length === 3 ? '' : ' nowires'}`}>{members.map((m) => <SystemCard key={m.job.id} m={m} shared={shared} />)}</div>
      ) : (
        <div className="empty" style={{ marginTop: 12 }}><div className="t">No member yet</div><p>Add a job to this group: it runs under the group key, so its tokens join the other members'.</p></div>
      )}

      <div className="gsplit">
        <ConsistencyVerdict result={group.last_check ?? null} />
        <CoverageTable result={group.last_check ?? null} />
      </div>
    </div>
  );
}
