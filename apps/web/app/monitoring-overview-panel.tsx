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
      ? 'Monitoring is degraded. Do not infer safety from low alert volume.'
      : liveFeed.stale
        ? 'Evidence is stale. Await fresh checkpoint and event updates.'
        : 'Monitoring is live with current evidence for this workspace.';

  return (
    <section className="summaryGrid">
      <article className="metricCard">
        <p className="metricLabel">Monitored targets in this workspace</p>
        <p className="metricValue">{liveFeed.loading ? '—' : liveFeed.counts.monitoredTargets}</p>
        <p className="metricMeta">{liveFeed.refreshing ? 'Refreshing…' : 'Protected systems with automatic monitoring enabled.'}</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Open alerts</p>
        <p className="metricValue">{liveFeed.loading ? '—' : liveFeed.counts.openAlerts}</p>
        <p className="metricMeta">Alerts for this workspace.</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Open incidents</p>
        <p className="metricValue">{liveFeed.loading ? '—' : liveFeed.counts.openIncidents}</p>
        <p className="metricMeta">Incidents requiring operator action.</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Workspace monitoring state</p>
        <p className="metricValue">{runtime ? monitoringModeLabel(runtime.mode) : (liveFeed.offline ? 'OFFLINE' : 'PENDING')}</p>
        <p className="metricMeta">{truthCopy}</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Evidence freshness</p>
        <p className="metricValue">{liveFeed.checkpointAgeSeconds != null ? `${liveFeed.checkpointAgeSeconds}s` : 'n/a'}</p>
        <p className="metricMeta">Last updated {liveFeed.lastUpdatedAt ? new Date(liveFeed.lastUpdatedAt).toLocaleString() : 'pending'}.</p>
      </article>
    </section>
  );
}

