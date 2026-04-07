'use client';

import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

export default function MonitoringOverviewPanel() {
  const liveFeed = useLiveWorkspaceFeed();
  const runtime = liveFeed.runtimeStatus;
  const presentation = normalizeMonitoringPresentation(runtime, {
    degraded: liveFeed.degraded,
    offline: liveFeed.offline,
    stale: liveFeed.stale,
  });
  const truthCopy = presentation.status === 'offline'
    ? 'Workspace monitoring offline. Fresh telemetry unavailable until connectivity returns.'
    : presentation.status === 'limited coverage'
      ? 'Limited coverage for this workspace. Verify open alerts and incidents before closing actions.'
      : presentation.status === 'degraded'
        ? 'Coverage degraded. Incident absence does not prove safety.'
        : presentation.status === 'stale'
          ? 'Monitoring data delayed. Await a fresh checkpoint and event updates.'
          : 'Monitoring is live with verified telemetry for this workspace.';

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
        <p className="metricValue">{runtime ? presentation.statusLabel : (liveFeed.offline ? 'OFFLINE' : 'PENDING')}</p>
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
