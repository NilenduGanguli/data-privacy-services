import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { JobDetail } from './pages/JobDetail';
import { Jobs } from './pages/Jobs';
import { JobWizard } from './pages/JobWizard';
import { Overview } from './pages/Overview';
import { Playground } from './pages/Playground';

function Soon({ title }: { title: string }): JSX.Element {
  return (
    <main className="page">
      <div className="card">
        <div className="card-title">{title}</div>
        <p className="muted">This screen arrives with its milestone (see docs/specs §11). The engine behind it is live in the Playground today.</p>
      </div>
    </main>
  );
}

export function App(): JSX.Element {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Overview />} />
        <Route path="/playground" element={<Playground />} />
        <Route path="/policies" element={<Soon title="Policies" />} />
        <Route path="/data-sources" element={<Jobs />} />
        <Route path="/data-sources/jobs" element={<Jobs />} />
        <Route path="/data-sources/jobs/new" element={<JobWizard />} />
        <Route path="/data-sources/jobs/:jobId" element={<JobDetail />} />
        <Route path="/scanner" element={<Soon title="UI scanner (M4)" />} />
        <Route path="/reports" element={<Soon title="Reports & audit (M5)" />} />
        <Route path="/admin" element={<Soon title="Admin (M5)" />} />
        <Route path="*" element={<Soon title="Page not found" />} />
      </Route>
    </Routes>
  );
}
