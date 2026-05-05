'use client';

import { hasLiveTelemetry, hasRealTelemetryBackedChain } from '../workspace-monitoring-truth';
import { useRuntimeSummary } from '../runtime-summary-context';

function formatTimestamp(value: string | null): string {
  if (!value) {
    return 'Unavailable';
  }
  return new Date(value).toLocaleString();
}

function formatStatus(value: string): string {
  return value.replaceAll('_', ' ');
}

function formatWorkspaceCoverage(reportingSystems: number, monitoredSystems: number, protectedAssets: number): string {
  return `${reportingSystems}/${monitoredSystems} reporting systems across ${protectedAssets} protected assets`;
}

export default function RuntimeBanner() {
  const { summary, loading, missingLabel, nextActionLabel, reasonMessageForCode } = useRuntimeSummary();

  if (loading) {
    return null;
  }

  const topReason = summary.continuity_reason_codes?.[0] ?? summary.status_reason;
  const reasonCopy = topReason ? reasonMessageForCode(topReason) : null;
  const healthProvable =
    summary.runtime_status === 'live'
    && summary.monitoring_status === 'live'
    && summary.telemetry_freshness === 'fresh'
    && summary.confidence === 'high'
    && hasLiveTelemetry(summary)
    && hasRealTelemetryBackedChain(summary)
    && !topReason;

  const monitoringStatusCopy = healthProvable ? 'Live' : 'Unverified';
  const freshnessStatusCopy = healthProvable ? 'Fresh' : formatStatus(summary.telemetry_freshness);
  const confidenceStatusCopy = healthProvable ? 'High' : formatStatus(summary.confidence);

  return (
    <section className="runtimeBanner" aria-live="polite">
      <p><strong>Monitoring status:</strong> {monitoringStatusCopy}</p>
      <p><strong>Freshness status:</strong> {freshnessStatusCopy}</p>
      <p><strong>Confidence status:</strong> {confidenceStatusCopy}</p>
      <p><strong>Last telemetry at:</strong> {formatTimestamp(summary.last_telemetry_at)}</p>
      <p><strong>Last heartbeat at:</strong> {formatTimestamp(summary.last_heartbeat_at)}</p>
      <p><strong>Last poll at:</strong> {formatTimestamp(summary.last_poll_at)}</p>
      <p><strong>Workspace coverage:</strong> {formatWorkspaceCoverage(summary.reporting_systems_count, summary.monitored_systems_count, summary.protected_assets_count)}</p>
      <p><strong>Next required action:</strong> {nextActionLabel}</p>
      {reasonCopy ? <p><strong>Current limitation:</strong> {reasonCopy}</p> : null}
      {!healthProvable ? <p><strong>Health proof:</strong> Live/healthy messaging is disabled until telemetry and proof chain are verified.</p> : null}
      {missingLabel && !reasonCopy ? <p><strong>Current limitation:</strong> {missingLabel}</p> : null}
    </section>
  );
}
