import type {
  DetectOut, DocumentDetectOut, DocumentOptions, DocumentTransformOut, EntitiesOut, PlanOut, PolicyDoc, Preset,
  PresetSummary, Readiness, RecognizerSpec, RecognizerTestOut, RevealOut, TransformOut,
} from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string, public code: string | null = null) { super(message); }
}

export interface Auth { apiKey: string; tenant: string }

function authHeaders(auth: Auth): Record<string, string> {
  const headers: Record<string, string> = {};
  if (auth.apiKey) headers['X-API-KEY'] = auth.apiKey;
  if (auth.tenant) headers['X-DPS-Tenant'] = auth.tenant;
  return headers;
}

async function toError(res: Response): Promise<ApiError> {
  let detail: unknown = res.statusText;
  let code: string | null = null;
  try {
    const body = (await res.json()) as { detail?: unknown; code?: unknown };
    detail = body.detail ?? detail;
    code = typeof body.code === 'string' ? body.code : null;
  } catch { /* non-JSON error body */ }
  return new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail), code);
}

async function call<T>(path: string, auth: Auth, init?: RequestInit): Promise<T> {
  const headers = { 'Content-Type': 'application/json', ...authHeaders(auth) };
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) } });
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

/** Multipart POST: the browser sets the boundary, so no Content-Type header here. */
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

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

export const api = {
  readyz: () => fetch('/readyz').then((r) => r.json() as Promise<Readiness>),
  metrics: () => fetch('/metrics').then((r) => r.text()),
  presets: (auth: Auth) => call<Preset[]>('/api/v1/policies/presets', auth),
  detect: (auth: Auth, body: Body) => call<DetectOut>('/api/v1/detect', auth, post(body)),
  obfuscate: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/obfuscate', auth, post(body)),
  synthesize: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/synthesize', auth, post(body)),
  reveal: (auth: Auth, body: { data?: unknown; text?: string; key_material: unknown; reveal_hints: unknown[] }) =>
    call<RevealOut>('/api/v1/reveal', auth, post(body)),
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
    validate: (auth: Auth, policy: Policy) => call<PolicyDoc>('/api/v1/policies/validate', auth, post({ policy })),
    plan: (auth: Auth, body: { data?: unknown; text?: string; policy: Policy; mode: 'obfuscate' | 'synthesize' }) =>
      call<PlanOut>('/api/v1/policies/plan', auth, post(body)),
    testRecognizer: (auth: Auth, recognizer: RecognizerSpec, text: string) =>
      call<RecognizerTestOut>('/api/v1/policies/test-recognizer', auth, post({ recognizer, text })),
  },
};

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
