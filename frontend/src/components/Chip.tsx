import type { Sensitivity } from '../lib/types';

const TONE: Record<Sensitivity, string> = { CRITICAL: 'rose', HIGH: 'amber', MEDIUM: 'amber', LOW: 'slate' };

export function EntityChip({ type, sensitivity }: { type: string; sensitivity: Sensitivity }): JSX.Element {
  return <span className={`chip ${TONE[sensitivity] ?? 'slate'}`}>{type}</span>;
}

export function Tone({ tone, children }: { tone: 'teal' | 'amber' | 'emerald' | 'rose' | 'violet' | 'slate'; children: React.ReactNode }): JSX.Element {
  return <span className={`chip ${tone}`}>{children}</span>;
}
