/**
 * Vitest helpers for console components: server-side render to static markup (Vitest, Node
 * environment; no DOM library is installed — contract: no new npm dependency). Effects do not run,
 * so a page renders its first frame; views that take data as props render it in full.
 * `html` renders inside a router only (M5 pages); `render` adds the console's SettingsProvider and an
 * optional route pattern (M4 scanner pages).
 */
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Route, Routes } from 'react-router-dom';
import { StaticRouter } from 'react-router-dom/server';
import { SettingsProvider } from '../hooks/useSettings';

export function html(el: ReactElement, path = '/'): string {
  return renderToStaticMarkup(<StaticRouter location={path}>{el}</StaticRouter>);
}

/** SettingsProvider reads the theme attribute at first render; give node a minimal document. */
function ensureDocument(): void {
  const g = globalThis as unknown as { document?: unknown };
  if (!g.document) g.document = { documentElement: { getAttribute: () => null, setAttribute: () => undefined } };
}

export function render(ui: ReactElement, { path = '/', route }: { path?: string; route?: string } = {}): string {
  ensureDocument();
  return renderToStaticMarkup(
    <SettingsProvider>
      <StaticRouter location={path}>
        {route ? <Routes><Route path={route} element={ui} /></Routes> : ui}
      </StaticRouter>
    </SettingsProvider>,
  );
}

/** The visible text of rendered markup (style blocks and tags dropped, entities decoded,
 *  whitespace collapsed). */
export function text(markup: string): string {
  return markup
    .replace(/<style[\s\S]*?<\/style>/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#x27;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

/** Count occurrences of `needle` in `hay`. */
export function occurrences(hay: string, needle: string): number {
  return hay.split(needle).length - 1;
}
