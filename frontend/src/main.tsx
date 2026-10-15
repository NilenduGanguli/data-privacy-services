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
import './styles/scanner.css';

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
// a production build, so this branch and the fixture modules are dropped from the bundle;
// scripts/check-bundle.mjs fails the build if fixture code ever reaches dist. The M5 fixture server
// (Admin, Policies, Reports…) is installed first; the UI-scanner fixtures wrap it, answer the scans
// and artefacts routes and hand every other request on.
if (import.meta.env.DEV && import.meta.env.VITE_DPS_FIXTURES === '1') {
  void Promise.all([import('./dev/fixtureServer'), import('./dev/fixtureMode')]).then(([server, scanner]) => {
    server.installFixtureServer();
    scanner.installFixtureMode();
    render();
  });
} else {
  render();
}
