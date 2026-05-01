const HARD_GUARD_FLAGS = new Set([
  'offline_with_current_telemetry',
  'telemetry_unavailable_with_high_confidence',
  'live_monitoring_without_reporting_systems',
  'live_telemetry_verified_without_timestamp',
  'idle_runtime_with_active_monitoring_claim',
  'coverage_only_persistent_no_evidence',
]);

type RuntimeWorkspaceStatus = {
  runtime_status?: string | null;
  status_reason?: string | null;
  db_failure_reason?: string | null;
  contradiction_flags?: string[] | null;
  guard_flags?: string[] | null;
  workspace_configured?: boolean | null;
  protected_assets_count?: number | null;
  monitored_systems_count?: number | null;
  reporting_systems_count?: number | null;
  active_alerts_count?: number | null;
  active_incidents_count?: number | null;
  last_telemetry_at?: string | null;
  last_detection_at?: string | null;
};

export type SecurityWorkspaceStatus = {
  posture: 'healthy' | 'degraded' | 'offline' | 'setup_required';
  customerMessage: string;
  protectedAssets: number;
  monitoredSystems: number;
  reportingSystems: number;
  openAlerts: number;
  activeIncidents: number;
  lastTelemetryAt: string | null;
  lastDetectionAt: string | null;
  recommendedNextAction: { label: string; href: string };
  details: string[];
};

function asCount(value: unknown): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
}

function toRuntimePosture(value: unknown): SecurityWorkspaceStatus['posture'] {
  const status = String(value ?? '').trim().toLowerCase();
  if (status === 'healthy' || status === 'live') return 'healthy';
  if (status === 'offline') return 'offline';
  if (status === 'degraded' || status === 'failed' || status === 'error') return 'degraded';
  if (status === 'setup_required' || status === 'idle' || status === 'disabled' || status === 'provisioning') return 'setup_required';
  return 'degraded';
}

function explicitSafeMessage(statusReason: string | null, telemetryAt: string | null, reportingSystems: number): string | null {
  const reason = String(statusReason ?? '').trim().toLowerCase();
  if (reason === 'summary_unavailable') return 'Monitoring summary temporarily unavailable';
  if (reason === 'db_failure') return 'Data service degraded';
  if (reason === 'stale_snapshot') return 'Showing last known state';
  if (reason === 'fetch_error') return 'Could not refresh live status';
  if (!telemetryAt) return 'No live signal received yet';
  if (reportingSystems === 0) return 'No active monitoring source';
  return null;
}

export function buildSecurityWorkspaceStatus(
  runtimeStatus: RuntimeWorkspaceStatus | null | undefined,
  detections: unknown[] | null | undefined,
  alerts: unknown[] | null | undefined,
  incidents: unknown[] | null | undefined,
  evidence: unknown[] | null | undefined,
): SecurityWorkspaceStatus {
  const protectedAssets = asCount(runtimeStatus?.protected_assets_count);
  const monitoredSystems = asCount(runtimeStatus?.monitored_systems_count);
  const reportingSystems = asCount(runtimeStatus?.reporting_systems_count);
  const openAlerts = asCount(runtimeStatus?.active_alerts_count) || (Array.isArray(alerts) ? alerts.length : 0);
  const activeIncidents = asCount(runtimeStatus?.active_incidents_count) || (Array.isArray(incidents) ? incidents.length : 0);
  const lastTelemetryAt = runtimeStatus?.last_telemetry_at ?? null;
  const lastDetectionAt = runtimeStatus?.last_detection_at ?? null;

  const contradictionFlags = Array.isArray(runtimeStatus?.contradiction_flags) ? runtimeStatus?.contradiction_flags : [];
  const guardFlags = Array.isArray(runtimeStatus?.guard_flags) ? runtimeStatus?.guard_flags : [];
  const hardGuardTriggered = [...contradictionFlags, ...guardFlags].some((flag) => HARD_GUARD_FLAGS.has(flag));

  const basePosture = toRuntimePosture(runtimeStatus?.runtime_status);
  let posture: SecurityWorkspaceStatus['posture'] = basePosture;

  const telemetryUnavailable = !lastTelemetryAt;
  const setupRequiredByCoverage = reportingSystems === 0 || monitoredSystems === 0 || protectedAssets === 0;

  if (runtimeStatus?.db_failure_reason || hardGuardTriggered || contradictionFlags.length > 0 || guardFlags.length > 0) {
    posture = basePosture === 'offline' ? 'offline' : 'degraded';
  }
  if (setupRequiredByCoverage && posture === 'healthy') {
    posture = 'setup_required';
  }
  if (telemetryUnavailable && posture === 'healthy') {
    posture = 'degraded';
  }

  const safeMappedMessage = explicitSafeMessage(runtimeStatus?.status_reason ?? null, lastTelemetryAt, reportingSystems);
  const customerMessage = safeMappedMessage
    ?? (posture === 'offline'
      ? 'Workspace monitoring is currently offline'
      : posture === 'setup_required'
        ? 'Complete setup to enable continuous monitoring'
        : posture === 'degraded'
          ? 'Monitoring coverage is degraded and needs attention'
          : 'Monitoring is active and operating normally');

  const details: string[] = [];
  if (safeMappedMessage) details.push(safeMappedMessage);
  if (Array.isArray(detections) && detections.length > 0) details.push(`${detections.length} detections recorded`);
  if (Array.isArray(evidence) && evidence.length > 0) details.push(`${evidence.length} evidence items available`);
  if (guardFlags.length > 0 || contradictionFlags.length > 0) details.push('Status guarded due to conflicting runtime signals');

  const recommendedNextAction = posture === 'offline'
    ? { label: 'Open monitoring settings', href: '/settings/security' }
    : posture === 'setup_required'
      ? { label: 'Finish monitoring setup', href: '/onboarding' }
      : posture === 'degraded'
        ? { label: 'Review alerts and incidents', href: '/alerts' }
        : { label: 'View threat operations', href: '/threat' };

  return {
    posture,
    customerMessage,
    protectedAssets,
    monitoredSystems,
    reportingSystems,
    openAlerts,
    activeIncidents,
    lastTelemetryAt,
    lastDetectionAt,
    recommendedNextAction,
    details,
  };
}
