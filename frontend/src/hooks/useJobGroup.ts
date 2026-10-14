import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, type Auth } from '../lib/api';
import { checkErrorText, sharedEntities } from '../lib/groups';
import { isTerminal } from '../lib/jobs';
import type { ClassificationSnapshot, Connection, Job, JobGroup } from '../lib/types';

export interface GroupMember { job: Job; connection: Connection | null; target: Connection | null; snapshot: ClassificationSnapshot | null }
export interface GroupView { group: JobGroup; members: GroupMember[]; shared: string[] }
export interface CheckError { message: string; code?: string }

const POLL_MS = 2000;

/** The run jobs of a group, newest first (one page of 200: the group route's own bound). */
async function runJobs(auth: Auth, groupId: string): Promise<Job[]> {
  return (await api.jobs.list(auth, { group_id: groupId, kind: 'run', limit: 200 })).items;
}

/**
 * A job group with its member runs, their source and target connections and accepted snapshots
 * (each id read once, in parallel), and the consistency check. Member jobs are re-read every 2 s
 * while any is not terminal (contract §14); the interval stops on unmount or when all are terminal.
 * `runCheck` posts `{sample: 500}` and stores the answer as the group's `last_check` in place.
 */
export function useJobGroup(auth: Auth, groupId: string | null): {
  view: GroupView | null; loading: boolean; error: string | null; checking: boolean; checkError: CheckError | null;
  reload: () => void; runCheck: () => Promise<void>;
} {
  const [view, setView] = useState<GroupView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<CheckError | null>(null);
  const [generation, setGeneration] = useState(0);
  const conns = useRef(new Map<string, Promise<Connection | null>>());
  const snaps = useRef(new Map<string, Promise<ClassificationSnapshot | null>>());

  const connection = useCallback((id: string | null | undefined): Promise<Connection | null> => {
    if (!id) return Promise.resolve(null);
    if (!conns.current.has(id)) conns.current.set(id, api.connections.get(auth, id).catch(() => null));
    return conns.current.get(id)!;
  }, [auth]);
  const snapshot = useCallback((id: string | null | undefined): Promise<ClassificationSnapshot | null> => {
    if (!id) return Promise.resolve(null);
    if (!snaps.current.has(id)) snaps.current.set(id, api.snapshots.get(auth, id).catch(() => null));
    return snaps.current.get(id)!;
  }, [auth]);

  const assemble = useCallback(async (group: JobGroup, jobs: Job[]): Promise<GroupView> => {
    const members = await Promise.all(jobs.map(async (job) => {
      const targetId = (job.spec?.target?.connection_id as string | undefined) ?? job.target_connection_id;
      const [source, target, snap] = await Promise.all([connection(job.connection_id), connection(targetId), snapshot(job.snapshot_id)]);
      return { job, connection: source, target: target ?? source, snapshot: snap };
    }));
    return { group, members, shared: sharedEntities(members.map((m) => m.snapshot)) };
  }, [connection, snapshot]);

  // the group, its members and their connections and snapshots
  useEffect(() => {
    setView(null); setError(null); setCheckError(null);
    if (!groupId) { setLoading(false); return; }
    let live = true;
    setLoading(true);
    (async () => {
      try {
        const [{ group }, jobs] = await Promise.all([api.groups.get(auth, groupId), runJobs(auth, groupId)]);
        const next = await assemble(group, jobs);
        if (live) setView(next);
      } catch (e) {
        if (live) setError(e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : String(e));
      } finally { if (live) setLoading(false); }
    })();
    return () => { live = false; };
  }, [auth, groupId, generation, assemble]);

  // re-read member jobs every 2 s while one of them can still change
  const latest = useRef<GroupView | null>(null);
  latest.current = view;
  const active = !!view && view.members.some((m) => !isTerminal(m.job));
  useEffect(() => {
    if (!active || !groupId) return undefined;
    let live = true;
    const timer = window.setInterval(() => {
      runJobs(auth, groupId)
        .then(async (jobs) => {
          const prev = latest.current;
          if (!live || !prev || prev.group.id !== groupId) return;
          const next = await assemble(prev.group, jobs);
          if (live) setView((v) => (v && v.group.id === groupId ? { ...next, group: v.group } : v));
        })
        .catch(() => undefined);   // a failed poll keeps the last view; the next tick retries
    }, POLL_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, [auth, groupId, active, assemble]);

  const reload = useCallback(() => { conns.current.clear(); snaps.current.clear(); setGeneration((g) => g + 1); }, []);

  const runCheck = useCallback(async (): Promise<void> => {
    if (!groupId) return;
    setChecking(true); setCheckError(null);
    try {
      const result = await api.groups.consistencyCheck(auth, groupId, { sample: 500 });
      setView((v) => (v && v.group.id === groupId ? { ...v, group: { ...v.group, last_check: result, last_check_at: result.checked_at } } : v));
    } catch (e) {
      setCheckError(e instanceof ApiError ? { message: checkErrorText(e.code, e.message), code: e.code } : { message: String(e) });
    } finally { setChecking(false); }
  }, [auth, groupId]);

  return { view, loading, error, checking, checkError, reload, runCheck };
}
