export type MonitoringMode = 'LIVE' | 'DEGRADED' | 'OFFLINE' | 'STALE' | 'LIMITED_COVERAGE';

export type WorkspaceMonitoringSummary = {
  workspace_configured: boolean;
  runtime_status: 'live' | 'degraded' | 'offline' | 'idle';
  monitoring_status: 'live' | 'limited' | 'offline';
  last_poll_at: string | null;
  last_heartbeat_at: string | null;
  last_telemetry_at: string | null;
  telemetry_freshness: 'fresh' | 'stale' | 'unavailable';
  confidence: 'high' | 'medium' | 'low' | 'unavailable';
  reporting_systems_count: number;
  monitored_systems_count: number;
  protected_assets_count: number;
  active_alerts_count: number;
  active_incidents_count: number;
  evidence_source_summary: 'live' | 'simulator' | 'replay' | 'none';
  continuity_status?: 'continuous_live' | 'continuous_no_evidence' | 'degraded' | 'offline' | 'idle_no_telemetry';
  continuity_reason_codes?: string[];
  continuity_signals?: Record<string, unknown>;
  continuity_slo_pass?: boolean;
  heartbeat_age_seconds?: number | null;
  telemetry_age_seconds?: number | null;
  event_ingestion_age_seconds?: number | null;
  detection_age_seconds?: number | null;
  detection_pipeline_age_seconds?: number | null;
  detection_eval_age_seconds?: number | null;
  thresholds_seconds?: {
    heartbeat?: number;
    telemetry?: number;
    event_ingestion?: number;
    detection_eval?: number;
  };
  required_thresholds_seconds?: {
    heartbeat?: number;
    event_ingestion?: number;
    detection_eval?: number;
  };
  ingestion_freshness?: 'fresh' | 'stale' | 'offline' | 'missing';
  detection_pipeline_freshness?: 'fresh' | 'stale' | 'offline' | 'missing';
  worker_heartbeat_freshness?: 'fresh' | 'stale' | 'offline' | 'missing';
  event_throughput_window?: 'in_window' | 'out_of_window' | 'offline' | 'no_events';
  event_throughput_window_seconds?: number;
  contradiction_flags: string[];
  guard_flags: string[];
  status_reason: string | null;
  configuration_reason?: string | null;
  configuration_reason_codes?: string[];
  db_failure_classification?: string | null;
  db_failure_reason?: string | null;
  coverage_only_warning?: {
    state?: 'coverage_only_persistent_no_evidence' | string | null;
    active?: boolean;
    cycle_count?: number;
    duration_seconds?: number;
    threshold_seconds?: number;
    first_seen_at?: string | null;
    last_cycle_at?: string | null;
  };
  enterprise_ready_pass?: boolean;
  failed_checks?: string[];
  check_results?: Array<{ name: string; pass: boolean; remediation_url?: string | null }>;
  remediation_links?: Record<string, string>;
};

export type MonitoringRuntimeStatus = {
  continuity_slo?: {
    pass?: boolean;
    heartbeat_age_seconds?: number | null;
    telemetry_age_seconds?: number | null;
    detection_age_seconds?: number | null;
    detection_pipeline_age_seconds?: number | null;
    detection_eval_age_seconds?: number | null;
    heartbeat_threshold_seconds?: number | null;
    telemetry_threshold_seconds?: number | null;
    detection_threshold_seconds?: number | null;
    thresholds_seconds?: WorkspaceMonitoringSummary['thresholds_seconds'];
    required_thresholds_seconds?: WorkspaceMonitoringSummary['required_thresholds_seconds'];
    continuity_thresholds_seconds?: WorkspaceMonitoringSummary['required_thresholds_seconds'];
    reason_codes?: string[];
  };
  error?: {
    code?: string;
    type?: string;
    message?: string;
    stage?: string;
    hint?: string;
  };
  field_reason_codes?: Record<string, string[]>;
  monitoring_status?: 'live' | 'limited' | 'offline';
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
  runtime_degraded_reason_codes?: string[];
  runtime_status_reason_codes?: string[];
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
  workspace_monitoring_summary?: WorkspaceMonitoringSummary;
  workspace_configured?: boolean;
  monitoring_mode?: 'live' | 'hybrid' | 'simulator' | 'offline' | 'unavailable';
  runtime_status?: 'live' | 'degraded' | 'offline' | 'idle';
  configured_systems?: number;
  reporting_systems?: number;
  coverage_state?: { configured_systems: number; reporting_systems: number; protected_assets: number };
  last_heartbeat_at?: string | null;
  last_telemetry_at?: string | null;
  last_coverage_telemetry_at?: string | null;
  telemetry_kind?: 'coverage' | 'target_event' | null;
  refreshed_at?: string | null;
  last_poll_at?: string | null;
  poll_freshness_status?: 'fresh' | 'stale' | 'unavailable';
  last_detection_at?: string | null;
  evidence_source?: 'live' | 'simulator' | 'replay' | 'none';
  status_reason?: string | null;
  configuration_reason?: string | null;
  configuration_reason_codes?: string[];
  contradiction_flags?: string[];
  guard_flags?: string[];
  db_failure_classification?: string | null;
  db_failure_reason?: string | null;
  coverage_only_warning?: WorkspaceMonitoringSummary['coverage_only_warning'];
  open_alerts?: number;
  active_incidents?: number;
  proof_chain_status?: 'complete' | 'incomplete' | string;
  proof_chain_missing?: string[];
  enterprise_ready_pass?: boolean;
  failed_checks?: string[];
  check_results?: Array<{ name: string; pass: boolean; remediation_url?: string | null }>;
  remediation_links?: Record<string, string>;
  continuity_slo_pass?: boolean;
  heartbeat_age_seconds?: number | null;
  telemetry_age_seconds?: number | null;
  detection_age_seconds?: number | null;
  heartbeat_threshold_seconds?: number | null;
  telemetry_threshold_seconds?: number | null;
  detection_threshold_seconds?: number | null;
  thresholds_seconds?: WorkspaceMonitoringSummary['thresholds_seconds'];
  required_thresholds_seconds?: WorkspaceMonitoringSummary['required_thresholds_seconds'];
  continuity_thresholds_seconds?: WorkspaceMonitoringSummary['required_thresholds_seconds'];
};

export type MonitoringTimelineLinkName =
  | 'monitoring_run'
  | 'telemetry_event'
  | 'detection'
  | 'detection_evidence'
  | 'alert'
  | 'incident'
  | 'response_action';

export type MonitoringInvestigationTimelineItem = {
  id: string;
  timestamp?: string | null;
  link_name: MonitoringTimelineLinkName | string;
  table_name?: string;
  evidence_source?: 'live' | 'simulator' | string;
};

export type MonitoringInvestigationTimeline = {
  ok?: boolean;
  workspace_id?: string;
  proof_chain_status: 'complete' | 'incomplete' | string;
  correlation_id?: string | null;
  linked_evidence_count?: number;
  chain_linked_ids?: {
    detection_id?: string | null;
    alert_id?: string | null;
    incident_id?: string | null;
    action_id?: string | null;
  } | null;
  canonical_collections?: Record<string, unknown>;
  collections?: Record<string, unknown>;
  items: MonitoringInvestigationTimelineItem[];
  missing?: string[];
};

export function runtimeStatusModeFromMonitoringStatus(value: MonitoringRuntimeStatus['monitoring_status']): MonitoringMode {
  if (value === 'live') {
    return 'LIVE';
  }
  if (value === 'offline') {
    return 'OFFLINE';
  }
  if (value === 'limited') {
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
