// Presentation helpers for Active Alerts + the AI Alert Triage Agent (Screen 6).
//
// Pure, framework-free mappers so severity/status badges, triage-mode labels,
// confidence copy, and the Run-Triage button stay consistent between the table,
// the triage panel, and the cluster drawer — and are unit-testable without a
// browser. Nothing here invents data; callers pass canonical backend values.
// This is the SINGLE place that maps backend triage facts -> UI, mirroring the
// canonical display-state helper used on Screen 3 (asset-risk-presentation.ts).

import type { PillVariant } from './components/ui-primitives';

/* ── Canonical severity (same four levels the backend uses) ─────────────── */
export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low';

export function severityLabel(severity: string | null | undefined): string {
  switch ((severity || '').toLowerCase()) {
    case 'critical': return 'Critical';
    case 'high': return 'High';
    case 'medium': return 'Medium';
    case 'low': return 'Low';
    default: return 'Unknown';
  }
}

export function severityVariant(severity: string | null | undefined): PillVariant {
  switch ((severity || '').toLowerCase()) {
    case 'critical':
    case 'high': return 'danger';
    case 'medium': return 'warning';
    case 'low': return 'success';
    default: return 'neutral';
  }
}

/* ── Canonical alert lifecycle status (never a frontend-only meaning) ────── */
export function statusLabel(status: string | null | undefined): string {
  switch ((status || '').toLowerCase()) {
    case 'open': return 'Open';
    case 'investigating': return 'Investigating';
    case 'acknowledged': return 'Acknowledged';
    case 'linked_to_incident': return 'Linked to Incident';
    case 'resolved': return 'Resolved';
    case 'suppressed': return 'Suppressed';
    case 'false_positive': return 'False Positive';
    default: return 'Unknown';
  }
}

export function statusVariant(status: string | null | undefined): PillVariant {
  switch ((status || '').toLowerCase()) {
    case 'open': return 'danger';
    case 'investigating':
    case 'acknowledged': return 'info';
    case 'linked_to_incident': return 'warning';
    case 'resolved': return 'success';
    case 'suppressed':
    case 'false_positive': return 'neutral';
    default: return 'neutral';
  }
}

/* ── Triage mode ────────────────────────────────────────────────────────── */
export type TriageMode = 'rules_based' | 'ai_assisted' | 'unavailable';

export function triageModeLabel(mode: string | null | undefined): string {
  switch ((mode || '').toLowerCase()) {
    case 'ai_assisted': return 'AI-assisted';
    case 'rules_based': return 'Rules-based';
    case 'unavailable': return 'Unavailable';
    default: return 'Rules-based';
  }
}

export function triageModeVariant(mode: string | null | undefined): PillVariant {
  switch ((mode || '').toLowerCase()) {
    case 'ai_assisted': return 'info';
    case 'rules_based': return 'neutral';
    case 'unavailable': return 'warning';
    default: return 'neutral';
  }
}

/* ── Confidence ─────────────────────────────────────────────────────────── */
// Truthful: a missing confidence reads "Not calculated", never 0%.
export function confidenceText(value: number | null | undefined, band?: string | null): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'Not calculated';
  const pct = Math.round(Number(value) * 100);
  const bandLabel = band ? band.charAt(0).toUpperCase() + band.slice(1) : '';
  return bandLabel ? `${pct}% (${bandLabel})` : `${pct}%`;
}

export function confidenceVariant(band: string | null | undefined): PillVariant {
  switch ((band || '').toLowerCase()) {
    case 'high': return 'success';
    case 'medium': return 'warning';
    case 'low': return 'danger';
    default: return 'neutral';
  }
}

/* ── Recommendation categories (fallback; backend supplies labels too) ───── */
const RECOMMENDATION_LABELS: Record<string, string> = {
  review_immediately: 'Review immediately',
  investigate_actor_activity: 'Investigate actor activity',
  validate_oracle_source: 'Validate oracle source',
  check_reserve_discrepancy: 'Check reserve discrepancy',
  escalate_to_incident: 'Escalate to incident',
  continue_monitoring: 'Continue monitoring',
};

export function recommendationLabel(category: string | null | undefined): string {
  return RECOMMENDATION_LABELS[(category || '').toLowerCase()] || 'Continue monitoring';
}

/* ── Time ───────────────────────────────────────────────────────────────── */
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

export function exactTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  return new Date(t).toLocaleString();
}

