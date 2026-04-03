export type MonitoringMode = 'DEMO' | 'LIVE' | 'HYBRID' | 'DEGRADED';

export type MonitoringRuntimeStatus = {
  mode: MonitoringMode;
  configured_mode?: MonitoringMode;
  source_type?: string | null;
  provider_health?: 'healthy' | 'degraded';
  provider_reachable?: boolean;
  latest_processed_block?: number | null;
  checkpoint_lag_blocks?: number | null;
  checkpoint_age_seconds?: number | null;
  degraded_reason?: string | null;
  sales_claims_allowed?: boolean;
  claim_validator_status?: 'PASS' | 'FAIL' | string;
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
