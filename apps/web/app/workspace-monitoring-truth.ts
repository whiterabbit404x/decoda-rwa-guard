import type { MonitoringRuntimeStatus, WorkspaceMonitoringSummary, WorkerStatusSummary } from './monitoring-status-contract';
import {
  realtimeLaneFaulted,
  realtimeLiveCoverageFresh,
  resolveRealtimeCoverageFacts,
  type RealtimeCoverageFacts,
} from './realtime-coverage-status';

export type WorkspaceMonitoringTruth = {
  workspace_slug: string | null;
  workspace_name: string | null;
  workspace_configured: boolean;
  runtime_status: 'live' | 'degraded' | 'offline' | 'idle';
  monitoring_status: 'live' | 'limited' | 'offline';
  monitoring_mode?: 'live' | 'hybrid' | 'simulator' | 'offline' | 'unavailable';
  configured_systems?: number;
  monitored_systems_count: number;
  reporting_systems_count: number;
  protected_assets_count: number;
  telemetry_freshness: 'fresh' | 'stale' | 'unavailable';
  confidence: 'high' | 'medium' | 'low' | 'unavailable';
  last_poll_at: string | null;
  last_heartbeat_at: string | null;
  last_telemetry_at: string | null;
  last_coverage_telemetry_at?: string | null;
  telemetry_kind?: 'coverage' | 'target_event' | null;
  last_detection_at?: string | null;
  active_alerts_count: number;
  active_incidents_count: number;
  evidence_source_summary: 'live' | 'simulator' | 'replay' | 'none';
  continuity_status: 'continuous_live' | 'continuous_no_evidence' | 'degraded' | 'offline' | 'idle_no_telemetry';
  continuity_reason_codes: string[];
  status_reason: string | null;
  db_failure_classification?: string | null;
  db_failure_reason?: string | null;
  contradiction_flags: string[];
  guard_flags: string[];
  reason_codes: string[];
  next_required_action?: string;
  current_step?: string;
  workflow_steps?: unknown[];
  worker_status?: WorkerStatusSummary | null;
  realtime_enabled?: boolean;
  // Canonical QuickNode Stream ingestion verdict from the runtime-status response.
  // null means the backend did not report one — never "healthy".
  realtime_ingestion?: RealtimeCoverageFacts | null;
};

const DEFAULT_TRUTH: WorkspaceMonitoringTruth = {
  workspace_slug: null,
  workspace_name: null,
  workspace_configured: false,
  runtime_status: 'offline',
  monitoring_status: 'offline',
  monitored_systems_count: 0,
  reporting_systems_count: 0,
  protected_assets_count: 0,
  telemetry_freshness: 'unavailable',
  confidence: 'unavailable',
  last_poll_at: null,
  last_heartbeat_at: null,
  last_telemetry_at: null,
  last_coverage_telemetry_at: null,
  telemetry_kind: null,
  last_detection_at: null,
  active_alerts_count: 0,
  active_incidents_count: 0,
  evidence_source_summary: 'none',
  continuity_status: 'idle_no_telemetry',
  continuity_reason_codes: ['summary_unavailable'],
  status_reason: 'summary_unavailable',
  db_failure_classification: null,
  db_failure_reason: null,
  contradiction_flags: [],
  guard_flags: [],
  reason_codes: [],
  next_required_action: 'review_reason_codes',
  current_step: 'asset_created',
  workflow_steps: [],
  worker_status: null,
  realtime_enabled: false,
  realtime_ingestion: null,
};

function asTrimmedString(value: unknown): string | null {
  const normalized = String(value ?? '').trim();
  return normalized ? normalized : null;
}

function asTimestamp(value: unknown): string | null {
  const normalized = asTrimmedString(value);
  if (!normalized) {
    return null;
  }
  return Number.isFinite(new Date(normalized).getTime()) ? normalized : null;
}

function asCount(value: unknown): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

