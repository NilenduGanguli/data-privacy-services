import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { KeyMaterialCard } from '../../components/KeyMaterialCard';
import { shortId, writeModeLabel } from '../../lib/jobs';
import type { RunOut, WriteMode } from '../../lib/types';

/**
 * Step 5 once Run job has created a job: the queued callout, the once-only key or seed, and Start a new
 * job. `JobWizard` holds the answer (in memory only) and renders this in place of the steps while the
 * draft is submitted, so nothing inside the wizard can unmount the key card or bring Run job back.
 * Start a new job waits for "I have stored the key"; closing or reloading the tab asks first.
 */
export function RunResult({ jobId, out, writeMode, onNewJob }: {
  jobId: string; out: RunOut | null; writeMode: WriteMode | null; onNewJob: () => void;
}): JSX.Element {
  const once = !!(out?.key_material || out?.synthesis_seed);
  const what = out?.key_material ? 'key' : 'seed';
  const syn = (out?.job.mode ?? 'obfuscate') === 'synthesize';
  const [stored, setStored] = useState(false);
  const unsaved = once && !stored;
  const used = writeMode === 'in_place' || writeMode === 'append';

  useEffect(() => {
    if (!unsaved) return undefined;
    const warn = (e: BeforeUnloadEvent): void => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [unsaved]);

  const partitions = out?.job.progress.partitions_total ?? 0;
  return (
    <section className="panel" aria-labelledby="h5s">
      <div className="panel-head"><h2 id="h5s">5 · Review and run</h2>
        <p>The job is submitted. This draft is closed: a new job starts from an empty draft{used ? ` and asks for its own ${writeModeLabel(writeMode)} confirmation` : ''}.</p></div>
      <div className="callout emerald" style={{ fontSize: 13 }}><b>Job {shortId(jobId)} queued.</b> {partitions ? `${partitions} partitions are waiting.` : 'A worker prepares the run plan and its partitions.'}{' '}
        <Link to="/data-sources" style={{ color: 'var(--teal)', fontWeight: 700 }}>Open in Jobs</Link> · <Link to={`/data-sources/jobs/${jobId}`} style={{ color: 'var(--teal)', fontWeight: 700 }}>Open job</Link></div>
      {once
        ? <KeyMaterialCard keyMaterial={out?.key_material} seed={out?.synthesis_seed} jobId={jobId} onStoredChange={setStored} />
        : <div className="card"><p className="muted" style={{ margin: 0 }}>The job runs with the {syn ? 'seed' : 'key'} you loaded; DPS returned none and keeps none.</p></div>}
      <div className="wfoot">
        <span className="hint">{unsaved ? `Tick "I have stored the ${what}" to start a new job: DPS keeps no copy, and this page shows it only once.` : 'Starting a new job clears this page.'}</span>
        <button className="btn btn-primary" type="button" disabled={unsaved} onClick={onNewJob}>Start a new job</button>
      </div>
    </section>
  );
}
