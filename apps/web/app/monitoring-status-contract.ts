export type MonitoringMode = 'DEMO' | 'LIVE' | 'HYBRID' | 'DEGRADED';

export type MonitoringRuntimeStatus = {
  mode: MonitoringMode;
  configured_mode?: MonitoringMode;
  status?: string;
  source_type?: string | null;
  provider_health?: 'healthy' | 'degraded';
  provider_reachable?: boolean;
  claim_safe?: boolean;
  synthetic?: boolean;
  evidence_present?: boolean;
  latest_processed_block?: number | null;
  checkpoint_lag_blocks?: number | null;
  checkpoint_age_seconds?: number | null;
  provider_name?: string | null;
  provider_kind?: string | null;
  degraded_reason?: string | null;
  sales_claims_allowed?: boolean;
  claim_validator_status?: 'PASS' | 'FAIL' | string;
  recent_evidence_state?: 'real' | 'demo' | 'degraded' | 'missing' | 'failed' | 'no_evidence' | string;
  recent_truthfulness_state?: 'claim_safe' | 'not_claim_safe' | 'unknown_risk' | string;
  recent_real_event_count?: number;
  last_real_event_at?: string | null;
  recent_confidence_basis?: 'provider_evidence' | 'backfill_evidence' | 'demo_scenario' | 'none' | string;
  synthetic_leak_detected?: boolean;
};

export function normalizeMonitoringMode(value: unknown): MonitoringMode {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (normalized === 'LIVE' || normalized === 'HYBRID' || normalized === 'DEGRADED') {
    return normalized;
  }
  return 'DEMO';
}

export function monitoringModeLabel(mode: MonitoringMode): string {
  if (mode === 'DEGRADED') {
    return 'DEGRADED';
  }
  return `${mode} MODE`;
}
