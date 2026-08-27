// ---------------------------------------------------------------------------
// Customer-facing copy for canonical runtime-status reason codes.
//
// Reason codes arrive from the backend either bare (`stale_telemetry`) or guard
// prefixed (`guard:incident_exists_without_alert`) when a hard guard won the
// prioritization in build_workspace_monitoring_summary. Both spell the same
// condition, so both resolve to the same sentence — the raw wire code is never
// what a customer reads.
// ---------------------------------------------------------------------------

const REASON_CODE_MESSAGES: Record<string, string> = {
  summary_unavailable: 'Runtime summary is unavailable. Recheck workspace connectivity.',
  workspace_unconfigured: 'Workspace setup is incomplete. Finish onboarding to enable live monitoring.',
  no_reporting_systems: 'No monitored systems are reporting. Enable and verify monitoring sources.',
  stale_telemetry: 'Telemetry is stale. Investigate worker health and source ingestion lag.',
  no_live_evidence: 'No live evidence has been persisted yet. Trigger and validate a real detection path.',
  live_worker_not_running: 'The monitoring worker is not running. Deploy the worker service with WORKER_ENABLED=true and EVM_RPC_URL set.',
  stale_heartbeat: 'Stable RPC polling worker heartbeat is stale. The polling worker may have stopped or lost its database connection. (The realtime WebSocket worker is reported separately.)',
  targets_blocked: 'The monitoring worker is alive, but one or more targets are blocked (dead-lettered) and are not being polled. Recover the affected target(s) to resume live coverage.',
  no_fresh_live_coverage_telemetry: 'Worker is running but has not received live chain data. Check EVM_RPC_URL connectivity in the worker service.',
  // Stable RPC polling is proven alive (fresh heartbeat/poll) but realtime is paused: this
  // is NOT an RPC connectivity problem, so the limitation must say so instead of blaming
  // EVM_RPC_URL. Mirrors the telemetry page's worker-status strip wording.
  realtime_paused_stable_polling_active: 'Realtime paused; stable polling active. Wallet transfers are detected by Stable RPC Polling.',
  // Stable RPC polling is active with realtime enabled — the loop is live and simply
  // awaiting new on-chain activity on monitored addresses (not an RPC failure).
  stable_polling_active_awaiting_coverage: 'Stable RPC polling is active. Awaiting new on-chain activity on monitored addresses.',
  runtime_contradiction_asset_monitoring_attached_but_no_monitored_systems: 'Assets are registered, but monitoring is not attached to any running systems.',
  runtime_contradiction_asset_count_mismatch_runtime_vs_registry: 'Asset counts are out of sync between registry and runtime.',
  runtime_contradiction_healthy_claim_with_reporting_systems_zero: 'Health cannot be verified because no systems are reporting.',
  runtime_contradiction_live_claim_with_no_telemetry: 'Live mode cannot be verified because telemetry is missing.',
  runtime_contradiction_simulator_evidence_rendered_as_live_provider: 'Simulator evidence was labeled as live provider data.',
  runtime_contradiction_alert_without_detection: 'An alert exists without linked detection evidence.',
  runtime_contradiction_incident_without_alert: 'An incident exists without a linked alert.',
  runtime_contradiction_response_action_without_incident: 'A response action exists without a linked incident.',
  live_monitoring_without_reporting_systems: 'Live monitoring requires at least one reporting monitored system.',
  asset_monitoring_attached_but_no_monitored_systems: 'Assets are configured but no monitored system is attached.',
  simulator_evidence_claimed_as_live_provider: 'Simulator telemetry is being represented as live provider evidence.',
  alert_exists_without_detection: 'Alerts must be backed by at least one detection.',
  incident_exists_without_alert: 'Incidents must be linked to at least one alert.',
  response_action_exists_without_incident: 'Response actions must be linked to an incident.',
  cross_page_count_mismatch: 'Cross-page count mismatch detected. Reconcile canonical runtime totals before proceeding.',
};


export function runtimeReasonMessage(code: string): string {
  const normalized = String(code ?? '').trim();
  const bare = normalized.startsWith('guard:') ? normalized.slice('guard:'.length) : normalized;
  return REASON_CODE_MESSAGES[normalized]
    ?? REASON_CODE_MESSAGES[bare]
    ?? `Runtime condition: ${bare.replaceAll('_', ' ')}.`;
}

export { REASON_CODE_MESSAGES };
