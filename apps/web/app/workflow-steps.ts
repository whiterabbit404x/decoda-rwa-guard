export const WORKFLOW_STEP_ORDER = [
  'workspace_created',
  'asset_created',
  'asset_verified',
  'monitoring_target_created',
  'monitored_system_created',
  'worker_reporting',
  'telemetry_received',
  'detection_created',
  'alert_created',
  'incident_opened',
  'response_ready',
  'evidence_export_ready',
] as const;

export const WORKFLOW_STEP_LABELS: Record<string, string> = {
  workspace_created: 'Workspace created',
  asset_created: 'Asset created',
  asset_verified: 'Asset verified',
  monitoring_target_created: 'Monitoring target created',
  monitored_system_created: 'Monitored system created',
  worker_reporting: 'Worker reporting',
  telemetry_received: 'Telemetry received',
  detection_created: 'Detection created',
  alert_created: 'Alert created',
  incident_opened: 'Incident opened',
  response_ready: 'Response ready',
  evidence_export_ready: 'Evidence export ready',
};

export const NEXT_ACTION_CTA: Record<string, string> = {
  add_asset: 'Add a protected asset',
  verify_asset: 'Verify asset',
  create_monitoring_target: 'Connect a monitoring target',
  enable_monitored_system: 'Enable monitoring',
  start_simulator_signal: 'Waiting for first telemetry',
  view_detection: 'View detection',
  open_incident: 'Open incident',
  export_evidence_package: 'Export evidence package',
  review_reason_codes: 'Review reason codes',
};

export const ONBOARDING_TOP_STEPPER = [
  { label: 'Workspace', canonicalStepId: 'workspace_created' },
  { label: 'Add Asset', canonicalStepId: 'asset_created' },
  { label: 'Connect Monitoring', canonicalStepId: 'monitoring_target_created' },
  { label: 'Enable System', canonicalStepId: 'monitored_system_created' },
  { label: 'First Signal', canonicalStepId: 'telemetry_received' },
] as const;
