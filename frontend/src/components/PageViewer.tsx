import { boxStyle, findingTone, hasGeometry, tokenFontSize } from '../lib/documents';
import type { PageSummary } from '../lib/types';

export type ViewerMode = 'source' | 'redacted' | 'tokens';

/** One page image with finding boxes positioned in percent of the page (boxes are in PDF points,
 *  origin top-left, exactly as the API returns them). `tokens` lays each replacement over its box;
 *  `redacted` shows the produced PDF page, whose boxes are already burned in. */
export function PageViewer({ page, mode, hot, onHot, synthetic = false }: {
  page: PageSummary; mode: ViewerMode; hot: number | null; onHot: (n: number | null) => void; synthetic?: boolean;
}): JSX.Element {
  const image = mode === 'redacted' ? page.redacted_preview_png_b64 : page.preview_png_b64;
  if (!hasGeometry(page)) {
    return (
      <div className="page-none" data-testid="page-frame">
        {page.source.toUpperCase()} page · no page image. {mode === 'tokens' ? 'The replacements are in the text below.' : 'Findings are listed by line.'}
      </div>
    );
  }
  return (
    <div className="page-frame" style={{ aspectRatio: `${page.width} / ${page.height}` }} data-testid="page-frame">
      {image ? <img src={`data:image/png;base64,${image}`} alt={`Page ${page.page}${mode === 'redacted' ? ', redacted' : ''}`} />
        : <div className="page-missing">No preview for this page</div>}
      {mode !== 'redacted' && page.findings.flatMap((f) => f.boxes.map((b, i) => {
        const style = boxStyle(b, page);
        if (mode === 'tokens') {
          if (!f.replacement) return null;
          return (
            <span key={`${f.n}-${i}`} className={`page-tok${synthetic ? ' syn' : ''}${hot === f.n ? ' hot' : ''}`} style={{ ...style, fontSize: tokenFontSize(b, page) }}
              onMouseEnter={() => onHot(f.n)} onMouseLeave={() => onHot(null)} title={`${f.n} · ${f.entity_type} · ${f.method ?? ''}`}>
              {i === 0 ? f.replacement : ''}
            </span>
          );
        }
        return (
          <span key={`${f.n}-${i}`} className={`page-box ${findingTone(f)}${hot === f.n ? ' hot' : ''}`} style={style} data-testid="page-box"
            onMouseEnter={() => onHot(f.n)} onMouseLeave={() => onHot(null)} title={`${f.n} · ${f.entity_type} · ${f.score.toFixed(2)}`}>
            {i === 0 && <b className="fbadge">{f.n}</b>}
          </span>
        );
      }))}
    </div>
  );
}
