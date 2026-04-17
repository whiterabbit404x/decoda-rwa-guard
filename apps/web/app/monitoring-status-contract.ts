export type MonitoringMode = 'LIVE' | 'DEGRADED' | 'OFFLINE' | 'STALE' | 'LIMITED_COVERAGE';

export type MonitoringRuntimeStatus = {
  error?: {
    code?: string;
    type?: string;
    message?: string;
    stage?: string;
    hint?: string;
  };
  field_reason_codes?: Record<string, string[]>;
  monitoring_status?: 'active' | 'idle' | 'degraded' | 'offline' | 'error';
  monitored_systems?: number;
  enabled_systems?: number;
  protected_assets?: number;
  active_systems?: number;
  last_heartbeat?: string | null;
  last_confirmed_checkpoint?: string | null;
  last_detection_evaluation_at?: string | null;
  telemetry_available?: boolean;
  mode: MonitoringMode;
  provider_mode?: string | null;
  configured_mode?: MonitoringMode;
  status?: string;
  detection_outcome?: 'DETECTION_CONFIRMED' | 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE' | 'NO_EVIDENCE' | 'MONITORING_DEGRADED' | 'ANALYSIS_FAILED' | 'DEMO_ONLY' | string;
  source_of_evidence?: string | null;
  source_type?: string | null;
  freshness_status?: string | null;
  confidence_status?: string | null;
  provider_health?: 'healthy' | 'degraded';
  provider_reachable?: boolean;
  claim_safe?: boolean;
  synthetic?: boolean;
  evidence_present?: boolean;
  evidence_state?: 'real' | 'degraded' | 'missing' | 'failed' | 'no_evidence' | string;
  truthfulness_state?: 'claim_safe' | 'not_claim_safe' | 'unknown_risk' | string;
  latest_processed_block?: number | null;
  latest_block?: number | null;
  checkpoint_lag_blocks?: number | null;
  checkpoint_age_seconds?: number | null;
  targets_monitored?: number;
  protected_assets_count?: number;
  monitored_systems_count?: number;
  systems_with_recent_heartbeat?: number;
  invalid_enabled_targets?: number;
  provider_name?: string | null;
  provider_kind?: string | null;
  degraded_reason?: string | null;
  error_code?: string | null;
  sales_claims_allowed?: boolean;
  claim_validator_status?: 'PASS' | 'FAIL' | string;
  recent_evidence_state?: 'real' | 'degraded' | 'missing' | 'failed' | 'no_evidence' | string;
  recent_truthfulness_state?: 'claim_safe' | 'not_claim_safe' | 'unknown_risk' | string;
  recent_real_event_count?: number;
  last_real_event_at?: string | null;
  recent_confidence_basis?: 'provider_evidence' | 'backfill_evidence' | 'none' | string;
  successful_detection_evaluation?: boolean;
  successful_detection_evaluation_recent?: boolean;
  synthetic_leak_detected?: boolean;
  workspace_monitoring_summary?: {
    workspace_configured: boolean;
    monitoring_mode: 'live' | 'hybrid' | 'simulator' | 'offline' | 'unavailable';
    runtime_status: 'provisioning' | 'healthy' | 'degraded' | 'idle' | 'failed' | 'disabled' | 'offline';
    configured_systems: number;
    reporting_systems: number;
    protected_assets: number;
    monitored_systems_count?: number;
    reporting_systems_count?: number;
    protected_assets_count?: number;
    coverage_state: {
      configured_systems: number;
      reporting_systems: number;
      protected_assets: number;
    };
    coverage_counts?: {
      configured_systems: number;
      monitored_systems_count: number;
      reporting_systems_count: number;
      protected_assets_count: number;
    };
    freshness_status: 'fresh' | 'stale' | 'unavailable';
    confidence_status: 'high' | 'medium' | 'low' | 'unavailable';
    freshness?: 'fresh' | 'stale' | 'unavailable';
    confidence?: 'high' | 'medium' | 'low' | 'unavailable';
    last_heartbeat_at: string | null;
    last_telemetry_at: string | null;
    last_coverage_telemetry_at?: string | null;
    telemetry_kind?: 'coverage' | 'target_event' | null;
    last_poll_at: string | null;
    poll_freshness_status?: 'fresh' | 'stale' | 'unavailable';
    last_detection_at: string | null;
    evidence_source: 'live' | 'simulator' | 'replay' | 'none';
    status_reason: string | null;
    configuration_reason?: string | null;
    valid_protected_asset_count?: number;
    linked_monitored_system_count?: number;
    persisted_enabled_config_count?: number;
    valid_target_system_link_count?: number;
    contradiction_flags: string[];
  };
  workspace_configured?: boolean;
  monitoring_mode?: 'live' | 'hybrid' | 'simulator' | 'offline' | 'unavailable';
  runtime_status?: 'provisioning' | 'healthy' | 'degraded' | 'idle' | 'failed' | 'disabled' | 'offline';
  configured_systems?: number;
  reporting_systems?: number;
  coverage_state?: { configured_systems: number; reporting_systems: number; protected_assets: number };
  last_heartbeat_at?: string | null;
  last_telemetry_at?: string | null;
  last_coverage_telemetry_at?: string | null;
  telemetry_kind?: 'coverage' | 'target_event' | null;
  last_poll_at?: string | null;
  poll_freshness_status?: 'fresh' | 'stale' | 'unavailable';
  last_detection_at?: string | null;
  evidence_source?: 'live' | 'simulator' | 'replay' | 'none';
  status_reason?: string | null;
  contradiction_flags?: string[];
};

export function runtimeStatusModeFromMonitoringStatus(value: MonitoringRuntimeStatus['monitoring_status']): MonitoringMode {
  if (value === 'active') {
    return 'LIVE';
  }
  if (value === 'offline' || value === 'error') {
    return 'OFFLINE';
  }
  if (value === 'degraded') {
    return 'DEGRADED';
  }
  if (value === 'idle') {
    return 'LIMITED_COVERAGE';
  }
  return 'LIMITED_COVERAGE';
}

export function normalizeMonitoringMode(value: unknown): MonitoringMode {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (normalized === 'LIVE') {
    return 'LIVE';
  }
  if (normalized === 'OFFLINE') {
    return 'OFFLINE';
  }
  if (normalized === 'STALE') {
    return 'STALE';
  }
  if (normalized === 'DEGRADED') {
    return 'DEGRADED';
  }
  if (normalized === 'LIMITED_COVERAGE' || normalized === 'HYBRID' || normalized === 'DEMO') {
    return 'LIMITED_COVERAGE';
  }
  if (normalized.includes('DEGRADED') || normalized.includes('FAILED')) {
    return 'DEGRADED';
  }
  if (normalized.includes('OFFLINE') || normalized.includes('UNREACHABLE')) {
    return 'OFFLINE';
  }
  if (normalized.includes('STALE')) {
    return 'STALE';
  }
  if (normalized.includes('DEMO') || normalized.includes('FALLBACK') || normalized.includes('HYBRID') || normalized.includes('SYNTHETIC')) {
    return 'LIMITED_COVERAGE';
  }
  if (normalized.includes('LIVE')) {
    return 'LIVE';
  }
  return 'LIMITED_COVERAGE';
}

export function monitoringModeLabel(mode: MonitoringMode): string {
  if (mode === 'LIVE') {
    return 'LIVE';
  }
  if (mode === 'DEGRADED') {
    return 'DEGRADED';
  }
  if (mode === 'OFFLINE') {
    return 'OFFLINE';
  }
  if (mode === 'STALE') {
    return 'STALE';
  }
  return 'LIMITED COVERAGE';
}
