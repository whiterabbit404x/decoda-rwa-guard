'use client';

import { useEffect, useState } from 'react';
import { usePilotAuth } from '../../pilot-auth-context';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export default function ExportsPage() {
  const { authHeaders } = usePilotAuth();
  const [jobs, setJobs] = useState<any[]>([]);
  const [message, setMessage] = useState('');

  async function loadJobs() {
    const response = await fetch(`${API_URL}/exports`, { headers: authHeaders(), cache: 'no-store' });
    const payload = response.ok ? await response.json() : { exports: [] };
    setJobs(payload.exports ?? []);
  }

  async function createExport(type: 'history' | 'alerts' | 'findings' | 'report', format: 'csv' | 'json') {
    const response = await fetch(`${API_URL}/exports/${type}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ format })
    });
    const payload = await response.json();
    setMessage(response.ok ? `Export ${payload.status}.` : payload.detail ?? 'Export failed.');
    await loadJobs();
  }

  useEffect(() => { void loadJobs(); }, []);

  return <main className="productPage"><section className="dataCard"><h1>Exports</h1><p className="muted">Generate real CSV/JSON artifacts from persisted workspace data.</p>
    <div className="buttonRow">
      <button type="button" onClick={() => createExport('history', 'csv')}>History CSV</button>
      <button type="button" onClick={() => createExport('alerts', 'json')}>Alerts JSON</button>
      <button type="button" onClick={() => createExport('findings', 'csv')}>Findings CSV</button>
    </div>
    {message ? <p className="statusLine">{message}</p> : null}
    {jobs.map((job) => <p key={job.id}>{job.export_type} · {job.format} · {job.status} {job.download_url ? <a href={`${API_URL}${job.download_url}`}>Download</a> : null}</p>)}
  </section></main>;
}
