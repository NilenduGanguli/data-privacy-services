/**
 * Vitest helpers for console components: server-side render to static markup (no DOM needed),
 * inside the console's providers. Effects do not run, so a page renders its first frame; components
 * that take data as props render it in full.
 */
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { SettingsProvider } from '../hooks/useSettings';

/** SettingsProvider reads the theme attribute at first render; give node a minimal document. */
function ensureDocument(): void {
  const g = globalThis as unknown as { document?: unknown };
  if (!g.document) g.document = { documentElement: { getAttribute: () => null, setAttribute: () => undefined } };
}

export function render(ui: ReactElement, { path = '/', route }: { path?: string; route?: string } = {}): string {
  ensureDocument();
  return renderToStaticMarkup(
    <SettingsProvider>
      <MemoryRouter initialEntries={[path]}>
        {route ? <Routes><Route path={route} element={ui} /></Routes> : ui}
      </MemoryRouter>
    </SettingsProvider>,
  );
}

/** The visible text of rendered markup (tags dropped, entities decoded, whitespace collapsed). */
export function text(html: string): string {
  return html.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/\s+/g, ' ').trim();
}
