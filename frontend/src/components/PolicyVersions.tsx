import { DownloadButton } from './DownloadButton';
import type { Download } from '../lib/api';
import { relative, shortHash, utcDate } from '../lib/format';
import { diffLines } from '../lib/policyEdit';
import type { PolicyDiff, TenantPolicyDetail } from '../lib/types';

/** Version history (mockup 05): the draft and every immutable version; the selected card shows
 *  its diff against the previous version (or active → draft) and offers the version's YAML. */
export function PolicyVersions({ detail, selected, diff, now, onSelect, downloadYaml, canPublish, onPublish }: {
  detail: TenantPolicyDetail; selected: number | 'draft' | null; diff: PolicyDiff | null; now: Date; onSelect: (v: number | 'draft') => void;
  downloadYaml: (v: number) => Promise<Download>; canPublish: boolean; onPublish: () => void;
}): JSX.Element {
  const active = detail.active_version ?? 1;
  const next = Math.max(active, ...detail.versions.map((v) => v.version)) + 1;
  const pinned = detail.versions.filter((v) => v.version < active && v.jobs_pinned > 0);
  const lines = diff ? diffLines(diff.changes) : [];
  const sel = selected ?? (detail.draft ? 'draft' : active);
  const selVersion = sel === 'draft' ? null : detail.versions.find((v) => v.version === sel) ?? null;
  return (
    <section className="card" id="versions" aria-labelledby="ver-t">
      <div className="card-head"><div className="card-title" id="ver-t">Version history</div><span className="faint sm">Published versions are immutable. Jobs pin a version or follow the latest.</span></div>
      <div className="ver">
        <div className="vlist" role="group" aria-label="Versions">
          {detail.draft && (
            <button type="button" className="vitem" aria-pressed={sel === 'draft'} onClick={() => onSelect('draft')}>
              <div className="v-top"><span className="v-n">v{next}</span><span className="chip amber">draft</span></div>
              <div className="v-by">{detail.draft_by ?? '—'} · {relative(detail.draft_updated_at, now)} · {detail.draft_changes} {detail.draft_changes === 1 ? 'change' : 'changes'}</div>
              <div className="v-hash">hash on publish</div>
            </button>
          )}
          {detail.versions.map((v) => (
            <button key={v.version} type="button" className="vitem" aria-pressed={sel === v.version} onClick={() => onSelect(v.version)}>
              <div className="v-top"><span className="v-n">v{v.version}</span>{v.version === active ? <span className="chip emerald">active</span> : <span className="faint sm">{v.note === 'shipped preset' ? 'shipped preset' : `${v.jobs_pinned} ${v.jobs_pinned === 1 ? 'job' : 'jobs'} pinned`}</span>}</div>
              <div className="v-by">{v.published_by ?? 'DPS'} · {utcDate(v.published_at)}{v.change_ticket ? ` · ${v.change_ticket}` : ''}</div>
              <div className="v-hash">{shortHash(v.hash)}{v.version === active ? ` · ${v.jobs_pinned} jobs follow latest` : ''}</div>
            </button>
          ))}
          {pinned.map((v) => <div key={v.version} className="callout amber sm" style={{ marginTop: 12 }}>{v.jobs_pinned} {v.jobs_pinned === 1 ? 'job is' : 'jobs are'} pinned to v{v.version}. Re-run {v.jobs_pinned === 1 ? 'it' : 'them'} on v{active} to apply the current treatment.</div>)}
        </div>
        <div>
          <div className="diff-head">
            <div>
              <div className="t">{sel === 'draft' ? `v${active} → draft v${next} · not published` : selVersion && selVersion.version > 1 ? `v${selVersion.version - 1} → v${selVersion.version}` : `v${sel} · ${selVersion?.note ?? 'first version'}`}</div>
              <div className="s">{sel === 'draft' ? `${detail.draft_changes} ${detail.draft_changes === 1 ? 'change' : 'changes'} · ${detail.draft_by ?? '—'}` : selVersion ? `${selVersion.note ?? 'no note'}${selVersion.change_ticket ? ` · ${selVersion.change_ticket}` : ''}` : ''}</div>
            </div>
            {sel === 'draft' ? (canPublish && <button className="btn btn-sm btn-primary" type="button" onClick={onPublish}>Publish v{next}</button>)
              : <DownloadButton label={`Download v${sel} YAML`} fetcher={() => downloadYaml(Number(sel))} />}
          </div>
          {diff ? (lines.length
            ? <div className="diff" role="region" aria-label={`Diff ${diff.from} to ${diff.to}`} tabIndex={0}>{lines.map((l, i) => <div key={i} className={l.kind === 'ctx' ? undefined : l.kind}>{l.text}</div>)}</div>
            : <p className="muted sm">No difference.</p>)
            : <p className="muted sm">{selVersion?.version === 1 ? 'Copied from the shipped preset. No earlier version to compare.' : 'Loading the diff…'}</p>}
        </div>
      </div>
    </section>
  );
}
