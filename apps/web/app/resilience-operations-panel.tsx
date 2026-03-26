'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { normalizeResiliencePolicy, resilienceDefaults, type ResiliencePolicy } from './policy-builders';

type Props = { apiUrl: string };
type Alert = { id: string; title: string; severity: string; status: string; module_key?: string };

export default function ResilienceOperationsPanel({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Array<{ id: string; name: string; chain_network: string }>>([]);
  const [selectedTarget, setSelectedTarget] = useState('');
  const [runType, setRunType] = useState<'reconcile' | 'backstop'>('reconcile');
  const [policy, setPolicy] = useState<ResiliencePolicy>(resilienceDefaults);
  const [advancedJson, setAdvancedJson] = useState(JSON.stringify(resilienceDefaults, null, 2));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [message, setMessage] = useState('');

  const summary = useMemo(() => `Oracle checks: ${policy.oracle_dependency_checks_enabled ? 'on' : 'off'} · Settlement checks: ${policy.settlement_control_checks_enabled ? 'on' : 'off'} · Emergency threshold: ${policy.emergency_trigger_threshold}.`, [policy]);

  async function save() {
    try {
      const next = showAdvanced ? normalizeResiliencePolicy(JSON.parse(advancedJson)) : policy;
      const response = await fetch(`${apiUrl}/modules/resilience/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ config: next })
      });
      setMessage(response.ok ? 'Resilience Monitoring policy saved.' : 'Unable to save resilience policy.');
    } catch {
      setMessage('Advanced policy configuration must be valid JSON.');
    }
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
    void loadAlerts();
  }

  useEffect(() => {
    Promise.all([
      fetch(`${apiUrl}/targets`, { headers: authHeaders() }),
      fetch(`${apiUrl}/modules/resilience/config`, { headers: authHeaders() }),
    ]).then(async ([targetsResponse, configResponse]) => {
      const targetsPayload = targetsResponse.ok ? await targetsResponse.json() : { targets: [] };
      const configPayload = configResponse.ok ? await configResponse.json() : { config: {} };
      setTargets(targetsPayload.targets ?? []);
      setSelectedTarget(targetsPayload.targets?.[0]?.id ?? '');
      const normalized = normalizeResiliencePolicy(configPayload.config ?? {});
      setPolicy(normalized);
      setAdvancedJson(JSON.stringify(normalized, null, 2));
    });
    void loadAlerts();
  }, []);

  async function run() {
    const response = await fetch(`${apiUrl}/pilot/resilience/${runType === 'reconcile' ? 'reconcile/state' : 'backstop/evaluate'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ target_id: selectedTarget, module_config: policy })
    });
    const payload = await response.json();
    setMessage(response.ok ? 'Resilience run persisted and alerts refreshed.' : (payload.detail ?? 'Run failed.'));
    await loadAlerts();
  }

  return (
    <div className="dataCard">
      <h3>Resilience Monitoring</h3>
      <p className="muted">Tune oracle risk, settlement concentration, and emergency trigger thresholds with guided controls.</p>
      <p className="statusLine">{summary}</p>
      <label><input type="checkbox" checked={policy.oracle_dependency_checks_enabled} onChange={(event) => setPolicy({ ...policy, oracle_dependency_checks_enabled: event.target.checked })} /> Oracle dependency checks</label>
      <label>Oracle sensitivity threshold</label>
      <input type="number" value={policy.oracle_sensitivity_threshold} onChange={(event) => setPolicy({ ...policy, oracle_sensitivity_threshold: Number(event.target.value) })} />
      <label><input type="checkbox" checked={policy.settlement_control_checks_enabled} onChange={(event) => setPolicy({ ...policy, settlement_control_checks_enabled: event.target.checked })} /> Settlement control checks</label>
      <label>Control concentration threshold</label>
      <input type="number" value={policy.control_concentration_threshold} onChange={(event) => setPolicy({ ...policy, control_concentration_threshold: Number(event.target.value) })} />
      <label><input type="checkbox" checked={policy.privileged_role_change_alerts} onChange={(event) => setPolicy({ ...policy, privileged_role_change_alerts: event.target.checked })} /> Privileged role change alerts</label>
      <label>Emergency trigger threshold</label>
      <select value={policy.emergency_trigger_threshold} onChange={(event) => setPolicy({ ...policy, emergency_trigger_threshold: event.target.value as ResiliencePolicy['emergency_trigger_threshold'] })}>
        <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option>
      </select>
      <label>Monitoring cadence (minutes)</label>
      <input type="number" value={policy.monitoring_cadence_minutes} onChange={(event) => setPolicy({ ...policy, monitoring_cadence_minutes: Number(event.target.value) })} />
      <details>
        <summary>Advanced policy configuration (JSON)</summary>
        <textarea value={advancedJson} onChange={(event) => setAdvancedJson(event.target.value)} rows={8} />
      </details>
      <select value={selectedTarget} onChange={(event) => setSelectedTarget(event.target.value)}>
        {targets.map((target) => <option key={target.id} value={target.id}>{target.name} · {target.chain_network}</option>)}
      </select>
      <div className="buttonRow">
        <button type="button" onClick={() => setShowAdvanced(!showAdvanced)}>{showAdvanced ? 'Use guided fields' : 'Use advanced JSON for save'}</button>
        <button type="button" onClick={save}>Save policy</button>
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
