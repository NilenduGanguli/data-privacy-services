// Policies page logic (mockup 05): the session draft, the treatment matrix, the recognizer
// builder's live preview and the plan summary. tests/test_console_policies.py checks the same
// module against the engine (methods, inline drafts, the recognizer route).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  DEFAULT_RECOGNIZER, EMPTY_DRAFT, METHODS, REVERSIBLE, TEST_TEXT, declareField, draftChanges, filterRows, inlinePolicy,
  listOf, livePreview, matchCount, planSummary, recognizerSpec, treatmentRows, withTreatment,
} from '../src/lib/policyDraft.ts';
import type { EntityInfo, PlanRow, PolicyDoc } from '../src/lib/types.ts';

const doc: PolicyDoc = {
  name: 'pci-dss', version: 1, description: 'x', locale: { default: null, by_country_field: null },
  detect: {
    tiers: ['structural', 'patterns'], min_score: 0.6,
    custom_recognizers: [{ name: 'base-rec', supported_entity: 'CASE_ID', patterns: [] }],
    declared_fields: [{ path: '$.a', entity_type: 'CUSTOMER_ID' }],
  },
  treat: {
    default_sensitive: { method: 'det_token', params: {} }, CVV: { method: 'redact', params: {} },
    CARD_PAN: { method: 'fpe_digits', params: { keep_prefix: 6, keep_suffix: 4 } },
  },
  synthesize: { default: { method: 'surrogate', params: {} }, overrides: { EMAIL: { method: 'surrogate', params: {} } }, keep_non_sensitive: true, llm_rewrite_text: false },
  consistency: { scope: 'key', field_scoped: false }, output: { token_style: 'natural' }, verify: { leakage_check: 'strict' },
};

const ent = (name: string, sensitivity: EntityInfo['sensitivity'], family = 'financial'): EntityInfo =>
  ({ name, family, sensitivity, description: '', jurisdictions: [], builtin: true });
const entities = [ent('CARD_PAN', 'CRITICAL'), ent('CVV', 'CRITICAL'), ent('SSN', 'CRITICAL', 'national_id'), ent('EMAIL', 'HIGH', 'contact'), ent('GENDER', 'LOW', 'demographic'), ent('PHONE', 'HIGH', 'contact')];

test('methods a draft may choose: the engine registry minus surrogate; reversible ones', () => {
  assert.ok(!METHODS.includes('surrogate') && METHODS.includes('keep') && METHODS.includes('external_tokenize'));
  assert.deepEqual([...REVERSIBLE].sort(), ['date_shift', 'det_token', 'email', 'external_tokenize', 'fpe_digits', 'fpe_text', 'phone']);
});

test('the inline policy is the preset plus the draft; lists carry the preset entries first', () => {
  const draft = { treat: { CVV: { method: 'null', params: {} } }, declared: [{ path: '$.b', entity_type: 'BUSINESS_SENSITIVE' }], recognizers: [recognizerSpec(DEFAULT_RECOGNIZER)] };
  const inline = inlinePolicy('pci-dss', doc, draft);
  assert.deepEqual(inline.preset, 'pci-dss');
  assert.deepEqual(inline.treat, { CVV: { method: 'null', params: {} } });
  const detect = inline.detect as { declared_fields: { path: string }[]; custom_recognizers: { name: string }[] };
  assert.deepEqual(detect.declared_fields.map((d) => d.path), ['$.a', '$.b']);
  assert.deepEqual(detect.custom_recognizers.map((r) => r.name), ['base-rec', 'employee-id']);
  assert.equal(draftChanges(draft), 3);
  assert.equal(draftChanges(EMPTY_DRAFT), 0);
});

test('choosing the shipped method again drops the draft entry (and restores the preset params)', () => {
  let draft = withTreatment(EMPTY_DRAFT, doc, 'CVV', 'det_token');
  assert.deepEqual(draft.treat, { CVV: { method: 'det_token', params: {} } });
  draft = withTreatment(draft, doc, 'CVV', 'redact');
  assert.deepEqual(draft.treat, {});
  assert.deepEqual(withTreatment(EMPTY_DRAFT, doc, 'SSN', 'det_token').treat, {}); // = default_sensitive
  assert.deepEqual(withTreatment(EMPTY_DRAFT, doc, 'SSN', 'redact').treat, { SSN: { method: 'redact', params: {} } });
});

