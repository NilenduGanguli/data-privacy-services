/**
 * Loaders that follow `next_cursor` (S1-M4 console r1 B1, B2). Each takes the page fetcher as an
 * argument (the pages pass `api.jobs.list` / `api.connections.list`), so the paging rules hold no
 * network code of their own and are tested against a recording fetcher. Ids, names, states and
 * counts only: nothing here ever reads a customer value.
 */
import type { Connection, Job, Page } from './types';

/** The Jobs page's page size, the window its head names, and the most pages it reads at once. */
export const JOBS_PAGE = 200;
export const JOBS_WINDOW_DAYS = 7;
export const JOBS_MAX_PAGES = 25;

export type JobsFetcher = (p: { limit: number; cursor?: string; state?: Job['state'] }) => Promise<Page<Job>>;

/** Jobs newest first, and the cursor of the next (older) page when there is one. */
export interface JobsWindow { items: Job[]; more: string | null }

/**
 * Every job of the window the Jobs page names — the last `days` days — newest first: pages through
 * `GET /jobs` until a page reaches past the window or no page is left. At `maxPages` it stops:
 * `more` then holds the cursor of the next, older page and the list is only the newest
 * `items.length` jobs (the page says so and offers to load older ones).
 */
export async function loadJobsWindow(
  list: JobsFetcher,
  { now = Date.now(), days = JOBS_WINDOW_DAYS, maxPages = JOBS_MAX_PAGES }: { now?: number; days?: number; maxPages?: number } = {},
): Promise<JobsWindow> {
  const cutoff = now - days * 86_400_000;
  const items: Job[] = [];
  let cursor: string | undefined;
  for (let n = 0; n < maxPages; n += 1) {
    const page = await list({ limit: JOBS_PAGE, ...(cursor ? { cursor } : {}) });
    items.push(...page.items);
    if (!page.next_cursor) return { items, more: null };
    cursor = page.next_cursor;
    const last = page.items[page.items.length - 1];
    if (!last || Date.parse(last.created_at) < cutoff) break;
  }
  return { items, more: cursor ?? null };
}

/** The next (older) page after `cursor`. */
export async function loadOlderJobs(list: JobsFetcher, cursor: string): Promise<JobsWindow> {
  const page = await list({ limit: JOBS_PAGE, cursor });
  return { items: page.items, more: page.next_cursor };
}

/** Every job in one of `states`, from the server (a Status filter over a list that is not complete). */
export async function loadJobsByState(list: JobsFetcher, states: Job['state'][], maxPages = JOBS_MAX_PAGES): Promise<Job[]> {
  const out: Job[] = [];
  for (const state of states) {
    let cursor: string | undefined;
    for (let n = 0; n < maxPages; n += 1) {
      const page = await list({ limit: JOBS_PAGE, state, ...(cursor ? { cursor } : {}) });
      out.push(...page.items);
      if (!page.next_cursor) break;
      cursor = page.next_cursor;
    }
  }
  return mergeJobs(out);
}

/** Job lists merged by id (an earlier list wins: it is the fresher one), newest first. */
export function mergeJobs(...lists: Job[][]): Job[] {
  const byId = new Map<string, Job>();
  for (const list of lists) for (const j of list) if (!byId.has(j.id)) byId.set(j.id, j);
  return [...byId.values()].sort((a, b) => (a.created_at === b.created_at ? (a.id < b.id ? 1 : -1) : a.created_at < b.created_at ? 1 : -1));
}

/**
 * The connections with `ids`, read from `GET /connections` — a list, so a connection deleted since
 * (`DELETE /connections/{id}` keeps the jobs that used it) is simply absent and the browser logs
 * no failed request: `null` = deleted. Ids not reached within `maxPages` pages are left out.
 */
export async function resolveConnections(
  list: (cursor?: string) => Promise<Page<Connection>>,
  ids: (string | null | undefined)[],
  maxPages = 10,
): Promise<Map<string, Connection | null>> {
  const want = new Set(ids.filter((x): x is string => !!x));
  const out = new Map<string, Connection | null>();
  if (!want.size) return out;
  let cursor: string | undefined;
  for (let n = 0; n < maxPages; n += 1) {
    const page = await list(cursor);
    for (const c of page.items) if (want.has(c.id)) out.set(c.id, c);
    if ([...want].every((id) => out.has(id))) return out;
    if (!page.next_cursor) {
      for (const id of want) if (!out.has(id)) out.set(id, null);
      return out;
    }
    cursor = page.next_cursor;
  }
  return out;
}
