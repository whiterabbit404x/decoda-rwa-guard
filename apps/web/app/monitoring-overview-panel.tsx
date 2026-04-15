'use client';

import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import { resolveWorkspaceMonitoringTruthFromSummary } from './workspace-monitoring-truth';

const LIVE_TELEMETRY_INVALIDATING_FLAGS = new Set([
  'offline_with_current_telemetry',
  'telemetry_unavailable_with_timestamp',
  'zero_coverage_with_live_telemetry',
  'poll_without_telemetry_timestamp',
  'heartbeat_without_telemetry_timestamp',
]);

function runtimeStatusAllowsLive(runtimeStatus: string): boolean {
  return runtimeStatus === 'healthy';
}

function formatTelemetryTimestamp(value: string | null): string {
  if (!value) {
    return 'Not available';
  }
  return new Date(value).toLocaleString();
}

export default function MonitoringOverviewPanel() {
  const liveFeed = useLiveWorkspaceFeed();
  const runtime = liveFeed.runtimeStatus;
  const truth = resolveWorkspaceMonitoringTruthFromSummary(runtime?.workspace_monitoring_summary);
  const presentation = normalizeMonitoringPresentation(truth);
  const hasInvalidatingLiveTelemetryContradiction = truth.contradiction_flags.some((flag) => LIVE_TELEMETRY_INVALIDATING_FLAGS.has(flag));
  const showLiveWithVerifiedTelemetry = runtimeStatusAllowsLive(truth.runtime_status)
    && truth.freshness_status === 'fresh'
    && truth.reporting_systems > 0
    && Boolean(truth.last_telemetry_at)
    && !hasInvalidatingLiveTelemetryContradiction;
  const truthCopy = presentation.status === 'offline'
    ? 'Workspace monitoring offline. Fresh telemetry unavailable until connectivity returns.'
    : presentation.status === 'limited coverage'
      ? 'Limited coverage for this workspace. Verify open alerts and incidents before closing actions.'
      : presentation.status === 'degraded'
        ? 'Coverage degraded. Incident absence does not prove safety.'
        : presentation.status === 'stale'
          ? 'Monitoring data delayed. Await fresh telemetry and event updates.'
          : showLiveWithVerifiedTelemetry
            ? 'Monitoring is live with verified telemetry for this workspace.'
            : 'Monitoring is active. Await verified telemetry before making final safety claims.';

  return (
    <section className="summaryGrid">
      <article className="metricCard">
        <p className="metricLabel">Monitored systems</p>
        <p className="metricValue">{liveFeed.loading ? '—' : liveFeed.counts.monitoredSystems}</p>
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
        <p className="metricValue">{runtime ? presentation.statusLabel : 'PENDING'}</p>
        <p className="metricMeta">{truthCopy}</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Coverage freshness</p>
        <p className="metricValue">{truth.freshness_status === 'unavailable' ? 'Unavailable' : truth.freshness_status.toUpperCase()}</p>
        <p className="metricMeta">Last telemetry {formatTelemetryTimestamp(truth.last_telemetry_at)}.</p>
        <p className="metricMeta">
          Last telemetry: {formatTelemetryTimestamp(truth.last_telemetry_at)} · Last heartbeat: {formatTelemetryTimestamp(truth.last_heartbeat_at)} · Last poll: {formatTelemetryTimestamp(truth.last_poll_at)}
        </p>
      </article>
    </section>
  );
}
