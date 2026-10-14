import { useEffect, useRef, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { useJobGroup } from '../../hooks/useJobGroup';
import type { Auth } from '../../lib/api';
import { pickGroup } from '../../lib/groups';
import type { Job, JobGroup } from '../../lib/types';
import { JobGroupCard } from './JobGroupCard';
import { NewGroupDialog } from './NewGroupDialog';

/**
 * The Jobs page's job-group card (mockup 07, `id="group"`): a group selector (`?group=<id>`, else
 * the newest group with members), New job group, and the selected group's card.
 */
export function JobGroupsSection({ auth, groups, jobs, onGroupsChanged }: {
  auth: Auth; groups: JobGroup[] | null; jobs: Pick<Job, 'group_id'>[]; onGroupsChanged: () => void;
}): JSX.Element {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [dialog, setDialog] = useState(false);
  const wanted = params.get('group');
  const selected = groups ? pickGroup(groups, jobs, wanted) : null;
  const { view, loading, error, checking, checkError, runCheck } = useJobGroup(auth, selected);
  const anchor = useRef<HTMLElement>(null);
  const scrolled = useRef(false);

  // /data-sources?group=<id>#group (Job detail's "Open consistency check"): scroll once the card is drawn
  useEffect(() => {
    if (scrolled.current || location.hash !== '#group' || !view) return;
    scrolled.current = true;
    anchor.current?.scrollIntoView({ block: 'start' });
  }, [location.hash, view]);

  function select(id: string): void {
    const next = new URLSearchParams(params);
    if (id) next.set('group', id); else next.delete('group');
    setParams(next, { replace: true });
  }

  return (
    <section className="card groups" id="group" ref={anchor} aria-labelledby={view ? 'hg' : 'hgs'}>
      <div className="gbar">
        <div className="card-title" id="hgs">Job groups</div>
        <div className="rowx">
          {groups && groups.length > 0 && (
            <div className="sel"><label className="sr" htmlFor="group-sel">Job group</label>
              <select className="input" id="group-sel" value={selected ?? ''} onChange={(e) => select(e.target.value)}>
                {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select></div>
          )}
          <button className="btn btn-sm" type="button" onClick={() => setDialog(true)}>New job group</button>
        </div>
      </div>
      {groups && groups.length === 0 && (
        <div className="empty"><div className="t">No job group yet</div><p>A job group protects one customer the same way across PostgreSQL, Oracle and MongoDB with one key that your team holds.</p></div>
      )}
      {error && <div className="err">Could not load the job group: {error}</div>}
      {loading && !view && <div className="skeleton" />}
      {view && <JobGroupCard view={view} checking={checking} checkError={checkError} onCheck={() => void runCheck()} />}
      {dialog && <NewGroupDialog auth={auth} onClose={(g) => { setDialog(false); if (g) { onGroupsChanged(); select(g.id); } }} />}
    </section>
  );
}
