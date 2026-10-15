// Job detail (mockup 03) for a finished job whose connection was deleted since (S1-M4 console r1 B1):
// DELETE /connections/{id} keeps the jobs that used it, so the page names the engine from the job's
// snapshot and words the connection "deleted", and offers no review that would read it.
import { describe, expect, it } from 'vitest';
import { EngineBadge } from '../components/EngineBadge';
import { connectionLabel, jobTitle } from '../lib/jobs';
import type { ClassificationSnapshot, Connection, Job } from '../lib/types';
import { html, text } from '../test/render';
import { SnapshotsCard } from './JobDetail';

const at = '2026-10-15T10:00:00Z';
const discover: Job = {
  id: 'j_01M50433D1QPEPCB464WA6F48K', tenant_id: 't1', kind: 'discover', state: 'succeeded', phase: 'finalize', mode: null, group_id: null,
  connection_id: 'c_01M504CG7Z2Y3X4W5V6U7T8S9R', target_connection_id: null, object_ref: 'bank.customers', snapshot_id: null, spec: {},
  purpose: {}, kid: null, progress: { snapshot_ids: ['cs_01M5043SNAP0000000000000AA'] } as unknown as Job['progress'], plan: null,
  error_code: null, error: null, created_by: null, created_at: at, updated_at: at, started_at: at, finished_at: at, expires_at: null,
};
const snapshot = {
  id: 'cs_01M5043SNAP0000000000000AA', tenant_id: 't1', job_id: discover.id, connection_id: discover.connection_id, engine: 'postgres',
  object_ref: 'bank.customers', status: 'proposed', policy_name: 'gdpr-pseudonymise', policy_hash: 'h', columns: [], sample_rows: 500, overrides: [],
  content_hash: 'c', created_at: at, accepted_at: null, accepted_by: null,
} as unknown as ClassificationSnapshot;
const deleted = new Map<string, Connection | null>([[discover.connection_id!, null]]);

describe('a job whose connection was deleted', () => {
  it('heads the page with the engine its snapshot was read from, never a dash', () => {
    const out = html(<div><EngineBadge engine={snapshot.engine} /><h1>{jobTitle(discover, snapshot.engine)}</h1></div>);
    expect(text(out)).toContain('PostgreSQL · bank.customers');
    expect(text(out)).not.toContain('— · bank.customers');
    expect(jobTitle(discover, undefined)).toBe('— · bank.customers'); // what the page showed before
  });

  it('words the connection as deleted, with its short id, once', () => {
    const head = html(<p>Discovery · {connectionLabel(discover.connection_id, deleted)}</p>);
    expect(text(head)).toBe('Discovery · connection deleted (c_01M504…9R)');
    expect(connectionLabel(discover.connection_id, deleted)).toBe('connection deleted (c_01M504…9R)');
    const live = new Map<string, Connection | null>([[discover.connection_id!, { name: 'bank-pg-ro' } as Connection]]);
    expect(connectionLabel(discover.connection_id, live)).toBe('bank-pg-ro');
    expect(connectionLabel(discover.connection_id, new Map())).toBe('c_01M504…9R'); // not resolved yet
  });

  it('offers no "Review and accept" for a snapshot whose connection is gone, and says why', () => {
    const out = html(<SnapshotsCard snapshots={[snapshot]} connections={deleted} />);
    expect(out).not.toContain('Review and accept');
    expect(out).not.toContain('jobs/new?snapshot=');
    expect(text(out)).toContain('Its connection was deleted (c_01M504…9R): start a new discovery to review this table.');
    const live = html(<SnapshotsCard snapshots={[snapshot]} connections={new Map([[snapshot.connection_id, { name: 'pg' } as Connection]])} />);
    expect(live).toContain('Review and accept');
  });
});
