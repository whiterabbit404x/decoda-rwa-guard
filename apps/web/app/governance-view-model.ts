// Governance Guard (Screen 11) view-model.
//
// Pure, testable mapping from backend governance responses to UI state. It keeps
// the truthfulness distinctions the product requires OUT of the JSX so they can be
// unit-tested directly:
//   * "evaluated, 0 findings"  != "never evaluated" != "evaluation failed"
//   * an API error NEVER renders as a green "0 Critical"
//   * missing evidence NEVER renders as a confident low risk / high score

export type LoadState = 'idle' | 'loading' | 'loaded' | 'permission_denied' | 'error';

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical' | 'unknown';

export type PolicyControl = {
  key: string;
  label: string;
  status: 'pass' | 'fail' | 'attention' | 'unknown' | 'unsupported';
  evidence_source: 'configuration' | 'access_evidence' | 'unavailable';
  points_earned: number;
  points_possible: number;
  detail: string;
};

export type PolicyRecommendation = {
  key: string;
  severity: RiskLevel;
  reason: string;
  recommended_action: string;
};

export type PolicyPosture = {
  risk_reduction_percent: number;
  access_risk: RiskLevel;
  evidence_status: 'insufficient' | 'partial' | 'complete';
  controls: PolicyControl[];
  controls_passing: number;
  controls_total: number;
  recommendations: PolicyRecommendation[];
  calculated_at: string;
  last_evaluated_at: string | null;
};

export type GovernanceSummary = {
  anomalies: {
    evaluated: boolean;
    last_evaluated_at: string | null;
    open_total: number;
    by_severity: Record<string, number>;
  };
  change_safeguards: { status: string; detail: string };
  pending_change_requests: number;
  can_manage: boolean;
  supported_anomaly_types: string[];
};

export type WorkspaceSettings = {
  workspace_id: string;
  name: string;
  timezone: string;
  currency: string;
  version: number;
  allowed_timezones: string[];
  allowed_currencies: string[];
  can_manage: boolean;
};

export type SecuritySettings = {
  mfa_enforcement: string;
  reauthentication_minutes: number;
  session_timeout_options: Array<{ value: number; label: string }>;
  audit_logging: { status: string; detail: string };
  ip_allowlist: { status: string; supported: boolean; detail: string };
  encryption: { status: string; detail: string };
  can_manage: boolean;
  pending_change_requests: number;
};

export type Approval = {
  id: string;
  change_type: string;
  target: string;
  before_value: Record<string, unknown>;
  proposed_value: Record<string, unknown>;
  risk_level: RiskLevel;
  risk_reason: string;
  status: string;
  requested_by: string;
  requested_by_user_id: string | null;
  approved_by: string | null;
  requested_at: string | null;
  approved_at: string | null;
  executed_at: string | null;
  failure_reason: string | null;
  decision_note: string | null;
};

export type ChangeLogItem = {
  id: string;
  action: string;
  change: string;
  target: string | null;
  risk_level: RiskLevel;
  approval: string;
  result: string;
  actor: string;
  source_ip: string | null;
  timestamp: string | null;
};

export type GovernanceAnomaly = {
  id: string;
  type: string;
  severity: RiskLevel;
  confidence: number | null;
  status: string;
  actor: string;
  evidence: Record<string, unknown>;
  first_seen_at: string | null;
  last_seen_at: string | null;
  resolved_at: string | null;
  recommended_actions?: string[];
};

export type GovernanceState = {
  settings: WorkspaceSettings | null;
  settingsState: LoadState;
  security: SecuritySettings | null;
  securityState: LoadState;
  posture: PolicyPosture | null;
  postureState: LoadState;
  summary: GovernanceSummary | null;
  summaryState: LoadState;
};

export function emptyGovernanceState(): GovernanceState {
  return {
    settings: null,
    settingsState: 'loading',
    security: null,
    securityState: 'loading',
    posture: null,
    postureState: 'loading',
    summary: null,
    summaryState: 'loading',
  };
}

export type PillTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export function riskPillVariant(risk: string): PillTone {
  switch (String(risk || '').toLowerCase()) {
    case 'low':
      return 'success';
    case 'medium':
      return 'warning';
    case 'high':
      return 'danger';
    case 'critical':
      return 'danger';
    case 'unknown':
      return 'neutral';
    default:
      return 'neutral';
  }
}

// Risk Reduction primary label. Honours §31: missing access evidence shows
// "Insufficient evidence" rather than a confident number; an error shows
// "Unavailable"; a loading state shows a placeholder. The numeric breakdown is
// still available in the details drawer.
export function posturePercentLabel(posture: PolicyPosture | null, state: LoadState): string {
  if (state === 'loading' || state === 'idle') return '—';
  if (state === 'permission_denied') return 'Restricted';
  if (state === 'error' || !posture) return 'Unavailable';
  if (posture.evidence_status === 'insufficient') return 'Insufficient evidence';
  return `${posture.risk_reduction_percent}%`;
}

export function accessRiskLabel(posture: PolicyPosture | null, state: LoadState): string {
  if (state === 'loading' || state === 'idle') return '—';
  if (state === 'permission_denied') return 'Restricted';
  if (state === 'error' || !posture) return 'Unknown';
  const risk = String(posture.access_risk || 'unknown');
  return risk.charAt(0).toUpperCase() + risk.slice(1);
}

export type AnomalyCardState = {
  value: string;
  detail: string;
  tone: PillTone;
};

// Governance Guard "Access Anomalies" card state (§30). The four cases are kept
// strictly distinct; an API error is never rendered as a green zero.
export function anomalyStateLabel(summary: GovernanceSummary | null, state: LoadState): AnomalyCardState {
  if (state === 'loading' || state === 'idle') {
    return { value: '—', detail: 'Loading governance status…', tone: 'neutral' };
  }
  if (state === 'permission_denied') {
    return { value: 'Restricted', detail: 'Requires security management permission.', tone: 'neutral' };
  }
  if (state === 'error' || !summary) {
    return { value: 'Unavailable', detail: 'Governance evaluation failed.', tone: 'danger' };
  }
  if (!summary.anomalies.evaluated) {
    return { value: 'Not evaluated', detail: 'Access evidence has not been evaluated yet.', tone: 'neutral' };
  }
  const critical = Number(summary.anomalies.by_severity?.critical || 0);
  const total = Number(summary.anomalies.open_total || 0);
  if (total > 0) {
    return {
      value: `${critical} Critical`,
      detail: `${total} open governance finding${total === 1 ? '' : 's'}.`,
      tone: critical > 0 ? 'danger' : 'warning',
    };
  }
  return { value: '0 Critical', detail: 'No critical access anomalies detected.', tone: 'success' };
}

export function changeSafeguardLabel(summary: GovernanceSummary | null, state: LoadState): { value: string; detail: string; tone: PillTone } {
  if (state === 'loading' || state === 'idle') return { value: '—', detail: '', tone: 'neutral' };
  if (state === 'permission_denied') return { value: 'Restricted', detail: 'Requires security management permission.', tone: 'neutral' };
  if (state === 'error' || !summary) return { value: 'Policy unavailable', detail: 'Could not load change safeguards.', tone: 'danger' };
  if (summary.change_safeguards.status === 'approval_required') {
    return { value: 'Approval required', detail: summary.change_safeguards.detail, tone: 'success' };
  }
  return { value: summary.change_safeguards.status, detail: summary.change_safeguards.detail, tone: 'warning' };
}
