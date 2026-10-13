// Policies (mockup 05, Task 23): store-backed presets and tenant policies, header with draft and
// versions, matrix, declared fields, recognizer builder, plan preview, version history with diff.
import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { diffBodies } from '../dev/fixtureServer';
import { changedEntities } from '../lib/policyEdit';
import { html, text } from '../test/render';
import { PoliciesView, type PoliciesViewProps } from './Policies';

const NOW = new Date('2026-10-13T10:15:00Z');
const detail = fx.gdprDetail(NOW);
const bodies = Object.fromEntries(fx.policyVersions().map((b) => [b.version, b as unknown as Record<string, any>]));
const draftDiff = { from: 3, to: 'draft' as const, changes: diffBodies(detail.active_body, detail.draft_body) };
const never = (): Promise<never> => new Promise(() => undefined);

function props(p: Partial<PoliciesViewProps> = {}): PoliciesViewProps {
  return {
    name: 'gdpr-pseudonymise', now: NOW, store: true, presets: fx.presetViews(), tenantPolicies: [detail], detail, presetBody: null,
    entities: fx.ENTITIES, fpe: true, readOnly: false, versionBodies: bodies, draftDiff, selected: 'draft', selectedDiff: draftDiff, error: null,
    actions: {
      saveDraft: never, discardDraft: never, publish: never, adopt: never, create: never, select: () => undefined, downloadYaml: never,
      testRecognizer: never, runPlan: never,
    },
    ...p,
  };
}

describe('PoliciesView', () => {
  const out = html(<PoliciesView {...props()} />, '/policies/gdpr-pseudonymise');
  const t = text(out);
  it('shows the crumb with the draft pill and FF1 state, and the head actions', () => {
    expect(t).toContain('Policies / gdpr-pseudonymise');
    expect(t).toContain('Draft v4 · 3 changes not published');
    expect(t).toContain('FF1 on');
    expect(t).toContain('Discard draft');
    expect(t).toContain('Preview plan');
    expect(t).toContain('Publish v4');
  });
  it('lists presets and tenant policies with versions, hashes, modes and job counts', () => {
    expect(t).toContain('gdpr-pseudonymise v3 sha256:18d24931…3a66');
    expect(t).toContain('reversible · 14 jobs');
    expect(t).toContain('pci-dss v1 · shipped');
    expect(t).toContain('one-way · 3 jobs');
    expect(t).toContain('New policy from a preset');
    expect(t).toMatch(/Treatment per entity \d+ Declared fields 5 Custom recognizers 1 Plan preview 0 Versions 4/);
  });
  it('renders the policy header from the draft with publication facts', () => {
    expect(t).toContain('v3 active');
    expect(t).toContain('draft v4 · 3 changes');
    expect(t).toContain('Active hash sha256:18d2493121bae95d…b34b1c5e3a66');
    expect(t).toMatch(/Published 02 Oct 2026 by platform-admin · 14 jobs use it/);
    expect(t).toContain('Minimum score 0.60');
    expect(t).toContain('pseudonymised and still personal data');
  });
  it('marks draft changes in the matrix and explains the default', () => {
    expect(out).toContain('id="matrix"');
    expect(t).toContain('Changed in draft');
    expect(t).toMatch(/ORG_NAME[^]*draft/);
    expect(t).toContain('default_sensitive → det_token');
    expect(t).toContain('area 900–999, never issued');
    expect(t).toContain('need at least 10⁶ free values');
  });
  it('lists declared fields with their origin and version', () => {
    expect(t).toContain('LOANS.APPLICANTS.EMPLOYER_NAME');
    expect(t).toContain('draft v4');
    expect(t).toContain('CRM.CUSTOMERS.RISK_SCORE');
    expect(t).toContain('Declare a field');
  });
  it('offers the recognizer builder and the plan preview', () => {
    expect(out).toContain('id="recognizers"');
    expect(t).toContain('Add recognizer to draft');
    expect(t).toContain('Copy as Presidio YAML');
    expect(out).toContain('id="plan"');
    expect(t).toContain('Re-run plan');
    expect(t).toContain('On · this deployment');
    expect(t).toContain('Rohan Desai');
  });
  it('shows version history with the selected diff and the pinned-jobs note', () => {
    expect(out).toContain('id="versions"');
    expect(t).toContain('v3 → draft v4');
    expect(t).toContain('3 jobs are pinned to v2');
    expect(out).toContain('<div class="add">');
    expect(t).toContain('ORG_NAME: { method: det_token }');
  });
});

describe('PoliciesView states', () => {
  it('is read-only without the admin scope', () => {
    const t = text(html(<PoliciesView {...props({ readOnly: true })} />));
    expect(t).toContain('Read-only: drafts and publishing need a key with the admin scope');
    expect(t).not.toContain('Publish v4');
    expect(t).not.toContain('Add recognizer to draft');
  });
  it('offers to adopt a shipped preset the tenant has not adopted', () => {
    const t = text(html(<PoliciesView {...props({ name: 'pci-dss', detail: null, presetBody: fx.gdprBody(1), selected: null, selectedDiff: null, draftDiff: null, versionBodies: {} })} />));
    expect(t).toContain('Adopt for this tenant');
    expect(t).toContain('v1 · shipped');
  });
  it('explains a deployment without the policy store', () => {
    const t = text(html(<PoliciesView {...props({ store: false, tenantPolicies: [], detail: null, presetBody: fx.gdprBody(1), selected: null, selectedDiff: null, draftDiff: null, versionBodies: {} })} />));
    expect(t).toContain('This deployment has no policy store yet');
    expect(t).not.toContain('Adopt for this tenant');
  });
  it('the changed-entity set matches the draft diff', () => {
    expect([...changedEntities(draftDiff.changes)]).toEqual(['ORG_NAME']);
  });
});
