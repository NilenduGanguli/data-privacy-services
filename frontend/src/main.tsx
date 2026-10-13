import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { SettingsProvider } from './hooks/useSettings';
import './styles.css';
import './styles/components.css';
import './styles/jobs.css';
import './styles/wizard.css';
import './styles/job-detail.css';
import './styles/evidence.css';
import './styles/m5.css';

function render(): void {
  ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
    <React.StrictMode>
      <SettingsProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </SettingsProvider>
    </React.StrictMode>,
  );
}

// Dev-only fixture mode (`npm run dev:fixtures`): `import.meta.env.DEV` is the constant `false` in
// a production build, so this branch and the fixture modules are dropped from the bundle.
if (import.meta.env.DEV && import.meta.env.VITE_DPS_FIXTURES === '1') {
  void import('./dev/fixtureServer').then((m) => { m.installFixtureServer(); render(); });
} else {
  render();
}
