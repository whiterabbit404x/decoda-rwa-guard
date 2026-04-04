'use client';

import { useEffect, useState } from 'react';

import { monitoringModeLabel, normalizeMonitoringMode, type MonitoringRuntimeStatus } from './monitoring-status-contract';
import { usePilotAuth } from './pilot-auth-context';

export default function MonitoringOverviewPanel({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [summary, setSummary] = useState({ monitoredTargets: 0, activeAlerts: 0, openIncidents: 0, latestCheck: 'n/a', worker: 'unknown' });
  const [liveStatus, setLiveStatus] = useState<MonitoringRuntimeStatus | null>(null);
  const [claim, setClaim] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    const load = async () => {
      const [targetsRes, alertsRes, incidentsRes, statusRes, claimRes] = await Promise.all([
        fetch(`${apiUrl}/monitoring/targets`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/alerts?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/incidents?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/ops/production-claim-validator`, { headers: authHeaders(), cache: 'no-store' }),
      ]);
      const targets = targetsRes.ok ? ((await targetsRes.json()).targets ?? []) : [];
      const alerts = alertsRes.ok ? ((await alertsRes.json()).alerts ?? []) : [];
      const incidents = incidentsRes.ok ? ((await incidentsRes.json()).incidents ?? []) : [];
      const statusPayload = statusRes.ok ? await statusRes.json() as MonitoringRuntimeStatus : null;
      const claimPayload = claimRes.ok ? await claimRes.json() : {};
      const monitored = targets.filter((item: any) => item.monitoring_enabled).length;
      const latest = targets.map((item: any) => item.last_checked_at).filter(Boolean).sort().reverse()[0];
      setSummary({
        monitoredTargets: monitored,
        activeAlerts: alerts.length,
        openIncidents: incidents.length,
        latestCheck: latest ? new Date(latest).toLocaleString() : 'never',
        worker: statusPayload?.checkpoint_age_seconds != null ? `checkpoint age ${statusPayload.checkpoint_age_seconds}s` : 'no checkpoint yet',
      });
      setLiveStatus(statusPayload ? { ...statusPayload, mode: normalizeMonitoringMode(statusPayload.mode) } : null);
      setClaim(claimPayload);
    };
    void load();
  }, [apiUrl]);

  const evidenceState = liveStatus?.recent_evidence_state ?? 'missing';
  const realEventCount = liveStatus?.recent_real_event_count ?? 0;
  const truthfulnessState = liveStatus?.recent_truthfulness_state ?? 'unknown_risk';
  const checkpointAge = liveStatus?.checkpoint_age_seconds ?? null;
  const truthCopy = evidenceState === 'real' && realEventCount > 0 && truthfulnessState !== 'unknown_risk'
    ? 'No confirmed anomaly detected in observed evidence.'
    : evidenceState === 'degraded' || evidenceState === 'failed'
      ? 'Monitoring degraded.'
      : checkpointAge != null && checkpointAge > 900
        ? 'Checkpoint stale. Awaiting live evidence.'
        : 'No real evidence observed yet. Zero alerts is not proof of safety.';
  return <section className="summaryGrid"><article className="metricCard"><p className="metricLabel">Monitored targets</p><p className="metricValue">{summary.monitoredTargets}</p><p className="metricMeta">Targets with automatic monitoring enabled.</p></article><article className="metricCard"><p className="metricLabel">Active alerts</p><p className="metricValue">{summary.activeAlerts}</p><p className="metricMeta">Open alerts from automatic + manual runs.</p></article><article className="metricCard"><p className="metricLabel">Open incidents</p><p className="metricValue">{summary.openIncidents}</p><p className="metricMeta">Incidents requiring triage.</p></article><article className="metricCard"><p className="metricLabel">Latest monitoring check</p><p className="metricValue">{summary.latestCheck}</p><p className="metricMeta">Worker health: {summary.worker}</p></article><article className="metricCard"><p className="metricLabel">Monitoring truth status</p><p className="metricValue">{liveStatus ? monitoringModeLabel(liveStatus.mode) : 'UNKNOWN'}</p><p className="metricMeta">source={liveStatus?.source_type ?? 'unknown'} · block={liveStatus?.latest_processed_block ?? 'n/a'} · lag={liveStatus?.checkpoint_lag_blocks ?? 'n/a'} · age={liveStatus?.checkpoint_age_seconds ?? 'n/a'}s · degraded={liveStatus?.degraded_reason ?? 'none'} · evidence={evidenceState} · real_events={liveStatus?.recent_real_event_count ?? 0} · truth={liveStatus?.recent_truthfulness_state ?? 'unknown_risk'}</p></article><article className="metricCard"><p className="metricLabel">Production claim</p><p className="metricValue">{claim?.status ?? 'unknown'}</p><p className="metricMeta">{claim?.reason ?? truthCopy}</p></article></section>;
}
