// Policies page loading (programme plan F4 polish M1-N, M5 console review N3/N4/R2-5): a quick
// switch between policies never shows the previous policy's late answer, a refused draft save is
// shown by the page instead of escaping as an unhandled rejection, and declared paths that are
// also Object.prototype names keep their "since" version.
import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { ApiError } from './api';
import { declaredSince } from './policyEdit';
import { Latest, loadPolicyDetail, saveEdit, type PolicyDetailSink, type PolicyDetailSource } from './policyLoad';
import type { PolicyDoc, TenantPolicyDetail } from './types';

interface Deferred<T> { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void }
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

/** What the page would show: the last value each setter received. */
function screen(): { shown: { detail?: TenantPolicyDetail | null; body?: PolicyDoc | null; error?: string | null }; sink: PolicyDetailSink } {
  const shown: { detail?: TenantPolicyDetail | null; body?: PolicyDoc | null; error?: string | null } = {};
  return { shown, sink: { detail: (d) => { shown.detail = d; }, presetBody: (b) => { shown.body = b; }, error: (m) => { shown.error = m; } } };
}

const NOW = new Date('2026-10-13T10:15:00Z');
const bodyA = { ...fx.gdprBody(1), description: 'preset A' } as unknown as PolicyDoc;
const bodyB = { ...fx.gdprBody(1), description: 'preset B' } as unknown as PolicyDoc;
const tenantDetail = fx.gdprDetail(NOW);

describe('Latest', () => {
  it('keeps only the newest load current', () => {
    const latest = new Latest();
    const first = latest.begin();
    expect(first()).toBe(true);
    const second = latest.begin();
    expect(first()).toBe(false);
    expect(second()).toBe(true);
  });
});

describe('loadPolicyDetail', () => {
  it('drops a slow preset answer that arrives after a quick switch to another preset', async () => {
    const latest = new Latest();
    const answers: Record<string, Deferred<PolicyDoc>> = { a: deferred(), b: deferred() };
    const src: PolicyDetailSource = { get: () => Promise.reject(new Error('not a tenant policy')), validate: (n) => answers[n].promise };
    const { shown, sink } = screen();
    const first = loadPolicyDetail('a', false, src, sink, latest.begin());
    const second = loadPolicyDetail('b', false, src, sink, latest.begin());
    answers.b.resolve(bodyB);
    await second;
    answers.a.resolve(bodyA);
    await first;
    expect(shown.body).toBe(bodyB);
    expect(shown.detail).toBeNull();
  });

  it('drops a slow tenant-policy detail after a switch to a shipped preset', async () => {
    const latest = new Latest();
    const slow = deferred<TenantPolicyDetail>();
    const src: PolicyDetailSource = { get: () => slow.promise, validate: async () => bodyB };
    const { shown, sink } = screen();
    const first = loadPolicyDetail('retail-kyc', true, src, sink, latest.begin());
    await loadPolicyDetail('pci-dss', false, src, sink, latest.begin());
    slow.resolve(tenantDetail);
    await first;
    expect(shown.detail).toBeNull();
    expect(shown.body).toBe(bodyB);
  });

  it('drops a late error of a policy the user already left', async () => {
    const latest = new Latest();
    const slow = deferred<PolicyDoc>();
    const src: PolicyDetailSource = { get: () => Promise.reject(new Error('unused')), validate: (n) => (n === 'a' ? slow.promise : Promise.resolve(bodyB)) };
    const { shown, sink } = screen();
    const first = loadPolicyDetail('a', false, src, sink, latest.begin());
    await loadPolicyDetail('b', false, src, sink, latest.begin());
    slow.reject(new ApiError(500, 'boom', 'internal_error'));
    await first;
    expect(shown.error).toBeNull();
    expect(shown.body).toBe(bodyB);
  });

  it('shows a tenant policy, and falls back to the preset when the store has no such policy', async () => {
    const latest = new Latest();
    const { shown, sink } = screen();
    await loadPolicyDetail('gdpr-pseudonymise', true, { get: async () => tenantDetail, validate: async () => bodyA }, sink, latest.begin());
    expect(shown.detail).toBe(tenantDetail);
    expect(shown.body).toBeNull();
    const missing: PolicyDetailSource = { get: () => Promise.reject(new ApiError(404, 'no such policy', 'policy_not_found')), validate: async () => bodyA };
    await loadPolicyDetail('gdpr-pseudonymise', true, missing, sink, latest.begin());
    expect(shown.detail).toBeNull();
    expect(shown.body).toBe(bodyA);
  });

  it('reports any other failure in words', async () => {
    const { shown, sink } = screen();
    await loadPolicyDetail('x', true, { get: () => Promise.reject(new ApiError(503, 'down', 'db_unavailable')), validate: async () => bodyA }, sink, new Latest().begin());
    expect(shown.error).toBe('503 db_unavailable: down');
  });
});

describe('saveEdit', () => {
  it('applies the edit to the current body and swallows a refusal the page already shows', async () => {
    const saved: unknown[] = [];
    const base = { preset: 'gdpr-pseudonymise' };
    saveEdit(async (b) => { saved.push(b); }, base, (b) => ({ ...b, edited: true }));
    expect(saved).toEqual([{ preset: 'gdpr-pseudonymise', edited: true }]);
    // A refused save rejects; saveEdit must handle it (an unhandled rejection fails this run).
    saveEdit(() => Promise.reject(new ApiError(422, 'invalid', 'invalid_policy')), base, (b) => b);
    await new Promise((r) => setTimeout(r, 0));
    let called = false;
    saveEdit(async () => { called = true; }, null, (b) => b);
    expect(called).toBe(false);
  });
});

describe('declaredSince', () => {
  it('names the version for paths that are also Object.prototype names', () => {
    const v1 = { detect: { declared_fields: [{ path: 'constructor', entity_type: 'ORG_NAME' }, { path: '__proto__', entity_type: 'ORG_NAME' }, { path: 'toString', entity_type: 'ORG_NAME' }] } };
    const since = declaredSince({ 1: v1 }, null, 2);
    expect(since['constructor']).toBe('v1');
    expect(since['toString']).toBe('v1');
    expect(Object.keys(since).sort()).toEqual(['__proto__', 'constructor', 'toString']);
  });
});
