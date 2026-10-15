import { Link } from 'react-router-dom';
import type { Download } from '../lib/api';
import { count, plural, utc, utcDay } from '../lib/format';
import { shortId } from '../lib/jobs';
import { LAYER_LABEL, rollupRoute, verificationChip, type EvidenceRow } from '../lib/reports';
import type { ApiRollup, EvidenceItem } from '../lib/types';
import { DownloadButton } from './DownloadButton';

export interface EvidenceDownloads {
  packJson: (item: EvidenceItem) => Promise<Download>;
  packPdf: (item: EvidenceItem) => Promise<Download>;
  rollup: (r: ApiRollup, format: 'json' | 'pdf') => Promise<Download>;
}

function packLine(item: EvidenceItem): string {
  const s = item.summary;
  const parts: string[] = [];
  if (s.ticket) parts.push(s.ticket);
  if (item.layer === 'ui') {
    // mockup 09: "38 pages · 12 forms · 20 personas · 0 submissions" (forms and personas from M4's scan block)
    parts.push(plural(s.pages ?? 0, 'page'));
    if (s.forms !== null && s.forms !== undefined) parts.push(plural(s.forms, 'form'));
    if (s.personas !== null && s.personas !== undefined) parts.push(plural(s.personas, 'persona'));
    parts.push(plural(s.submissions ?? 0, 'submission'));
  } else {
    if (s.write_mode) parts.push(s.write_mode);
    // a run commits per batch: one that stopped may have written rows before it (S2 console r3 B1)
    if (item.outcome === 'failed') {
      const written = Number(s.rows_written ?? 0);
      parts.push(written > 0 ? `stopped · ${count(written)} ${s.mode === 'synthesize' ? 'synthetic rows' : 'rows'} written before it` : 'stopped, nothing written');
    } else if (s.rows_written !== null && s.rows_written !== undefined) parts.push(`${count(s.rows_written)} ${s.mode === 'synthesize' ? 'synthetic rows' : 'rows'}`);
    if (s.fidelity_score !== null && s.fidelity_score !== undefined) parts.push(`fidelity ${s.fidelity_score.toFixed(2)}`);
  }
  if (item.outcome !== 'succeeded' && item.outcome !== 'failed') parts.push(item.outcome.replace('_', ' '));
  return parts.join(' · ');
}

function packWhat(item: EvidenceItem): string {
  const s = item.summary;
  if (item.layer === 'ui') return `UI scan of ${s.source ?? '—'}`;
  if (s.write_mode === 'in_place') return `${s.source ?? '—'}, in place`;
  return `${s.source ?? '—'} → ${s.target ?? '—'}`;
}

/** Evidence packs and daily API roll-ups (mockup 09), newest first, with JSON and PDF downloads. */
export function EvidenceTable({ rows, downloads, hasOlder, busy, onOlder, total }: {
  rows: EvidenceRow[]; downloads: EvidenceDownloads; hasOlder: boolean; busy: boolean; onOlder: () => void; total: number | null;
}): JSX.Element {
  return (
    <>
      <div className="tbl-scroll"><table className="tbl stack" id="packs"><thead><tr><th style={{ width: '34%' }}>Job or request</th><th>Layer</th><th>Mode</th><th>Policy</th><th>Verification</th><th>Created (UTC)</th><th>Download</th></tr></thead><tbody>
        {rows.map((row) => {
          const chip = verificationChip(row);
          if (row.kind === 'rollup') {
            const r = row.rollup;
            const label = `API roll-up for ${utcDay(r.day)}`;
            return (
              <tr key={`r-${r.day}-${r.action}`}>
                <td className="sub" data-l="Job or request"><span className="mono">{rollupRoute(r.action)}</span> · daily roll-up, {utcDay(r.day)}{row.open ? ' · open until 00:05 UTC' : ''}
                  <span className="s2">{plural(r.calls, 'call')} · {plural(r.findings, 'finding')} · {plural(r.values, 'value')}</span></td>
                <td data-l="Layer">API</td>
                <td data-l="Mode">{r.action === 'obfuscate' || r.action === 'synthesize' ? <span className={`chip ${r.action === 'synthesize' ? 'violet' : 'teal'}`}>{r.action}</span> : <span className="faint">{r.action}</span>}</td>
                <td className="faint" data-l="Policy">—</td>
                <td data-l="Verification"><span className={`chip ${chip.tone}`}>{chip.text}</span></td>
                <td className="t" data-l="Created (UTC)">{utc(row.created_at)}</td>
                <td data-l="Download"><div className="dl">
                  <DownloadButton label="JSON" ariaLabel={`Download ${label} as JSON`} fetcher={() => downloads.rollup(r, 'json')} />
                  <DownloadButton label="PDF" ariaLabel={`Download ${label} as PDF`} fetcher={() => downloads.rollup(r, 'pdf')} />
                </div></td>
              </tr>
            );
          }
          const item = row.item;
          const s = item.summary;
          const sid = shortId(item.job_id);
          return (
            <tr key={item.id}>
              <td className="sub" data-l="Job or request"><Link className="mono" to={`/reports/evidence/${encodeURIComponent(item.id)}`} title={`${item.job_id} · pack ${item.id}`}>{sid}</Link> · {packWhat(item)}
                <span className="s2">{packLine(item)}</span></td>
              <td data-l="Layer">{LAYER_LABEL[item.layer] ?? item.layer}</td>
              <td data-l="Mode">{s.mode ? <span className={`chip ${s.mode === 'synthesize' ? 'violet' : 'teal'}`}>{s.mode}</span> : '—'}</td>
              <td className="mono" data-l="Policy">{s.policy ? `${s.policy}${s.policy_version ? ` v${s.policy_version}` : ''}` : '—'}</td>
              <td data-l="Verification"><span className={`chip ${chip.tone}`}>{chip.text}</span></td>
              <td className="t" data-l="Created (UTC)">{utc(item.created_at)}</td>
              <td data-l="Download"><div className="dl">
                <DownloadButton label="JSON" ariaLabel={`Download evidence pack ${sid} as JSON`} fetcher={() => downloads.packJson(item)} />
                <DownloadButton label="PDF" ariaLabel={`Download evidence pack ${sid} as PDF`} fetcher={() => downloads.packPdf(item)} />
              </div></td>
            </tr>
          );
        })}
        {!rows.length && <tr className="empty-row"><td colSpan={7}>No evidence pack matches these filters. Clear the filters, or widen the date range to 30 days.</td></tr>}
      </tbody></table></div>
      <div className="card-foot"><span>Showing {count(rows.length)}{total !== null ? ` of ${count(total)}` : ''} packs and roll-ups, newest first. Times are UTC.</span>
        {hasOlder && <button className="btn btn-sm" type="button" disabled={busy} onClick={onOlder}>Show older packs</button>}</div>
    </>
  );
}
