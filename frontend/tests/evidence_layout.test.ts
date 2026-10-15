// The evidence pack's Summary (S1-M4 console r1 B3): a schema-qualified name has no break opportunity
// ("_" and "." are none), so the lead paragraph and the key-value cells must break it themselves, on
// screen and in print, as the page head and the meta cells already do (src/styles/evidence.css).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const css = readFileSync(new URL('../src/styles/evidence.css', import.meta.url), 'utf8');
const rules = (selector: string): string[] => css.split('\n').filter((l) => l.trim().startsWith(selector));

test('the Summary lead wraps a long name on screen and in print', () => {
  const lead = rules('.lead {');
  assert.equal(lead.length, 2, 'a screen rule and a print rule');
  for (const rule of lead) assert.ok(rule.includes('overflow-wrap: anywhere'), rule);
});

test('the key-value cells wrap a long policy or scope name', () => {
  assert.ok(rules('& dl.kv dd {').some((r) => r.includes('overflow-wrap: anywhere')));
});
