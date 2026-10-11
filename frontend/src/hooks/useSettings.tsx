import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

interface Settings {
  apiKey: string; setApiKey: (v: string) => void;
  /** "Remember key on this device": true keeps the key in localStorage instead of sessionStorage. */
  rememberKey: boolean; setRememberKey: (v: boolean) => void;
  tenant: string; setTenant: (v: string) => void;
  theme: 'dark' | 'light'; toggleTheme: () => void;
}

const Ctx = createContext<Settings | null>(null);

const KEY_API = 'dps.apiKey';
const KEY_REMEMBER = 'dps.rememberKey';

type Area = 'local' | 'session';

// Every access is guarded: storage can be missing or throw (private mode, blocked site data).
function area(which: Area): Storage { return which === 'local' ? localStorage : sessionStorage; }
function read(key: string, fallback: string, which: Area = 'local'): string {
  try { return area(which).getItem(key) ?? fallback; } catch { return fallback; }
}
function write(key: string, value: string, which: Area = 'local'): void {
  try { area(which).setItem(key, value); } catch { /* private mode */ }
}
function remove(key: string, which: Area): void {
  try { area(which).removeItem(key); } catch { /* private mode */ }
}

/** Where the key lives (O5): sessionStorage unless the operator ticked "Remember key on this device". */
function initialKey(remember: boolean): string {
  if (remember) return read(KEY_API, '', 'local');
  // M0 kept the key in localStorage unconditionally: carry a leftover into this tab's session
  // and drop the persistent copy, so it no longer outlives the tab without an opt-in.
  const legacy = read(KEY_API, '', 'local');
  remove(KEY_API, 'local');
  const session = read(KEY_API, '', 'session') || legacy;
  if (session) write(KEY_API, session, 'session');
  return session;
}

/**
 * Console settings shared through context. The API key is a raw credential: by default it lives
 * in sessionStorage (gone when the tab closes; a persistent copy is exposed to any XSS and to the
 * next user of a shared workstation). Ticking "Remember key on this device" moves it to
 * localStorage; unticking moves it back and removes the persistent copy. Tenant and theme are not
 * sensitive and stay in localStorage.
 */
export function SettingsProvider({ children }: { children: ReactNode }): JSX.Element {
  const [rememberKey, setRememberState] = useState(() => read(KEY_REMEMBER, 'false') === 'true');
  const [apiKey, setApiKeyState] = useState(() => initialKey(read(KEY_REMEMBER, 'false') === 'true'));
  const [tenant, setTenantState] = useState(() => read('dps.tenant', ''));
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (read('dps.theme', document.documentElement.getAttribute('data-theme') || 'dark') === 'light' ? 'light' : 'dark'));

  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); write('dps.theme', theme); }, [theme]);
  const setApiKey = useCallback((v: string) => {
    setApiKeyState(v);
    write(KEY_API, v, rememberKey ? 'local' : 'session');
  }, [rememberKey]);
  const setRememberKey = useCallback((v: boolean) => {
    setRememberState(v);
    write(KEY_REMEMBER, v ? 'true' : 'false');
    if (v) {
      write(KEY_API, apiKey, 'local');
      remove(KEY_API, 'session');
    } else {
      write(KEY_API, apiKey, 'session');
      remove(KEY_API, 'local');
    }
  }, [apiKey]);
  const setTenant = useCallback((v: string) => { setTenantState(v); write('dps.tenant', v); }, []);
  const toggleTheme = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), []);

  const value = useMemo(
    () => ({ apiKey, setApiKey, rememberKey, setRememberKey, tenant, setTenant, theme, toggleTheme }),
    [apiKey, setApiKey, rememberKey, setRememberKey, tenant, setTenant, theme, toggleTheme],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSettings(): Settings {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useSettings outside SettingsProvider');
  return ctx;
}
