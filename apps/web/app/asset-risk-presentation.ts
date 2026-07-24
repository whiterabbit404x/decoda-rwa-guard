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

const WORKER_UNAVAILABLE_HINT =
  'The background assessor is not running and on-demand assessment is disabled. Enable the Asset Risk Assessor worker or on-demand assessment to run assessments.';

// Per-asset Run button. Renders ONLY from persisted backend job/assessment state
// plus runtime capability — never from an ambiguous "unassessed asset count".
export function assessmentActionLabel(
  status: string | null | undefined,
  capability?: AssessmentCapability | null,
): AssessmentAction {
  const s = (status || '').toLowerCase();
  const mode = capability?.execution_mode;
  // A persisted active job always wins — it is the true state.
  if (s === 'running') return { label: 'Assessment running', disabled: true };
  if (s === 'queued') return { label: 'Assessment queued', disabled: true };
  // No execution path: cannot start a new assessment.
  if (mode === 'unavailable') return { label: 'Worker unavailable', disabled: true, hint: WORKER_UNAVAILABLE_HINT };
  switch (s) {
    case 'complete':
    case 'completed':
    case 'partial':
    case 'degraded':
    case 'stale':
      return { label: 'Run again', disabled: false };
    case 'failed':
    case 'blocked':
      return { label: 'Retry assessment', disabled: false };
    default:
      return { label: 'Run assessment', disabled: false };
  }
}

// Workspace-level Run button on the AI panel. Same canonical rules, plus the
// bounded on-demand affordance ("Run limited assessment") when the background
// worker is not healthy but on-demand execution is available.
export function workspaceAssessmentAction(args: {
  assessmentStatus?: string | null;
  capability?: AssessmentCapability | null;
  running?: boolean;
  hasAssets?: boolean;
}): AssessmentAction {
  const { assessmentStatus, capability, running, hasAssets } = args;
  const mode = capability?.execution_mode;
  if (running) return { label: 'Running assessment…', disabled: true };
  if (mode === 'unavailable') return { label: 'Worker unavailable', disabled: true, hint: WORKER_UNAVAILABLE_HINT };
  // Only surface "queued" when a healthy worker / valid route exists to drain it.
  if ((assessmentStatus || '').toLowerCase() === 'queued') return { label: 'Assessment queued', disabled: true };
  if (hasAssets === false) return { label: 'Run assessment', disabled: true };
  if (mode === 'on_demand') return { label: 'Run limited assessment', disabled: false };
  return { label: 'Run assessment', disabled: false };
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
