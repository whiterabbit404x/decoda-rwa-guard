'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type Props = { apiUrl: string };

export default function ComplianceOperationsPanel({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Array<{ id: string; name: string; chain_network: string }>>([]);
  const [selectedTarget, setSelectedTarget] = useState('');
  const [runType, setRunType] = useState<'transfer' | 'residency'>('transfer');
  const [config, setConfig] = useState('{"required_review_checklist":["kyc","jurisdiction"],"evidence_retention_period_days":90}');
  const [output, setOutput] = useState('');
  const [running, setRunning] = useState(false);

  useEffect(() => {
    fetch(`${apiUrl}/targets`, { headers: authHeaders() })
      .then((response) => response.ok ? response.json() : { targets: [] })
      .then((payload) => {
        setTargets(payload.targets ?? []);
        setSelectedTarget(payload.targets?.[0]?.id ?? '');
      });
  }, []);

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

  async function run() {
    setRunning(true);
    const response = await fetch(`${apiUrl}/pilot/compliance/screen/${runType}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ target_id: selectedTarget, module_config: JSON.parse(config) })
    });
    const payload = await response.json();
    setRunning(false);
    setOutput(response.ok ? `Run complete: ${JSON.stringify(payload)}` : payload.detail ?? 'Run failed');
    if (response.ok) {
      window.dispatchEvent(new Event('pilot-history-refresh'));
    }
  }

  return (
    <div className="dataCard">
      <h3>Compliance Controls</h3>
      <p className="muted">Persist checklist, retention, and approval workflow requirements per workspace.</p>
      <textarea value={config} onChange={(event) => setConfig(event.target.value)} rows={7} />
      <select value={selectedTarget} onChange={(event) => setSelectedTarget(event.target.value)}>
        {targets.map((target) => <option key={target.id} value={target.id}>{target.name} · {target.chain_network}</option>)}
      </select>
      <select value={runType} onChange={(event) => setRunType(event.target.value as 'transfer' | 'residency')}>
        <option value="transfer">Policy compliance screening</option>
        <option value="residency">Residency screening</option>
      </select>
      <div className="buttonRow">
        <button type="button" onClick={save}>Save</button>
        <button type="button" onClick={run} disabled={running}>{running ? 'Running…' : 'Run'}</button>
        <button type="button" onClick={exportReport}>Export</button>
      </div>
      {output ? <p className="statusLine">{output}</p> : null}
    </div>
  );
}
