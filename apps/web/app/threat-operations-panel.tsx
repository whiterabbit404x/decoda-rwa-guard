'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { normalizeThreatPolicy, parseTagInput, threatDefaults, type Severity, type ThreatPolicy } from './policy-builders';
import {
  buildContractPayload,
  buildMarketPayload,
  buildTransactionPayload,
  contractPresets,
  marketPresets,
  suggestedThreatAnalysisType,
  transactionPresets,
  validateAnalysisCombination,
  type ContractScenarioInput,
  type MarketScenarioInput,
  type ThreatAnalysisType,
  type ThreatTarget,
  type TransactionScenarioInput,
} from './threat-payload-builders';

type Props = { apiUrl: string };

type RunResult = {
  analysis_type: string;
  score: number;
  severity: string;
  recommended_action: string;
  source: string;
  degraded: boolean;
  matched_patterns?: Array<{ label?: string }>;
};
type MonitoringConfig = {
  monitoring_enabled: boolean;
  monitoring_interval_seconds: number;
  severity_threshold: Severity;
  auto_create_alerts: boolean;
  auto_create_incidents: boolean;
  last_checked_at?: string | null;
  last_run_status?: string | null;
  last_run_id?: string | null;
  monitoring_demo_scenario?: string | null;
};
const MONITORING_DEMO_SCENARIOS = ['safe', 'low_risk', 'medium_risk', 'high_risk', 'flash_loan_like', 'admin_abuse_like', 'risky_approval_like'] as const;

function toCsv(values: string[]) {
  return values.join(', ');
}

function parseCsv(value: string) {
  return parseTagInput(value);
}

