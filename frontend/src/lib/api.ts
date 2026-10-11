import type { DetectOut, Preset, Readiness, RevealOut, TransformOut } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export interface Auth { apiKey: string; tenant: string }

async function call<T>(path: string, auth: Auth, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (auth.apiKey) headers['X-API-KEY'] = auth.apiKey;
  if (auth.tenant) headers['X-DPS-Tenant'] = auth.tenant;
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) } });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return (await res.json()) as T;
}

export type Policy = string | Record<string, unknown> | null;
export interface Body { data?: unknown; text?: string; policy?: Policy; options?: Record<string, unknown> }

export const api = {
  readyz: () => fetch('/readyz').then((r) => r.json() as Promise<Readiness>),
  metrics: () => fetch('/metrics').then((r) => r.text()),
  presets: (auth: Auth) => call<Preset[]>('/api/v1/policies/presets', auth),
  detect: (auth: Auth, body: Body) => call<DetectOut>('/api/v1/detect', auth, { method: 'POST', body: JSON.stringify(body) }),
  obfuscate: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/obfuscate', auth, { method: 'POST', body: JSON.stringify(body) }),
  synthesize: (auth: Auth, body: Body) => call<TransformOut>('/api/v1/synthesize', auth, { method: 'POST', body: JSON.stringify(body) }),
  reveal: (auth: Auth, body: { data?: unknown; text?: string; key_material: unknown; reveal_hints: unknown[] }) =>
    call<RevealOut>('/api/v1/reveal', auth, { method: 'POST', body: JSON.stringify(body) }),
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
