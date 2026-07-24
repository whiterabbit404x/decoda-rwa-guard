// Presentation helpers for the Protected Asset Registry (Screen 3).
//
// Pure, framework-free mappers so risk badges, monitoring-health pills, RWA type
// labels, and reserve-status copy stay consistent between the table, the details
// drawer, and the AI Asset Risk Assessor panel — and are unit-testable without a
// browser. Nothing here invents data; callers pass canonical backend values.

import type { PillVariant } from './components/ui-primitives';

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical' | 'unassessed';

// Canonical 0-100 scale where HIGHER means MORE risk (see backend asset-risk-v1).
export const RISK_SCORE_TOOLTIP =
  'Higher values represent greater operational, reserve, market, and monitoring risk. ' +
  'Scored 0-100: 0-29 low, 30-59 medium, 60-79 high, 80-100 critical.';

export function riskLevelForScore(score: number | null | undefined): RiskLevel {
  if (score === null || score === undefined || Number.isNaN(Number(score))) return 'unassessed';
  const n = Number(score);
  if (n >= 80) return 'critical';
  if (n >= 60) return 'high';
  if (n >= 30) return 'medium';
  return 'low';
}

export function riskLevelLabel(level: RiskLevel): string {
  switch (level) {
    case 'critical': return 'Critical';
    case 'high': return 'High';
    case 'medium': return 'Medium';
    case 'low': return 'Low';
    default: return 'Not assessed';
  }
}

export function riskLevelVariant(level: RiskLevel): PillVariant {
  switch (level) {
    case 'critical':
    case 'high': return 'danger';
    case 'medium': return 'warning';
    case 'low': return 'success';
    default: return 'neutral';
  }
}

// Truthful monitoring-health states. "healthy" is only shown when the backend
// says so; every other state fails toward caution.
export type MonitoringHealth =
  | 'healthy' | 'warning' | 'critical' | 'degraded' | 'provisioning' | 'not_configured' | 'unknown';

export function monitoringHealthLabel(health: string | null | undefined): string {
  switch ((health || '').toLowerCase()) {
    case 'healthy': return 'Healthy';
    case 'warning': return 'Warning';
    case 'critical': return 'Critical';
    case 'degraded': return 'Degraded';
    case 'provisioning': return 'Provisioning';
    case 'not_configured': return 'Not configured';
    default: return 'Unknown';
  }
}

export function monitoringHealthVariant(health: string | null | undefined): PillVariant {
  switch ((health || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'warning':
    case 'degraded': return 'warning';
    case 'critical': return 'danger';
    case 'provisioning': return 'info';
    default: return 'neutral';
  }
}

export function reserveStatusLabel(status: string | null | undefined): string {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'Healthy';
    case 'warning': return 'Warning';
    case 'critical': return 'Critical';
    case 'over_collateralized': return 'Over-collateralized';
    case 'insufficient_evidence': return 'Insufficient evidence';
    case 'not_configured': return 'Not configured';
    case 'not_applicable':
    case 'not_required': return 'Not applicable';
    default: return 'Unknown';
  }
}

export function reserveStatusVariant(status: string | null | undefined): PillVariant {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'warning':
    case 'over_collateralized': return 'warning';
    case 'critical': return 'danger';
    case 'insufficient_evidence': return 'neutral';
    case 'not_configured': return 'neutral';
    case 'not_applicable':
    case 'not_required': return 'info';
    default: return 'neutral';
  }
}

// Human-readable message for the reserve-coverage state on the AI panel. Uses
// structured values only — never invents a percentage.
export function reserveCoverageMessage(status: string | null | undefined, reserveBackedCount = 0): string {
  switch ((status || '').toLowerCase()) {
    case 'not_configured':
      return reserveBackedCount > 0
        ? 'Reserve coverage cannot be verified for the current asset set.'
        : 'No reserve-backed assets are configured.';
    case 'insufficient_evidence':
      return 'Reserve-backed assets exist but have no verified reserve evidence.';
    case 'not_applicable':
    case 'not_required':
      return 'Reserve coverage does not apply to the current asset set.';
    case 'critical':
      return 'Aggregate reserve coverage is below the required minimum.';
    case 'warning':
      return 'Aggregate reserve coverage is slightly below target.';
    default:
      return '';
  }
}

// Assessment lifecycle status (workspace rollup or per-asset).
export type AssessmentStatus =
  | 'not_started' | 'not_assessed' | 'queued' | 'running' | 'complete' | 'completed'
  | 'partial' | 'degraded' | 'failed' | 'blocked' | 'stale';

export function assessmentStatusLabel(status: string | null | undefined): string {
  switch ((status || '').toLowerCase()) {
    case 'not_started':
    case 'not_assessed': return 'Not started';
    case 'queued': return 'Queued';
    case 'running': return 'Running';
    case 'complete':
    case 'completed': return 'Complete';
    case 'partial': return 'Partial';
    case 'degraded': return 'Degraded';
    case 'failed': return 'Failed';
    case 'blocked': return 'Blocked';
    case 'stale': return 'Stale';
    default: return 'Unknown';
  }
}

