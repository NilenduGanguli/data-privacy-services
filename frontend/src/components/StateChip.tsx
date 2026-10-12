import { jobStatus } from '../lib/jobs';
import type { Job } from '../lib/types';

/** The job's status chip (mockup 07): label and signal colour from `jobStatus`. */
export function StateChip({ job, proposed }: { job: Job; proposed?: Set<string> }): JSX.Element {
  const { label, tone } = jobStatus(job, proposed);
  return <span className={`chip ${tone}`}>{label}</span>;
}
