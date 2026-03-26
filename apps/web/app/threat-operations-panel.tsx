'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type Props = { apiUrl: string };

type Target = { id: string; name: string; target_type: string; chain_network: string; enabled: boolean };

export default function ThreatOperationsPanel({ apiUrl }: Props) {
  const { isAuthenticated, authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Target[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [analysisType, setAnalysisType] = useState<'contract' | 'transaction' | 'market'>('contract');
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
    const target = targets.find((item) => item.id === selectedTarget);
    const configResponse = await fetch(`${apiUrl}/modules/threat/config`, { headers: { ...authHeaders() } });
    const configPayload = configResponse.ok ? await configResponse.json() : { config: {} };
    const body = {
      target_id: selectedTarget,
      target_name: target?.name,
      chain_network: target?.chain_network,
      target_type: target?.target_type,
      module_config: configPayload.config ?? {}
    };
    const response = await fetch(`${apiUrl}/pilot/threat/analyze/${analysisType}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body)
    });
    const runPayload = await response.json();
    if (!response.ok) {
      setState('error');
      setMessage(runPayload.detail ?? 'Threat run failed.');
      return;
    }
    const historyResponse = await fetch(`${apiUrl}/pilot/history?limit=10`, { headers: { ...authHeaders() } });
    const historyPayload = await historyResponse.json();
    setHistory(JSON.stringify({ latest_run: runPayload, recent_runs: historyPayload.analysis_runs ?? [] }, null, 2));
    setState('success');
    setMessage('Threat Monitoring run completed and history refreshed.');
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
      <label htmlFor="threat-analysis">Analysis</label>
      <select id="threat-analysis" value={analysisType} onChange={(event) => setAnalysisType(event.target.value as 'contract' | 'transaction' | 'market')}>
        <option value="contract">Contract analysis</option>
        <option value="transaction">Transaction simulation</option>
        <option value="market">Market anomaly checks</option>
      </select>
      <div className="buttonRow">
        <button type="button" onClick={saveConfig} disabled={state === 'saving'}>Save</button>
        <button type="button" onClick={run} disabled={state === 'running'}>Run</button>
      </div>
      <p className="statusLine">{message}</p>
      {history ? <pre>{history}</pre> : null}
    </div>
  );
}
