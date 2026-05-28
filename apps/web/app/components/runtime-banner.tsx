'use client';

import { hasLiveTelemetry, hasRealTelemetryBackedChain } from '../workspace-monitoring-truth';
import { useRuntimeSummary } from '../runtime-summary-context';
import type { WorkspaceMonitoringTruth } from '../workspace-monitoring-truth';

function formatAge(iso: string | null): string {
  if (!iso) return 'never';
  const diffMs = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diffMs / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

type BannerField = { label: string; value: string };

function Field({ label, value }: BannerField) {
  return (
    <span className="runtimeBannerField">
      <span className="runtimeBannerLabel">{label}</span>
      <span className="runtimeBannerValue">{value}</span>
    </span>
  );
}

function Sep() {
  return <span className="runtimeBannerSep" aria-hidden="true">·</span>;
}

function deriveMonitoringLabel(summary: WorkspaceMonitoringTruth, healthProvable: boolean): string {
  if (healthProvable) return 'Live';
  const runtimeApiMissing = summary.status_reason === 'summary_unavailable';
  if (runtimeApiMissing) return 'Setup required';
  if (summary.runtime_status === 'offline' && summary.protected_assets_count === 0 && !summary.last_heartbeat_at) return 'Offline';
  if (!summary.workspace_configured || summary.protected_assets_count === 0) return 'Setup required';
  if (summary.reporting_systems_count === 0) return 'Setup required';
  return 'Limited coverage';
}

function deriveFreshnessLabel(summary: WorkspaceMonitoringTruth, healthProvable: boolean): string {
  if (healthProvable) return 'Fresh';
  if (!summary.last_telemetry_at) return 'Waiting for telemetry';
  if (summary.telemetry_freshness === 'stale') return 'Stale';
  if (summary.telemetry_freshness === 'fresh') return 'Fresh';
  return 'Unknown';
}

function deriveConfidenceLabel(summary: WorkspaceMonitoringTruth, healthProvable: boolean): string {
  if (healthProvable) return 'Verified';
  if (!summary.last_telemetry_at) return 'Pending evidence';
  if (summary.confidence === 'high') return 'Verified';
  if (summary.confidence === 'medium') return 'Partial';
  return 'Unavailable';
}

const NEXT_ACTION_LABELS: Record<string, string> = {
  add_asset: 'Add protected asset',
  verify_asset: 'Verify asset',
  create_monitoring_target: 'Create monitoring target',
  enable_monitored_system: 'Enable monitored system',
  start_simulator_signal: 'Start telemetry signal',
  view_detection: 'Review detections',
  open_incident: 'Open incident',
  export_evidence_package: 'Export evidence',
  resolve_runtime_contradictions: 'Resolve contradictions',
  review_reason_codes: 'Complete setup',
};

export default function RuntimeBanner() {
  const { summary, loading, nextActionLabel: contextNextActionLabel, reasonMessageForCode } = useRuntimeSummary();

  if (loading) return null;

  const topReason = summary.continuity_reason_codes?.[0] ?? summary.status_reason;
  const healthProvable =
    summary.runtime_status === 'live'
    && summary.monitoring_status === 'live'
    && summary.telemetry_freshness === 'fresh'
    && summary.confidence === 'high'
    && hasLiveTelemetry(summary)
    && hasRealTelemetryBackedChain(summary)
    && !topReason;

  const monitoringValue = deriveMonitoringLabel(summary, healthProvable);
  const freshnessValue = deriveFreshnessLabel(summary, healthProvable);
  const confidenceValue = deriveConfidenceLabel(summary, healthProvable);

  const nextAction = summary.next_required_action;
  const nextActionDisplay = nextAction ? (NEXT_ACTION_LABELS[nextAction] ?? contextNextActionLabel) : contextNextActionLabel;

  const reasonCopy = (topReason && topReason !== 'summary_unavailable')
    ? reasonMessageForCode(topReason)
    : null;

  const toneClass = healthProvable
    ? 'runtimeBannerLive'
    : summary.monitoring_status === 'limited'
      ? 'runtimeBannerStale'
      : 'runtimeBannerDead';

  return (
    <section
      className={`runtimeBanner ${toneClass}`}
      aria-label="Monitoring runtime status"
      aria-live="polite"
    >
      <Field label="Monitoring" value={monitoringValue} />
      <Sep />
      <Field label="Freshness" value={freshnessValue} />
      <Sep />
      <Field label="Confidence" value={confidenceValue} />
      <Sep />
      <Field label="Telemetry" value={formatAge(summary.last_telemetry_at)} />
      <Sep />
      <Field label="Heartbeat" value={formatAge(summary.last_heartbeat_at)} />
      <Sep />
      <Field label="Poll" value={formatAge(summary.last_poll_at)} />
      <Sep />
      <Field label="Next action" value={nextActionDisplay} />
      {reasonCopy ? (
        <>
          <Sep />
          <Field label="Limitation" value={reasonCopy} />
        </>
      ) : null}
    </section>
  );
}
