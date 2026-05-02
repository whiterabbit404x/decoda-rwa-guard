import type { MonitoringRuntimeStatus } from '../monitoring-status-contract';
import { buildSecurityWorkspaceStatus } from '../security-workspace-status';

type BuildMonitoringHealthModelInput = {
  runtimeStatusSnapshot: MonitoringRuntimeStatus | null;
  detections: any[];
  alerts: any[];
  incidents: any[];
  evidence: any[];
  telemetryAt: string | null;
  heartbeatAt: string | null;
  pollAt: string | null;
  contradictionFlags: string[];
  continuityChecks: string[];
};

export function buildMonitoringHealthModel(input: BuildMonitoringHealthModelInput) {
  const securityStatus = buildSecurityWorkspaceStatus(
    input.runtimeStatusSnapshot,
    input.detections,
    input.alerts,
    input.incidents,
    input.evidence,
  );

  return {
    securityStatus,
    telemetryAt: input.telemetryAt,
    heartbeatAt: input.heartbeatAt,
    pollAt: input.pollAt,
    contradictionFlags: input.contradictionFlags,
    continuityChecks: input.continuityChecks,
  };
}