function parseMaybeJson<T>(value: string, fallback: T): T {
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

export default function ThreatOperationsPanel({ apiUrl }: Props) {
  const { isAuthenticated, authHeaders } = usePilotAuth();
  const [targets, setTargets] = useState<ThreatTarget[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [analysisType, setAnalysisType] = useState<ThreatAnalysisType>('contract');
  const [policy, setPolicy] = useState<ThreatPolicy>(threatDefaults);
  const [advancedJson, setAdvancedJson] = useState(JSON.stringify(threatDefaults, null, 2));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [history, setHistory] = useState('');
  const [state, setState] = useState<'idle' | 'loading' | 'saving' | 'running' | 'error' | 'success'>('idle');
  const [message, setMessage] = useState('Configure your threat policy with guided controls, then run analysis on any saved target.');
  const [result, setResult] = useState<RunResult | null>(null);
  const [monitoringConfig, setMonitoringConfig] = useState<MonitoringConfig>({
    monitoring_enabled: false,
    monitoring_interval_seconds: 300,
    severity_threshold: 'medium',
    auto_create_alerts: true,
    auto_create_incidents: false,
    last_checked_at: null,
    last_run_status: null,
    last_run_id: null,
    monitoring_demo_scenario: null,
  });

  const [transactionScenario, setTransactionScenario] = useState<TransactionScenarioInput>(transactionPresets[0].scenario);
  const [contractScenario, setContractScenario] = useState<ContractScenarioInput>(contractPresets[0].scenario);
  const [marketScenario, setMarketScenario] = useState<MarketScenarioInput>(marketPresets[0].scenario);
  const [contractFunctionText, setContractFunctionText] = useState('');
  const [marketCandlesJson, setMarketCandlesJson] = useState(JSON.stringify(marketPresets[0].scenario.candles, null, 2));
  const [marketWalletActivityJson, setMarketWalletActivityJson] = useState(JSON.stringify(marketPresets[0].scenario.wallet_activity, null, 2));

  const selectedTargetRecord = useMemo(() => targets.find((item) => item.id === selectedTarget), [targets, selectedTarget]);
  const comboWarning = useMemo(() => validateAnalysisCombination(selectedTargetRecord, analysisType), [selectedTargetRecord, analysisType]);

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

  function hydrateTargetDrivenScenario(target: ThreatTarget | undefined, nextAnalysisType: ThreatAnalysisType) {
    if (!target) return;
    if (nextAnalysisType === 'transaction') {
      setTransactionScenario((prev) => ({
        ...prev,
        wallet: target.wallet_address || prev.wallet,
        protocol: target.contract_identifier || target.name || prev.protocol,
        asset: target.asset_type || prev.asset,
      }));
    }
    if (nextAnalysisType === 'contract') {
      setContractScenario((prev) => ({
        ...prev,
        contract_name: target.name || prev.contract_name,
        address: target.contract_identifier || target.wallet_address || prev.address,
      }));
    }
    if (nextAnalysisType === 'market') {
      setMarketScenario((prev) => ({ ...prev, asset: target.asset_type || target.name || prev.asset, venue: target.chain_network || prev.venue }));
    }
  }

  async function loadTargetsAndPolicy() {
    if (!isAuthenticated) return;
    setState('loading');
    const [targetsResponse, configResponse] = await Promise.all([
      fetch(`${apiUrl}/targets`, { headers: { ...authHeaders() } }),
      fetch(`${apiUrl}/modules/threat/config`, { headers: { ...authHeaders() } }),
    ]);
    const targetsPayload = targetsResponse.ok ? await targetsResponse.json() : { targets: [] };
    const loadedTargets = (targetsPayload.targets ?? []) as ThreatTarget[];
    setTargets(loadedTargets);
    const nextTargetId = loadedTargets[0]?.id ?? '';
    setSelectedTarget(nextTargetId);
    const nextTarget = loadedTargets.find((item) => item.id === nextTargetId);
    if (nextTarget) {
      setMonitoringConfig({
        monitoring_enabled: Boolean((nextTarget as any).monitoring_enabled),
        monitoring_interval_seconds: Number((nextTarget as any).monitoring_interval_seconds ?? 300),
        severity_threshold: (((nextTarget as any).severity_threshold as Severity) || 'medium'),
        auto_create_alerts: Boolean((nextTarget as any).auto_create_alerts ?? true),
        auto_create_incidents: Boolean((nextTarget as any).auto_create_incidents ?? false),
        last_checked_at: (nextTarget as any).last_checked_at ?? null,
        last_run_status: (nextTarget as any).last_run_status ?? null,
        last_run_id: (nextTarget as any).last_run_id ?? null,
        monitoring_demo_scenario: (nextTarget as any).monitoring_demo_scenario ?? null,
      });
    }
    const nextType = suggestedThreatAnalysisType(nextTarget);
    setAnalysisType(nextType);
    hydrateTargetDrivenScenario(nextTarget, nextType);

    const configPayload = configResponse.ok ? await configResponse.json() : { config: {} };
    const normalized = normalizeThreatPolicy(configPayload.config ?? {});
    updatePolicy(normalized);
    setState('success');
  }

  async function saveMonitoringConfig() {
    if (!selectedTargetRecord) return;
    const response = await fetch(`${apiUrl}/monitoring/targets/${selectedTargetRecord.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ ...monitoringConfig, monitoring_mode: 'poll', notification_channels: [] }),
    });
    setMessage(response.ok ? 'Automatic monitoring settings saved.' : 'Unable to save automatic monitoring settings.');
    if (response.ok) void loadTargetsAndPolicy();
  }

  async function runMonitoringOnce() {
    if (!selectedTargetRecord) return;
    const response = await fetch(`${apiUrl}/monitoring/run-once/${selectedTargetRecord.id}`, { method: 'POST', headers: { ...authHeaders() } });
    setMessage(response.ok ? 'Automatic monitoring run completed for target.' : 'Unable to trigger automatic monitoring run.');
    if (response.ok) void loadTargetsAndPolicy();
  }

  useEffect(() => {
    void loadTargetsAndPolicy();
  }, [isAuthenticated]);

  useEffect(() => {
    setContractFunctionText(contractScenario.function_summaries.map((fn) => `${fn.name}|${fn.summary}|${fn.risk_flags.join(',')}`).join('\n'));
  }, [contractScenario]);

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

  function buildRequestPayload(target: ThreatTarget) {
    if (analysisType === 'transaction') {
      return buildTransactionPayload(target, policy, transactionScenario);
    }
    if (analysisType === 'market') {
      const scenario = {
        ...marketScenario,
        candles: parseMaybeJson(marketCandlesJson, marketScenario.candles),
        wallet_activity: parseMaybeJson(marketWalletActivityJson, marketScenario.wallet_activity),
      };
      return buildMarketPayload(target, policy, scenario);
    }
    const functionSummaries = contractFunctionText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [name = 'unknownFunction', summaryText = 'No summary provided.', flags = ''] = line.split('|');
        return { name, summary: summaryText, risk_flags: parseCsv(flags) };
      });
    const scenario = { ...contractScenario, function_summaries: functionSummaries };
    return buildContractPayload(target, policy, scenario);
  }

  async function run() {
    if (!selectedTargetRecord) {
      setState('error');
      setMessage('Create your first target before running analysis.');
      return;
    }
    if (comboWarning) {
      setState('error');
      setMessage(comboWarning);
      return;
    }
    setState('running');
    const body = buildRequestPayload(selectedTargetRecord);
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
    setResult(runPayload as RunResult);
    setState('success');
    setMessage('Threat Monitoring run completed and history refreshed.');
  }

  function applyTransactionPreset(id: string) {
    const preset = transactionPresets.find((item) => item.id === id);
    if (!preset) return;
    setTransactionScenario(preset.scenario);
  }

  function applyContractPreset(id: string) {
    const preset = contractPresets.find((item) => item.id === id);
    if (!preset) return;
    setContractScenario(preset.scenario);
  }

  function applyMarketPreset(id: string) {
    const preset = marketPresets.find((item) => item.id === id);
    if (!preset) return;
    setMarketScenario(preset.scenario);
    setMarketCandlesJson(JSON.stringify(preset.scenario.candles, null, 2));
    setMarketWalletActivityJson(JSON.stringify(preset.scenario.wallet_activity, null, 2));
  }

  return (
    <div className="dataCard">
      <h3>Threat Monitoring</h3>
      <p className="muted">Use a guided policy builder for approvals, transfer thresholds, and escalation behavior.</p>
      <p className="statusLine">Effective policy summary: {summary}</p>
      <div className="dataCard" style={{ marginBottom: 12 }}>
        <h4>Always-on monitoring</h4>
        <p className="muted">Enable scheduled backend monitoring for this target while preserving manual run workflows.</p>
        <div className="buttonRow">
          <label><input type="checkbox" checked={monitoringConfig.monitoring_enabled} onChange={(event) => setMonitoringConfig((prev) => ({ ...prev, monitoring_enabled: event.target.checked }))} /> Enable monitoring</label>
          <label><input type="checkbox" checked={monitoringConfig.auto_create_alerts} onChange={(event) => setMonitoringConfig((prev) => ({ ...prev, auto_create_alerts: event.target.checked }))} /> Auto alerts</label>
          <label><input type="checkbox" checked={monitoringConfig.auto_create_incidents} onChange={(event) => setMonitoringConfig((prev) => ({ ...prev, auto_create_incidents: event.target.checked }))} /> Auto incidents</label>
        </div>
        <div className="buttonRow">
          <input type="number" min={30} step={30} value={monitoringConfig.monitoring_interval_seconds} onChange={(event) => setMonitoringConfig((prev) => ({ ...prev, monitoring_interval_seconds: Number(event.target.value) || 300 }))} />
          <select value={monitoringConfig.severity_threshold} onChange={(event) => setMonitoringConfig((prev) => ({ ...prev, severity_threshold: event.target.value as Severity }))}>
            <option value="low">threshold: low</option><option value="medium">threshold: medium</option><option value="high">threshold: high</option><option value="critical">threshold: critical</option>
          </select>
          <select value={monitoringConfig.monitoring_demo_scenario ?? ''} onChange={(event) => setMonitoringConfig((prev) => ({ ...prev, monitoring_demo_scenario: event.target.value || null }))}>
            <option value="">demo scenario: off (default live-like)</option>
            {MONITORING_DEMO_SCENARIOS.map((scenario) => <option key={scenario} value={scenario}>demo scenario: {scenario}</option>)}
          </select>
          <button type="button" onClick={() => void saveMonitoringConfig()}>Save monitoring</button>
          <button type="button" onClick={() => void runMonitoringOnce()}>Run once now</button>
        </div>
        <p className="tableMeta">Status: {monitoringConfig.monitoring_enabled ? 'active' : 'paused'} · last checked: {monitoringConfig.last_checked_at ? new Date(monitoringConfig.last_checked_at).toLocaleString() : 'never'} · last run: {monitoringConfig.last_run_status ?? 'n/a'}</p>
      </div>
      <label htmlFor="threat-target">Target</label>
      <select
        id="threat-target"
        value={selectedTarget}
        onChange={(event) => {
          const nextTarget = targets.find((item) => item.id === event.target.value);
          const nextType = suggestedThreatAnalysisType(nextTarget);
          setSelectedTarget(event.target.value);
          setAnalysisType(nextType);
          hydrateTargetDrivenScenario(nextTarget, nextType);
          if (nextTarget) {
            setMonitoringConfig({
              monitoring_enabled: Boolean((nextTarget as any).monitoring_enabled),
              monitoring_interval_seconds: Number((nextTarget as any).monitoring_interval_seconds ?? 300),
              severity_threshold: (((nextTarget as any).severity_threshold as Severity) || 'medium'),
              auto_create_alerts: Boolean((nextTarget as any).auto_create_alerts ?? true),
              auto_create_incidents: Boolean((nextTarget as any).auto_create_incidents ?? false),
              last_checked_at: (nextTarget as any).last_checked_at ?? null,
              last_run_status: (nextTarget as any).last_run_status ?? null,
              last_run_id: (nextTarget as any).last_run_id ?? null,
              monitoring_demo_scenario: (nextTarget as any).monitoring_demo_scenario ?? null,
            });
          }
        }}
      >
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
      <select id="threat-analysis" value={analysisType} onChange={(event) => setAnalysisType(event.target.value as ThreatAnalysisType)}>
        <option value="contract">Contract analysis</option>
        <option value="transaction">Transaction simulation</option>
        <option value="market">Market anomaly checks</option>
      </select>

      {comboWarning ? <p className="statusLine">⚠️ {comboWarning}</p> : null}

      {analysisType === 'transaction' ? (
        <>
          <p className="sectionEyebrow">Transaction scenario presets</p>
          <div className="buttonRow">{transactionPresets.map((preset) => <button key={preset.id} type="button" onClick={() => applyTransactionPreset(preset.id)}>{preset.label}</button>)}</div>
          <label>Wallet</label>
          <input value={transactionScenario.wallet} onChange={(event) => setTransactionScenario({ ...transactionScenario, wallet: event.target.value })} />
          <label>Actor</label>
          <input value={transactionScenario.actor} onChange={(event) => setTransactionScenario({ ...transactionScenario, actor: event.target.value })} />
          <label>Action type</label>
          <input value={transactionScenario.action_type} onChange={(event) => setTransactionScenario({ ...transactionScenario, action_type: event.target.value })} />
          <label>Protocol</label>
          <input value={transactionScenario.protocol} onChange={(event) => setTransactionScenario({ ...transactionScenario, protocol: event.target.value })} />
          <label>Amount</label>
          <input type="number" value={transactionScenario.amount} onChange={(event) => setTransactionScenario({ ...transactionScenario, amount: Number(event.target.value) })} />
          <label>Asset</label>
          <input value={transactionScenario.asset} onChange={(event) => setTransactionScenario({ ...transactionScenario, asset: event.target.value })} />
          <label>Call sequence (comma-separated)</label>
          <input value={toCsv(transactionScenario.call_sequence)} onChange={(event) => setTransactionScenario({ ...transactionScenario, call_sequence: parseCsv(event.target.value) })} />
          <label>Counterparty reputation (0-100)</label>
          <input type="number" value={transactionScenario.counterparty_reputation} onChange={(event) => setTransactionScenario({ ...transactionScenario, counterparty_reputation: Number(event.target.value) })} />
          <label>Actor role</label>
          <input value={transactionScenario.actor_role} onChange={(event) => setTransactionScenario({ ...transactionScenario, actor_role: event.target.value })} />
          <label>Expected actor roles (comma-separated)</label>
          <input value={toCsv(transactionScenario.expected_actor_roles)} onChange={(event) => setTransactionScenario({ ...transactionScenario, expected_actor_roles: parseCsv(event.target.value) })} />
          <label>Burst actions last 5m</label>
          <input type="number" value={transactionScenario.burst_actions_last_5m} onChange={(event) => setTransactionScenario({ ...transactionScenario, burst_actions_last_5m: Number(event.target.value) })} />
          <label><input type="checkbox" checked={transactionScenario.flags.contains_flash_loan} onChange={(event) => setTransactionScenario({ ...transactionScenario, flags: { ...transactionScenario.flags, contains_flash_loan: event.target.checked } })} /> Contains flash loan</label>
          <label><input type="checkbox" checked={transactionScenario.flags.unexpected_admin_call} onChange={(event) => setTransactionScenario({ ...transactionScenario, flags: { ...transactionScenario.flags, unexpected_admin_call: event.target.checked } })} /> Unexpected admin call</label>
          <label><input type="checkbox" checked={transactionScenario.flags.untrusted_contract} onChange={(event) => setTransactionScenario({ ...transactionScenario, flags: { ...transactionScenario.flags, untrusted_contract: event.target.checked } })} /> Untrusted contract</label>
          <label><input type="checkbox" checked={transactionScenario.flags.rapid_drain_indicator} onChange={(event) => setTransactionScenario({ ...transactionScenario, flags: { ...transactionScenario.flags, rapid_drain_indicator: event.target.checked } })} /> Rapid drain indicator</label>
        </>
      ) : null}

      {analysisType === 'contract' ? (
        <>
          <p className="sectionEyebrow">Contract scenario presets</p>
          <div className="buttonRow">{contractPresets.map((preset) => <button key={preset.id} type="button" onClick={() => applyContractPreset(preset.id)}>{preset.label}</button>)}</div>
          <label>Contract name</label>
          <input value={contractScenario.contract_name} onChange={(event) => setContractScenario({ ...contractScenario, contract_name: event.target.value })} />
          <label>Address</label>
          <input value={contractScenario.address} onChange={(event) => setContractScenario({ ...contractScenario, address: event.target.value })} />
          <label><input type="checkbox" checked={contractScenario.verified_source} onChange={(event) => setContractScenario({ ...contractScenario, verified_source: event.target.checked })} /> Verified source</label>
          <label>Audit count</label>
          <input type="number" value={contractScenario.audit_count} onChange={(event) => setContractScenario({ ...contractScenario, audit_count: Number(event.target.value) })} />
          <label>Created days ago</label>
          <input type="number" value={contractScenario.created_days_ago} onChange={(event) => setContractScenario({ ...contractScenario, created_days_ago: Number(event.target.value) })} />
          <label>Admin roles (comma-separated)</label>
          <input value={toCsv(contractScenario.admin_roles)} onChange={(event) => setContractScenario({ ...contractScenario, admin_roles: parseCsv(event.target.value) })} />
          <label>Calling actor</label>
          <input value={contractScenario.calling_actor} onChange={(event) => setContractScenario({ ...contractScenario, calling_actor: event.target.value })} />
          <label>Function summaries (name|summary|flag1,flag2 per line)</label>
          <textarea rows={4} value={contractFunctionText} onChange={(event) => setContractFunctionText(event.target.value)} />
          <label>Findings (one per line)</label>
          <textarea rows={4} value={contractScenario.findings.join('\n')} onChange={(event) => setContractScenario({ ...contractScenario, findings: event.target.value.split('\n').map((line) => line.trim()).filter(Boolean) })} />
          <label><input type="checkbox" checked={contractScenario.flags.delegatecall} onChange={(event) => setContractScenario({ ...contractScenario, flags: { ...contractScenario.flags, delegatecall: event.target.checked } })} /> Delegatecall</label>
          <label><input type="checkbox" checked={contractScenario.flags.untrusted_external_call} onChange={(event) => setContractScenario({ ...contractScenario, flags: { ...contractScenario.flags, untrusted_external_call: event.target.checked } })} /> Untrusted external call</label>
          <label><input type="checkbox" checked={contractScenario.flags.unsafe_admin_action} onChange={(event) => setContractScenario({ ...contractScenario, flags: { ...contractScenario.flags, unsafe_admin_action: event.target.checked } })} /> Unsafe admin action</label>
          <label><input type="checkbox" checked={contractScenario.flags.high_value_drain_path} onChange={(event) => setContractScenario({ ...contractScenario, flags: { ...contractScenario.flags, high_value_drain_path: event.target.checked } })} /> High value drain path</label>
          <label><input type="checkbox" checked={contractScenario.flags.burst_risk_actions} onChange={(event) => setContractScenario({ ...contractScenario, flags: { ...contractScenario.flags, burst_risk_actions: event.target.checked } })} /> Burst risk actions</label>
        </>
      ) : null}

      {analysisType === 'market' ? (
        <>
          <p className="sectionEyebrow">Market scenario presets</p>
          <div className="buttonRow">{marketPresets.map((preset) => <button key={preset.id} type="button" onClick={() => applyMarketPreset(preset.id)}>{preset.label}</button>)}</div>
          <label>Asset</label>
          <input value={marketScenario.asset} onChange={(event) => setMarketScenario({ ...marketScenario, asset: event.target.value })} />
          <label>Venue</label>
          <input value={marketScenario.venue} onChange={(event) => setMarketScenario({ ...marketScenario, venue: event.target.value })} />
          <label>Timeframe minutes</label>
          <input type="number" value={marketScenario.timeframe_minutes} onChange={(event) => setMarketScenario({ ...marketScenario, timeframe_minutes: Number(event.target.value) })} />
          <label>Current volume</label>
          <input type="number" value={marketScenario.current_volume} onChange={(event) => setMarketScenario({ ...marketScenario, current_volume: Number(event.target.value) })} />
          <label>Baseline volume</label>
          <input type="number" value={marketScenario.baseline_volume} onChange={(event) => setMarketScenario({ ...marketScenario, baseline_volume: Number(event.target.value) })} />
          <label>Participant diversity</label>
          <input type="number" value={marketScenario.participant_diversity} onChange={(event) => setMarketScenario({ ...marketScenario, participant_diversity: Number(event.target.value) })} />
          <label>Dominant cluster share (0-1)</label>
          <input type="number" step="0.01" value={marketScenario.dominant_cluster_share} onChange={(event) => setMarketScenario({ ...marketScenario, dominant_cluster_share: Number(event.target.value) })} />
          <label>Large orders</label>
          <input type="number" value={marketScenario.order_flow_summary.large_orders ?? 0} onChange={(event) => setMarketScenario({ ...marketScenario, order_flow_summary: { ...marketScenario.order_flow_summary, large_orders: Number(event.target.value) } })} />
          <label>Rapid cancellations</label>
          <input type="number" value={marketScenario.order_flow_summary.rapid_cancellations ?? 0} onChange={(event) => setMarketScenario({ ...marketScenario, order_flow_summary: { ...marketScenario.order_flow_summary, rapid_cancellations: Number(event.target.value) } })} />
          <label>Rapid swings</label>
          <input type="number" value={marketScenario.order_flow_summary.rapid_swings ?? 0} onChange={(event) => setMarketScenario({ ...marketScenario, order_flow_summary: { ...marketScenario.order_flow_summary, rapid_swings: Number(event.target.value) } })} />
          <label>Circular trade loops</label>
          <input type="number" value={marketScenario.order_flow_summary.circular_trade_loops ?? 0} onChange={(event) => setMarketScenario({ ...marketScenario, order_flow_summary: { ...marketScenario.order_flow_summary, circular_trade_loops: Number(event.target.value) } })} />
          <label>Self trade markers</label>
          <input type="number" value={marketScenario.order_flow_summary.self_trade_markers ?? 0} onChange={(event) => setMarketScenario({ ...marketScenario, order_flow_summary: { ...marketScenario.order_flow_summary, self_trade_markers: Number(event.target.value) } })} />
          <label>Candles JSON</label>
          <textarea rows={4} value={marketCandlesJson} onChange={(event) => setMarketCandlesJson(event.target.value)} />
          <label>Wallet activity JSON</label>
          <textarea rows={4} value={marketWalletActivityJson} onChange={(event) => setMarketWalletActivityJson(event.target.value)} />
        </>
      ) : null}

      <div className="buttonRow">
        <button type="button" onClick={() => setShowAdvanced(!showAdvanced)}>{showAdvanced ? 'Use guided fields' : 'Use advanced JSON for save'}</button>
        <button type="button" onClick={saveConfig} disabled={state === 'saving'}>Save policy</button>
        <button type="button" onClick={run} disabled={state === 'running' || !!comboWarning}>Run</button>
      </div>
      <p className="statusLine">{message}</p>

      {result ? (
        <div>
          <div className="listHeader">
            <h3>Result summary</h3>
            <span className={`statusBadge ${result.source === 'live' && !result.degraded ? 'statusBadge-live' : 'statusBadge-fallback'}`}>
              {result.source === 'live' && !result.degraded ? 'Live success' : 'Fallback / degraded'}
            </span>
          </div>
          {result.source === 'live' && !result.degraded ? <p className="statusLine">✅ Live threat-engine analysis completed without degradation.</p> : <p className="statusLine">⚠️ Result came from fallback or degraded mode. Review engine health before relying on this run.</p>}
          <div className="chipRow">
            <span className="ruleChip">Type: {result.analysis_type}</span>
            <span className="ruleChip">Score: {result.score}</span>
            <span className="ruleChip">Severity: {result.severity}</span>
            <span className="ruleChip">Recommended action: {result.recommended_action}</span>
            <span className="ruleChip">Source: {result.source}</span>
            <span className="ruleChip">Degraded: {String(result.degraded)}</span>
          </div>
          <p className="statusLine">Matched patterns: {(result.matched_patterns ?? []).map((item) => item.label).filter(Boolean).join(', ') || 'None'}</p>
        </div>
      ) : null}
      {history ? <pre>{history}</pre> : null}
    </div>
  );
}
