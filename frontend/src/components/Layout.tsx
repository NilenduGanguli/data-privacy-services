import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useSettings } from '../hooks/useSettings';

type IconName = 'overview' | 'play' | 'policy' | 'db' | 'scan' | 'report' | 'admin';

// Same line icons as the approved Vault mockups (docs/design/mockups/vault.js), as JSX.
const ICON_PATHS: Record<IconName, JSX.Element> = {
  overview: (<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>),
  play: (<><path d="M4 6h16M4 12h10M4 18h7" /><circle cx="18" cy="16" r="3" /></>),
  policy: (<><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /><path d="M9 12l2 2 4-4" /></>),
  db: (<><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>),
  scan: (<><rect x="3" y="4" width="18" height="14" rx="2" /><path d="M3 9h18M8 21h8" /></>),
  report: (<><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></>),
  admin: (<><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4.4l-.4 2.6a7 7 0 0 0-2 1.2l-2.3-.9-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2 1.2l.4 2.6h4.4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" /></>),
};

function Icon({ name }: { name: IconName }): JSX.Element {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">{ICON_PATHS[name]}</svg>;
}

const NAV: { label: string; items: { to: string; text: string; icon: IconName }[] }[] = [
  { label: 'Operate', items: [{ to: '/', text: 'Overview', icon: 'overview' }, { to: '/playground', text: 'Playground', icon: 'play' }, { to: '/policies', text: 'Policies', icon: 'policy' }] },
  { label: 'Layers', items: [{ to: '/data-sources', text: 'Data sources & jobs', icon: 'db' }, { to: '/scanner', text: 'UI scanner', icon: 'scan' }] },
  { label: 'Govern', items: [{ to: '/reports', text: 'Reports & audit', icon: 'report' }, { to: '/admin', text: 'Admin', icon: 'admin' }] },
];

export function Layout(): JSX.Element {
  const { apiKey, setApiKey, rememberKey, setRememberKey, tenant, setTenant, theme, toggleTheme } = useSettings();
  const [show, setShow] = useState(false);
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Primary">
        <div className="brand">
          <div className="brand-mark">D</div>
          <div><div className="brand-title">Data Privacy Services</div><div className="brand-sub">Privacy Console</div></div>
        </div>
        <div>
          {NAV.map((g) => (
            <div key={g.label}>
              <div className="nav-label">{g.label}</div>
              {g.items.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.to === '/'} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}><Icon name={i.icon} />{i.text}</NavLink>
              ))}
            </div>
          ))}
        </div>
        <div className="sidebar-foot"><span>v0.1.0 · M0</span><span className="mono">/api/v1</span></div>
      </nav>
      <div className="main">
        <header className="topbar">
          <label className="field"><span className="field-label">Tenant</span>
            <input className="input input-inline" value={tenant} onChange={(e) => setTenant(e.target.value)} placeholder="default" spellCheck={false} /></label>
          <label className="field"><span className="field-label">API key</span>
            <span className="key-wrap">
              <input className="input input-inline" type={show ? 'text' : 'password'} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="X-API-KEY" spellCheck={false} autoComplete="off" />
              <button type="button" className="key-peek" onClick={() => setShow((v) => !v)} aria-label={show ? 'Hide key' : 'Show key'}>{show ? 'hide' : 'show'}</button>
            </span></label>
          <label className="field-inline" title="Keep the key in this browser's local storage after the tab closes. Unticked, it lives only as long as this tab.">
            <input type="checkbox" checked={rememberKey} onChange={(e) => setRememberKey(e.target.checked)} />
            <span className="field-label">Remember key on this device</span></label>
          <span className="topbar-spacer" />
          <button type="button" className="btn btn-sm" onClick={toggleTheme}>{theme === 'dark' ? 'Light' : 'Dark'} theme</button>
        </header>
        <Outlet />
      </div>
    </div>
  );
}
