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
  targets?: any[];
};

export function buildMonitoringHealthModel(input: BuildMonitoringHealthModelInput) {
  const securityStatus = buildSecurityWorkspaceStatus(
    input.runtimeStatusSnapshot,
    input.detections,
    input.alerts,
    input.incidents,
    input.evidence,
  );

  const runtimeFreshness = String(input.runtimeStatusSnapshot?.freshness_status ?? 'unavailable');
  const runtimeConfidence = String(input.runtimeStatusSnapshot?.confidence_status ?? 'unavailable');
  const reportingSystems = Number(
    input.runtimeStatusSnapshot?.reporting_systems
    ?? 0,
  );
  const configuredSystems = Number(input.runtimeStatusSnapshot?.monitored_systems_count ?? 0);
  const statusLabel = securityStatus.posture === 'healthy'
    ? 'LIVE'
    : securityStatus.posture === 'offline'
      ? 'OFFLINE'
      : securityStatus.posture === 'setup_required'
        ? 'SETUP REQUIRED'
        : 'DEGRADED';
  const healthClaim = securityStatus.customerMessage;
  const targetRows = input.targets ?? [];
  const treasuryAssetLabel = targetRows.find((target) => String(target?.name ?? '').includes('Wallet Monitor'))?.name
    ? 'Treasury-backed asset'
    : null;
  const issuerContractLabel = targetRows.find((target) => String(target?.name ?? '').includes('Issuer Contract'))?.name ?? null;
  const custodyWalletLabel = targetRows.find((target) => String(target?.name ?? '').includes('Custody Wallet'))?.name ?? null;
  const oracleNavLabel = 'Oracle/NAV feed source';
  const redemptionPathLabel = 'Redemption path metadata';
  const complianceSourceLabel = 'Compliance source metadata';

  return {
    securityStatus,
    statusLabel,
    healthClaim,
    reportingSystems,
    configuredSystems,
    freshnessStatus: runtimeFreshness,
    confidenceStatus: runtimeConfidence,
    telemetryAt: input.telemetryAt,
    heartbeatAt: input.heartbeatAt,
    pollAt: input.pollAt,
    contradictionFlags: input.contradictionFlags,
    continuityChecks: input.continuityChecks,
    domainLabels: [treasuryAssetLabel, issuerContractLabel, custodyWalletLabel, oracleNavLabel, redemptionPathLabel, complianceSourceLabel].filter(Boolean) as string[],
  };
}
