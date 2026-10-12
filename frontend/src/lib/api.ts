import type {
  ClassificationSnapshot, Connection, ConnectionSpec, ConnectionTestResult, CountOut, DetectOut, DocumentDetectOut,
  DocumentOptions, DocumentTransformOut, EntitiesOut, EvidenceOut, EvidenceSummary, Job, JobGroup, JobKind, JobState,
  JobStats, KeyMaterial, ObjectRef, Page, PartitionView, PlanOut, PolicyDoc, Preset, PresetSummary, Readiness,
  RecognizerSpec, RecognizerTestOut, RevealOut, RunOut, RunSpec, Selection, SnapshotOverride, TableShape,
  TransformOut, WriteMode,
} from './types';

/** An API refusal: `message` is the server's value-free `detail`, `code` its machine code (absent on
 * M0's `{"detail"}` responses), `extra` any further fields of M2's `{"detail", "code", **extra}`. */
export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public extra: Record<string, unknown> = {}) { super(message); }
}

export interface Auth { apiKey: string; tenant: string }

function authHeaders(auth: Auth): Record<string, string> {
  const headers: Record<string, string> = {};
  if (auth.apiKey) headers['X-API-KEY'] = auth.apiKey;
  if (auth.tenant) headers['X-DPS-Tenant'] = auth.tenant;
  return headers;
}

/** The one error shape (contract §8.2) from any refused response; non-JSON bodies keep the status text. */
async function toError(res: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try { body = (await res.json()) as Record<string, unknown>; } catch { /* non-JSON error body */ }
  const { detail, code, ...extra } = body as { detail?: unknown; code?: unknown } & Record<string, unknown>;
  return new ApiError(res.status, detailText(detail) ?? res.statusText, typeof code === 'string' ? code : undefined, extra);
}

/** Every M2+ call goes through here: auth headers, JSON body when there is one, coded errors. */
async function request(path: string, auth: Auth, init: RequestInit = {}): Promise<Response> {
  const headers: Record<string, string> = { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...authHeaders(auth) };
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as Record<string, string> | undefined) } });
  if (!res.ok) throw await toError(res);
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

/** M0/M1's call (kept for the L1 call sites): JSON in, JSON out, coded errors. */
async function call<T>(path: string, auth: Auth, init?: RequestInit): Promise<T> {
  return json<T>(path, auth, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers as Record<string, string> | undefined) } });
}

/** Multipart POST (M1 documents): the browser sets the boundary, so no Content-Type header here. */
async function callForm<T>(path: string, auth: Auth, form: FormData): Promise<T> {
  const res = await fetch(path, { method: 'POST', body: form, headers: authHeaders(auth) });
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

export type Policy = string | Record<string, unknown> | null;
export interface Body { data?: unknown; text?: string; policy?: Policy; options?: Record<string, unknown> }

function documentForm(file: File, policy: Policy, options: DocumentOptions): FormData {
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('request', JSON.stringify({ policy, options }));
  return form;
}

/** A JSON POST's RequestInit for the L1 `call` sites. */
const postInit = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });
const enc = encodeURIComponent;

export const api = {
  // M0: readiness, metrics and the Layer 1 engine routes.
  readyz: () => fetch('/readyz').then((r) => r.json() as Promise<Readiness>),
  metrics: () => fetch('/metrics').then((r) => r.text()),
  presets: (auth: Auth) => call<Preset[]>('/api/v1/policies/presets', auth),
  detect: (auth: Auth, body: Body) => call<DetectOut>('/api/v1/detect', auth, postInit(body)),
  obfuscate: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/obfuscate', auth, postInit(body)),
  synthesize: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/synthesize', auth, postInit(body)),
  reveal: (auth: Auth, body: { data?: unknown; text?: string; key_material: unknown; reveal_hints: unknown[] }) =>
    call<RevealOut>('/api/v1/reveal', auth, postInit(body)),

  // M1: documents, entities, policy tooling (contract §9).
  documents: {
    detect: (auth: Auth, file: File, policy: Policy, options: DocumentOptions) =>
      callForm<DocumentDetectOut>('/api/v1/documents/detect', auth, documentForm(file, policy, options)),
    obfuscate: (auth: Auth, file: File, policy: Policy, options: DocumentOptions) =>
      callForm<DocumentTransformOut>('/api/v1/documents/obfuscate', auth, documentForm(file, policy, options)),
    synthesize: (auth: Auth, file: File, policy: Policy, options: DocumentOptions) =>
      callForm<DocumentTransformOut>('/api/v1/documents/synthesize', auth, documentForm(file, policy, options)),
  },
  entities: (auth: Auth) => call<EntitiesOut>('/api/v1/entities', auth),
  policies: {
    presets: (auth: Auth) => call<PresetSummary[]>('/api/v1/policies/presets', auth),
    validate: (auth: Auth, policy: Policy) => call<PolicyDoc>('/api/v1/policies/validate', auth, postInit({ policy })),
    plan: (auth: Auth, body: { data?: unknown; text?: string; policy: Policy; mode: 'obfuscate' | 'synthesize' }) =>
      call<PlanOut>('/api/v1/policies/plan', auth, postInit(body)),
    testRecognizer: (auth: Auth, recognizer: RecognizerSpec, text: string) =>
      call<RecognizerTestOut>('/api/v1/policies/test-recognizer', auth, postInit({ recognizer, text })),
  },

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
    /** What `POST /jobs/run` would refuse, without queueing anything (step 5's server check). */
    validateRun: (a: Auth, spec: RunSpec) => post<{ ok: boolean }>('/api/v1/jobs/run/validate', a, spec),
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

/** A human message for an ApiError (status, code, detail) — never includes request data. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}${e.code ? ` ${e.code}` : ''}: ${e.message}`;
  return e instanceof Error ? e.message : String(e);
}

/** Decode base64 into a Blob (used for the redacted PDF download). */
export function base64Blob(b64: string, type: string): Blob {
  const raw = atob(b64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return new Blob([bytes], { type });
}
