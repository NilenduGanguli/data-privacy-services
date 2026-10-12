import { engineBadge, engineName } from '../lib/jobs';

/** The mockups' square engine badge: PG, ORA, MDB, S3, PVC. */
export function EngineBadge({ engine }: { engine: string | null | undefined }): JSX.Element {
  return <span className="eb" title={engineName(engine)}>{engineBadge(engine)}</span>;
}
