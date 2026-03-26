'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type Props = { apiUrl: string };

type Alert = { id: string; title: string; severity: string; status: string; module_key?: string };

export default function ResilienceOperationsPanel({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
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
    loadAlerts();
  }, []);

  return (
    <div className="dataCard">
      <h3>Resilience Monitoring</h3>
      <textarea value={config} onChange={(event) => setConfig(event.target.value)} rows={7} />
      <button type="button" onClick={save}>Save</button>
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
