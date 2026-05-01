import type { NormalizedRisk } from './dashboard-data';

export function renderRiskLabel(risk?: NormalizedRisk | null): string {
  if (!risk) return 'Risk profile pending evidence';
  if (risk.contagion_risk_label === 'guarded_due_to_stale_telemetry') return 'Guarded state (telemetry stale)';
  if (risk.exposure_severity === 'critical') return 'Critical exposure';
  if (risk.exposure_severity === 'high') return 'High exposure';
  return 'Contained exposure';
}

