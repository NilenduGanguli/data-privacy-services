import type {
  ClassificationSnapshot, Connection, ConnectionSpec, ConnectionTestResult, CountOut, DetectOut, EvidenceOut,
  EvidenceSummary, Job, JobGroup, JobKind, JobState, JobStats, KeyMaterial, ObjectRef, Page, PartitionView, Preset,
  Readiness, RevealOut, RunOut, RunSpec, Selection, SnapshotOverride, TableShape, TransformOut, WriteMode,
} from './types';

/** An API refusal: `message` is the server's value-free `detail`, `code` its machine code. */
export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public extra: Record<string, unknown> = {}) { super(message); }
}

export interface Auth { apiKey: string; tenant: string }

/** Every M2+ call goes through here: auth headers, JSON body when there is one, coded errors. */
async function request(path: string, auth: Auth, init: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = { ...(init.body ? { 'Content-Type': 'application/json' } : {}) };
  if (auth.apiKey) headers['X-API-KEY'] = auth.apiKey;
  if (auth.tenant) headers['X-DPS-Tenant'] = auth.tenant;
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as Record<string, string> | undefined) } });
  if (!res.ok) {
    let body: Record<string, unknown> = {};
    try { body = (await res.json()) as Record<string, unknown>; } catch { /* non-JSON error body */ }
    const { detail, code, ...extra } = body as { detail?: unknown; code?: unknown } & Record<string, unknown>;
    throw new ApiError(res.status, detailText(detail) ?? res.statusText, typeof code === 'string' ? code : undefined, extra);
  }
  return res;
}

/** A refusal's `detail`: the server's phrase, or FastAPI's validation list as "field: message". */
function detailText(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      const e = d as { loc?: unknown[]; msg?: unknown };
      const where = Array.isArray(e.loc) ? e.loc.filter((x) => x !== 'body').join('.') : '';
      return `${where ? `${where}: ` : ''}${typeof e.msg === 'string' ? e.msg : 'invalid'}`;
    });
    return parts.length ? parts.join('; ') : undefined;
  }
  return undefined;
}

const json = <T,>(path: string, auth: Auth, init?: RequestInit): Promise<T> =>
  request(path, auth, init).then((r) => (r.status === 204 ? (undefined as T) : (r.json() as Promise<T>)));

const post = <T,>(path: string, auth: Auth, body?: unknown, headers?: Record<string, string>): Promise<T> =>
  json<T>(path, auth, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body), headers });

/** Query string from the defined, non-empty params (`?a=1&b=x`, or '' when there are none). */
export const qs = (p: Record<string, string | number | undefined | null>): string => {
  const s = new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => [k, String(v)]));
  return s.toString() ? `?${s}` : '';
};

/** M0's call (kept for the L1 call sites): JSON in, JSON out, coded errors. */
async function call<T>(path: string, auth: Auth, init?: RequestInit): Promise<T> {
  return json<T>(path, auth, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers as Record<string, string> | undefined) } });
}

export type Policy = string | Record<string, unknown> | null;
export interface Body { data?: unknown; text?: string; policy?: Policy; options?: Record<string, unknown> }

const enc = encodeURIComponent;