test('matrix rows: policy entries, every critical type and drafted types; resolution as the planner does it', () => {
  const draft = withTreatment(EMPTY_DRAFT, doc, 'PHONE', 'mask_partial');
  const rows = treatmentRows(doc, draft, entities, false);
  assert.deepEqual(rows.map((r) => r.entity), ['CARD_PAN', 'CVV', 'EMAIL', 'PHONE', 'SSN']);
  const by = Object.fromEntries(rows.map((r) => [r.entity, r]));
  assert.deepEqual(by.CARD_PAN.obf, { method: 'fpe_digits', params: { keep_prefix: 6, keep_suffix: 4 } });
  assert.equal(by.SSN.obf.method, 'det_token');
  assert.ok(by.SSN.inherited && !by.CVV.inherited);
  assert.ok(by.PHONE.changed && by.PHONE.obf.method === 'mask_partial');
  assert.equal(by.EMAIL.syn.method, 'surrogate');
  const all = treatmentRows(doc, EMPTY_DRAFT, entities, true);
  assert.equal(all.length, 6);
  assert.equal(all.find((r) => r.entity === 'GENDER')?.syn.method, 'keep'); // LOW + keep_non_sensitive
  assert.equal(treatmentRows({ ...doc, synthesize: { ...doc.synthesize, keep_non_sensitive: false } }, EMPTY_DRAFT, entities, true)
    .find((r) => r.entity === 'GENDER')?.syn.method, 'surrogate');
});

test('matrix filters: critical, changed in draft, and a search over entity, method and family', () => {
  const rows = treatmentRows(doc, withTreatment(EMPTY_DRAFT, doc, 'PHONE', 'redact'), entities, true);
  assert.deepEqual(filterRows(rows, 'critical', '').map((r) => r.entity), ['CARD_PAN', 'CVV', 'SSN']);
  assert.deepEqual(filterRows(rows, 'draft', '').map((r) => r.entity), ['PHONE']);
  assert.deepEqual(filterRows(rows, 'all', 'redact').map((r) => r.entity), ['CVV', 'PHONE']);
  assert.deepEqual(filterRows(rows, 'all', 'national').map((r) => r.entity), ['SSN']);
});

test('declaring a field adds the declaration and its treatment (bucket gets ranges)', () => {
  const d = declareField(EMPTY_DRAFT, { path: ' $.loan.internal_score ', entity_type: 'BUSINESS_SENSITIVE', method: 'bucket' });
  assert.deepEqual(d.declared, [{ path: '$.loan.internal_score', entity_type: 'BUSINESS_SENSITIVE' }]);
  assert.deepEqual(d.treat.BUSINESS_SENSITIVE, { method: 'bucket', params: { ranges: [0, 300, 600, 900] } });
  assert.equal(declareField(EMPTY_DRAFT, { path: '  ', entity_type: 'X', method: 'redact' }), EMPTY_DRAFT);
});

test('recognizer spec from the form fields', () => {
  assert.deepEqual(listOf(' a, b ,,c '), ['a', 'b', 'c']);
  const spec = recognizerSpec({ ...DEFAULT_RECOGNIZER, score: 'x' });
  assert.equal(spec.patterns[0].score, 0);
  assert.deepEqual(recognizerSpec(DEFAULT_RECOGNIZER), {
    name: 'employee-id', supported_entity: 'EMPLOYEE_ID',
    patterns: [{ name: 'employee-id', regex: '\\bEMP-\\d{6}\\b', score: 0.85 }],
    context: ['staff id', 'employee', 'emp no'], deny_list: [], allow_list: ['EMP-000000'],
  });
});

test('live preview: the default recognizer finds EMP-204417 only; the allow-listed fixture is struck out', () => {
  const live = livePreview(DEFAULT_RECOGNIZER.regex, listOf(DEFAULT_RECOGNIZER.allow), TEST_TEXT);
  assert.equal(live.error, null);
  assert.deepEqual(live.hits.map((h) => [TEST_TEXT.slice(h.start, h.end), h.allowed]), [['EMP-204417', false], ['EMP-000000', true]]);
  assert.equal(matchCount(live.hits), '1 match');
  assert.equal(matchCount([]), '0 matches');
});

test('live preview: an empty, invalid or empty-matching pattern never hangs or throws', () => {
  assert.match(livePreview('', [], 'x').error ?? '', /Enter a pattern/);
  assert.match(livePreview('(EMP', [], 'x').error ?? '', /cannot compile/);
  assert.deepEqual(livePreview('x*', [], 'abc').hits, []);
});

test('plan summary: rows per method and fallback count', () => {
  const row = (method: string, reason: string): PlanRow => ({ path: '$.a', span: null, entity_type: 'SSN', score: 1, method, params: {}, reason, reversible: true });
  const s = planSummary([row('det_token', 'policy:SSN|fallback:fpe_disabled'), row('det_token', 'policy:default_sensitive'), row('redact', 'policy:CVV')]);
  assert.deepEqual(s.counts, [['det_token', 2], ['redact', 1]]);
  assert.equal(s.fallbacks, 1);
});
