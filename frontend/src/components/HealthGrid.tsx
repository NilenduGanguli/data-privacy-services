import type { HealthTile } from '../lib/admin';

/** Service health (mockup 11): one tile per readiness component and per worker role. DPS keeps no
 *  readiness history, so the mockup's 30-day strips are not drawn. */
export function HealthGrid({ tiles }: { tiles: HealthTile[] }): JSX.Element {
  return (
    <section className="card" aria-labelledby="healthT">
      <div className="card-head"><div className="card-title" id="healthT">Service health</div>
        <div className="legend"><span><i style={{ background: 'var(--emerald)' }} />ready</span><span><i style={{ background: 'var(--amber)' }} />degraded</span><span className="faint">from /readyz and worker heartbeats</span></div></div>
      <div className="health">
        {tiles.map((t) => (
          <div className="svc" key={t.name}>
            <div className="top"><span className="nm">{t.name}</span><span className={`chip ${t.ok ? 'emerald' : 'amber'}`}>{t.label}</span></div>
            <div className="d">{t.detail || '—'}</div>
          </div>
        ))}
        {!tiles.length && <p className="muted sm">Readiness is not available.</p>}
      </div>
    </section>
  );
}
