'use client';

import { useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type OperationsPanelProps = {
  apiUrl: string;
};

const reconcileScenarios = {
  healthy_matched_multi_ledger_state: {
    label: 'Healthy matched state',
    body: {
      asset_id: 'USTB-2026',
      expected_total_supply: 1000000,
      ledgers: [
        { ledger_name: 'ethereum', reported_supply: 600000, locked_supply: 50000, pending_settlement: 5000, last_updated_at: '2026-03-18T11:55:00Z', transfer_count: 82, reconciliation_weight: 1.0 },
        { ledger_name: 'avalanche', reported_supply: 300000, locked_supply: 15000, pending_settlement: 3000, last_updated_at: '2026-03-18T11:56:00Z', transfer_count: 65, reconciliation_weight: 1.0 },
        { ledger_name: 'private-bank-ledger', reported_supply: 165000, locked_supply: 0, pending_settlement: 2000, last_updated_at: '2026-03-18T11:54:00Z', transfer_count: 18, reconciliation_weight: 1.0 }
      ]
    }
  },
  critical_supply_divergence_double_count_risk: {
    label: 'Critical divergence',
    body: {
      asset_id: 'USTB-2026',
      expected_total_supply: 1000000,
      ledgers: [
        { ledger_name: 'ethereum', reported_supply: 740000, locked_supply: 10000, pending_settlement: 45000, last_updated_at: '2026-03-18T11:40:00Z', transfer_count: 125, reconciliation_weight: 1.0 },
        { ledger_name: 'avalanche', reported_supply: 510000, locked_supply: 5000, pending_settlement: 38000, last_updated_at: '2026-03-18T11:42:00Z', transfer_count: 118, reconciliation_weight: 1.0 },
        { ledger_name: 'private-bank-ledger', reported_supply: 210000, locked_supply: 0, pending_settlement: 12000, last_updated_at: '2026-03-18T09:10:00Z', transfer_count: 21, reconciliation_weight: 1.0 }
      ]
    }
  },
  stale_private_ledger_data: {
    label: 'Stale private ledger',
    body: {
      asset_id: 'USTB-2026',
      expected_total_supply: 1000000,
      ledgers: [
        { ledger_name: 'ethereum', reported_supply: 595000, locked_supply: 45000, pending_settlement: 8000, last_updated_at: '2026-03-18T11:57:00Z', transfer_count: 81, reconciliation_weight: 1.0 },
        { ledger_name: 'avalanche', reported_supply: 302000, locked_supply: 12000, pending_settlement: 9000, last_updated_at: '2026-03-18T11:56:00Z', transfer_count: 61, reconciliation_weight: 1.0 },
        { ledger_name: 'private-bank-ledger', reported_supply: 170000, locked_supply: 0, pending_settlement: 2500, last_updated_at: '2026-03-18T08:00:00Z', transfer_count: 15, reconciliation_weight: 1.0 }
      ]
    }
  }
} as const;

const backstopScenarios = {
  high_volatility_alert: {
    label: 'High volatility alert',
    body: {
      asset_id: 'USTB-2026',
      volatility_score: 87,
      cyber_alert_score: 39,
      reconciliation_severity: 44,
      oracle_confidence_score: 52,
      compliance_incident_score: 28,
      current_market_mode: 'normal'
    }
  },
  cyber_triggered_restricted_mode: {
    label: 'Cyber restricted mode',
    body: {
      asset_id: 'USTB-2026',
      volatility_score: 74,
      cyber_alert_score: 84,
      reconciliation_severity: 52,
      oracle_confidence_score: 41,
      compliance_incident_score: 64,
      current_market_mode: 'alert'
    }
  },
  critical_mismatch_paused_bridge: {
    label: 'Pause bridge',
    body: {
      asset_id: 'USTB-2026',
      volatility_score: 71,
      cyber_alert_score: 89,
      reconciliation_severity: 81,
      oracle_confidence_score: 36,
      compliance_incident_score: 74,
      current_market_mode: 'restricted'
    }
  }
} as const;

const incidentScenarios = {
  incident_record_reconciliation_failure: {
    label: 'Reconciliation failure',
    body: {
      event_type: 'reconciliation-failure',
      trigger_source: 'reconciliation-engine',
      related_asset_id: 'USTB-2026',
      affected_assets: ['USTB-2026'],
      affected_ledgers: ['ethereum', 'avalanche', 'private-bank-ledger'],
      severity: 'critical',
      status: 'open',
      summary: 'Critical multi-ledger divergence with duplicate mint risk triggered a bridge pause.',
      metadata: { scenario: 'critical-supply-divergence-double-count-risk', ticket: 'RES-4001' }
    }
  },
  incident_record_market_circuit_breaker: {
    label: 'Market circuit breaker',
    body: {
      event_type: 'market-circuit-breaker',
      trigger_source: 'backstop-engine',
      related_asset_id: 'USTB-2026',
      affected_assets: ['USTB-2026'],
      affected_ledgers: ['ethereum', 'avalanche'],
      severity: 'high',
      status: 'contained',
      summary: 'Cyber alert escalation triggered deterministic circuit breaker safeguards and temporary trading pause.',
      metadata: { scenario: 'cyber-triggered-restricted-mode', ticket: 'RES-4002' }
    }
  }
} as const;

export default function ResilienceOperationsPanel({ apiUrl }: OperationsPanelProps) {
  const { isAuthenticated, user, authHeaders } = usePilotAuth();
  const [reconcileScenario, setReconcileScenario] = useState<keyof typeof reconcileScenarios>('critical_supply_divergence_double_count_risk');
  const [backstopScenario, setBackstopScenario] = useState<keyof typeof backstopScenarios>('critical_mismatch_paused_bridge');
  const [incidentScenario, setIncidentScenario] = useState<keyof typeof incidentScenarios>('incident_record_reconciliation_failure');
  const [reconcileResult, setReconcileResult] = useState('Run reconciliation to compare ledger supplies and detect drift.');
  const [backstopResult, setBackstopResult] = useState('Run backstop evaluation to inspect triggered safeguards.');
  const [incidentResult, setIncidentResult] = useState('Record an incident to append a deterministic resilience ledger entry.');
  const [loading, setLoading] = useState<'reconcile' | 'backstop' | 'incident' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const currentReconcile = useMemo(() => reconcileScenarios[reconcileScenario], [reconcileScenario]);
  const currentBackstop = useMemo(() => backstopScenarios[backstopScenario], [backstopScenario]);
  const currentIncident = useMemo(() => incidentScenarios[incidentScenario], [incidentScenario]);

  async function postJson(path: string, body: unknown) {
    const livePrefix = isAuthenticated && user?.current_workspace?.id ? '/pilot' : '';
    const response = await fetch(`${apiUrl}${livePrefix}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    return response.json();
  }

  async function runReconciliation() {
    setLoading('reconcile');
    setError(null);
    try {
      const result = await postJson('/resilience/reconcile/state', currentReconcile.body);
      setReconcileResult(JSON.stringify(result, null, 2));
      window.dispatchEvent(new Event('pilot-history-refresh'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to run reconciliation.');
    } finally {
      setLoading(null);
    }
  }

  async function runBackstop() {
    setLoading('backstop');
    setError(null);
    try {
      const result = await postJson('/resilience/backstop/evaluate', currentBackstop.body);
      setBackstopResult(JSON.stringify(result, null, 2));
      window.dispatchEvent(new Event('pilot-history-refresh'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to run backstop evaluation.');
    } finally {
      setLoading(null);
    }
  }

  async function recordIncident() {
    setLoading('incident');
    setError(null);
    try {
      const result = await postJson('/resilience/incidents/record', currentIncident.body);
      setIncidentResult(JSON.stringify(result, null, 2));
      window.dispatchEvent(new Event('pilot-history-refresh'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to record incident.');
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="dataCard complianceDemoPanel">
      <div className="sectionHeader compact">
        <div>
          <h3>Resilience Operations</h3>
          <p>Run reconciliation, evaluate backstops, and append resilience incidents from the browser.</p>
        </div>
        <span className="pill">{isAuthenticated && user?.current_workspace ? `Live workspace: ${user.current_workspace.name}` : 'Workspace records'}</span>
      </div>

      <label htmlFor="reconcile-scenario">Reconciliation scenario</label>
      <select id="reconcile-scenario" value={reconcileScenario} onChange={(event) => setReconcileScenario(event.target.value as keyof typeof reconcileScenarios)}>
        {Object.entries(reconcileScenarios).map(([key, scenario]) => (
          <option key={key} value={key}>{scenario.label}</option>
        ))}
      </select>
      <button type="button" onClick={runReconciliation} disabled={loading === 'reconcile'}>
        {loading === 'reconcile' ? 'Running reconciliation…' : 'Run reconciliation'}
      </button>
      <pre>{reconcileResult}</pre>

      <label htmlFor="backstop-scenario">Backstop scenario</label>
      <select id="backstop-scenario" value={backstopScenario} onChange={(event) => setBackstopScenario(event.target.value as keyof typeof backstopScenarios)}>
        {Object.entries(backstopScenarios).map(([key, scenario]) => (
          <option key={key} value={key}>{scenario.label}</option>
        ))}
      </select>
      <button type="button" onClick={runBackstop} disabled={loading === 'backstop'}>
        {loading === 'backstop' ? 'Evaluating backstop…' : 'Run backstop evaluation'}
      </button>
      <pre>{backstopResult}</pre>

      <label htmlFor="incident-scenario">Incident scenario</label>
      <select id="incident-scenario" value={incidentScenario} onChange={(event) => setIncidentScenario(event.target.value as keyof typeof incidentScenarios)}>
        {Object.entries(incidentScenarios).map(([key, scenario]) => (
          <option key={key} value={key}>{scenario.label}</option>
        ))}
      </select>
      <button type="button" onClick={recordIncident} disabled={loading === 'incident'}>
        {loading === 'incident' ? 'Recording incident…' : 'Record incident'}
      </button>
      <pre>{incidentResult}</pre>

      {error ? <p className="statusLine">{error}</p> : null}
      <div className="demoPayload">
        <p className="label">Selected reconciliation payload</p>
        <pre>{JSON.stringify(currentReconcile.body, null, 2)}</pre>
      </div>
    </div>
  );
}