export const api = {
  // M0: readiness, metrics and the Layer 1 engine routes (unchanged call sites).
  readyz: () => fetch('/readyz').then((r) => r.json() as Promise<Readiness>),
  metrics: () => fetch('/metrics').then((r) => r.text()),
  presets: (auth: Auth) => call<Preset[]>('/api/v1/policies/presets', auth),
  detect: (auth: Auth, body: Body) => call<DetectOut>('/api/v1/detect', auth, { method: 'POST', body: JSON.stringify(body) }),
  obfuscate: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/obfuscate', auth, { method: 'POST', body: JSON.stringify(body) }),
  synthesize: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/synthesize', auth, { method: 'POST', body: JSON.stringify(body) }),
  reveal: (auth: Auth, body: { data?: unknown; text?: string; key_material: unknown; reveal_hints: unknown[] }) =>
    call<RevealOut>('/api/v1/reveal', auth, { method: 'POST', body: JSON.stringify(body) }),

  // M2: connections, jobs, snapshots, job groups, evidence, artefacts (contract §9).
  connections: {
    list: (a: Auth, limit = 100) => json<Page<Connection>>(`/api/v1/connections${qs({ limit })}`, a),
    get: (a: Auth, id: string) => json<Connection>(`/api/v1/connections/${enc(id)}`, a),
    create: (a: Auth, spec: ConnectionSpec) => post<Connection>('/api/v1/connections', a, spec),
    update: (a: Auth, id: string, patch: Partial<ConnectionSpec>) =>
      json<Connection>(`/api/v1/connections/${enc(id)}`, a, { method: 'PATCH', body: JSON.stringify(patch) }),
    remove: (a: Auth, id: string) => json<void>(`/api/v1/connections/${enc(id)}`, a, { method: 'DELETE' }),
    test: (a: Auth, id: string, body: { role: 'source' | 'target'; object?: string; write_mode?: WriteMode }) =>
      post<ConnectionTestResult>(`/api/v1/connections/${enc(id)}/test`, a, body),
    objects: (a: Auth, id: string, owner?: string) => json<Page<ObjectRef>>(`/api/v1/connections/${enc(id)}/objects${qs({ owner })}`, a),
    describe: (a: Auth, id: string, object: string) => json<TableShape>(`/api/v1/connections/${enc(id)}/describe${qs({ object })}`, a),
    count: (a: Auth, id: string, object: ObjectRef, selection: Selection) =>
      post<CountOut>(`/api/v1/connections/${enc(id)}/count`, a, { object, selection }),
  },
  jobs: {
    list: (a: Auth, p: { kind?: JobKind; state?: JobState; group_id?: string; limit?: number; cursor?: string } = {}) =>
      json<Page<Job>>(`/api/v1/jobs${qs(p)}`, a),
    get: (a: Auth, id: string) => json<Job>(`/api/v1/jobs/${enc(id)}`, a),
    partitions: (a: Auth, id: string) => json<{ items: PartitionView[] }>(`/api/v1/jobs/${enc(id)}/partitions`, a),
    stats: (a: Auth) => json<JobStats>('/api/v1/jobs/stats', a),
    discover: (a: Auth, body: { connection_id: string; objects: ObjectRef[]; selection?: Selection; sample_rows?: number; policy?: string; mode?: 'obfuscate' | 'synthesize' }) =>
      post<{ job: Job }>('/api/v1/jobs/discover', a, body),
    run: (a: Auth, spec: RunSpec, idempotencyKey: string) => post<RunOut>('/api/v1/jobs/run', a, spec, { 'Idempotency-Key': idempotencyKey }),
    cancel: (a: Auth, id: string) => post<Job>(`/api/v1/jobs/${enc(id)}/cancel`, a),
    pause: (a: Auth, id: string) => post<Job>(`/api/v1/jobs/${enc(id)}/pause`, a),
    resume: (a: Auth, id: string) => post<Job>(`/api/v1/jobs/${enc(id)}/resume`, a),
    retry: (a: Auth, id: string, consistency?: Record<string, unknown>) =>
      post<Job>(`/api/v1/jobs/${enc(id)}/retry`, a, consistency ? { consistency } : {}),
  },
  snapshots: {
    list: (a: Auth, p: { connection_id?: string; object?: string; status?: string; limit?: number } = {}) =>
      json<Page<ClassificationSnapshot>>(`/api/v1/snapshots${qs(p)}`, a),
    get: (a: Auth, id: string) => json<ClassificationSnapshot>(`/api/v1/snapshots/${enc(id)}`, a),
    patch: (a: Auth, id: string, overrides: SnapshotOverride[]) =>
      json<ClassificationSnapshot>(`/api/v1/snapshots/${enc(id)}`, a, { method: 'PATCH', body: JSON.stringify({ overrides }) }),
    accept: (a: Auth, id: string) => post<ClassificationSnapshot>(`/api/v1/snapshots/${enc(id)}/accept`, a),
  },
  groups: {
    list: (a: Auth) => json<Page<JobGroup>>('/api/v1/job-groups', a),
    create: (a: Auth, name: string) =>
      post<{ group: JobGroup; key_material: KeyMaterial; synthesis_seed: Record<string, unknown> }>('/api/v1/job-groups', a, { name }),
  },
  evidence: {
    list: (a: Auth, p: { job_id?: string; limit?: number; cursor?: string } = {}) => json<Page<EvidenceSummary>>(`/api/v1/evidence${qs(p)}`, a),
    get: (a: Auth, id: string) => json<EvidenceOut>(`/api/v1/evidence/${enc(id)}`, a),
  },
  artefacts: {
    /** Fetch with the API key (artefacts are not public links), then hand the browser a blob to save. */
    download: async (a: Auth, id: string, filename: string): Promise<void> => {
      const blob = await (await request(`/api/v1/artefacts/${enc(id)}`, a)).blob();
      saveBlob(blob, filename);
    },
  },
};

/** Offer `blob` to the browser as a download named `filename` (no storage, no public link). */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Sum a Prometheus counter family from the exposition text (good enough for the overview tiles). */
export function metricTotal(text: string, name: string): number {
  let total = 0;
  for (const line of text.split('\n')) {
    if (line.startsWith(name) && !line.startsWith('#')) {
      const value = Number(line.trim().split(/\s+/).pop());
      if (!Number.isNaN(value)) total += value;
    }
  }
  return total;
}
