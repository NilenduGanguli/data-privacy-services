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

// Dev-only fixture mode for the UI-scanner pages (VITE_DPS_FIXTURES=1 npm run dev). `import.meta.env.DEV`
// is false in a production build, so the branch and its dynamic import are dropped from the bundle;
// scripts/check-bundle.mjs fails the build if fixture code ever reaches dist.
if (import.meta.env.DEV && import.meta.env.VITE_DPS_FIXTURES === '1') {
  void import('./dev/fixtureMode').then((m) => { m.installFixtureMode(); render(); });
} else {
  render();
}
