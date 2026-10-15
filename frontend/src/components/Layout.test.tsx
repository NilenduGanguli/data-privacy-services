// The console shell's navigation below 1100px (S2 console r1 B1; mockups 01-12, vault.js renderSidebar
// and vault.css): a menu button in the topbar opens the sidebar as a drawer over a scrim. The browser
// test tests/console/test_console_nav.py drives it at 390 and 1024 px, tests/layout.test.ts checks the CSS;
// this checks the markup.
import { describe, expect, it } from 'vitest';
import { occurrences, render } from '../test/render';
import { Layout } from './Layout';

describe('Layout navigation', () => {
  const out = render(<Layout />, { path: '/admin' });
  it('renders the menu button in the topbar, closed, controlling the sidebar', () => {
    const button = /<header class="topbar"><button type="button" class="nav-toggle"([^>]*)>/.exec(out);
    expect(button).not.toBeNull();
    const attrs = button?.[1] ?? '';
    expect(attrs).toContain('aria-controls="sidebar"');
    expect(attrs).toContain('aria-expanded="false"');
    expect(attrs).toContain('aria-label="Open navigation"');
    expect(out).toContain('<nav class="sidebar" id="sidebar" aria-label="Primary">');
    expect(out).toMatch(/<div class="shell">/); // closed: no nav-open
    expect(out).toContain('<div class="nav-scrim" aria-hidden="true"></div>');
  });
  it('keeps all seven sections in the sidebar the drawer shows', () => {
    expect(occurrences(out, 'class="nav-item')).toBe(7);
    for (const href of ['/', '/playground', '/policies', '/data-sources', '/scanner', '/reports', '/admin']) expect(out).toContain(`href="${href}"`);
    expect(out).toContain('class="nav-item active" href="/admin"');
  });
});
