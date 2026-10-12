// Policy export (Policies page → Download YAML). tests/test_console_yaml.py also loads every
// preset's inline form back through PyYAML; these pin the emitter's shapes.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { toYaml } from '../src/lib/yaml.ts';

test('block mappings and sequences, two-space indent', () => {
  assert.equal(
    toYaml({ preset: 'pci-dss', treat: { CVV: { method: 'redact', params: {} } }, tiers: ['structural', 'patterns'] }),
    'preset: pci-dss\ntreat:\n  CVV:\n    method: redact\n    params: {}\ntiers:\n  - structural\n  - patterns\n',
  );
});

test('a sequence of mappings keeps the keys of one item aligned', () => {
  assert.equal(
    toYaml({ declared_fields: [{ path: '$.loan.internal_score', entity_type: 'BUSINESS_SENSITIVE' }] }),
    'declared_fields:\n  - path: "$.loan.internal_score"\n    entity_type: BUSINESS_SENSITIVE\n',
  );
});

test('strings that YAML would read as something else are quoted', () => {
  const out = toYaml({ a: 'yes', b: 'NO', c: 'null', d: '0.85', e: '2026-10-11', f: 'two words', g: '', h: '~', i: 'on' });
  assert.equal(out, 'a: "yes"\nb: "NO"\nc: "null"\nd: "0.85"\ne: "2026-10-11"\nf: "two words"\ng: ""\nh: "~"\ni: "on"\n');
});

test('regex patterns survive as JSON-quoted strings (valid YAML double quotes)', () => {
  assert.equal(toYaml({ regex: '\\bEMP-\\d{6}\\b' }), 'regex: "\\\\bEMP-\\\\d{6}\\\\b"\n');
});

test('numbers, booleans, null and empty collections are scalars', () => {
  assert.equal(toYaml({ min_score: 0.6, t: true, z: null, l: [], m: {} }), 'min_score: 0.6\nt: true\nz: null\nl: []\nm: {}\n');
  assert.equal(toYaml({ n: 1, y: 2 }), '"n": 1\n"y": 2\n'); // YAML 1.1 booleans as keys are quoted too
  assert.equal(toYaml({ ranges: [0, 300, 600] }), 'ranges:\n  - 0\n  - 300\n  - 600\n');
});

test('nested sequences and a top-level scalar', () => {
  assert.equal(toYaml({ m: [[1, 2], []] }), 'm:\n  - - 1\n    - 2\n  - []\n');
  assert.equal(toYaml('plain'), 'plain\n');
});
