'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { normalizeThreatPolicy, parseTagInput, threatDefaults, type Severity, type ThreatPolicy } from './policy-builders';

type Props = { apiUrl: string };
type Target = { id: string; name: string; target_type: string; chain_network: string; enabled: boolean };

export default function ThreatOperationsPanel({ apiUrl }: Props) {
  const { isAuthenticated, authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Target[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [analysisType, setAnalysisType] = useState<'contract' | 'transaction' | 'market'>('contract');
  const [policy, setPolicy] = useState<ThreatPolicy>(threatDefaults);
  const [advancedJson, setAdvancedJson] = useState(JSON.stringify(threatDefaults, null, 2));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [history, setHistory] = useState('');
  const [state, setState] = useState<'idle' | 'loading' | 'saving' | 'running' | 'error' | 'success'>('idle');
  const [message, setMessage] = useState('Configure your threat policy with guided controls, then run analysis on any saved target.');

  const summary = useMemo(() => {
    return `Block unlimited approvals: ${policy.unlimited_approval_detection_enabled ? 'yes' : 'no'} · ` +
      `Privileged function sensitivity: ${policy.privileged_function_sensitivity} · ` +
      `Escalate large transfers over ${policy.large_transfer_threshold.toLocaleString()}.`;
  }, [policy]);

  function updatePolicy(next: ThreatPolicy) {
    setPolicy(next);
    setAdvancedJson(JSON.stringify(next, null, 2));
  }

  function validate(p: ThreatPolicy): string | null {
    if (p.unknown_target_threshold < 0 || p.unknown_target_threshold > 50) return 'Unknown target threshold must be between 0 and 50.';
    if (p.large_transfer_threshold <= 0) return 'Large transfer threshold must be greater than 0.';
    return null;
  }

  async function loadTargetsAndPolicy() {
    if (!isAuthenticated) return;
    setState('loading');
    const [targetsResponse, configResponse] = await Promise.all([
      fetch(`${apiUrl}/targets`, { headers: { ...authHeaders() } }),
      fetch(`${apiUrl}/modules/threat/config`, { headers: { ...authHeaders() } }),
    ]);
    const targetsPayload = targetsResponse.ok ? await targetsResponse.json() : { targets: [] };
    setTargets(targetsPayload.targets ?? []);
    setSelectedTarget((targetsPayload.targets ?? [])[0]?.id ?? '');
    const configPayload = configResponse.ok ? await configResponse.json() : { config: {} };
    const normalized = normalizeThreatPolicy(configPayload.config ?? {});
    updatePolicy(normalized);
    setState('success');
  }

  useEffect(() => {
    void loadTargetsAndPolicy();
  }, [isAuthenticated]);

  async function saveConfig() {
    try {
      setState('saving');
      const parsed = showAdvanced ? normalizeThreatPolicy(JSON.parse(advancedJson)) : policy;
      const error = validate(parsed);
      if (error) throw new Error(error);
      const response = await fetch(`${apiUrl}/modules/threat/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ config: parsed })
      });
      if (!response.ok) throw new Error('Save failed');
      updatePolicy(parsed);
      setState('success');
      setMessage('Threat Monitoring policy saved. Alerts and live analysis now use this business policy.');
    } catch (error) {
      setState('error');
      setMessage(error instanceof Error ? error.message : 'Unable to save policy.');
    }
  }

  async function run() {
    if (!selectedTarget) {
      setState('error');
      setMessage('Create your first target before running analysis.');
      return;
    }
    setState('running');
    const target = targets.find((item) => item.id === selectedTarget);
    const body = {
      target_id: selectedTarget,
      target_name: target?.name,
      chain_network: target?.chain_network,
      target_type: target?.target_type,
      module_config: policy,
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
      <p className="muted">Use a guided policy builder for approvals, transfer thresholds, and escalation behavior.</p>
      <p className="statusLine">Effective policy summary: {summary}</p>
      <label htmlFor="threat-target">Target</label>
      <select id="threat-target" value={selectedTarget} onChange={(event) => setSelectedTarget(event.target.value)}>
        <option value="">Select target</option>
        {targets.map((target) => <option key={target.id} value={target.id}>{target.name} · {target.target_type}</option>)}
      </select>

      <label><input type="checkbox" checked={policy.risky_approvals_enabled} onChange={(event) => updatePolicy({ ...policy, risky_approvals_enabled: event.target.checked })} /> Risky approvals checks</label>
      <label><input type="checkbox" checked={policy.unlimited_approval_detection_enabled} onChange={(event) => updatePolicy({ ...policy, unlimited_approval_detection_enabled: event.target.checked })} /> Unlimited approval detection</label>
      <label>Unknown target threshold</label>
      <input type="number" value={policy.unknown_target_threshold} onChange={(event) => updatePolicy({ ...policy, unknown_target_threshold: Number(event.target.value) })} />
      <label>Privileged/admin function sensitivity</label>
      <select value={policy.privileged_function_sensitivity} onChange={(event) => updatePolicy({ ...policy, privileged_function_sensitivity: event.target.value as Severity })}>
        <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option>
      </select>
      <label>Large transfer threshold (USD)</label>
      <input type="number" value={policy.large_transfer_threshold} onChange={(event) => updatePolicy({ ...policy, large_transfer_threshold: Number(event.target.value) })} />
      <label>Allowlist (comma-separated)</label>
      <input value={policy.allowlist.join(', ')} onChange={(event) => updatePolicy({ ...policy, allowlist: parseTagInput(event.target.value) })} />
      <label>Denylist (comma-separated)</label>
      <input value={policy.denylist.join(', ')} onChange={(event) => updatePolicy({ ...policy, denylist: parseTagInput(event.target.value) })} />

      <details>
        <summary>Advanced policy configuration (JSON)</summary>
        <textarea id="threat-config" value={advancedJson} onChange={(event) => setAdvancedJson(event.target.value)} rows={8} />
      </details>
      <label htmlFor="threat-analysis">Analysis</label>
      <select id="threat-analysis" value={analysisType} onChange={(event) => setAnalysisType(event.target.value as 'contract' | 'transaction' | 'market')}>
        <option value="contract">Contract analysis</option>
        <option value="transaction">Transaction simulation</option>
        <option value="market">Market anomaly checks</option>
      </select>
      <div className="buttonRow">
        <button type="button" onClick={() => setShowAdvanced(!showAdvanced)}>{showAdvanced ? 'Use guided fields' : 'Use advanced JSON for save'}</button>
        <button type="button" onClick={saveConfig} disabled={state === 'saving'}>Save policy</button>
        <button type="button" onClick={run} disabled={state === 'running'}>Run</button>
      </div>
      <p className="statusLine">{message}</p>
      {history ? <pre>{history}</pre> : null}
    </div>
  );
}
