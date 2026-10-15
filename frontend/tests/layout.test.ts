// The drawer's CSS (S2 console r1 B1): ported from the approved mockups' vault.css, so below 1100px the
// menu button shows and an open drawer is a fixed panel over a scrim (src/components/Layout.tsx).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');
const mock = readFileSync(new URL('../../docs/design/mockups/vault.css', import.meta.url), 'utf8');

test('the menu button and the scrim are hidden on a wide screen', () => {
  assert.ok(css.includes('.nav-toggle, .nav-scrim { display: none; }'));
  assert.ok(/@media \(max-width: 1100px\) \{[^\n]*\.sidebar \{ display: none; \}/.test(css), 'the sidebar hides below 1100px');
});

test('below 1100px the console carries the mockup rules for the button, the drawer and the scrim', () => {
  const block = (text: string): string => {
    const at = text.indexOf('@media screen and (max-width: 1100px) {\n  .nav-toggle');
    assert.ok(at >= 0, 'the drawer block');
    return text.slice(at, text.indexOf('\n}\n', at));
  };
  const rules = (text: string): string[] => block(text).split('\n').map((l) => l.trim()).filter((l) => l.startsWith('.'));
  assert.deepEqual(rules(css), rules(mock));
  assert.ok(rules(css).some((r) => r.startsWith('.shell.nav-open .sidebar { display: flex; position: fixed;')));
});
