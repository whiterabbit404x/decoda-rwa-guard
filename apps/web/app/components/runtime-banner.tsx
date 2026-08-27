'use client';

import {
  hasCanonicalLiveCoverage,
  hasLiveTelemetry,
  hasRealTelemetryBackedChain,
  quietMonitoringNote,
} from '../workspace-monitoring-truth';
import { realtimeWorkerStatusLine } from '../realtime-coverage-status';
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
  if (summary.reporting_systems_count === 0) {
    // Heartbeat present → worker is alive but no transfer events persisted yet.
    // Show 'Limited coverage' rather than 'Setup required' so the UI does not
    // falsely claim the worker is not running when heartbeats are arriving.
    if (summary.last_heartbeat_at) return 'Limited coverage';
    return 'Setup required';
  }
  if (
    summary.runtime_status === 'live'
    || summary.status_reason === 'live_runtime_verified'
    || hasCanonicalLiveCoverage(summary)
  ) return 'Live';
  return 'Limited coverage';
}

function deriveFreshnessLabel(summary: WorkspaceMonitoringTruth, healthProvable: boolean): string {
  if (healthProvable) return 'Fresh';
  // Canonical realtime verdict outranks the summary freshness field, and keeps the
  // two live-evidence kinds apart exactly as the backend does: a matched
  // monitored-wallet transfer inside the window is fresh telemetry, while a healthy
  // near-tip Stream block evaluated against the target proves current COVERAGE and
  // is labelled as such — never as fresh event telemetry, and never as stale.
  const realtime = summary.realtime_ingestion;
  if (realtime?.healthy && realtime.live_security_telemetry_fresh) return 'Fresh';
  if (realtime?.healthy && realtime.live_coverage_fresh) return 'Live coverage current';
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
  diagnose_ingestion: 'Diagnose ingestion',
  open_incident: 'Open incident',
  export_evidence_package: 'Export evidence',
  resolve_runtime_contradictions: 'Resolve contradictions',
  review_reason_codes: 'Complete setup',
};

export default function RuntimeBanner() {
  const { summary, loading, nextActionLabel: contextNextActionLabel, reasonMessageForCode } = useRuntimeSummary();

  if (loading) return null;

  const topReason = summary.continuity_reason_codes?.[0] ?? summary.status_reason;
  // Live/healthy display disabled until telemetry verified
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

  // Separated worker status: a paused or rate-limited realtime WebSocket worker
  // must never read as a dead worker while the stable RPC polling worker is alive.
  const workerStatus = summary.worker_status ?? null;
  const stablePollingActive = workerStatus?.stable_polling?.active ?? false;
  const isHeartbeatStaleReason =
    typeof topReason === 'string' && (topReason === 'stale_heartbeat' || topReason.startsWith('heartbeat_'));
  // Suppress the generic "worker heartbeat is stale" limitation when the stable
  // polling worker is actually active (it would be misleading).
  const suppressHeartbeatLimitation = isHeartbeatStaleReason && stablePollingActive;
  // Fail-closed guard: never show the "Check EVM_RPC_URL connectivity" limitation while
  // stable RPC polling is proven active (fresh heartbeat/poll). The backend now emits a
  // truthful reason in that case, but a stale/cached 'no_fresh_live_coverage_telemetry'
  // must never contradict a live stable-polling worker. The separated worker line still
  // surfaces the truthful stable-polling headline plus the canonical Stream condition.
  const isRpcConnectivityReason = topReason === 'no_fresh_live_coverage_telemetry';
  const suppressRpcConnectivityLimitation = isRpcConnectivityReason && stablePollingActive;
  const suppressLimitation = suppressHeartbeatLimitation || suppressRpcConnectivityLimitation;
  const reasonCopy = (topReason && topReason !== 'summary_unavailable' && !suppressLimitation)
    ? reasonMessageForCode(topReason)
    : null;
  // Worker line: the stable-polling half of the canonical headline plus the
  // canonical QuickNode Stream condition. The legacy realtime WebSocket clause is
  // stripped — WebSocket is intentionally disabled and is not the realtime
  // monitoring path, so a paused WebSocket is never reported as a coverage problem
  // and a stable-polling fallback never claims a WebSocket failure.
  const workerLine = realtimeWorkerStatusLine(workerStatus, summary.realtime_ingestion);
  // Neutral copy for a healthy runtime that simply has nothing to report. Only set
  // when the canonical verdict proves live coverage with no reported reason, so it
  // never appears alongside a genuine limitation.
  const quietNote = reasonCopy ? null : quietMonitoringNote(summary);

  // A runtime the canonical verdict proves is carrying current live coverage is not
  // "stale" — an idle (quiet) workspace must not be toned as a degraded one.
  const toneClass = healthProvable || hasCanonicalLiveCoverage(summary)
    ? 'runtimeBannerLive'
    : summary.monitoring_status === 'offline'
      ? 'runtimeBannerDead'
      : 'runtimeBannerStale';

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
      {workerLine ? (
        <>
          <Sep />
          <Field label="Workers" value={workerLine} />
        </>
      ) : null}
      {reasonCopy ? (
        <>
          <Sep />
          <Field label="Limitation" value={reasonCopy} />
        </>
      ) : null}
      {quietNote ? (
        <>
          <Sep />
          <Field label="Activity" value={quietNote} />
        </>
      ) : null}
    </section>
  );
}
