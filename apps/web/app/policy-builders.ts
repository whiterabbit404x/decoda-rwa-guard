export type Severity = 'low' | 'medium' | 'high' | 'critical';

export type ThreatPolicy = {
  risky_approvals_enabled: boolean;
  unlimited_approval_detection_enabled: boolean;
  unknown_target_threshold: number;
  privileged_function_sensitivity: Severity;
  large_transfer_threshold: number;
  allowlist: string[];
  denylist: string[];
  escalation_map: Record<Severity, Severity>;
};

export type CompliancePolicy = {
  evidence_retention_period_days: number;
  required_review_checklist: string[];
  required_approvers_count: number;
  classification_mapping: Record<string, string>;
  exception_policy: 'blocked' | 'manual_review' | 'owner_approval';
  reporting_profile: 'standard' | 'regulated' | 'enterprise';
};

export type ResiliencePolicy = {
  oracle_dependency_checks_enabled: boolean;
  oracle_sensitivity_threshold: number;
  settlement_control_checks_enabled: boolean;
  control_concentration_threshold: number;
  privileged_role_change_alerts: boolean;
  emergency_trigger_threshold: Severity;
  monitoring_cadence_minutes: number;
};

export const threatDefaults: ThreatPolicy = {
  risky_approvals_enabled: true,
  unlimited_approval_detection_enabled: true,
  unknown_target_threshold: 2,
  privileged_function_sensitivity: 'high',
  large_transfer_threshold: 250000,
  allowlist: [],
  denylist: [],
  escalation_map: { low: 'low', medium: 'medium', high: 'high', critical: 'critical' },
};

export const complianceDefaults: CompliancePolicy = {
  evidence_retention_period_days: 90,
  required_review_checklist: ['kyc', 'jurisdiction'],
  required_approvers_count: 2,
  classification_mapping: { pii: 'restricted', transaction: 'confidential' },
  exception_policy: 'manual_review',
  reporting_profile: 'standard',
};

export const resilienceDefaults: ResiliencePolicy = {
  oracle_dependency_checks_enabled: true,
  oracle_sensitivity_threshold: 70,
  settlement_control_checks_enabled: true,
  control_concentration_threshold: 65,
  privileged_role_change_alerts: true,
  emergency_trigger_threshold: 'high',
  monitoring_cadence_minutes: 15,
};

function bool(input: unknown, fallback: boolean): boolean {
  return typeof input === 'boolean' ? input : fallback;
}
function num(input: unknown, fallback: number): number {
  if (typeof input === 'number' && Number.isFinite(input)) return input;
  const parsed = Number(input);
  return Number.isFinite(parsed) ? parsed : fallback;
}
function severity(input: unknown, fallback: Severity): Severity {
  return ['low', 'medium', 'high', 'critical'].includes(String(input)) ? (input as Severity) : fallback;
}
function list(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  return input.map((x) => String(x).trim()).filter(Boolean);
}

export function normalizeThreatPolicy(input: Record<string, unknown>): ThreatPolicy {
  return {
    risky_approvals_enabled: bool(input.risky_approvals_enabled, threatDefaults.risky_approvals_enabled),
    unlimited_approval_detection_enabled: bool(input.unlimited_approval_detection_enabled, threatDefaults.unlimited_approval_detection_enabled),
    unknown_target_threshold: num(input.unknown_target_threshold, threatDefaults.unknown_target_threshold),
    privileged_function_sensitivity: severity(input.privileged_function_sensitivity, threatDefaults.privileged_function_sensitivity),
    large_transfer_threshold: num(input.large_transfer_threshold, threatDefaults.large_transfer_threshold),
    allowlist: list(input.allowlist),
    denylist: list(input.denylist),
    escalation_map: {
      low: severity((input.escalation_map as any)?.low, 'low'),
      medium: severity((input.escalation_map as any)?.medium, 'medium'),
      high: severity((input.escalation_map as any)?.high, 'high'),
      critical: severity((input.escalation_map as any)?.critical, 'critical'),
    },
  };
}

export function normalizeCompliancePolicy(input: Record<string, unknown>): CompliancePolicy {
  return {
    evidence_retention_period_days: num(input.evidence_retention_period_days, complianceDefaults.evidence_retention_period_days),
    required_review_checklist: list(input.required_review_checklist),
    required_approvers_count: num(input.required_approvers_count, complianceDefaults.required_approvers_count),
    classification_mapping: typeof input.classification_mapping === 'object' && input.classification_mapping
      ? Object.fromEntries(Object.entries(input.classification_mapping as Record<string, unknown>).map(([key, value]) => [key, String(value)]))
      : complianceDefaults.classification_mapping,
    exception_policy: ['blocked', 'manual_review', 'owner_approval'].includes(String(input.exception_policy)) ? (input.exception_policy as CompliancePolicy['exception_policy']) : complianceDefaults.exception_policy,
    reporting_profile: ['standard', 'regulated', 'enterprise'].includes(String(input.reporting_profile)) ? (input.reporting_profile as CompliancePolicy['reporting_profile']) : complianceDefaults.reporting_profile,
  };
}

export function normalizeResiliencePolicy(input: Record<string, unknown>): ResiliencePolicy {
  return {
    oracle_dependency_checks_enabled: bool(input.oracle_dependency_checks_enabled, resilienceDefaults.oracle_dependency_checks_enabled),
    oracle_sensitivity_threshold: num(input.oracle_sensitivity_threshold, resilienceDefaults.oracle_sensitivity_threshold),
    settlement_control_checks_enabled: bool(input.settlement_control_checks_enabled, resilienceDefaults.settlement_control_checks_enabled),
    control_concentration_threshold: num(input.control_concentration_threshold, resilienceDefaults.control_concentration_threshold),
    privileged_role_change_alerts: bool(input.privileged_role_change_alerts, resilienceDefaults.privileged_role_change_alerts),
    emergency_trigger_threshold: severity(input.emergency_trigger_threshold, resilienceDefaults.emergency_trigger_threshold),
    monitoring_cadence_minutes: num(input.monitoring_cadence_minutes, resilienceDefaults.monitoring_cadence_minutes),
  };
}

export function parseTagInput(input: string): string[] {
  return input
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}
