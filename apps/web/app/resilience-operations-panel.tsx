'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type Props = { apiUrl: string };

type Alert = { id: string; title: string; severity: string; status: string; module_key?: string };

export default function ResilienceOperationsPanel({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Array<{ id: string; name: string; chain_network: string }>>([]);
  const [selectedTarget, setSelectedTarget] = useState('');
  const [runType, setRunType] = useState<'reconcile' | 'backstop'>('reconcile');
  const [config, setConfig] = useState('{"oracle_dependency_sensitivity":"high","control_concentration_alerts":true,"emergency_action_threshold":"high"}');
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [message, setMessage] = useState('');

  async function save() {
    const response = await fetch(`${apiUrl}/modules/resilience/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ config: JSON.parse(config) })
    });
    setMessage(response.ok ? 'Resilience Monitoring config saved.' : 'Unable to save resilience config.');
  }

  async function loadAlerts() {
    const response = await fetch(`${apiUrl}/alerts?module=resilience`, { headers: { ...authHeaders() } });
    if (!response.ok) return;
    const payload = await response.json();
    setAlerts(payload.alerts ?? []);
  }

  async function acknowledge(alertId: string) {
    await fetch(`${apiUrl}/alerts/${alertId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ status: 'acknowledged' })
    });
    loadAlerts();
  }

  useEffect(() => {
    fetch(`${apiUrl}/targets`, { headers: authHeaders() })
      .then((response) => response.ok ? response.json() : { targets: [] })
      .then((payload) => {
        setTargets(payload.targets ?? []);
        setSelectedTarget(payload.targets?.[0]?.id ?? '');
      });
    loadAlerts();
  }, []);

  async function run() {
    const response = await fetch(`${apiUrl}/pilot/resilience/${runType === 'reconcile' ? 'reconcile/state' : 'backstop/evaluate'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ target_id: selectedTarget, module_config: JSON.parse(config) })
    });
    const payload = await response.json();
    setMessage(response.ok ? 'Resilience run persisted and alerts refreshed.' : (payload.detail ?? 'Run failed.'));
    await loadAlerts();
  }

  return (
    <div className="dataCard">
      <h3>Resilience Monitoring</h3>
      <textarea value={config} onChange={(event) => setConfig(event.target.value)} rows={7} />
      <select value={selectedTarget} onChange={(event) => setSelectedTarget(event.target.value)}>
        {targets.map((target) => <option key={target.id} value={target.id}>{target.name} · {target.chain_network}</option>)}
      </select>
      <div className="buttonRow">
        <button type="button" onClick={save}>Save</button>
        <select value={runType} onChange={(event) => setRunType(event.target.value as 'reconcile' | 'backstop')}>
          <option value="reconcile">Reconciliation checks</option>
          <option value="backstop">Control / failure-mode checks</option>
        </select>
        <button type="button" onClick={run}>Run</button>
      </div>
      {message ? <p className="statusLine">{message}</p> : null}
      <h4>Alerts</h4>
      {alerts.length === 0 ? <p className="muted">No resilience alerts yet.</p> : alerts.map((alert) => (
        <div key={alert.id} className="listHeader">
          <span>{alert.title} · {alert.severity} · {alert.status}</span>
          {alert.status === 'open' ? <button type="button" onClick={() => acknowledge(alert.id)}>Acknowledge</button> : null}
        </div>
      ))}
    </div>
  );
}