// Accessible description distinguishing assessment EXECUTION status ("Complete" =
// the run finished) from a monitoring CONDITION ("Critical" = the resulting state
// needs attention). These are different axes and must never be conflated: a
// successfully-completed assessment stays "Complete" even when its result is high
// or critical risk.
export function assessmentStatusTooltip(status: string | null | undefined): string {
  switch ((status || '').toLowerCase()) {
    case 'complete':
    case 'completed': return 'The assessment finished successfully.';
    case 'partial':
    case 'degraded': return 'The assessment finished, but some evidence was incomplete or stale.';
    case 'running': return 'An assessment is currently running.';
    case 'queued': return 'An assessment is queued to run.';
    case 'failed': return 'The assessment did not finish. Retry to run it again.';
    case 'blocked': return 'The assessment cannot run — no execution path is available.';
    case 'stale': return 'The last assessment is older than its freshness window.';
    case 'not_started':
    case 'not_assessed': return 'No assessment has run for this asset yet.';
    default: return 'Assessment execution status.';
  }
}

export function assessmentStatusVariant(status: string | null | undefined): PillVariant {
  switch ((status || '').toLowerCase()) {
    case 'complete':
    case 'completed': return 'success';
    case 'running':
    case 'queued': return 'info';
    case 'partial':
    case 'degraded':
    case 'stale': return 'warning';
    case 'failed':
    case 'blocked': return 'danger';
    default: return 'neutral';
  }
}

// Canonical runtime capability from the API (services/api/.../summary.build_assessment_capability).
// The frontend consumes THIS instead of inferring worker health from a missing
// assessment or an environment variable.
export type ExecutionMode = 'background' | 'on_demand' | 'unavailable';

export type AssessmentCapability = {
  background_enabled: boolean;
  on_demand_enabled: boolean;
  worker_healthy: boolean;
  last_heartbeat_at: string | null;
  execution_mode: ExecutionMode;
  queue_depth?: number;
  oldest_queued_job_age_seconds?: number | null;
  active_job_count?: number;
  blocked_job_count?: number;
  last_successful_assessment_at?: string | null;
  last_assessment_failure?: { code: string | null; message: string | null; at: string | null } | null;
};

export type AssessmentAction = { label: string; disabled: boolean; hint?: string };

// A persisted assessment job (from asset_risk_jobs) — the canonical proof that an
// assessment is actually queued or running. The frontend consumes THIS instead of
// inferring "queued/running" from a capability queue-depth or an in-flight POST.
// null/undefined means no active job exists.
export type AssessmentJob = {
  status?: string | null; // 'queued' | 'running' | 'blocked' | ...
  job_id?: string | null;
  asset_id?: string | null;
} | null;

// The single canonical display state for an asset/workspace assessment. Both the
// status pill AND the Run button are derived from this one object so the table,
// the details drawer, and the AI panel can never disagree.
export type AssessmentDisplayState = {
  statusLabel: string;
  statusVariant: PillVariant;
  actionLabel: string;
  actionDisabled: boolean;
  actionBusy: boolean;
  hint?: string;
};

const WORKER_UNAVAILABLE_HINT =
  'The background assessor is not running and on-demand assessment is disabled. Enable the Asset Risk Assessor worker or on-demand assessment to run assessments.';

// ── THE canonical assessment display-state selector ───────────────────────────
// Pure. Every assessment surface (asset table cell, details drawer, AI panel)
// derives its status pill and Run button from this one function, so there is
// exactly one place that maps backend facts → UI. It renders ONLY from:
//   * assessmentStatus — the last persisted assessment's status (never queued/running by itself)
//   * activeJob        — a persisted queued/running job (the true "in progress" fact)
//   * capability       — canonical runtime capability (execution_mode)
//   * mutationInFlight — the POST request is actively in flight (transient, local)
//
// Priority (fail-closed):
//   mutationInFlight            → "Starting assessment…"   (button only; pill unchanged)
//   activeJob/status running    → "Assessment running"
//   activeJob/status queued     → "Assessment queued"
//   execution_mode unavailable  → "Worker unavailable"     (cannot start — disabled)
//   no assets                   → "Run assessment"         (disabled)
//   status failed/blocked       → "Retry assessment"
//   status complete/partial/…   → "Run again"
//   execution_mode on_demand    → "Run limited assessment"
//   otherwise                   → "Run assessment"
export function getAssetAssessmentDisplayState(args: {
  assessmentStatus?: string | null;
  activeJob?: AssessmentJob;
  capability?: AssessmentCapability | null;
  mutationInFlight?: boolean;
  hasAssets?: boolean;
}): AssessmentDisplayState {
  const { assessmentStatus, activeJob, capability, mutationInFlight, hasAssets } = args;
  const status = (assessmentStatus || '').toLowerCase();
  const jobStatus = (activeJob?.status || '').toLowerCase();
  const mode = capability?.execution_mode;

  // Persisted active job is the live truth. A queued/running JOB is only ever
  // created behind a healthy worker (background) or run inline (on_demand); a
  // stuck queued job is reconciled to "blocked" server-side, so a surviving
  // "queued" here is genuinely drainable — no extra client-side gate needed.
  const running = jobStatus === 'running' || status === 'running';
  const queued = !running && (jobStatus === 'queued' || status === 'queued');

  // The status PILL reflects a persisted active job when one exists; otherwise the
  // last assessment's status. A mutation in flight does NOT move the pill (no job
  // has been persisted yet) — only the button changes.
  const effectiveStatus = running ? 'running' : queued ? 'queued' : (status || 'not_started');
  const statusLabel = assessmentStatusLabel(effectiveStatus);
  const statusVariant = assessmentStatusVariant(effectiveStatus);

  const out = (actionLabel: string, actionDisabled: boolean, actionBusy = false, hint?: string): AssessmentDisplayState => ({
    statusLabel, statusVariant, actionLabel, actionDisabled, actionBusy, ...(hint ? { hint } : {}),
  });

  if (mutationInFlight) return out('Starting assessment…', true, true);
  if (running) return out('Assessment running', true, true);
  if (queued) return out('Assessment queued', true);
  // No execution path → cannot start a new assessment, regardless of prior result.
  if (mode === 'unavailable') return out('Worker unavailable', true, false, WORKER_UNAVAILABLE_HINT);
  if (hasAssets === false) return out('Run assessment', true);
  if (status === 'failed' || status === 'blocked') return out('Retry assessment', false);
  if (status === 'complete' || status === 'completed' || status === 'partial' || status === 'degraded' || status === 'stale') {
    return out('Run again', false);
  }
  // No prior assessment + no active job: the bounded on-demand affordance when the
  // background worker is down but stored-evidence assessment can still run.
  if (mode === 'on_demand') return out('Run limited assessment', false);
  return out('Run assessment', false);
}

