import type { MonitoringRuntimeStatus, WorkspaceMonitoringSummary } from './monitoring-status-contract';

type CanonicalRequiredKeys =
  | 'workspace_configured'
  | 'monitoring_status'
  | 'freshness_status'
  | 'confidence_status'
  | 'protected_assets'
  | 'monitored_systems'
  | 'reporting_systems'
  | 'last_heartbeat_at'
  | 'last_poll_at'
  | 'last_telemetry_at'
  | 'last_detection_at'
  | 'reason_codes'
  | 'contradiction_flags'
  | 'next_required_action';

type MissingKeys<T, Keys extends PropertyKey> = Exclude<Keys, keyof T>;
type AssertNoMissing<T extends never> = T;

// Fail compilation if the canonical runtime summary drops required keys.
type _SummaryHasRequiredCanonicalKeys = AssertNoMissing<MissingKeys<WorkspaceMonitoringSummary, CanonicalRequiredKeys>>;

// Fail compilation if runtime top-level aliases drop required keys.
type _RuntimeHasRequiredCanonicalAliases = AssertNoMissing<MissingKeys<MonitoringRuntimeStatus, CanonicalRequiredKeys>>;

export {};
