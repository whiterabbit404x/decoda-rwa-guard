'use client';

import { monitoringModeLabel } from './monitoring-status-contract';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

export default function MonitoringOverviewPanel() {
  const liveFeed = useLiveWorkspaceFeed();
  const runtime = liveFeed.runtimeStatus;
  const evidenceState = runtime?.recent_evidence_state ?? 'missing';
  const truthCopy = liveFeed.offline
    ? 'Telemetry is offline. Treat this workspace as unverified until connectivity returns.'
    : liveFeed.degraded || evidenceState === 'degraded' || evidenceState === 'failed'
      ? 'Monitoring is degraded. Incident absence does not prove safety.'
      : liveFeed.stale
        ? 'Evidence is stale. Await fresh checkpoint and event updates.'
        : 'Monitoring is live with current evidence for this workspace.';

  return (
    <section className="summaryGrid">
      <article className="metricCard">
        <p className="metricLabel">Monitored systems</p>
        <p className="metricValue">{liveFeed.loading ? '—' : liveFeed.counts.monitoredTargets}</p>
        <p className="metricMeta">Protected assets with automatic monitoring enabled.</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Alerts for this workspace</p>
        <p className="metricValue">{liveFeed.loading ? '—' : liveFeed.counts.openAlerts}</p>
        <p className="metricMeta">{liveFeed.refreshing ? 'Refreshing…' : 'Open findings requiring investigation.'}</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Incidents affecting this workspace</p>
        <p className="metricValue">{liveFeed.loading ? '—' : liveFeed.counts.openIncidents}</p>
        <p className="metricMeta">Current incidents requiring operator action.</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Monitoring state</p>
        <p className="metricValue">{runtime ? monitoringModeLabel(runtime.mode) : (liveFeed.offline ? 'OFFLINE' : 'PENDING')}</p>
        <p className="metricMeta">{truthCopy}</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Coverage freshness</p>
        <p className="metricValue">{liveFeed.checkpointAgeSeconds != null ? `${liveFeed.checkpointAgeSeconds}s` : 'n/a'}</p>
        <p className="metricMeta">Last updated {liveFeed.lastUpdatedAt ? new Date(liveFeed.lastUpdatedAt).toLocaleString() : 'pending'}.</p>
      </article>
    </section>
  );
}
