import type { ReactNode } from 'react';

/** A KPI tile (the mockups' `.card.kpi`): `hero` for the lead tile, `zero` for a must-stay-zero
 *  tile at zero, `alert` (rose) for a must-stay-zero tile that is not. */
export function Kpi({ label, value, delta, hero, zero, alert }: {
  label: string; value: ReactNode; delta?: ReactNode; hero?: boolean; zero?: boolean; alert?: boolean;
}): JSX.Element {
  return (
    <div className={`card kpi${hero ? ' hero' : ''}${zero ? ' zero' : ''}${alert ? ' alert' : ''}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta !== undefined && delta !== null && <div className="delta">{delta}</div>}
    </div>
  );
}
