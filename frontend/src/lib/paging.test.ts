// Jobs page and job detail loaders (S1-M4 console r1 B1, B2): the Jobs page lists every job of the
// window its head names, not the newest page as if it were all of them; a job's connections come
// from GET /connections, so a deleted one is worded, never fetched by id into a 404.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, type Auth } from './api';
import { filterJobs, jobsCountText, NO_FILTERS, STATUS_STATES, statusBucket } from './jobs';
import { loadJobsByState, loadJobsWindow, loadOlderJobs, mergeJobs, resolveConnections } from './paging';
import type { Connection, Job, Page } from './types';

const auth: Auth = { apiKey: 'dps_test_key_value', tenant: 't1' };
const NOW = Date.parse('2026-10-15T16:00:00Z');

function job(n: number, state: Job['state'], hoursAgo: number, kind: Job['kind'] = 'run'): Job {
  const at = new Date(NOW - hoursAgo * 3_600_000).toISOString();
  return {
    id: `j_${String(n).padStart(26, '0')}`, tenant_id: 't1', kind, state, phase: 'finalize', mode: 'obfuscate', group_id: null,
    connection_id: 'c_src', target_connection_id: null, object_ref: 'bank.customers', snapshot_id: null, spec: {}, purpose: {}, kid: null,
    progress: { snapshot_ids: [], rows_written: 0 } as unknown as Job['progress'], plan: null, error_code: state === 'failed' ? 'leakage' : null,
    error: null, created_by: null, created_at: at, updated_at: at, started_at: at, finished_at: at, expires_at: null,
  };
}

/** Page 1: the newest 200 jobs, all succeeded; page 2: older jobs of the same week, three failed. */
const PAGE1 = Array.from({ length: 200 }, (_, i) => job(1000 - i, 'succeeded', 1 + i * 0.01));
const PAGE2 = [job(700, 'failed', 30), job(699, 'succeeded', 31), job(698, 'dead', 40), job(697, 'failed', 50)];

function fetcher(pages: Record<string, Page<Job>>): { list: (p: { limit: number; cursor?: string; state?: string }) => Promise<Page<Job>>; calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    list: async (p) => {
      calls.push(`${p.state ?? ''}:${p.cursor ?? ''}`);
      return pages[`${p.state ?? ''}:${p.cursor ?? ''}`] ?? { items: [], next_cursor: null };
    },
  };
}

describe('loadJobsWindow', () => {
  it('reads every page of the 7 days: failed jobs on page 2 are listed and counted', async () => {
    const f = fetcher({ ':': { items: PAGE1, next_cursor: 'c2' }, ':c2': { items: PAGE2, next_cursor: null } });
    const w = await loadJobsWindow(f.list, { now: NOW });
    expect(f.calls).toEqual([':', ':c2']);
    expect(w.items).toHaveLength(204);
    expect(w.more).toBeNull();
    const proposed = new Set<string>();
    expect(w.items.filter((j) => statusBucket(j, proposed) === 'failed')).toHaveLength(3);
    const failed = filterJobs(w.items, { ...NO_FILTERS, status: 'failed' }, { proposed, engines: {} });
    expect(failed.map((j) => j.id)).toEqual([PAGE2[0].id, PAGE2[2].id, PAGE2[3].id]);
    expect(filterJobs(w.items, { ...NO_FILTERS, q: PAGE2[3].id.toLowerCase() }, { proposed, engines: {} })).toHaveLength(1);
    expect(jobsCountText(failed.length, w.items.length, w.more !== null)).toBe('Showing 3 of 204 jobs');
  });

  it('stops at the first page past the window and says the list is the newest jobs', async () => {
    const old = [job(600, 'failed', 24 * 9)];
    const f = fetcher({ ':': { items: PAGE1, next_cursor: 'c2' }, ':c2': { items: [...PAGE2, ...old], next_cursor: 'c3' } });
    const w = await loadJobsWindow(f.list, { now: NOW });
    expect(f.calls).toEqual([':', ':c2']);
    expect(w.items).toHaveLength(205);
    expect(w.more).toBe('c3');
    expect(jobsCountText(205, 205, true)).toBe('Showing 205 of the newest 205 jobs · counts are over these');
    const older = await loadOlderJobs(f.list, 'c3');
    expect(older).toEqual({ items: [], more: null });
  });

  it('stops at its page cap inside the window and leaves the next cursor', async () => {
    const f = fetcher({ ':': { items: PAGE1, next_cursor: 'c2' }, ':c2': { items: PAGE2, next_cursor: 'c3' } });
    const w = await loadJobsWindow(f.list, { now: NOW, maxPages: 1 });
    expect(w.items).toHaveLength(200);
    expect(w.more).toBe('c2');
  });

  it('asks the server for a status: every state of the bucket, page by page', async () => {
    const f = fetcher({
      'failed:': { items: [PAGE2[0]], next_cursor: 'f2' },
      'failed:f2': { items: [PAGE2[3]], next_cursor: null },
      'dead:': { items: [PAGE2[2]], next_cursor: null },
    });
    const got = await loadJobsByState(f.list, STATUS_STATES.failed);
    expect(f.calls).toEqual(['failed:', 'failed:f2', 'dead:']);
    expect(got.map((j) => j.id)).toEqual([PAGE2[0].id, PAGE2[2].id, PAGE2[3].id]);
  });

  it('merges lists by id, the first one winning, newest first', () => {
    const fresh = { ...PAGE2[0], state: 'canceled' as const };
    const merged = mergeJobs([fresh], PAGE2, PAGE1.slice(0, 2));
    expect(merged).toHaveLength(6);
    expect(merged[0].id).toBe(PAGE1[0].id);
    expect(merged.find((j) => j.id === fresh.id)?.state).toBe('canceled');
  });
});

describe('the Jobs page loader through the API', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('follows next_cursor with the documented query', async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url);
      return Response.json(url.includes('cursor=') ? { items: PAGE2, next_cursor: null } : { items: PAGE1, next_cursor: 'c2' });
    }));
    const w = await loadJobsWindow((p) => api.jobs.list(auth, p), { now: NOW });
    expect(urls).toEqual(['/api/v1/jobs?limit=200', '/api/v1/jobs?limit=200&cursor=c2']);
    expect(w.items).toHaveLength(204);
  });
});

describe('resolveConnections', () => {
  afterEach(() => vi.unstubAllGlobals());
  const conn = (id: string, name: string): Connection => ({ id, name, engine: 'oracle' } as unknown as Connection);

  it('reads the list, page by page, and marks a deleted connection without fetching it by id', async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url);
      return Response.json(url.includes('cursor=k2')
        ? { items: [conn('c_target', 'uat-oracle')], next_cursor: null }
        : { items: [conn('c_other', 'other')], next_cursor: 'k2' });
    }));
    const found = await resolveConnections((cursor) => api.connections.list(auth, 200, cursor), ['c_gone', 'c_target', null]);
    expect(urls).toEqual(['/api/v1/connections?limit=200', '/api/v1/connections?limit=200&cursor=k2']);
    expect(urls.some((u) => /\/connections\/c_/.test(u))).toBe(false);
    expect(found.get('c_gone')).toBeNull();
    expect(found.get('c_target')?.name).toBe('uat-oracle');
  });

  it('stops as soon as every connection is found', async () => {
    const calls: (string | undefined)[] = [];
    const found = await resolveConnections(async (cursor) => { calls.push(cursor); return { items: [conn('c_a', 'a')], next_cursor: 'more' }; }, ['c_a']);
    expect(calls).toEqual([undefined]);
    expect(found.get('c_a')?.name).toBe('a');
  });
});
