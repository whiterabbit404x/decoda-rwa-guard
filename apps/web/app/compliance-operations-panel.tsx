'use client';

import { useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type Props = { apiUrl: string };

export default function ComplianceOperationsPanel({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [config, setConfig] = useState('{"required_review_checklist":["kyc","jurisdiction"],"evidence_retention_period_days":90}');
  const [output, setOutput] = useState('');

  async function save() {
    const response = await fetch(`${apiUrl}/modules/compliance/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ config: JSON.parse(config) })
    });
    setOutput(response.ok ? 'Compliance Controls saved.' : 'Failed to save Compliance Controls.');
  }

  async function exportReport() {
    const response = await fetch(`${apiUrl}/exports/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ format: 'json', filters: { module: 'compliance' } })
    });
    const payload = await response.json();
    setOutput(response.ok ? `Export ready: ${payload.download_url}` : 'Export not available for current plan.');
  }

  return (
    <div className="dataCard">
      <h3>Compliance Controls</h3>
      <p className="muted">Persist checklist, retention, and approval workflow requirements per workspace.</p>
      <textarea value={config} onChange={(event) => setConfig(event.target.value)} rows={7} />
      <div className="buttonRow">
        <button type="button" onClick={save}>Save</button>
        <button type="button" onClick={exportReport}>Export</button>
      </div>
      {output ? <p className="statusLine">{output}</p> : null}
    </div>
  );
}
