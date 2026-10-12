import { sparkPath } from '../lib/jobs';

/** A one-line trend (throughput, queue depth) drawn from `[label, value]` samples. */
export function Sparkline({ points, width = 160, height = 34, label, muted = false }: {
  points: [string, number][]; width?: number; height?: number; label: string; muted?: boolean;
}): JSX.Element {
  return (
    <svg className={`sparkline${muted ? ' muted' : ''}`} role="img" aria-label={label} viewBox={`0 0 ${width} ${height}`}
         preserveAspectRatio="none" style={{ height }}>
      <path d={sparkPath(points, width, height)} />
    </svg>
  );
}
