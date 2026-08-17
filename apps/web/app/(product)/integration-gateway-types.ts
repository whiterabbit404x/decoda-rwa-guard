// Canonical Screen-10 (Integration Gateway) frontend types + formatting helpers.
// These mirror the backend integration_gateway vocabularies (§22). Keeping them in
// one module avoids ad-hoc types scattered across components.

import type { PillVariant } from '../components/ui-primitives';

export type IntegrationHealthStatus = 'healthy' | 'degraded' | 'disconnected' | 'unknown';
export type IntegrationRisk = 'low' | 'medium' | 'high' | 'critical' | 'unknown';
export type ExecutionMode = 'auto' | 'approval_required' | 'manual' | 'advisory';
export type RecommendationSeverity = 'low' | 'medium' | 'high' | 'critical';

export type ProviderRow = {
  integration_key: string;
  provider: string;
  host: string;
  type: string;
  status: IntegrationHealthStatus;
  role: string;
  networks: string[];
  chains: string[];
  target_count: number;
  last_check_at: string | null;
  latency_ms: number | null;
  evidence_source: string | null;
  degraded_reason: string | null;
  credential_age_days: number | null;
  credential_configured: boolean | null;
};

export type ApiRow = {
  integration_key: string;
  name: string;
  authentication: string;
  scope: string;
  status: string;
  provider: string | null;
  credential_configured: boolean;
  message: string | null;
  credential_hint: string | null;
  error_rate: number | null;
  latency_ms: number | null;
  last_request_at: string | null;
};

export type WebhookRow = {
  integration_key: string;
  id: string;
  endpoint: string;
  description: string | null;
  event_types: string[];
  status: string;
  enabled: boolean;
  last_delivery_at: string | null;
  success_rate: number | null;
  delivery_total: number;
  failures: number;
  pending: number;
  retry_state: string;
  signing_secret_configured: boolean;
};

export type ConnectionRow = {
  integration_key: string;
  source: string;
  via: string;
  destination: string;
  purpose: string;
  state: string;
  role: string;
  failover: string;
  last_verified_at: string | null;
};

export type Recommendation = {
  id: string;
  integration_key: string;
  integration_kind: string;
  recommendation_type: string;
  severity: RecommendationSeverity;
  title: string;
  description: string | null;
  reason: string | null;
  evidence: Record<string, unknown> | null;
  recommended_action: string | null;
  confidence: string | null;
  risk_if_ignored: string | null;
  execution_mode: ExecutionMode;
  status: string;
  detected_count: number;
  created_at: string;
  updated_at: string;
};

export type LastScan = {
  id: string;
  status: string;
  trigger: string;
  checked_count: number;
  healthy_count: number;
  degraded_count: number;
  failed_count: number;
  recommendations_open: number;
  connection_risk: string | null;
  started_at: string | null;
  completed_at: string | null;
};

export type ControlPlaneSummary = {
  total_integrations: number;
  total_providers: number;
  healthy: number;
  degraded: number;
  disconnected: number;
  unknown: number;
  webhooks: number;
  apis: number;
  recommendations: number;
};

export type ControlPlane = {
  workspace: { id: string };
  server_time: string;
  last_scan: LastScan | null;
  providers: ProviderRow[];
  apis: ApiRow[];
  webhooks: WebhookRow[];
  connections: ConnectionRow[];
  recommendations: Recommendation[];
  summary: ControlPlaneSummary;
  connection_risk: { level: IntegrationRisk; reason: string };
  settings: { auto_routing_enabled: boolean };
  permissions: { can_manage: boolean; can_approve: boolean };
  agent: {
    state: string;
    connection_risk: string;
    connection_risk_reason: string;
    open_recommendations: number;
    last_scan_at: string | null;
    auto_routing_enabled: boolean;
  };
};

// ── Status / risk / severity → pill variant mappers ─────────────────────────
export function providerStatusVariant(status: string): PillVariant {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'degraded': return 'warning';
    case 'disconnected': return 'danger';
    default: return 'neutral';
  }
}

export function providerStatusLabel(status: string): string {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'Healthy';
    case 'degraded': return 'Degraded';
    case 'disconnected': return 'Disconnected';
    case 'unknown': return 'Unknown';
    default: return status || 'Unknown';
  }
}

export function apiStatusVariant(status: string): PillVariant {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'configured': return 'info';
    case 'degraded': return 'warning';
    case 'missing': return 'danger';
    default: return 'neutral';
  }
}

export function webhookStatusVariant(status: string): PillVariant {
  switch ((status || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'degraded': return 'warning';
    case 'failed': return 'danger';
    case 'disabled': return 'neutral';
    case 'pending': return 'info';
    default: return 'neutral';
  }
}

export function connectionStateVariant(state: string): PillVariant {
  switch ((state || '').toLowerCase()) {
    case 'healthy': return 'success';
    case 'degraded': return 'warning';
    case 'offline': return 'danger';
    case 'disabled': return 'neutral';
    default: return 'neutral';
  }
}

export function riskVariant(level: string): PillVariant {
  switch ((level || '').toLowerCase()) {
    case 'low': return 'success';
    case 'medium': return 'warning';
    case 'high': return 'danger';
    case 'critical': return 'danger';
    default: return 'neutral';
  }
}

export function severityVariant(severity: string): PillVariant {
  switch ((severity || '').toLowerCase()) {
    case 'critical': case 'high': return 'danger';
    case 'medium': return 'warning';
    case 'low': return 'info';
    default: return 'neutral';
  }
}

export function executionModeLabel(mode: string): string {
  switch ((mode || '').toLowerCase()) {
    case 'approval_required': return 'Approval required';
    case 'manual': return 'Action required';
    case 'auto': return 'Automatic';
    default: return 'Advisory';
  }
}

// ── Formatting helpers (self-contained; no cross-screen import) ──────────────
export function fmtRelative(value?: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  const diff = Date.now() - parsed.getTime();
  if (diff < 60_000) return `${Math.max(0, Math.floor(diff / 1000))}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return parsed.toLocaleDateString();
}

export function fmtExact(value?: string | null): string {
  if (!value) return 'No timestamp recorded';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'No timestamp recorded';
  return parsed.toLocaleString();
}

export function fmtLatency(value?: number | null): string {
  if (value == null) return '—';
  return `${Math.round(value).toLocaleString()} ms`;
}