export function shortHash(hash?: string | null): string {
  if (!hash) return '—';
  return hash.length > 14 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

/* ── Backend read-model types (Screen 6 summary) ────────────────────────── */
export type Screen6Cluster = {
  id: string;
  title: string | null;
  severity?: string | null;
  member_count?: number | null;
};

export type Screen6Alert = {
  id: string;
  title: string;
  severity: string;
  status: string;
  asset_id: string | null;
  asset_name: string;
  asset_known: boolean;
  chain_id: string | null;
  tx_hash: string | null;
  contract_address: string | null;
  actor: string | null;
  detection_family: string | null;
  detection_family_label: string | null;
  occurred_at: string | null;
  incident_id: string | null;
  detection_id: string | null;
  cluster: Screen6Cluster | null;
};

export type SeverityTotals = { all: number; critical: number; high: number; medium: number; low: number };

export type TriageClusterView = {
  id: string;
  title: string | null;
  derived_severity: string | null;
  confidence: number | null;
  confidence_band: string | null;
  confidence_factors?: Array<Record<string, unknown>>;
  severity_factors?: Array<Record<string, unknown>>;
  reason_codes?: string[];
  reason_labels?: string[];
  recommendation_category: string | null;
  recommendation_label?: string | null;
  recommendation_summary: string | null;
  recommendation_source: string | null;
  primary_asset_name: string | null;
  detection_family_label: string | null;
  member_count: number;
};

export type TriageRun = {
  id: string;
  status: string;
  mode: string;
  triggered_by?: string;
  alerts_considered: number;
  clusters_total: number;
  clusters_created: number;
  unclustered_count: number;
  ai_enriched_count?: number;
  error_code?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

export type TriageState = {
  schema_ready: boolean;
  ai_available: boolean;
  mode: string;
  processing: boolean;
  last_run: TriageRun | null;
  last_success_at?: string | null;
  cluster_count: number;
  unclustered_count: number | null;
  top_recommendation: TriageClusterView | null;
  clusters_preview: TriageClusterView[];
  state: 'provisioning' | 'not_run' | 'processing' | 'degraded' | 'ready';
};

export type Screen6Summary = {
  alerts: Screen6Alert[];
  severity_totals: SeverityTotals;
  pagination: {
    limit: number; offset: number; total: number; has_more: boolean;
    showing_from: number; showing_to: number;
  };
  filters: {
    assets: Array<{ value: string; label: string }>;
    statuses: string[];
    active: Record<string, string | null>;
  };
  triage: TriageState;
  active_alert_count: number;
  truncated: boolean;
  generated_at: string | null;
};

/* ── THE canonical triage display-state selector ────────────────────────── */
// Both the Run-Triage button and the triage status pill derive from this one
// function, so the panel can never contradict itself. Renders ONLY from:
//   * triage.state / triage.processing — persisted run facts (never optimistic)
//   * mutationInFlight — the POST is actively in flight (transient, local)
//   * canRun — the viewer holds the permission to run triage
export type TriageDisplayState = {
  statusLabel: string;
  statusVariant: PillVariant;
  runLabel: string;
  runDisabled: boolean;
  runBusy: boolean;
  hint?: string;
};

export function getTriageDisplayState(args: {
  triage: TriageState | null;
  mutationInFlight?: boolean;
  canRun?: boolean;
}): TriageDisplayState {
  const { triage, mutationInFlight, canRun } = args;
  const state = triage?.state ?? 'not_run';
  const processing = triage?.processing || state === 'processing';

  const out = (
    statusLabel: string, statusVariant: PillVariant, runLabel: string,
    runDisabled: boolean, runBusy = false, hint?: string,
  ): TriageDisplayState => ({ statusLabel, statusVariant, runLabel, runDisabled, runBusy, ...(hint ? { hint } : {}) });

  if (triage && triage.schema_ready === false) {
    return out('Provisioning', 'warning', 'Run Triage', true, false,
      'Alert triage storage is provisioning. Try again shortly.');
  }
  if (mutationInFlight) return out('Running…', 'info', 'Running…', true, true);
  if (processing) return out('Running…', 'info', 'Triage running…', true, true);
  if (canRun === false) {
    return out(state === 'ready' ? 'Ready' : 'Triage not run',
      state === 'ready' ? 'success' : 'neutral', 'Run Triage', true, false,
      'You need the monitoring-config permission to run triage.');
  }
  if (state === 'not_run') return out('Triage not run', 'neutral', 'Run Triage', false);
  if (state === 'degraded') return out('Last run failed', 'warning', 'Retry Triage', false,
    false, 'The most recent run failed; the previous successful result is shown.');
  return out('Ready', 'success', 'Run Triage', false);
}

/* ── Triage summary sentence (evidence-grounded, truthful) ──────────────── */
export function triageSummaryText(triage: TriageState | null): string {
  if (!triage) return 'Triage not run.';
  if (triage.schema_ready === false) return 'Alert triage storage is provisioning.';
  const run = triage.last_run && triage.last_run.status === 'completed' ? triage.last_run : null;
  if (!run) {
    if (triage.state === 'processing') return 'Triage is running…';
    return 'Triage not run.';
  }
  const grouped = Math.max(0, (run.alerts_considered || 0) - (run.unclustered_count || 0));
  const clusterWord = run.clusters_total === 1 ? 'cluster' : 'clusters';
  const alertWord = grouped === 1 ? 'alert' : 'alerts';
  if (run.clusters_total === 0) {
    return `No related-alert clusters found across ${run.alerts_considered} active ${run.alerts_considered === 1 ? 'alert' : 'alerts'}.`;
  }
  return `Grouped ${grouped} related ${alertWord} into ${run.clusters_total} ${clusterWord}.`;
}
