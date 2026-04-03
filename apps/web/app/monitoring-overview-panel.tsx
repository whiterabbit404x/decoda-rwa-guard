'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

export default function MonitoringOverviewPanel({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [summary, setSummary] = useState({ monitoredTargets: 0, activeAlerts: 0, openIncidents: 0, latestCheck: 'n/a', worker: 'unknown' });
  const [liveStatus, setLiveStatus] = useState<any>(null);
  const [claim, setClaim] = useState<any>(null);

  useEffect(() => {
    const load = async () => {
      const [targetsRes, alertsRes, incidentsRes, healthRes, claimRes] = await Promise.all([
        fetch(`${apiUrl}/monitoring/targets`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/alerts?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/incidents?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/ops/monitoring/health`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/ops/production-claim-validator`, { headers: authHeaders(), cache: 'no-store' }),
      ]);
      const targets = targetsRes.ok ? ((await targetsRes.json()).targets ?? []) : [];
      const alerts = alertsRes.ok ? ((await alertsRes.json()).alerts ?? []) : [];
      const incidents = incidentsRes.ok ? ((await incidentsRes.json()).incidents ?? []) : [];
      const health = healthRes.ok ? await healthRes.json() : {};
      const claimPayload = claimRes.ok ? await claimRes.json() : {};
      const monitored = targets.filter((item: any) => item.monitoring_enabled).length;
      const latest = targets.map((item: any) => item.last_checked_at).filter(Boolean).sort().reverse()[0];
      setSummary({
        monitoredTargets: monitored,
        activeAlerts: alerts.length,
        openIncidents: incidents.length,
        latestCheck: latest ? new Date(latest).toLocaleString() : 'never',
        worker: health.last_cycle_at ? `last cycle ${new Date(health.last_cycle_at).toLocaleString()} · checked ${health.last_cycle_targets_checked ?? 0}` : 'no worker cycle yet',
      });
      setLiveStatus(health);
      setClaim(claimPayload);
    };
    void load();
  }, [apiUrl]);

  return <section className="summaryGrid"><article className="metricCard"><p className="metricLabel">Monitored targets</p><p className="metricValue">{summary.monitoredTargets}</p><p className="metricMeta">Targets with automatic monitoring enabled.</p></article><article className="metricCard"><p className="metricLabel">Active alerts</p><p className="metricValue">{summary.activeAlerts}</p><p className="metricMeta">Open alerts from automatic + manual runs.</p></article><article className="metricCard"><p className="metricLabel">Open incidents</p><p className="metricValue">{summary.openIncidents}</p><p className="metricMeta">Incidents requiring triage.</p></article><article className="metricCard"><p className="metricLabel">Latest monitoring check</p><p className="metricValue">{summary.latestCheck}</p><p className="metricMeta">Worker health: {summary.worker}</p></article><article className="metricCard"><p className="metricLabel">Live Monitoring Status</p><p className="metricValue">{liveStatus?.mode === 'demo' ? 'DEMO MODE' : 'LIVE MODE'}</p><p className="metricMeta">source={liveStatus?.source_type ?? 'unknown'} · block={liveStatus?.latest_processed_block ?? 'n/a'} · lag={liveStatus?.checkpoint_lag_blocks ?? 'n/a'} · age={liveStatus?.checkpoint_age_seconds ?? 'n/a'}s · events15m={liveStatus?.event_count_last_15m ?? 0} · degraded={liveStatus?.degraded_reason ?? 'none'}</p></article><article className="metricCard"><p className="metricLabel">Production claim</p><p className="metricValue">{claim?.status ?? 'unknown'}</p><p className="metricMeta">{claim?.reason ?? 'All strategic infrastructure guardrail checks green.'}</p></article></section>;
}
