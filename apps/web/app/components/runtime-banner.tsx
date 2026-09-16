'use client';

import {
  hasCanonicalLiveCoverage,
  hasLiveTelemetry,
  hasRealTelemetryBackedChain,
  quietMonitoringNote,
  runtimeRequiresOperatorAction,
} from '../workspace-monitoring-truth';
import { isSuccessRuntimeReason, runtimeReasonMessage } from '../runtime-reason-copy';
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

/**
 * How a runtime VALUE reads, not what it says.
 *
 * `neutral` is the default and the fail-closed one: a field with nothing to
 * report, an age that has never happened, a state this mapping does not
 * recognise. Absence of data is never toned as healthy, so an unmapped
 * reading can only ever come out grey — it can never borrow the colour of a
 * runtime that is proven live.
 */
type FieldTone = 'positive' | 'caution' | 'critical' | 'neutral';

/**
 * The tone for a value one of the three derivations above produced.
 *
 * It maps the LABELS those functions return rather than the raw backend
 * fields, so the colour and the words cannot disagree: both come from the
 * same derivation, one step apart. Nothing here decides a runtime state —
 * healthProvable and the derivations above already did that.
 *
 * Everything else is neutral, and that is the fail-closed half: the clock
 * readings, the worker headline, a quiet-workspace note, and every
 * "Waiting for telemetry" / "Unknown" / "Pending evidence" / "Unavailable"
 * verdict. An absence is never toned as a confirmation.
 */
export function runtimeFieldTone(label: string): FieldTone {
  switch (label) {
    // canonical healthy verdicts
    case 'Live':
    case 'Fresh':
    case 'Live coverage current':
    case 'Verified':
      return 'positive';
    // canonical degraded verdicts — a reported condition, not an absence
    case 'Limited coverage':
    case 'Setup required':
    case 'Stale':
    case 'Partial':
      return 'caution';
    case 'Offline':
      return 'critical';
    default:
      return 'neutral';
  }
}

/**
 * One label/value pair in the strip.
 *
 * The tone follows the VALUE by default, so a field cannot be given a colour
 * its own words do not support, and a label added to a derivation without
 * being added to the tone map renders grey rather than green.
 */
type BannerField = { label: string; value: string; tone?: FieldTone; action?: boolean };

function Field({ label, value, tone, action }: BannerField) {
  return (
    <span
      className={`runtimeBannerField${action ? ' runtimeBannerField--action' : ''}`}
      data-tone={tone ?? runtimeFieldTone(value)}
    >
      <span className="runtimeBannerLabel">{label}</span>
      <span className="runtimeBannerValue">{value}</span>
    </span>
  );
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

/**
 * The reason code this banner speaks for: the top continuity reason the backend
 * reported, otherwise its status reason.
 */
export function runtimeBannerTopReason(summary: WorkspaceMonitoringTruth): string | null {
  return summary.continuity_reason_codes?.[0] ?? summary.status_reason;
}

/**
 * The "Next action" value, or null when the field must not render at all.
 *
 * Next action is an operator instruction, so it only appears when there is an
 * instruction to give. On a runtime the canonical facts prove healthy — the backend's
 * own next action is 'monitoring_live', or the generic review-reason-codes
 * placeholder whose only reported reason is a success one — nothing is required, and
 * "Review runtime reason codes" would be a false to-do. Fail-closed: every limited /
 * degraded / offline runtime, and any specific action the backend names (export
 * evidence, open incident, …), keeps its next action. See
 * runtimeRequiresOperatorAction for the verdict itself.
 */
export function runtimeBannerNextAction(
  summary: WorkspaceMonitoringTruth,
  fallbackLabel: string,
): string | null {
  if (!runtimeRequiresOperatorAction(summary)) {
    return null;
  }
  const nextAction = summary.next_required_action;
  return nextAction ? (NEXT_ACTION_LABELS[nextAction] ?? fallbackLabel) : fallbackLabel;
}

/**
 * The "Limitation" value, or null when there is no limitation to report.
 *
 * Three things are never a limitation, and each is dropped rather than reworded:
 *  - a SUCCESS reason ('live_runtime_verified'), which is the backend CONFIRMING the
 *    runtime. Filing it under "Limitation" tells a customer their verified-live
 *    runtime is degraded; Monitoring / Freshness / Confidence already carry the
 *    healthy state, and this banner only renders fields that have something to say.
 *  - a stale-heartbeat reason while the stable RPC polling worker is proven active.
 *  - a stale/cached EVM_RPC_URL connectivity reason, likewise.
 *
 * Everything else reaches the customer unchanged: no genuine reason is suppressed.
 */
export function runtimeBannerLimitation(
  summary: WorkspaceMonitoringTruth,
  reasonMessage: (code: string) => string = runtimeReasonMessage,
): string | null {
  const topReason = runtimeBannerTopReason(summary);
  if (!topReason || topReason === 'summary_unavailable') {
    return null;
  }
  if (isSuccessRuntimeReason(topReason)) {
    return null;
  }
  // Separated worker status: a paused or rate-limited realtime WebSocket worker
  // must never read as a dead worker while the stable RPC polling worker is alive.
  const stablePollingActive = summary.worker_status?.stable_polling?.active ?? false;
  const isHeartbeatStaleReason = topReason === 'stale_heartbeat' || topReason.startsWith('heartbeat_');
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
  if (suppressHeartbeatLimitation || suppressRpcConnectivityLimitation) {
    return null;
  }
  return reasonMessage(topReason);
}

export default function RuntimeBanner() {
  const { summary, loading, nextActionLabel: contextNextActionLabel, reasonMessageForCode } = useRuntimeSummary();

  if (loading) return null;

  const topReason = runtimeBannerTopReason(summary);
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

  const nextActionDisplay = runtimeBannerNextAction(summary, contextNextActionLabel);
  const reasonCopy = runtimeBannerLimitation(summary, reasonMessageForCode);
  const workerStatus = summary.worker_status ?? null;
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
      {/* Three groups, not ten equal readings. An analyst scanning this strip
          is answering three different questions — what does the runtime say,
          when was each lane last seen, and is anything mine to do — and the
          fields were previously one undifferentiated middot-separated run, which
          is what made it read as a log line rather than a status. Every field
          that rendered before still renders, in the same order. */}
      <div className="runtimeBannerGroup" data-group="verdict">
        <Field label="Monitoring" value={monitoringValue} />
        <Field label="Freshness" value={freshnessValue} />
        <Field label="Confidence" value={confidenceValue} />
      </div>
      {/* Three separate proofs, never collapsed into one: heartbeat proves the
          worker is alive, poll proves the monitoring loop ran, telemetry proves
          monitored data actually arrived. They are ages, not verdicts, so they
          stay neutral — an age is never evidence that the runtime is healthy. */}
      <div className="runtimeBannerGroup" data-group="evidence">
        <Field label="Telemetry" value={formatAge(summary.last_telemetry_at)} />
        <Field label="Heartbeat" value={formatAge(summary.last_heartbeat_at)} />
        <Field label="Poll" value={formatAge(summary.last_poll_at)} />
      </div>
      {nextActionDisplay || workerLine || reasonCopy || quietNote ? (
        <div className="runtimeBannerGroup" data-group="operator">
          {nextActionDisplay ? (
            <Field label="Next action" value={nextActionDisplay} action />
          ) : null}
          {workerLine ? (
            <Field label="Workers" value={workerLine} />
          ) : null}
          {reasonCopy ? (
            <Field label="Limitation" value={reasonCopy} tone="caution" />
          ) : null}
          {quietNote ? (
            <Field label="Activity" value={quietNote} />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