// Per-asset Run button. Thin wrapper over the canonical selector (kept for callers
// that only need the button label/disabled/hint).
export function assessmentActionLabel(
  status: string | null | undefined,
  capability?: AssessmentCapability | null,
  activeJob?: AssessmentJob,
): AssessmentAction {
  const s = getAssetAssessmentDisplayState({ assessmentStatus: status, capability, activeJob });
  return { label: s.actionLabel, disabled: s.actionDisabled, ...(s.hint ? { hint: s.hint } : {}) };
}

// Workspace-level Run button on the AI panel. Thin wrapper over the canonical
// selector; `running` maps to an in-flight bounded assessment (mutation in flight).
export function workspaceAssessmentAction(args: {
  assessmentStatus?: string | null;
  capability?: AssessmentCapability | null;
  running?: boolean;
  hasAssets?: boolean;
  activeJob?: AssessmentJob;
}): AssessmentAction {
  const { assessmentStatus, capability, running, hasAssets, activeJob } = args;
  const s = getAssetAssessmentDisplayState({
    assessmentStatus, capability, activeJob, mutationInFlight: running, hasAssets,
  });
  return { label: s.actionLabel, disabled: s.actionDisabled, ...(s.hint ? { hint: s.hint } : {}) };
}

const RWA_TYPE_LABELS: Record<string, string> = {
  tokenized_treasury: 'Tokenized Treasury',
  stablecoin: 'Stablecoin',
  money_market_fund: 'Money Market Fund',
  fund_share: 'Fund Share',
  corporate_bond: 'Corporate Bond',
  private_credit: 'Private Credit',
  invoice_financing: 'Invoice Financing',
  commodity: 'Commodity',
  real_estate: 'Real Estate',
  other: 'Other',
};

export const RWA_TYPE_OPTIONS = Object.entries(RWA_TYPE_LABELS).map(([value, label]) => ({ value, label }));

// RWA product types whose value is a claim on off-chain reserves — reserve
// backing is required for these (mirrors backend RWA_ASSET_TYPES.reserve_required).
// real_estate / other have no on-chain liability model, so reserve config is
// optional there.
export const RESERVE_BACKED_RWA_TYPES = new Set<string>([
  'tokenized_treasury', 'stablecoin', 'money_market_fund', 'fund_share',
  'corporate_bond', 'private_credit', 'invoice_financing', 'commodity',
]);

export function isReserveBackedRwaType(value: string | null | undefined): boolean {
  return RESERVE_BACKED_RWA_TYPES.has((value || '').toLowerCase());
}

export function rwaTypeLabel(value: string | null | undefined, fallback?: string | null): string {
  const key = (value || '').toLowerCase();
  if (RWA_TYPE_LABELS[key]) return RWA_TYPE_LABELS[key];
  if (fallback) return fallback.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return 'Unclassified';
}

export function formatUsd(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '--';
  const n = Number(value);
  if (Number.isNaN(n)) return '--';
  if (Math.abs(n) >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function formatPercent(value: number | string | null | undefined, digits = 0): string {
  if (value === null || value === undefined || value === '') return '--';
  const n = Number(value);
  if (Number.isNaN(n)) return '--';
  return `${n.toFixed(digits)}%`;
}

// "5m ago" / "2h ago" from an ISO timestamp; truthful "never" when absent.
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 'never';
  const secs = Math.max(0, Math.floor((now - t) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
