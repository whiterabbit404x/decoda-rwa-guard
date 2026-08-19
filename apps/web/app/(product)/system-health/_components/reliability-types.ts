// Screen 12 Self-Healing Reliability Agent — frontend contract mirroring the
// backend GET /ops/reliability/overview payload. Kept intentionally permissive
// (nullable metric values, explicit states) so the UI can render TRUTHFUL
// "Insufficient history" / "Unavailable" / "Unknown" instead of fabricated data.

export type NormalizedStatus = 'healthy' | 'degraded' | 'unhealthy' | 'unknown' | 'disabled';
export type Severity = 'low' | 'medium' | 'high' | 'critical';
export type ActionPolicy = 'none' | 'auto' | 'approval_required' | 'forbidden' | 'unsupported';

export type MetricState =
  | 'ok'
  | 'insufficient_history'
  | 'no_data'
  | 'unavailable'
  | 'point_sample';

export type UptimeMetric = {
  value: number | null;
  state: MetricState;
  sample_count?: number;
  window_label?: string | null;
  source?: string;
};

export type ResponseMetric = {
  value_ms: number | null;
  state: MetricState;
  window_label?: string | null;
  source?: string;
  trend?: 'improving' | 'worsening' | 'unchanged' | null;
};

export type ErrorRateMetric = {
  value: number | null;
  state: MetricState;
  sample_count?: number;
  window_label?: string | null;
  trend?: 'improving' | 'worsening' | 'unchanged' | null;
  source?: string;
};

export type ReliabilityMetrics = {
  uptime: UptimeMetric;
  response_time: ResponseMetric;
  error_rate: ErrorRateMetric;
};

export type ReliabilityComponent = {
  component: string;
  label: string;
  status: NormalizedStatus;
  reason: string | null;
  age_human: string | null;
  last_healthy_at: string | null;
  last_healthy_ago: string | null;
  metric: string | null;
  disabled_reason: string | null;
  monitoring_path: boolean;
  critical: boolean;
};

export type ReliabilityFinding = {
  component: string;
  label: string;
  signal: string;
  severity: Severity;
  status: NormalizedStatus;
  summary: string;
  reason: string | null;
  age_human: string | null;
  last_healthy_at: string | null;
  recommended_action?: string;
  action_policy?: ActionPolicy;
  action_note?: string | null;
  finding_id?: string | null;
  evidence?: Record<string, unknown>;
};

export type RootCause = {
  domain: string | null;
  domain_label?: string;
  severity?: Severity;
  summary: string;
  confidence: string;
  symptoms: string[];
};

export type ReliabilityDiagnosis = {
  state: 'nominal' | 'partial_observability' | 'degraded' | 'action_required';
  headline: string;
  overall_status: string;
  observations: string[];
  root_cause: RootCause;
  auto_healing_mode: 'enabled' | 'recommendation_only' | 'disabled';
};

export type AutonomousStatus = {
  auto_healing: 'enabled' | 'recommendation_only' | 'disabled';
  events_24h: number | null;
  restarts: number | null;
  policies: number;
  configured_mechanisms: string[];
  last_event: {
    component: string | null;
    summary: string | null;
    status: string | null;
    at: string | null;
    ago: string | null;
  } | null;
  ledger_available?: boolean;
};

export type ReliabilityOverview = {
  generated_at: string;
  overall_status: 'healthy' | 'degraded' | 'failing' | 'unavailable';
  summary: string;
  environment: string | null;
  api_commit: string | null;
  version: string | null;
  ledger_available: boolean;
  metrics: ReliabilityMetrics;
  components: ReliabilityComponent[];
  findings: ReliabilityFinding[];
  diagnosis: ReliabilityDiagnosis;
  autonomous: AutonomousStatus;
};

// The normalized reliability status vocabulary is richer than the design system's
// {healthy,degraded,failing,unavailable}. Map it onto the existing status-pill
// classes so a real failure reads red, unknown reads grey (NOT green), and a
// deliberately-disabled component reads as a distinct neutral state.
export function reliabilityPillClass(status: NormalizedStatus): string {
  switch (status) {
    case 'healthy':
      return 'statusBadge statusBadge-live';
    case 'degraded':
      return 'statusBadge statusBadge-degraded';
    case 'unhealthy':
      return 'statusBadge statusBadge-offline';
    case 'disabled':
    case 'unknown':
    default:
      return 'statusBadge statusBadge-unavailable';
  }
}

export function reliabilityStatusLabel(status: NormalizedStatus): string {
  switch (status) {
    case 'healthy':
      return 'Healthy';
    case 'degraded':
      return 'Degraded';
    case 'unhealthy':
      return 'Unhealthy';
    case 'disabled':
      return 'Disabled';
    case 'unknown':
    default:
      return 'Unknown';
  }
}

export function severityPillClass(severity: Severity): string {
  if (severity === 'critical' || severity === 'high') return 'statusBadge statusBadge-offline';
  if (severity === 'medium') return 'statusBadge statusBadge-degraded';
  return 'statusBadge statusBadge-unavailable';
}
