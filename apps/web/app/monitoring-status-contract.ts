export type MonitoringMode = 'LIVE' | 'DEGRADED' | 'OFFLINE' | 'STALE' | 'LIMITED_COVERAGE';

export type MonitoringRuntimeStatus = {
  mode: MonitoringMode;
  configured_mode?: MonitoringMode;
  status?: string;
  detection_outcome?: 'DETECTION_CONFIRMED' | 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE' | 'NO_EVIDENCE' | 'MONITORING_DEGRADED' | 'ANALYSIS_FAILED' | 'DEMO_ONLY' | string;
  source_type?: string | null;
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
  synthetic_leak_detected?: boolean;
};

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
