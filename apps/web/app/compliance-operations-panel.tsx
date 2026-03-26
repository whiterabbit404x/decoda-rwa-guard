'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { complianceDefaults, normalizeCompliancePolicy, parseTagInput, type CompliancePolicy } from './policy-builders';

type Props = { apiUrl: string };

export default function ComplianceOperationsPanel({ apiUrl }: Props) {
  const { authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<Array<{ id: string; name: string; chain_network: string }>>([]);
  const [selectedTarget, setSelectedTarget] = useState('');
  const [runType, setRunType] = useState<'transfer' | 'residency'>('transfer');
  const [policy, setPolicy] = useState<CompliancePolicy>(complianceDefaults);
  const [advancedJson, setAdvancedJson] = useState(JSON.stringify(complianceDefaults, null, 2));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [output, setOutput] = useState('');
  const [running, setRunning] = useState(false);

  const summary = useMemo(() => `Retain evidence for ${policy.evidence_retention_period_days} days, require ${policy.required_approvers_count} approvers, and use ${policy.reporting_profile} reporting profile.`, [policy]);

  useEffect(() => {
    Promise.all([
      fetch(`${apiUrl}/targets`, { headers: authHeaders() }),
      fetch(`${apiUrl}/modules/compliance/config`, { headers: authHeaders() }),
    ])
      .then(async ([targetsResponse, configResponse]) => {
        const targetsPayload = targetsResponse.ok ? await targetsResponse.json() : { targets: [] };
        const configPayload = configResponse.ok ? await configResponse.json() : { config: {} };
        setTargets(targetsPayload.targets ?? []);
        setSelectedTarget(targetsPayload.targets?.[0]?.id ?? '');
        const normalized = normalizeCompliancePolicy(configPayload.config ?? {});
        setPolicy(normalized);
        setAdvancedJson(JSON.stringify(normalized, null, 2));
      });
  }, []);

  async function save() {
    try {
      const next = showAdvanced ? normalizeCompliancePolicy(JSON.parse(advancedJson)) : policy;
      const response = await fetch(`${apiUrl}/modules/compliance/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ config: next })
      });
      setOutput(response.ok ? 'Compliance Controls policy saved.' : 'Failed to save Compliance Controls policy.');
    } catch {
      setOutput('Advanced policy configuration must be valid JSON.');
    }
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
      body: JSON.stringify({ target_id: selectedTarget, module_config: policy })
    });
    const payload = await response.json();
    setRunning(false);
    setOutput(response.ok ? `Run complete: ${JSON.stringify(payload)}` : payload.detail ?? 'Run failed');
    if (response.ok) window.dispatchEvent(new Event('pilot-history-refresh'));
  }

  return (
    <div className="dataCard">
      <h3>Compliance Controls</h3>
      <p className="muted">Configure retention, review requirements, exception handling, and reporting in business language.</p>
      <p className="statusLine">{summary}</p>
      <label>Evidence retention period (days)</label>
      <input type="number" value={policy.evidence_retention_period_days} onChange={(event) => setPolicy({ ...policy, evidence_retention_period_days: Number(event.target.value) })} />
      <label>Review checklist controls (comma-separated)</label>
      <input value={policy.required_review_checklist.join(', ')} onChange={(event) => setPolicy({ ...policy, required_review_checklist: parseTagInput(event.target.value) })} />
      <label>Required approvers count</label>
      <input type="number" value={policy.required_approvers_count} onChange={(event) => setPolicy({ ...policy, required_approvers_count: Number(event.target.value) })} />
      <label>Exception policy</label>
      <select value={policy.exception_policy} onChange={(event) => setPolicy({ ...policy, exception_policy: event.target.value as CompliancePolicy['exception_policy'] })}>
        <option value="blocked">Block exceptions</option><option value="manual_review">Manual review</option><option value="owner_approval">Owner approval</option>
      </select>
      <label>Reporting profile</label>
      <select value={policy.reporting_profile} onChange={(event) => setPolicy({ ...policy, reporting_profile: event.target.value as CompliancePolicy['reporting_profile'] })}>
        <option value="standard">Standard</option><option value="regulated">Regulated</option><option value="enterprise">Enterprise</option>
      </select>
      <details>
        <summary>Advanced policy configuration (JSON)</summary>
        <textarea value={advancedJson} onChange={(event) => setAdvancedJson(event.target.value)} rows={8} />
      </details>
      <select value={selectedTarget} onChange={(event) => setSelectedTarget(event.target.value)}>
        {targets.map((target) => <option key={target.id} value={target.id}>{target.name} · {target.chain_network}</option>)}
      </select>
      <select value={runType} onChange={(event) => setRunType(event.target.value as 'transfer' | 'residency')}>
        <option value="transfer">Policy compliance screening</option>
        <option value="residency">Residency screening</option>
      </select>
      <div className="buttonRow">
        <button type="button" onClick={() => setShowAdvanced(!showAdvanced)}>{showAdvanced ? 'Use guided fields' : 'Use advanced JSON for save'}</button>
        <button type="button" onClick={save}>Save policy</button>
        <button type="button" onClick={run} disabled={running}>{running ? 'Running…' : 'Run analysis'}</button>
        <button type="button" onClick={exportReport}>Export report</button>
      </div>
      {output ? <p className="statusLine">{output}</p> : null}
    </div>
  );
}
