import { useMemo } from 'react';
import type { Auth } from '../lib/api';
import { useSettings } from './useSettings';

/** The console's API key and tenant as a stable `Auth` (a dependency of every page loader). */
export function useAuth(): Auth {
  const { apiKey, tenant } = useSettings();
  return useMemo(() => ({ apiKey, tenant }), [apiKey, tenant]);
}
