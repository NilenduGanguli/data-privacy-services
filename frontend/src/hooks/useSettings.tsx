import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

interface Settings {
  apiKey: string; setApiKey: (v: string) => void;
  tenant: string; setTenant: (v: string) => void;
  theme: 'dark' | 'light'; toggleTheme: () => void;
}

const Ctx = createContext<Settings | null>(null);

function read(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function write(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

export function SettingsProvider({ children }: { children: ReactNode }): JSX.Element {
  const [apiKey, setApiKeyState] = useState(() => read('dps.apiKey', ''));
  const [tenant, setTenantState] = useState(() => read('dps.tenant', ''));
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (read('dps.theme', document.documentElement.getAttribute('data-theme') || 'dark') === 'light' ? 'light' : 'dark'));

  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); write('dps.theme', theme); }, [theme]);
  const setApiKey = useCallback((v: string) => { setApiKeyState(v); write('dps.apiKey', v); }, []);
  const setTenant = useCallback((v: string) => { setTenantState(v); write('dps.tenant', v); }, []);
  const toggleTheme = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), []);

  const value = useMemo(() => ({ apiKey, setApiKey, tenant, setTenant, theme, toggleTheme }), [apiKey, setApiKey, tenant, setTenant, theme, toggleTheme]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSettings(): Settings {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useSettings outside SettingsProvider');
  return ctx;
}
