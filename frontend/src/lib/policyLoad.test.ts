// Policies page loading (programme plan F4 polish M1-N, M5 console review N3/N4/R2-5): a quick
// switch between policies never shows the previous policy, neither its late answer nor its detail
// while the next one loads, and an edit is saved only to the policy it was made on; a refused
// draft save is shown by the page instead of escaping as an unhandled rejection, and declared
// paths that are also Object.prototype names keep their "since" version.
import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { ApiError } from './api';
import { declaredSince, type Body } from './policyEdit';
import { editTarget, Latest, loadPolicyDetail, onScreen, saveEdit, type PolicyDetailSink, type PolicyDetailSource } from './policyLoad';
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
// a second tenant policy (pci-dss adopted: v1 = the preset, no draft)
const pciDetail: TenantPolicyDetail = {
  ...tenantDetail, name: 'pci-dss', base_preset: 'pci-dss', active_version: 1, draft: false, draft_changes: 0, draft_body: null, draft_by: null, draft_updated_at: null,
  active_body: { ...fx.gdprBody(1), name: 'pci-dss', description: 'pci-dss v1' }, versions: tenantDetail.versions.slice(-1),
};
const analyticsBody = { ...fx.gdprBody(1), name: 'analytics-safe', description: 'analytics-safe preset' } as PolicyDoc;

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

describe('a switch while the next policy loads (M1-N, console review N4)', () => {
  it('tenant A to tenant B: A leaves the page at once, and nothing of it can be shown or edited while B is pending', async () => {
    const latest = new Latest();
    const pending = deferred<TenantPolicyDetail>();
    const src: PolicyDetailSource = { get: (n) => (n === tenantDetail.name ? Promise.resolve(tenantDetail) : pending.promise), validate: () => Promise.reject(new Error('unused')) };
    const { shown, sink } = screen();
    await loadPolicyDetail(tenantDetail.name, true, src, sink, latest.begin());
    expect(shown.detail).toBe(tenantDetail);
    const switching = loadPolicyDetail(pciDetail.name, true, src, sink, latest.begin());
    // pci-dss has not answered: the page holds nothing of gdpr-pseudonymise any more
    expect(shown.detail).toBeNull();
    expect(shown.body).toBeNull();
    // and before the load starts (the render that already names pci-dss) gdpr's detail is neither shown nor editable
    expect(onScreen(pciDetail.name, tenantDetail, null)).toEqual({ detail: null, presetBody: null });
    expect(editTarget(onScreen(pciDetail.name, tenantDetail, null).detail, true)).toBeNull();
    pending.resolve(pciDetail);
    await switching;
    expect(shown.detail).toBe(pciDetail);
    expect(onScreen(pciDetail.name, pciDetail, null).detail).toBe(pciDetail);
  });

  it('preset to tenant: the preset body leaves the page at once and is never shown under the tenant policy', async () => {
    const latest = new Latest();
    const pending = deferred<TenantPolicyDetail>();
    const src: PolicyDetailSource = { get: () => pending.promise, validate: async () => analyticsBody };
    const { shown, sink } = screen();
    await loadPolicyDetail('analytics-safe', false, src, sink, latest.begin());
    expect(shown.body).toBe(analyticsBody);
    const switching = loadPolicyDetail(tenantDetail.name, true, src, sink, latest.begin());
    expect(shown.body).toBeNull();
    expect(shown.detail).toBeNull();
    expect(onScreen(tenantDetail.name, null, analyticsBody)).toEqual({ detail: null, presetBody: null });
    pending.resolve(tenantDetail);
    await switching;
    expect(shown.detail).toBe(tenantDetail);
    expect(shown.body).toBeNull();
  });

  it('a reload of the same policy (after publish) keeps it on screen until the answer', async () => {
    const latest = new Latest();
    const again = deferred<TenantPolicyDetail>();
    let calls = 0;
    const src: PolicyDetailSource = { get: () => (calls++ === 0 ? Promise.resolve(tenantDetail) : again.promise), validate: () => Promise.reject(new Error('unused')) };
    const { shown, sink } = screen();
    await loadPolicyDetail(tenantDetail.name, true, src, sink, latest.begin());
    const reload = loadPolicyDetail(tenantDetail.name, true, src, sink, latest.begin(), true);
    expect(shown.detail).toBe(tenantDetail);
    const published = { ...tenantDetail, active_version: 4, draft: false, draft_body: null };
    again.resolve(published);
    await reload;
    expect(shown.detail).toBe(published);
  });

  it('shows a preset body only under its own name', () => {
    expect(onScreen('analytics-safe', null, analyticsBody).presetBody).toBe(analyticsBody);
    expect(onScreen('pci-dss', null, analyticsBody).presetBody).toBeNull();
    expect(onScreen(tenantDetail.name, tenantDetail, analyticsBody)).toEqual({ detail: tenantDetail, presetBody: null });
  });
});

describe('editTarget and saveEdit', () => {
  it('saves an edit to the policy it was made on, and swallows a refusal the page already shows', async () => {
    const target = editTarget(tenantDetail, true);
    expect(target).toEqual({ policy: 'gdpr-pseudonymise', base: tenantDetail.draft_body });
    expect(editTarget(pciDetail, true)).toEqual({ policy: 'pci-dss', base: pciDetail.active_body });
    const saved: [string, Body][] = [];
    saveEdit(async (policy, b) => { saved.push([policy, b]); }, target, (b) => ({ ...b, edited: true }));
    expect(saved).toEqual([['gdpr-pseudonymise', { ...tenantDetail.draft_body, edited: true }]]);
    // A refused save rejects; saveEdit must handle it (an unhandled rejection fails this run).
    saveEdit(() => Promise.reject(new ApiError(422, 'invalid', 'invalid_policy')), target, (b) => b);
    await new Promise((r) => setTimeout(r, 0));
  });

  it('has no target, so saves nothing, without an editable detail on screen', () => {
    expect(editTarget(null, true)).toBeNull();
    expect(editTarget(tenantDetail, false)).toBeNull(); // read-only key, or no policy store
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
