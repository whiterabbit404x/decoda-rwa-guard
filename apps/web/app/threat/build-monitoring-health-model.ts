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
  assets?: any[];
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
  const primaryAsset = (input.assets ?? []).find((asset) => String(asset?.identifier ?? '').toLowerCase() === 'demo-seed-wallet-monitor');
  const treasuryAssetLabel = primaryAsset ? String(primaryAsset.name || 'Treasury-backed asset') : null;
  const issuerContractLabel = (input.targets ?? []).find((target) => String(target?.name ?? '').includes('Issuer Contract'))?.name ?? null;
  const custodyWalletLabel = (input.targets ?? []).find((target) => String(target?.name ?? '').includes('Custody Wallet'))?.name ?? null;
  const oracleNavLabel = Array.isArray(primaryAsset?.oracle_sources) && primaryAsset.oracle_sources[0]
    ? `Oracle/NAV: ${typeof primaryAsset.oracle_sources[0] === 'string' ? primaryAsset.oracle_sources[0] : (primaryAsset.oracle_sources[0].feed_name ?? 'configured')}`
    : null;
  const redemptionPathLabel = Array.isArray(primaryAsset?.expected_flow_patterns) && primaryAsset.expected_flow_patterns[0]
    ? 'Redemption path metadata configured'
    : null;
  const complianceSourceLabel = Array.isArray(primaryAsset?.jurisdiction_tags) && primaryAsset.jurisdiction_tags.find((value: string) => String(value).includes('compliance_source'))
    ? 'Compliance source metadata configured'
    : null;

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
