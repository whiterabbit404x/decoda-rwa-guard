'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type Props = { apiUrl: string };

type Target = { id: string; name: string; target_type: string; chain_network: string; enabled: boolean };

export default function ThreatOperationsPanel({ apiUrl }: Props) {
  const { isAuthenticated, authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Target[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [config, setConfig] = useState('{"unknown_target_threshold": 2, "large_transfer_threshold": 250000}');
  const [history, setHistory] = useState<string>('');
  const [state, setState] = useState<'idle' | 'loading' | 'saving' | 'running' | 'error' | 'success'>('idle');
  const [message, setMessage] = useState('Load your workspace targets and save a threat policy to begin monitoring.');

  async function loadTargets() {
    if (!isAuthenticated) return;
    setState('loading');
    const response = await fetch(`${apiUrl}/targets`, { headers: { ...authHeaders() } });
    if (!response.ok) {
      setState('error');
      setMessage('Unable to load targets.');
      return;
    }
    const payload = await response.json();
    setTargets(payload.targets ?? []);
    setSelectedTarget((payload.targets ?? [])[0]?.id ?? '');
    setState('success');
  }

  useEffect(() => {
    loadTargets();
  }, [isAuthenticated]);

  async function saveConfig() {
    try {
      setState('saving');
      const parsed = JSON.parse(config);
      const response = await fetch(`${apiUrl}/modules/threat/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ config: parsed })
      });
      if (!response.ok) throw new Error('Save failed');
      setState('success');
      setMessage('Threat Monitoring policy saved for this workspace.');
    } catch {
      setState('error');
      setMessage('Provide valid JSON configuration before saving.');
    }
  }

  async function run() {
    if (!selectedTarget) {
      setState('error');
      setMessage('Create a target first or select one from your workspace list.');
      return;
    }
    setState('running');
    const response = await fetch(`${apiUrl}/pilot/history?limit=10`, { headers: { ...authHeaders() } });
    const payload = await response.json();
    setHistory(JSON.stringify(payload.analysis_runs ?? [], null, 2));
    setState('success');
    setMessage('Recent Threat Monitoring history loaded.');
  }

  return (
    <div className="dataCard">
      <h3>Threat Monitoring</h3>
      <p className="muted">Create/Edit/Save/Run with workspace targets and persisted policies.</p>
      <label htmlFor="threat-target">Target</label>
      <select id="threat-target" value={selectedTarget} onChange={(event) => setSelectedTarget(event.target.value)}>
        <option value="">Select target</option>
        {targets.map((target) => <option key={target.id} value={target.id}>{target.name} · {target.target_type}</option>)}
      </select>
      <label htmlFor="threat-config">Policy JSON</label>
      <textarea id="threat-config" value={config} onChange={(event) => setConfig(event.target.value)} rows={7} />
      <div className="buttonRow">
        <button type="button" onClick={saveConfig} disabled={state === 'saving'}>Save</button>
        <button type="button" onClick={run} disabled={state === 'running'}>Run</button>
      </div>
      <p className="statusLine">{message}</p>
      {history ? <pre>{history}</pre> : null}
    </div>
  );
}
