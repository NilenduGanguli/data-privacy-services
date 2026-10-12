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

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <SettingsProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </SettingsProvider>
  </React.StrictMode>,
);