// The backend emits monitoring status in several canonical words: the runtime
// runner decides 'active' / 'idle' / 'degraded' / 'offline', while the workspace
// summary normalizer emits 'live' / 'limited' / 'offline' and older payloads
// carried 'healthy'. Every one of them is canonical, so all are mapped here.
// 'active' previously fell through to the unknown-value branch and reported
// 'limited', which is what pinned an active runtime to a LIMITED COVERAGE banner.
// 'idle' stays 'limited': idle means no fresh evidence, which is never "live".
function normalizeMonitoringStatus(
  monitoringStatus: unknown,
  runtimeStatus: WorkspaceMonitoringTruth['runtime_status'],
): WorkspaceMonitoringTruth['monitoring_status'] {
  const normalized = String(monitoringStatus ?? '').trim().toLowerCase();
  if (normalized === 'live' || normalized === 'limited' || normalized === 'offline') {
    return normalized;
  }
  if (normalized === 'healthy' || normalized === 'active') {
    return 'live';
  }
  if (
    normalized === 'degraded'
    || normalized === 'idle'
    || normalized === 'not_configured'
    || normalized === 'unknown'
  ) {
    return 'limited';
  }
  return runtimeStatus === 'offline' ? 'offline' : 'limited';
}

export function resolveWorkspaceMonitoringTruthFromSummary(summary: WorkspaceMonitoringSummary | null | undefined): WorkspaceMonitoringTruth {
  if (!summary) {
    return DEFAULT_TRUTH;
  }
  const runtimeStatus = summary.runtime_status;
  const telemetryFreshness = summary.telemetry_freshness;
  const confidence = summary.confidence;
  // The API substitutes the literal 'unknown' whenever the backend reported no
  // status reason at all (`str(status_reason or 'unknown')`). That placeholder is
  // the absence of a reason, not a degraded condition, so it must never be
  // rendered to a customer as one.
  const rawStatusReason = asTrimmedString(summary.status_reason);
  const resolvedStatusReason = rawStatusReason && rawStatusReason.toLowerCase() === 'unknown'
    ? null
    : rawStatusReason;
  const dbFailureClassification = asTrimmedString((summary as Record<string, unknown>).db_failure_classification);
  const dbFailureReason = asTrimmedString((summary as Record<string, unknown>).db_failure_reason)
    ?? (resolvedStatusReason && resolvedStatusReason.toLowerCase().includes('database') ? resolvedStatusReason : null);
  const lastCoverageTelemetryAt = asTimestamp((summary as Record<string, unknown>).last_coverage_telemetry_at);
  // Canonical QuickNode Stream verdict. When it proves realtime live evidence
  // arrived inside the freshness window, a missing raw timestamp in the flat
  // runtime payload is a serialization gap, not a runtime contradiction — so the
  // timestamp guards below stop firing on it. Nothing else is suppressed: an
  // unhealthy or absent verdict leaves every guard exactly as it was.
  const realtimeIngestionFacts = resolveRealtimeCoverageFacts(
    (summary as Record<string, unknown>).realtime_ingestion,
  );
  const realtimeCoverageProven = realtimeLiveCoverageFresh(realtimeIngestionFacts);
  const telemetryKind = null;
  const lastTelemetryAt = asTimestamp(summary.last_telemetry_at);
  const lastDetectionAt = asTimestamp(summary.last_detection_at);
  const reportingSystemsCount = asCount(summary.reporting_systems_count ?? (summary as Record<string, unknown>).reporting_systems);
  const monitoredSystemsCount = asCount(summary.monitored_systems_count ?? (summary as Record<string, unknown>).configured_systems);
  const protectedAssetsCount = asCount(summary.protected_assets_count ?? (summary as Record<string, unknown>).protected_assets);
  const lastHeartbeatAt = asTimestamp(summary.last_heartbeat_at);
  const lastPollAt = asTimestamp(summary.last_poll_at);
  const evidenceSourceSummary = summary.evidence_source_summary;
  const contradictionFlags = Array.isArray(summary.contradiction_flags)
    ? (summary.contradiction_flags as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  const workspaceConfigured = Boolean(summary.workspace_configured);
  const runtimeStatusLabel = String(runtimeStatus ?? '').trim().toLowerCase();
  // 'healthy' and 'active' are the backend's own words for a running runtime
  // (runtime_status_summary vs. the runner's capitalized decision).
  const normalizedRuntimeStatus = runtimeStatusLabel === 'healthy' || runtimeStatusLabel === 'active'
    ? 'live'
    : runtimeStatusLabel;
  const rawMonitoringStatus = normalizeMonitoringStatus(
    summary.monitoring_status ?? (summary as Record<string, unknown>).monitoring_mode,
    normalizedRuntimeStatus as WorkspaceMonitoringTruth['runtime_status'],
  );
  // When the backend has authoritatively verified live runtime, trust it — the flat
  // response shape may omit monitoring_status, which would otherwise default to 'limited'.
  const normalizedMonitoringStatus: WorkspaceMonitoringTruth['monitoring_status'] =
    resolvedStatusReason === 'live_runtime_verified' && normalizedRuntimeStatus === 'live'
      ? 'live'
      : rawMonitoringStatus;
  const normalizedTelemetryFreshness = telemetryFreshness === 'fresh' || telemetryFreshness === 'stale' || telemetryFreshness === 'unavailable'
    ? telemetryFreshness
    : ((summary as Record<string, unknown>).freshness_status as WorkspaceMonitoringTruth['telemetry_freshness']) ?? 'unavailable';
  const normalizedConfidence = confidence === 'high' || confidence === 'medium' || confidence === 'low' || confidence === 'unavailable'
    ? confidence
    : ((summary as Record<string, unknown>).confidence_status as WorkspaceMonitoringTruth['confidence']) ?? 'unavailable';
  // The summary normalizer spells a live provider source 'live_provider'; the flat
  // runtime payload spells the same fact 'live'. Both mean chosen_evidence_source=live.
  const canonicalEvidenceSource = (value: unknown): WorkspaceMonitoringTruth['evidence_source_summary'] | null => {
    const normalized = String(value ?? '').trim().toLowerCase();
    if (normalized === 'live' || normalized === 'live_provider') return 'live';
    if (normalized === 'simulator' || normalized === 'replay' || normalized === 'none') return normalized;
    return null;
  };
  const normalizedEvidenceSource = canonicalEvidenceSource(evidenceSourceSummary)
    ?? canonicalEvidenceSource((summary as Record<string, unknown>).evidence_source)
    ?? 'none';
  const continuityStatusValue = asTrimmedString((summary as Record<string, unknown>).continuity_status);
  const continuityStatus = continuityStatusValue === 'continuous_live'
    || continuityStatusValue === 'continuous_no_evidence'
    || continuityStatusValue === 'degraded'
    || continuityStatusValue === 'offline'
    || continuityStatusValue === 'idle_no_telemetry'
    ? continuityStatusValue
    : 'idle_no_telemetry';
  const continuityReasonCodes = Array.isArray((summary as Record<string, unknown>).continuity_reason_codes)
    ? ((summary as Record<string, unknown>).continuity_reason_codes as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  // Hard guard priority order: top entry wins for status_reason override.
  const GUARD_PRIORITY = [
    'offline_with_current_telemetry',
    'idle_runtime_with_active_monitoring_claim',
    'live_monitoring_without_reporting_systems',
    'live_telemetry_verified_without_timestamp',
    'telemetry_unavailable_with_high_confidence',
    'workspace_unconfigured_with_coverage',
    'workspace_configured_missing_required_links',
    'heartbeat_without_telemetry_timestamp',
    'poll_without_telemetry_timestamp',
    'coverage_only_persistent_no_evidence',
  ] as const;

  const derivedContradictionFlags = [...contradictionFlags];
  const derivedHardGuards: string[] = [];

  function addGuard(flag: string) {
    derivedContradictionFlags.push(flag);
    derivedHardGuards.push(flag);
  }

  // runtime offline but telemetry claims current/fresh
  if (normalizedRuntimeStatus === 'offline' && (lastTelemetryAt !== null || normalizedTelemetryFreshness === 'fresh')) {
    addGuard('offline_with_current_telemetry');
  }

  // idle runtime with active monitoring claims (skip when caller already explains why via degraded reason)
  if (
    normalizedRuntimeStatus === 'idle' &&
    !resolvedStatusReason?.startsWith('runtime_status_degraded') &&
    (normalizedTelemetryFreshness === 'fresh' || lastTelemetryAt !== null || lastCoverageTelemetryAt !== null)
  ) {
    addGuard('idle_runtime_with_active_monitoring_claim');
  }

  // no reporting systems but runtime/freshness claims coverage
  // Suppress this guard when the backend has already verified the live state authoritatively.
  if (
    resolvedStatusReason !== 'live_runtime_verified' &&
    reportingSystemsCount === 0 &&
    (normalizedRuntimeStatus === 'live' || normalizedTelemetryFreshness === 'fresh')
  ) {
    addGuard('live_monitoring_without_reporting_systems');
  }

  // claims fresh telemetry quality but no timestamp exists
  // Suppress when backend has explicitly verified live runtime (it may not return raw timestamps).
  if (
    resolvedStatusReason !== 'live_runtime_verified' &&
    !realtimeCoverageProven &&
    normalizedTelemetryFreshness === 'fresh' &&
    lastTelemetryAt === null &&
    lastCoverageTelemetryAt === null
  ) {
    addGuard('live_telemetry_verified_without_timestamp');
  }

  // telemetry unavailable but confidence claims high
  if (normalizedTelemetryFreshness === 'unavailable' && normalizedConfidence === 'high') {
    addGuard('telemetry_unavailable_with_high_confidence');
  }

  // workspace not configured but systems or assets exist
  // Suppress when backend has authoritatively verified live runtime — the backend owns
  // workspace_configured state and may not echo it in every flat response shape.
  if (
    resolvedStatusReason !== 'live_runtime_verified' &&
    !workspaceConfigured &&
    (monitoredSystemsCount > 0 || protectedAssetsCount > 0)
  ) {
    addGuard('workspace_unconfigured_with_coverage');
  }

  // configured workspace but persisted enabled config count is explicitly 0
  const persistedEnabledConfigCount = (summary as Record<string, unknown>).persisted_enabled_config_count;
  const validProtectedAssetCountForGuard = Number((summary as Record<string, unknown>).valid_protected_asset_count ?? 0);
  if (workspaceConfigured && persistedEnabledConfigCount === 0 && validProtectedAssetCountForGuard > 0) {
    addGuard('workspace_configured_missing_required_links');
  }

  // heartbeat exists but no telemetry and no poll (poll guard below is more specific)
  // Suppress when backend has authoritatively verified live runtime — it may not return raw timestamps.
  if (
    resolvedStatusReason !== 'live_runtime_verified' &&
    !realtimeCoverageProven &&
    lastHeartbeatAt !== null && lastTelemetryAt === null && lastCoverageTelemetryAt === null && lastPollAt === null
  ) {
    addGuard('heartbeat_without_telemetry_timestamp');
  }

  // poll ran but no telemetry arrived
  // Suppress when backend has authoritatively verified live runtime — it may not return raw timestamps.
  if (
    resolvedStatusReason !== 'live_runtime_verified' &&
    !realtimeCoverageProven &&
    lastPollAt !== null && lastTelemetryAt === null && lastCoverageTelemetryAt === null
  ) {
    addGuard('poll_without_telemetry_timestamp');
  }

  // coverage-only persistent no-evidence from continuity signals or explicit status_reason
  if (
    continuityReasonCodes.includes('coverage_only_persistent_no_evidence') ||
    resolvedStatusReason === 'coverage_only_persistent_no_evidence'
  ) {
    addGuard('coverage_only_persistent_no_evidence');
  }

  const normalizedContradictionFlags = [...new Set(derivedContradictionFlags)].sort();

  const declaredGuardFlags = Array.isArray(summary.guard_flags)
    ? (summary.guard_flags as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  const normalizedGuardFlags = [...new Set([...declaredGuardFlags, ...derivedHardGuards])].sort();

  // Derive guard-priority status_reason: top-priority fired guard wins over null input reason.
  const topFiredGuard = GUARD_PRIORITY.find((g) => derivedHardGuards.includes(g));
  const guardStatusReason = topFiredGuard ? `guard:${topFiredGuard}` : null;
  const reasonCodes = Array.isArray((summary as Record<string, unknown>).reason_codes)
    ? ((summary as Record<string, unknown>).reason_codes as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  const normalizedReasonCodes = [...new Set(reasonCodes)].sort();
  return {
    workspace_slug: null,
    workspace_name: null,
    workspace_configured: workspaceConfigured,
    runtime_status: normalizedRuntimeStatus as WorkspaceMonitoringTruth['runtime_status'],
    monitoring_status: normalizedMonitoringStatus,
    monitored_systems_count: monitoredSystemsCount,
    reporting_systems_count: reportingSystemsCount,
    protected_assets_count: protectedAssetsCount,
    telemetry_freshness: normalizedTelemetryFreshness,
    confidence: normalizedConfidence,
    last_poll_at: lastPollAt,
    last_heartbeat_at: lastHeartbeatAt,
    last_telemetry_at: lastTelemetryAt,
    last_coverage_telemetry_at: lastCoverageTelemetryAt,
    telemetry_kind: telemetryKind,
    last_detection_at: lastDetectionAt,
    active_alerts_count: Number(summary.active_alerts_count ?? 0),
    active_incidents_count: Number(summary.active_incidents_count ?? 0),
    evidence_source_summary: normalizedEvidenceSource,
    continuity_status: continuityStatus,
    continuity_reason_codes: continuityReasonCodes,
    status_reason: guardStatusReason ?? resolvedStatusReason,
    db_failure_classification: dbFailureClassification,
    db_failure_reason: dbFailureReason,
    contradiction_flags: normalizedContradictionFlags,
    guard_flags: normalizedGuardFlags,
    reason_codes: normalizedReasonCodes,
    realtime_ingestion: realtimeIngestionFacts,
    next_required_action: asTrimmedString((summary as Record<string, unknown>).next_required_action) ?? 'review_reason_codes',
    current_step: asTrimmedString((summary as Record<string, unknown>).current_step) ?? 'asset_created',
    workflow_steps: Array.isArray((summary as Record<string, unknown>).workflow_steps) ? ((summary as Record<string, unknown>).workflow_steps as unknown[]) : [],
  };
}

export function resolveWorkspaceMonitoringTruth(status: MonitoringRuntimeStatus | null): WorkspaceMonitoringTruth {
  // The production /ops/monitoring/runtime-status endpoint returns top-level fields directly
  // (no nested workspace_monitoring_summary). Fall back to the flat status object so all fields
  // (protected_assets, reporting_systems, last_poll_at, contradiction_flags, etc.) are read.
  const summarySource = status?.workspace_monitoring_summary ?? (status as unknown as WorkspaceMonitoringSummary | null);
  const truth = resolveWorkspaceMonitoringTruthFromSummary(summarySource);
  const workspaceRecord = (status as Record<string, unknown> | null)?.workspace;
  const workspaceName = workspaceRecord && typeof workspaceRecord === 'object'
    ? asTrimmedString((workspaceRecord as Record<string, unknown>).name)
    : null;
  const statusRecord = status as Record<string, unknown> | null;
  // worker_status is a top-level canonical field (not nested in the summary).
  const workerStatusRaw = statusRecord?.worker_status;
  const workerStatus = (workerStatusRaw && typeof workerStatusRaw === 'object')
    ? (workerStatusRaw as WorkerStatusSummary)
    : null;
  // Top-level realtime_ingestion is the canonical production shape; the nested
  // summary copy (already resolved above) is the fallback.
  const topLevelRealtimeIngestion = resolveRealtimeCoverageFacts(statusRecord?.realtime_ingestion);
  return {
    ...truth,
    realtime_ingestion: topLevelRealtimeIngestion ?? truth.realtime_ingestion ?? null,
    next_required_action: asTrimmedString(statusRecord?.next_required_action) ?? truth.next_required_action,
    current_step: asTrimmedString(statusRecord?.current_step) ?? truth.current_step,
    workflow_steps: Array.isArray(statusRecord?.workflow_steps) ? (statusRecord?.workflow_steps as unknown[]) : truth.workflow_steps,
    workspace_slug: asTrimmedString(statusRecord?.workspace_slug),
    workspace_name: workspaceName ?? asTrimmedString(statusRecord?.workspace_name),
    worker_status: workerStatus,
    realtime_enabled: Boolean(statusRecord?.realtime_enabled),
  };
}

/**
 * Canonical live-coverage verdict, read straight from the runtime-status response:
 * the backend's QuickNode Stream realtime ingestion is healthy with live evidence
 * inside the freshness window, the live evidence source is the chosen one, and the
 * backend reports NO status reason, NO contradiction, and NO database failure.
 *
 * Why the summary `telemetry_freshness` field is not required to be 'fresh' here:
 * that field is derived from the reconciliation RPC poll's own coverage timestamp,
 * on a 900s cadence. The realtime Stream lane is a separate, faster proof, and
 * CLAUDE.md keeps heartbeat / poll / telemetry as separate proofs precisely so a
 * quiet RPC poll can never be read as "live telemetry is stale" while the Stream is
 * demonstrably delivering. The canonical realtime verdict is the authority on
 * whether live coverage is current; the poll-based field is not.
 *
 * This is a READ of canonical backend facts, never a frontend-manufactured healthy
 * state. Every one of these must hold, so a single genuine degraded condition — any
 * reported status reason, contradiction, guard flag, non-live evidence source, an
 * offline runtime, an unhealthy or absent realtime verdict — returns false and the
 * warning surfaces stay up.
 */
export function hasCanonicalLiveCoverage(truth: WorkspaceMonitoringTruth): boolean {
  if (!realtimeLiveCoverageFresh(truth.realtime_ingestion)) {
    return false;
  }
  // The backend must be reporting no problem at all. Any real status reason —
  // a coverage gap, a proof-chain break, a blocked target — keeps the warning up.
  const reason = truth.status_reason;
  if (reason !== null && reason !== 'live_runtime_verified') {
    return false;
  }
  return truth.workspace_configured
    && truth.monitoring_status !== 'offline'
    && truth.runtime_status !== 'offline'
    && truth.evidence_source_summary === 'live'
    && truth.protected_assets_count > 0
    && truth.reporting_systems_count > 0
    && !truth.db_failure_reason
    && (truth.guard_flags ?? []).length === 0
    && (truth.contradiction_flags ?? []).length === 0;
}

/**
 * True when the canonical realtime verdict proves live evidence arrived inside the
 * freshness window. A surface that reads this must not claim live telemetry is
 * stale — the backend has said the opposite.
 */
export function hasFreshRealtimeLiveEvidence(truth: WorkspaceMonitoringTruth): boolean {
  return realtimeLiveCoverageFresh(truth.realtime_ingestion);
}

/**
 * True when the canonical verdict reports an active fault on the primary realtime
 * (QuickNode Stream) path. A faulted stream lane is a genuine degraded condition,
 * so it must keep the warning surfaces up even when the runtime status field alone
 * still reads live.
 */
export function hasFaultedRealtimeLane(truth: WorkspaceMonitoringTruth): boolean {
  return realtimeLaneFaulted(truth.realtime_ingestion);
}

export function hasLiveTelemetry(truth: WorkspaceMonitoringTruth): boolean {
  const telemetryTimestamp = truth.last_telemetry_at ?? truth.last_coverage_telemetry_at ?? null;
  const monitoringStatus = truth.monitoring_status ?? (truth.runtime_status === 'live' ? 'live' : 'limited');
  return truth.runtime_status === 'live'
    && truth.workspace_configured
    && monitoringStatus === 'live'
    && truth.evidence_source_summary === 'live'
    && truth.telemetry_freshness === 'fresh'
    && truth.confidence !== 'unavailable'
    && truth.reporting_systems_count > 0
    && Boolean(telemetryTimestamp)
    && (truth.guard_flags ?? []).length === 0
    && !truth.db_failure_reason
    && (truth.contradiction_flags ?? []).length === 0;
}

export function hasRealTelemetryBackedChain(truth: WorkspaceMonitoringTruth): boolean {
  const continuityIsLive = truth.continuity_status === 'continuous_live';
  return hasLiveTelemetry(truth)
    && continuityIsLive
    && (truth.guard_flags ?? []).length === 0
    && (truth.contradiction_flags ?? []).length === 0
    && !truth.db_failure_reason;
}

export function monitoringHealthyCopyAllowed(truth: WorkspaceMonitoringTruth): boolean {
  const monitoringStatus = truth.monitoring_status ?? (truth.runtime_status === 'live' ? 'live' : 'limited');
  return truth.runtime_status === 'live'
    && monitoringStatus === 'live'
    && truth.reporting_systems_count > 0
    && hasLiveTelemetry(truth)
    && truth.continuity_status === 'continuous_live'
    && (truth.guard_flags ?? []).length === 0
    && (truth.contradiction_flags ?? []).length === 0
    && !truth.db_failure_reason;
}
