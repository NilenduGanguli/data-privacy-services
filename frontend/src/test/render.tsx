/** Server-side rendering for page and component tests (Vitest, Node environment; no DOM library
 *  is installed — contract: no new npm dependency). Views are pure: data in, markup out. */
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server';

export function html(el: ReactElement, path = '/'): string {
  return renderToStaticMarkup(<StaticRouter location={path}>{el}</StaticRouter>);
}

/** The visible text of rendered markup (tags dropped, entities decoded, whitespace collapsed). */
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
