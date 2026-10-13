import type { ReactNode } from 'react';

/** A KPI tile (the mockups' `.card.kpi`): `hero` for the lead tile, `zero` for a must-stay-zero tile. */
export function Kpi({ label, value, delta, hero, zero }: {
  label: string; value: ReactNode; delta?: ReactNode; hero?: boolean; zero?: boolean;
}): JSX.Element {
  return (
    <div className={`card kpi${hero ? ' hero' : ''}${zero ? ' zero' : ''}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta !== undefined && delta !== null && <div className="delta">{delta}</div>}
    </div>
  );
}
