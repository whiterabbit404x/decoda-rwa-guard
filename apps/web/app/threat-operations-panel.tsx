'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import type { MonitoringPresentationStatus } from './monitoring-status-presentation';
import { usePilotAuth } from 'app/pilot-auth-context';
import { hasLiveTelemetry, monitoringHealthyCopyAllowed } from './workspace-monitoring-truth';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

type Props = { apiUrl: string };
const RUNTIME_STATUS_PROXY_PATH = '/api/ops/monitoring/runtime-status';
const MONITORING_SYSTEMS_PROXY_PATH = '/api/monitoring/systems';

type TargetRow = {
  id: string;
  name: string;
  target_type?: string;
  contract_identifier?: string | null;
  wallet_address?: string | null;
  chain_network?: string | null;
  monitoring_enabled?: boolean;
  last_checked_at?: string | null;
  last_run_status?: string | null;
  asset_type?: string | null;
  health_status?: string | null;
  health_reason?: string | null;
  asset_missing?: boolean;
};

type MonitoredSystemRow = {
  id: string;
  target_id?: string | null;
  target_name?: string | null;
  asset_name?: string | null;
  chain?: string | null;
  is_enabled?: boolean;
  runtime_status?: string | null;
  freshness_status?: string | null;
  confidence_status?: string | null;
  status?: string | null;
  last_heartbeat?: string | null;
  last_event_at?: string | null;
  coverage_reason?: string | null;
  last_error_text?: string | null;
};

type AlertRow = {
  id: string;
  title: string;
  severity?: string;
  status?: string;
  created_at?: string;
  explanation?: string;
  payload?: Record<string, any>;
  findings?: Record<string, any>;
  alert_type?: string;
  source?: string;
  source_service?: string;
  target_id?: string;
  response_action_mode?: string | null;
};

type IncidentRow = {
  id: string;
  title?: string;
  event_type?: string;
  severity?: string;
  status?: string;
  created_at?: string;
  response_action_mode?: string | null;
};

type HistoryRun = {
  id: string;
  title: string;
  created_at: string;
};
type MonitoringRunRow = {
  id: string;
  started_at?: string | null;
  completed_at?: string | null;
  status?: string | null;
  trigger_type?: string | null;
  systems_checked_count?: number | null;
  assets_checked_count?: number | null;
  detections_created_count?: number | null;
  alerts_created_count?: number | null;
  telemetry_records_seen_count?: number | null;
  notes?: string | null;
};
type EvidenceRow = {
  id: string;
  observed_at?: string;
  severity?: string;
  summary?: string;
  event_type?: string;
  tx_hash?: string | null;
  block_number?: number | null;
  counterparty?: string | null;
  amount_text?: string | null;
  token_address?: string | null;
  contract_address?: string | null;
  risk_score?: number | null;
  rule_label?: string | null;
  source_provider?: string | null;
  asset_name?: string | null;
  target_name?: string | null;
};
type DetectionRow = {
  id: string;
  monitored_system_id?: string | null;
  protected_asset_id?: string | null;
  detection_type?: string | null;
  severity?: string | null;
  confidence?: number | null;
  title?: string | null;
  evidence_summary?: string | null;
  evidence_source?: string | null;
  source_rule?: string | null;
  status?: string | null;
  detected_at?: string | null;
  raw_evidence_json?: Record<string, any> | null;
  monitoring_run_id?: string | null;
  linked_alert_id?: string | null;
};

type ThreatFeedState = 'Live' | 'Historical' | 'Test' | 'Stale' | 'Investigating' | 'Resolved';
export type PageOperationalState =
  | 'healthy_live'
  | 'configured_no_signals'
  | 'degraded_partial'
  | 'offline_no_telemetry'
  | 'unconfigured_workspace'
  | 'fetch_error';

type SnapshotFailureKey = 'targets' | 'systems' | 'alerts' | 'incidents' | 'history' | 'evidence' | 'runs' | 'detections';

const STRUCTURAL_CONFIGURATION_REASON_CODES = new Set([
  'no_valid_protected_assets',
  'no_linked_monitored_systems',
  'no_persisted_enabled_monitoring_config',
  'target_system_linkage_invalid',
]);

export function hasRuntimeQueryFailureMarker(params: {
  statusReason?: string | null;
  configurationReason?: string | null;
  configurationReasonCodes?: string[];
  fieldReasonCodes?: Record<string, string[] | undefined> | null;
  runtimeErrorCode?: string | null;
  runtimeDegradedReason?: string | null;
  runtimeMonitoringStatus?: string | null;
  summaryStatusReason?: string | null;
  summaryConfigurationReason?: string | null;
  summaryConfigurationReasonCodes?: string[];
}): boolean {
  const {
    statusReason,
    configurationReason,
    configurationReasonCodes = [],
    fieldReasonCodes,
    runtimeErrorCode,
    runtimeDegradedReason,
    runtimeMonitoringStatus,
    summaryStatusReason,
    summaryConfigurationReason,
    summaryConfigurationReasonCodes = [],
  } = params;
  const statusReasonValues = [statusReason, summaryStatusReason]
    .map((reason) => String(reason ?? '').toLowerCase())
    .filter(Boolean);
  const configurationReasonValues = [configurationReason, summaryConfigurationReason]
    .map((reason) => String(reason ?? '').toLowerCase())
    .filter(Boolean);
  const errorCodeValue = String(runtimeErrorCode ?? '').toLowerCase();
  const degradedReasonValue = String(runtimeDegradedReason ?? '').toLowerCase();
  const monitoringStatusValue = String(runtimeMonitoringStatus ?? '').toLowerCase();
  const summaryAndTopLevelReasonCodes = [...configurationReasonCodes, ...summaryConfigurationReasonCodes]
    .map((code) => String(code ?? '').toLowerCase())
    .filter(Boolean);
  const normalizedFieldReasonCodes = Object.values(fieldReasonCodes ?? {})
    .flatMap((codes) => (Array.isArray(codes) ? codes : []))
    .map((code) => String(code ?? '').toLowerCase())
    .filter(Boolean);
  const reasonCodes = [...summaryAndTopLevelReasonCodes, ...normalizedFieldReasonCodes];
  const queryFailureCodePresent = reasonCodes.some((code) => (
    code.includes('query_failure')
    || code.includes('query_failed')
    || code.includes('query_error')
    || code.includes('database_error')
    || code.includes('db_error')
    || code.includes('runtime_status_unavailable')
  ));

  return statusReasonValues.some((value) => value.startsWith('runtime_status_degraded:database_error'))
    || (configurationReasonValues.includes('runtime_status_unavailable') && queryFailureCodePresent)
    || errorCodeValue.includes('database_error')
    || errorCodeValue.includes('query_failure')
    || degradedReasonValue.includes('database_error')
    || degradedReasonValue.includes('query_failure')
    || (monitoringStatusValue === 'error' && configurationReasonValues.includes('runtime_status_unavailable'));
}

type DetectionItem = {
  id: string;
  timestamp: string;
  severity: string;
  title: string;
  assetName: string;
  assetType: string;
  monitoringStatus: string;
  evidenceSummary: string;
  txHash?: string | null;
  blockNumber?: string | number | null;
  counterparty?: string | null;
  amount?: string | null;
  tokenOrContract?: string | null;
  ruleId?: string | null;
  sourceProvider?: string | null;
  targetName?: string | null;
  state: ThreatFeedState;
  href: string;
  source: 'alert' | 'incident' | 'evidence';
};

type TimelineItem = {
  id: string;
  timestamp: string;
  category: 'Telemetry Event' | 'Detection' | 'Alert' | 'Incident' | 'Action';
  description: string;
  href: string;
};

type EvidenceDrawerState = {
  title: string;
  summary: string;
  raw: Record<string, any> | null;
};

const TELEMETRY_STALE_MS = 20 * 60 * 1000;
const DETECTION_LIVE_MS = 15 * 60 * 1000;

function configurationReasonMessage(reason: string | null | undefined): string {
  switch (reason) {
    case 'no_valid_protected_assets':
      return 'No valid protected assets are linked to enabled monitoring yet.';
    case 'no_linked_monitored_systems':
      return 'No linked monitored systems exist for enabled workspace targets.';
    case 'no_persisted_enabled_monitoring_config':
      return 'No persisted enabled monitoring configuration exists yet.';
    case 'target_system_linkage_invalid':
      return 'Target/system linkage is invalid and must be repaired.';
    default:
      return 'Configuration is partial. Complete persisted asset, system, and linkage setup.';
  }
}

function formatRelativeTime(value?: string | null): string {
  if (!value) return 'Not available';
  const diffMs = Date.now() - new Date(value).getTime();
  if (Number.isNaN(diffMs) || diffMs < 0) return 'Not available';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatAbsoluteTime(value?: string | null): string {
  if (!value) return 'Not available';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Not available' : date.toLocaleString();
}

export function formatOperationalStateLabel(value: unknown): string {
  const normalized = String(value ?? '').trim();
  return normalized ? normalized.replaceAll('_', ' ') : 'unknown';
}

function severityClass(severity?: string) {
  const normalized = String(severity ?? '').toLowerCase();
  if (normalized.includes('critical')) return 'critical';
  if (normalized.includes('high')) return 'high';
  if (normalized.includes('medium')) return 'medium';
  return 'low';
}

function severityLabel(severity?: string) {
  const normalized = String(severity ?? '').toLowerCase();
  if (normalized.includes('critical')) return 'Critical';
  if (normalized.includes('high')) return 'High';
  if (normalized.includes('medium')) return 'Medium';
  return 'Low';
}

function isTestOrLabSignal(text: string | undefined): boolean {
  const value = String(text ?? '').toLowerCase();
  return ['test', 'lab', 'synthetic', 'simulation'].some((term) => value.includes(term));
}

function normalizeCoverageStatus(target: TargetRow): 'Full' | 'Partial' | 'Stale' | 'Missing' | 'Offline' {
  if (target.health_status === 'broken' || target.asset_missing) return 'Missing';
  if (!target.monitoring_enabled) return 'Offline';
  if (!target.last_checked_at) return 'Missing';
  const lastChecked = new Date(target.last_checked_at).getTime();
  if (Number.isNaN(lastChecked)) return 'Missing';
  if (Date.now() - lastChecked > TELEMETRY_STALE_MS) return 'Stale';
  if (!target.contract_identifier && !target.wallet_address) return 'Partial';
  return 'Full';
}

function coverageTone(status: ReturnType<typeof normalizeCoverageStatus>) {
  if (status === 'Full') return 'healthy';
  if (status === 'Partial' || status === 'Stale' || status === 'Missing') return 'attention';
  return 'offline';
}

function monitoringTone(status: MonitoringPresentationStatus) {
  if (status === 'live') return 'healthy';
  if (status === 'offline') return 'offline';
  return 'attention';
}

function stateTone(state: ThreatFeedState) {
  if (state === 'Live' || state === 'Investigating') return 'healthy';
  if (state === 'Resolved') return 'low';
  if (state === 'Test') return 'attention';
  return 'offline';
}

function categoryTone(category: TimelineItem['category']) {
  if (category === 'Alert' || category === 'Incident') return 'attention';
  if (category === 'Detection') return 'high';
  if (category === 'Action') return 'healthy';
  return 'low';
}

function displayIdentifier(target: TargetRow): string {
  if (target.wallet_address) {
    const value = target.wallet_address;
    return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
  }
  return target.contract_identifier || 'Identifier unavailable';
}

export function derivePageState(params: {
  loadingSnapshot: boolean;
  snapshotError: boolean;
  targets: TargetRow[];
  liveDetections: DetectionItem[];
  workspaceConfigured: boolean;
  freshnessStatus: string;
  contradictionFlags: string[];
  reportingSystems: number;
  runtimeStatus: string;
  monitoredSystems: number;
  hasLiveTelemetry: boolean;
  statusReason?: string | null;
  configurationReason?: string | null;
  configurationReasonCodes?: string[];
  runtimeErrorCode?: string | null;
  runtimeDegradedReason?: string | null;
  runtimeMonitoringStatus?: string | null;
  fieldReasonCodes?: Record<string, string[] | undefined> | null;
  summaryStatusReason?: string | null;
  summaryConfigurationReason?: string | null;
  summaryConfigurationReasonCodes?: string[];
}): PageOperationalState {
  const {
    loadingSnapshot,
    snapshotError,
    targets,
    liveDetections,
    workspaceConfigured,
    freshnessStatus,
    contradictionFlags,
    reportingSystems,
    runtimeStatus,
    monitoredSystems,
    hasLiveTelemetry,
    statusReason,
    configurationReason,
    configurationReasonCodes = [],
    runtimeErrorCode,
    runtimeDegradedReason,
    runtimeMonitoringStatus,
    fieldReasonCodes,
    summaryStatusReason,
    summaryConfigurationReason,
    summaryConfigurationReasonCodes = [],
  } = params;
  const runtimeQueryFailure = hasRuntimeQueryFailureMarker({
    statusReason,
    configurationReason,
    configurationReasonCodes,
    fieldReasonCodes,
    runtimeErrorCode,
    runtimeDegradedReason,
    runtimeMonitoringStatus,
    summaryStatusReason,
    summaryConfigurationReason,
    summaryConfigurationReasonCodes,
  });
  const structuralUnconfiguredReason = STRUCTURAL_CONFIGURATION_REASON_CODES.has(String(configurationReason ?? '').toLowerCase());

  if (runtimeQueryFailure) {
    return 'fetch_error';
  }

  if (!workspaceConfigured && structuralUnconfiguredReason && !runtimeQueryFailure) {
    return 'unconfigured_workspace';
  }
  if (!workspaceConfigured) return 'fetch_error';

  if (runtimeStatus === 'offline') {
    return 'offline_no_telemetry';
  }

  if (
    runtimeStatus === 'degraded'
    || runtimeStatus === 'failed'
    || runtimeStatus === 'disabled'
    || runtimeStatus === 'provisioning'
    || freshnessStatus === 'stale'
    || contradictionFlags.length > 0
  ) {
    return 'degraded_partial';
  }

  if (hasLiveTelemetry && liveDetections.length > 0) {
    return 'healthy_live';
  }

  if (hasLiveTelemetry) {
    return 'configured_no_signals';
  }

  return 'degraded_partial';
}

function formatSnapshotErrorMessage(failedEndpoints: SnapshotFailureKey[]): string | null {
  if (failedEndpoints.length === 0) return null;
  return `Monitoring snapshot partially unavailable (${failedEndpoints.length} endpoint${failedEndpoints.length === 1 ? '' : 's'} failed).`;
}

export function formatSystemsPanelWarning(failedEndpoints: SnapshotFailureKey[]): string | null {
  return failedEndpoints.includes('systems') ? 'Systems list unavailable' : null;
}

export function pageStatePrimaryCopy(state: PageOperationalState, configurationReason?: string | null): string {
  if (state === 'healthy_live') {
    return 'Live monitoring is healthy. Telemetry freshness and threat detections reflect current workspace conditions.';
  }
  if (state === 'configured_no_signals') {
    return 'Monitoring healthy. No active detections right now. Live telemetry remains current across reporting systems.';
  }
  if (state === 'unconfigured_workspace') {
    return `Workspace is not configured: ${configurationReasonMessage(configurationReason)} Live threat detection starts only after persisted linkage is valid.`;
  }
  if (state === 'offline_no_telemetry') {
    return 'Monitoring is offline.';
  }
  if (state === 'fetch_error') {
    return 'Backend telemetry/runtime retrieval failed, so monitoring data is temporarily unavailable.';
  }
  return 'Monitoring is partially degraded. Threat outcomes may be delayed or incomplete.';
}

function PageStateBanner({ state, telemetryLabel, pollLabel, reason, configurationReason }: { state: PageOperationalState; telemetryLabel: string; pollLabel: string; reason?: string | null; configurationReason?: string | null }) {
  if (state === 'healthy_live') {
    return <p className="explanation">{pageStatePrimaryCopy(state, configurationReason)}</p>;
  }
  if (state === 'configured_no_signals') {
    return <p className="explanation">{pageStatePrimaryCopy(state, configurationReason)}</p>;
  }
  if (state === 'unconfigured_workspace') {
    return <p className="explanation">{pageStatePrimaryCopy(state, configurationReason)}</p>;
  }
  if (state === 'offline_no_telemetry') {
    return <p className="explanation">{pageStatePrimaryCopy(state, configurationReason)} Reason: {reason || 'no active reporting systems'}. Add one monitored system and confirm telemetry flow.</p>;
  }
  if (state === 'fetch_error') {
    return (
      <div className="emptyStatePanel">
        <h4>Telemetry retrieval degraded</h4>
        <p className="muted">{pageStatePrimaryCopy(state, configurationReason)}</p>
        {reason ? <p className="tableMeta">Backend reason: {reason}</p> : null}
        <p className="tableMeta">Last telemetry: {telemetryLabel} · Last successful poll: {pollLabel}</p>
        <div className="buttonRow">
          <Link href="/threat" prefetch={false}>Retry</Link>
          <Link href="/integrations" prefetch={false}>Inspect backend integration status</Link>
          <Link href="/history" prefetch={false}>Review recent runtime history</Link>
        </div>
      </div>
    );
  }
  return <p className="explanation">Monitoring is partially degraded. Threat outcomes may be delayed or incomplete.</p>;
}

export default function ThreatOperationsPanel({ apiUrl }: Props) {
  const { authHeaders, isAuthenticated, user } = usePilotAuth();
  const feed = useLiveWorkspaceFeed();
  const [loadingSnapshot, setLoadingSnapshot] = useState(true);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [systemsPanelWarning, setSystemsPanelWarning] = useState<string | null>(null);
  const [targets, setTargets] = useState<TargetRow[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [incidents, setIncidents] = useState<IncidentRow[]>([]);
  const [historyRuns, setHistoryRuns] = useState<HistoryRun[]>([]);
  const [monitoringRuns, setMonitoringRuns] = useState<MonitoringRunRow[]>([]);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [detections, setDetections] = useState<DetectionRow[]>([]);
  const [monitoredSystems, setMonitoredSystems] = useState<MonitoredSystemRow[]>([]);
  const [evidenceDrawer, setEvidenceDrawer] = useState<EvidenceDrawerState | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function refreshSnapshot() {
      if (!active || !isAuthenticated || !user?.current_workspace?.id) {
        return;
      }
      try {
        const [targetsResult, systemsResult, alertsResult, incidentsResult, historyResult, evidenceResult, runsResult, detectionsResult] = await Promise.allSettled([
          fetch(`${apiUrl}/monitoring/targets`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(MONITORING_SYSTEMS_PROXY_PATH, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/alerts?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/incidents?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/pilot/history?limit=12`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/ops/monitoring/evidence?limit=50`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/monitoring/runs?limit=12`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/detections?limit=50`, { headers: authHeaders(), cache: 'no-store' }),
        ]);
        if (!active) return;
        const responseEntries: [SnapshotFailureKey, PromiseSettledResult<Response>][] = [
          ['targets', targetsResult],
          ['systems', systemsResult],
          ['alerts', alertsResult],
          ['incidents', incidentsResult],
          ['history', historyResult],
          ['evidence', evidenceResult],
          ['runs', runsResult],
          ['detections', detectionsResult],
        ];
        const failedEndpoints = responseEntries
          .filter(([, result]) => !(result.status === 'fulfilled' && result.value.ok))
          .map(([key]) => key);
        const responses = responseEntries.map(([, result]) => (
          result.status === 'fulfilled' && result.value.ok ? result.value : null
        ));
        const [targetsResponse, systemsResponse, alertsResponse, incidentsResponse, historyResponse, evidenceResponse, runsResponse, detectionsResponse] = responses;
        const targetsPayload = targetsResponse ? await targetsResponse.json().catch(() => ({})) : {};
        const systemsPayload = systemsResponse ? await systemsResponse.json().catch(() => ({})) : {};
        const alertsPayload = alertsResponse ? await alertsResponse.json().catch(() => ({})) : {};
        const incidentsPayload = incidentsResponse ? await incidentsResponse.json().catch(() => ({})) : {};
        const historyPayload = historyResponse ? await historyResponse.json().catch(() => ({})) : {};
        const evidencePayload = evidenceResponse ? await evidenceResponse.json().catch(() => ({})) : {};
        const runsPayload = runsResponse ? await runsResponse.json().catch(() => ({})) : {};
        const detectionsPayload = detectionsResponse ? await detectionsResponse.json().catch(() => ({})) : {};

        if (targetsResponse) {
          setTargets((targetsPayload?.targets ?? []) as TargetRow[]);
        }
        if (systemsResponse) {
          setMonitoredSystems((systemsPayload?.systems ?? []) as MonitoredSystemRow[]);
        }
        if (alertsResponse) {
          setAlerts((alertsPayload?.alerts ?? []) as AlertRow[]);
        }
        if (incidentsResponse) {
          setIncidents((incidentsPayload?.incidents ?? []) as IncidentRow[]);
        }
        if (historyResponse) {
          setHistoryRuns((historyPayload?.analysis_runs ?? []) as HistoryRun[]);
        }
        if (evidenceResponse) {
          setEvidence((evidencePayload?.evidence ?? []) as EvidenceRow[]);
        }
        if (runsResponse) {
          setMonitoringRuns((runsPayload?.runs ?? []) as MonitoringRunRow[]);
        }
        if (detectionsResponse) {
          setDetections((detectionsPayload?.detections ?? []) as DetectionRow[]);
        }
        setSystemsPanelWarning(formatSystemsPanelWarning(failedEndpoints));
        setSnapshotError(formatSnapshotErrorMessage(failedEndpoints));
      } catch {
        if (active) {
          setSnapshotError('Monitoring snapshot refresh failed');
          setSystemsPanelWarning('Systems list unavailable');
        }
      } finally {
        if (active) {
          setLoadingSnapshot(false);
        }
      }
    }

    function nextDelay() {
      return document.visibilityState === 'hidden' ? 60000 : 20000;
    }

    function schedule() {
      if (!active) return;
      timer = setTimeout(async () => {
        await refreshSnapshot();
        schedule();
      }, nextDelay());
    }

    void refreshSnapshot();
    schedule();

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void refreshSnapshot();
      }
    };
    window.addEventListener('pilot-history-refresh', onVisible as EventListener);
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      active = false;
      window.removeEventListener('pilot-history-refresh', onVisible as EventListener);
      document.removeEventListener('visibilitychange', onVisible);
      if (timer) clearTimeout(timer);
    };
  }, [apiUrl, authHeaders, isAuthenticated, user?.current_workspace?.id]);

  const openAlerts = alerts.length;
  const activeIncidents = incidents.length;
  const truth = feed.monitoring.truth;
  const canonicalPresentation = feed.monitoring.presentation;
  const simulatorMode = truth.evidence_source_summary === 'simulator';
  const protectedAssetCount = Number(truth.protected_assets_count ?? feed.counts.protectedAssets);
  const workspaceConfigured = truth.workspace_configured;
  const configuredSystems = truth.monitored_systems_count;
  const reportingSystems = truth.reporting_systems_count;
  const monitoringMode = truth.evidence_source_summary;
  const contradictionFlags: string[] = Array.isArray(truth.contradiction_flags) ? truth.contradiction_flags : [];
  const runtimeStatus = String(truth.runtime_status ?? '').toLowerCase();
  const presentationStatus = canonicalPresentation.status;
  const monitoringPresentation = {
    status: presentationStatus,
    tone: monitoringTone(presentationStatus),
    statusLabel: canonicalPresentation.statusLabel,
    summary: canonicalPresentation.summary,
    evidenceSourceLabel: String(truth.evidence_source_summary || 'none'),
    lastTelemetryAt: feed.monitoring.lastTelemetryAt,
    lastHeartbeatAt: feed.monitoring.lastHeartbeatAt,
    lastPollAt: feed.monitoring.lastPollAt,
    telemetryLabel: formatRelativeTime(feed.monitoring.lastTelemetryAt),
    heartbeatLabel: formatRelativeTime(feed.monitoring.lastHeartbeatAt),
    pollLabel: formatRelativeTime(feed.monitoring.lastPollAt),
    hasLiveTelemetry: hasLiveTelemetry(truth),
  };
  const showLiveTelemetry = monitoringPresentation.hasLiveTelemetry;
  const telemetryLabel = monitoringPresentation.telemetryLabel;
  const coverageTelemetryAt = monitoringPresentation.lastTelemetryAt;
  const hasTelemetryTimestamp = Boolean(coverageTelemetryAt);
  const telemetryDisplayLabel = formatRelativeTime(coverageTelemetryAt);
  const pollLabel = monitoringPresentation.pollLabel;
  const detectionEvalLabel = formatRelativeTime(monitoringPresentation.lastTelemetryAt ?? monitoringPresentation.lastPollAt);

  const baseDetections = useMemo<DetectionItem[]>(() => {
    const matchedAsset = targets[0];
    const fallbackAssetName = matchedAsset?.name || 'Unbound workspace asset';
    const fallbackAssetType = matchedAsset?.target_type || matchedAsset?.asset_type || 'system';
    return detections.slice(0, 50).map((item) => {
      const rawEvidence = item.raw_evidence_json || {};
      const rawEvent = rawEvidence.event || {};
      const responsePayload = rawEvidence.response || {};
      const linkedAlert = item.linked_alert_id ? alerts.find((alert) => alert.id === item.linked_alert_id) : null;
      const isTest = isTestOrLabSignal(item.title ?? undefined) || isTestOrLabSignal(item.evidence_summary ?? undefined);
      return {
        id: `detection-${item.id}`,
        timestamp: item.detected_at || new Date(0).toISOString(),
        severity: severityLabel(item.severity || linkedAlert?.severity),
        title: item.title || linkedAlert?.title || 'Detection matched',
        assetName: fallbackAssetName,
        assetType: fallbackAssetType,
        monitoringStatus: matchedAsset?.monitoring_enabled ? 'Monitored' : 'Status unavailable',
        evidenceSummary: item.evidence_summary || linkedAlert?.explanation || 'Rule matched from monitored evidence.',
        txHash: rawEvent.tx_hash ?? responsePayload.observed_evidence?.tx_hash ?? null,
        blockNumber: rawEvent.block_number ?? responsePayload.observed_evidence?.block_number ?? null,
        counterparty: rawEvent.counterparty ?? rawEvent.from ?? null,
        amount: rawEvent.amount ? String(rawEvent.amount) : null,
        tokenOrContract: rawEvent.contract_address ?? rawEvent.token_address ?? null,
        ruleId: item.source_rule ?? responsePayload.findings?.rule_id ?? item.detection_type ?? null,
        sourceProvider: item.evidence_source ?? linkedAlert?.source_service ?? linkedAlert?.source ?? null,
        targetName: matchedAsset?.name ?? null,
        state: isTest ? ('Test' as const) : ('Live' as const),
        href: item.linked_alert_id ? '/alerts' : '/threat',
        source: 'evidence' as const,
      };
    }).sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  }, [alerts, detections, targets]);

  const categorizedDetections = useMemo(() => {
    const now = Date.now();
    const live: DetectionItem[] = [];
    const historical: DetectionItem[] = [];

    baseDetections.forEach((item) => {
      const ageMs = now - new Date(item.timestamp).getTime();
      const telemetryFresh = monitoringPresentation.status === 'live' && monitoringPresentation.hasLiveTelemetry;
      const liveCandidate = telemetryFresh && ageMs <= DETECTION_LIVE_MS && item.state !== 'Test';
      if (liveCandidate) {
        live.push(item);
        return;
      }
      historical.push({
        ...item,
        state: item.state === 'Test' ? 'Test' : ageMs > DETECTION_LIVE_MS ? 'Historical' : 'Stale',
      });
    });

    return { live, historical };
  }, [baseDetections, monitoringPresentation.hasLiveTelemetry, monitoringPresentation.status]);

  const pageState = derivePageState({
    loadingSnapshot,
    snapshotError: Boolean(snapshotError),
    targets,
    liveDetections: categorizedDetections.live,
    workspaceConfigured,
    freshnessStatus: truth.telemetry_freshness,
    contradictionFlags,
    reportingSystems,
    runtimeStatus,
    monitoredSystems: feed.counts.monitoredSystems,
    hasLiveTelemetry: showLiveTelemetry,
    statusReason: truth.status_reason,
    configurationReason: null,
    configurationReasonCodes: [],
    runtimeErrorCode: feed.runtimeStatus?.error_code ?? null,
    runtimeDegradedReason: feed.runtimeStatus?.degraded_reason ?? null,
    runtimeMonitoringStatus: feed.runtimeStatus?.monitoring_status ?? null,
    fieldReasonCodes: feed.runtimeStatus?.field_reason_codes ?? null,
    summaryStatusReason: truth.status_reason,
    summaryConfigurationReason: null,
    summaryConfigurationReasonCodes: [],
  });

  const coverageSummary = `${Math.max(reportingSystems, 0)} / ${Math.max(configuredSystems, 0)}`;
  const hasCoverageFromRuntime = workspaceConfigured && (protectedAssetCount > 0 || configuredSystems > 0);
  const hasTargetCoverageRows = targets.length > 0;
  const hasMonitoredSystemCoverageRows = !hasTargetCoverageRows && monitoredSystems.length > 0;
  const showRuntimeCoverageFallback = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && hasCoverageFromRuntime;
  const showCoverageEmptyState = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && !hasCoverageFromRuntime;
  const runtimeCoverageStatusNote = contradictionFlags.length > 0
    ? 'Runtime consistency guards are active. Displaying enterprise-safe fallback coverage text.'
    : !workspaceConfigured
    ? 'Workspace not configured: monitoring setup is incomplete.'
    : monitoringPresentation.status === 'offline'
    ? 'Runtime reports coverage, but telemetry is currently offline.'
    : monitoringPresentation.status === 'degraded' || monitoringPresentation.status === 'stale' || monitoringPresentation.status === 'limited coverage'
      ? 'Runtime reports partial or stale telemetry. Detailed protected system rows are still syncing.'
      : 'Runtime reports healthy coverage. Detailed protected system rows are still syncing.';
  const latestRiskScore = useMemo(() => {
    if (alerts.some((item) => severityClass(item.severity) === 'critical')) return { value: 92, tier: 'High' };
    if (alerts.some((item) => severityClass(item.severity) === 'high')) return { value: 78, tier: 'Elevated' };
    if (alerts.length > 0 || incidents.length > 0) return { value: 62, tier: 'Guarded' };
    return { value: 28, tier: 'Low' };
  }, [alerts, incidents]);

  const riskFreshness = pageState === 'healthy_live' || (pageState === 'configured_no_signals' && reportingSystems > 0)
    ? `last evaluated ${detectionEvalLabel} across ${Math.max(configuredSystems, 0)} monitored systems`
    : `last known score from ${detectionEvalLabel}; current telemetry unavailable`;

  const detectionsToRender = pageState === 'healthy_live' ? categorizedDetections.live : categorizedDetections.historical;
  const linkedAlertRows = alerts.slice(0, 10).map((alert) => {
    const linkedDetection = detections.find((item) => item.linked_alert_id === alert.id) ?? null;
    return { alert, linkedDetection };
  });
  const incidentTimelineItems = useMemo<TimelineItem[]>(() => {
    const telemetryItems: TimelineItem[] = [{
      id: `telemetry-${monitoringPresentation.lastTelemetryAt ?? monitoringPresentation.lastPollAt ?? 'none'}`,
      timestamp: monitoringPresentation.lastTelemetryAt ?? monitoringPresentation.lastPollAt ?? new Date(0).toISOString(),
      category: 'Telemetry Event',
      description: hasTelemetryTimestamp
        ? `Latest telemetry seen ${telemetryDisplayLabel}.`
        : 'No current telemetry timestamp available.',
      href: '/threat',
    }];
    const detectionItems = detections.slice(0, 4).map((item) => ({
      id: `incident-detection-${item.id}`,
      timestamp: item.detected_at || new Date(0).toISOString(),
      category: 'Detection' as const,
      description: item.title || item.evidence_summary || 'Detection matched a monitoring rule.',
      href: '/alerts',
    }));
    const alertItems = alerts.slice(0, 4).map((item) => ({
      id: `incident-alert-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Alert' as const,
      description: item.title || 'Alert created',
      href: '/alerts',
    }));
    const incidentItems = incidents.slice(0, 4).map((item) => ({
      id: `incident-row-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Incident' as const,
      description: item.title || item.event_type || 'Incident opened',
      href: '/incidents',
    }));
    const actionItems = historyRuns.slice(0, 4).map((item) => ({
      id: `incident-action-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Action' as const,
      description: item.title,
      href: '/history',
    }));
    return [...telemetryItems, ...detectionItems, ...alertItems, ...incidentItems, ...actionItems]
      .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
      .slice(0, 10);
  }, [alerts, detections, hasTelemetryTimestamp, historyRuns, incidents, monitoringPresentation.lastPollAt, monitoringPresentation.lastTelemetryAt, telemetryDisplayLabel]);

  return (
    <section className="stack monitoringConsoleStack">
      <article className="dataCard monitoringHeaderCard">
        <div className="monitoringHeaderTop">
          <div>
            <p className="sectionEyebrow">Threat monitoring command center</p>
            <h2>{user?.current_workspace?.name ?? 'Workspace monitoring console'}</h2>
          </div>
          <div className="monitoringHeaderActions">
            <Link href="/alerts" prefetch={false} className="secondaryCta">Review alerts</Link>
            <Link href="/incidents" prefetch={false} className="secondaryCta">Open incident queue</Link>
            <Link href="/monitored-systems" prefetch={false} className="secondaryCta">Manage monitored systems</Link>
          </div>
        </div>
        <div className="chipRow monitoringHeaderChips">
          <span className={`statusBadge statusBadge-${monitoringPresentation.tone}`}>{monitoringPresentation.statusLabel}</span>
          {monitoringMode === 'simulator' || simulatorMode ? <span className="statusBadge statusBadge-attention">SIMULATOR MODE</span> : null}
          <span className="ruleChip">Operational state {formatOperationalStateLabel(pageState)}</span>
          <span className="ruleChip">{showLiveTelemetry ? `Live telemetry ${telemetryLabel}` : 'Current telemetry unavailable'}</span>
          <span className="ruleChip">Last poll {pollLabel}</span>
          <span className="ruleChip">Evidence source {monitoringPresentation.evidenceSourceLabel}</span>
          <span className="ruleChip">Protected assets {protectedAssetCount}</span>
          <span className="ruleChip">Monitored systems {configuredSystems}</span>
          <span className="ruleChip">Reporting systems {reportingSystems}</span>
          <span className="ruleChip">Evidence records {evidence.length}</span>
          {!workspaceConfigured ? <span className="ruleChip">Workspace not configured</span> : null}
          {contradictionFlags.length > 0 ? <span className="statusBadge statusBadge-attention">Guarded fallback copy active</span> : null}
          {systemsPanelWarning ? <span className="statusBadge statusBadge-attention">{systemsPanelWarning}</span> : null}
          <span className="ruleChip">Open alerts {openAlerts}</span>
          <span className="ruleChip">Active incidents {activeIncidents}</span>
        </div>
        <PageStateBanner state={pageState} telemetryLabel={telemetryLabel} pollLabel={pollLabel} reason={truth.status_reason} configurationReason={null} />
        <p className="tableMeta">
          Last telemetry: {hasTelemetryTimestamp ? telemetryDisplayLabel : 'Not available'} · Last detection evaluation: {detectionEvalLabel} · Last poll: {pollLabel} · Last heartbeat: {monitoringPresentation.heartbeatLabel} · Runtime freshness: {String(truth.telemetry_freshness || 'unavailable')} · Runtime confidence: {String(truth.confidence || 'unavailable')}
        </p>
        {feed.loading ? <p className="statusLine">Loading monitoring state…</p> : null}
        {feed.refreshing ? <p className="statusLine">Refreshing monitoring state…</p> : null}
      </article>

      <section className="monitoringKpiGrid" aria-label="Monitoring KPIs">
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Monitoring Status</p>
          <p className="kpiValue">{monitoringPresentation.statusLabel}</p>
          <p className="tableMeta">{monitoringPresentation.summary}</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Telemetry Freshness</p>
          <p className="kpiValue">{hasTelemetryTimestamp ? telemetryDisplayLabel : 'Unavailable'}</p>
          <p className="tableMeta">Detection evaluation {detectionEvalLabel}. Polling and heartbeat timestamps never count as telemetry.</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Protected Assets</p>
          <p className="kpiValue">{loadingSnapshot ? '—' : protectedAssetCount}</p>
          <p className="tableMeta">Assets with monitoring definitions.</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Open Alerts</p>
          <p className="kpiValue">{loadingSnapshot ? '—' : openAlerts}</p>
          <p className="tableMeta"><Link href="/alerts" prefetch={false}>Review alert queue</Link></p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Active Incidents</p>
          <p className="kpiValue">{loadingSnapshot ? '—' : activeIncidents}</p>
          <p className="tableMeta"><Link href="/incidents" prefetch={false}>Open incident queue</Link></p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Latest Risk Score</p>
          <p className="kpiValue">{latestRiskScore.value} / {latestRiskScore.tier}</p>
          <p className="tableMeta">{riskFreshness}</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Coverage State</p>
          <p className="kpiValue">{coverageSummary}</p>
          <p className="tableMeta">Systems reporting telemetry.</p>
        </article>
      </section>

      <article className="dataCard">
        <div className="listHeader">
          <div>
            <p className="sectionEyebrow">Recent Detections</p>
            <h3>Detection records from monitoring rules</h3>
          </div>
          <Link href="/alerts" prefetch={false}>Review alerts</Link>
        </div>
        <div className="chipRow">
          <span className="ruleChip">Category: Telemetry Events</span>
          <span className="ruleChip">Category: Detections</span>
          <span className="ruleChip">Category: Alerts</span>
          <span className="ruleChip">Category: Incidents</span>
          <span className="ruleChip">Category: Actions</span>
        </div>
        {loadingSnapshot ? <p className="muted">Loading detection records…</p> : null}
        {!loadingSnapshot && detectionsToRender.length === 0 ? (
          <div className="emptyStatePanel">
            <h4>
              {pageState === 'configured_no_signals'
                ? 'No active threat signals'
                : pageState === 'unconfigured_workspace'
                  ? 'No monitored systems configured'
                  : 'No detections available'}
            </h4>
            <p className="muted">
              {contradictionFlags.length > 0
                ? 'Monitoring copy is guarded while runtime consistency checks complete.'
                : pageState === 'configured_no_signals'
                ? 'Monitoring is healthy and no active detections are currently open.'
                : pageState === 'unconfigured_workspace'
                  ? 'Workspace not configured: monitoring setup is incomplete.'
                  : 'No historical detections are available for display at this time.'}
            </p>
            <div className="buttonRow">
              <Link href="/monitored-systems" prefetch={false}>Manage monitored systems</Link>
              <Link href="/history" prefetch={false}>View workspace history</Link>
              <Link href="/integrations" prefetch={false}>Inspect integration health</Link>
            </div>
          </div>
        ) : null}
        <div className="stack compactStack">
          {detectionsToRender.map((signal) => (
            <div key={signal.id} className="overviewListItem signalRow">
              <div>
                <p className="signalTitle">
                  <span className={`statusBadge statusBadge-${severityClass(signal.severity)}`}>{signal.severity}</span>{' '}
                  {signal.title}
                </p>
                <p className="muted">
                  {signal.assetName} ({signal.assetType}) · {signal.monitoringStatus} · {signal.evidenceSummary}
                  {simulatorMode ? ' · Simulator evidence' : ''}
                </p>
                <p className="tableMeta">
                  {formatAbsoluteTime(signal.timestamp)} · {formatRelativeTime(signal.timestamp)} · Source: {signal.source}
                </p>
                <p className="tableMeta">tx: {signal.txHash || 'n/a'} · block: {signal.blockNumber || 'n/a'} · counterparty: {signal.counterparty || 'n/a'} · amount: {signal.amount || 'n/a'} · contract/token: {signal.tokenOrContract || 'n/a'} · rule: {signal.ruleId || 'n/a'} · target: {signal.targetName || 'n/a'} · provider: {signal.sourceProvider || 'n/a'}</p>
              </div>
              <div className="signalActions">
                <span className={`statusBadge statusBadge-${stateTone(signal.state)}`}>{signal.state}</span>
                <span className="statusBadge statusBadge-high">Detection</span>
                <Link href="/alerts" prefetch={false}>View alert</Link>
                <Link href="/incidents" prefetch={false}>Open incident</Link>
                <Link href="/alerts" prefetch={false}>Mute rule</Link>
                <button
                  type="button"
                  className="secondaryCta"
                  onClick={() => setEvidenceDrawer({
                    title: signal.title,
                    summary: signal.evidenceSummary,
                    raw: detections.find((item) => `detection-${item.id}` === signal.id)?.raw_evidence_json ?? null,
                  })}
                >
                  Open evidence
                </button>
                <Link href={signal.href} prefetch={false}>View destination</Link>
              </div>
            </div>
          ))}
        </div>
      </article>

      <section className="twoColumnSection monitoringLowerGrid">
        <article className="dataCard">
          <div className="listHeader">
            <div>
              <p className="sectionEyebrow">Asset Coverage</p>
              <h3>Protected systems and telemetry coverage</h3>
            </div>
            <Link href="/monitored-systems" prefetch={false}>Manage monitored systems</Link>
          </div>
          {loadingSnapshot ? <p className="muted">Loading monitored systems…</p> : null}
          {showCoverageEmptyState ? (
            <div className="emptyStatePanel">
              <h4>No protected systems configured</h4>
              <p className="muted">Live monitoring requires at least one protected system in this workspace.</p>
              <div className="buttonRow">
                <Link href="/monitored-systems" prefetch={false}>Enable monitoring on this target</Link>
                <Link href="/help" prefetch={false}>View setup guide</Link>
              </div>
            </div>
          ) : null}
          {showRuntimeCoverageFallback ? (
            <div className="emptyStatePanel">
              <h4>Coverage detected from runtime monitoring summary</h4>
              <p className="muted">{runtimeCoverageStatusNote}</p>
              <ul className="tableMeta">
                <li>Configured systems: {Math.max(configuredSystems, 0)}</li>
                <li>Reporting systems: {reportingSystems}</li>
                <li>Protected assets: {protectedAssetCount}</li>
                <li>Last telemetry: {hasTelemetryTimestamp ? telemetryDisplayLabel : 'Not available'}</li>
                <li>Last poll: {pollLabel}</li>
                <li>Last heartbeat: {monitoringPresentation.heartbeatLabel}</li>
              </ul>
              <div className="buttonRow">
                <Link href="/monitored-systems" prefetch={false}>Open monitored systems</Link>
              </div>
            </div>
          ) : null}
          {(hasTargetCoverageRows || hasMonitoredSystemCoverageRows) ? (
            <div className="tableWrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset/System</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Coverage</th>
                    <th>Last telemetry</th>
                    <th>Last poll</th>
                    <th>Last heartbeat</th>
                    <th>Latest signal</th>
                    <th>Risk</th>
                    <th>Destination</th>
                  </tr>
                </thead>
                <tbody>
                  {hasTargetCoverageRows ? targets.slice(0, 10).map((target) => {
                    const coverage = normalizeCoverageStatus(target);
                    const risk = openAlerts > 0 ? 'High' : 'Low';
                    return (
                      <tr key={target.id}>
                        <td>{target.name}<span className="tableMeta">{displayIdentifier(target)}</span></td>
                        <td>{target.target_type || target.asset_type || 'System'}</td>
                        <td><span className={`statusBadge statusBadge-${target.health_status === 'broken' ? 'attention' : (target.monitoring_enabled ? 'healthy' : 'offline')}`}>{target.health_status === 'broken' ? 'Broken' : (target.monitoring_enabled ? 'Monitored' : 'Offline')}</span></td>
                        <td><span className={`statusBadge statusBadge-${coverageTone(coverage)}`}>{coverage}</span></td>
                        <td>{hasTelemetryTimestamp ? telemetryDisplayLabel : 'Not available'}</td>
                        <td>{pollLabel}</td>
                        <td>{monitoringPresentation.heartbeatLabel}</td>
                        <td>{alerts[0]?.title || incidents[0]?.title || 'No active signals'}</td>
                        <td><span className={`statusBadge statusBadge-${risk === 'High' ? 'high' : 'low'}`}>{risk}</span></td>
                        <td><Link href="/monitored-systems" prefetch={false}>Open asset coverage view</Link></td>
                      </tr>
                    );
                  }) : monitoredSystems.slice(0, 10).map((system) => {
                    const runtimeStatus = String(system.runtime_status || 'idle').toLowerCase();
                    const statusTone = runtimeStatus === 'failed' || runtimeStatus === 'degraded'
                      ? 'attention'
                      : (runtimeStatus === 'disabled' || !system.is_enabled ? 'offline' : 'healthy');
                    const statusLabel = runtimeStatus === 'healthy'
                      ? 'Monitored'
                      : runtimeStatus === 'failed'
                        ? 'Error'
                        : runtimeStatus === 'degraded'
                          ? 'Degraded'
                          : runtimeStatus === 'disabled'
                            ? 'Offline'
                            : 'Idle';
                    const coverage = runtimeStatus === 'healthy'
                      ? 'Full'
                      : runtimeStatus === 'idle'
                        ? 'Partial'
                        : runtimeStatus === 'disabled'
                          ? 'Offline'
                          : 'Stale';
                    const risk = openAlerts > 0 ? 'High' : 'Low';
                    return (
                      <tr key={system.id}>
                        <td>{system.target_name || system.asset_name || 'Monitored system'}<span className="tableMeta">{system.chain || 'Unknown chain'}</span></td>
                        <td>System</td>
                        <td><span className={`statusBadge statusBadge-${statusTone}`}>{statusLabel}</span></td>
                        <td><span className={`statusBadge statusBadge-${coverageTone(coverage)}`}>{coverage}</span></td>
                        <td>{system.last_event_at ? formatRelativeTime(system.last_event_at) : 'Not available'}</td>
                        <td>{pollLabel}</td>
                        <td>{formatRelativeTime(system.last_heartbeat)}</td>
                        <td>{system.last_error_text || system.coverage_reason || alerts[0]?.title || incidents[0]?.title || 'No active signals'}</td>
                        <td><span className={`statusBadge statusBadge-${risk === 'High' ? 'high' : 'low'}`}>{risk}</span></td>
                        <td><Link href="/monitored-systems" prefetch={false}>Open asset coverage view</Link></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </article>

        <div className="stack compactStack">
          <article className="dataCard">
            <div className="listHeader">
              <div>
                <p className="sectionEyebrow">Recent Monitoring Runs</p>
                <h3>Workspace cycle persistence</h3>
              </div>
              <Link href="/history" prefetch={false}>Open full history</Link>
            </div>
            {loadingSnapshot ? <p className="muted">Loading recent monitoring runs…</p> : null}
            {!loadingSnapshot && monitoringRuns.length === 0 ? (
              <div className="emptyStatePanel">
                <h4>No monitoring runs recorded yet</h4>
                <p className="muted">Run monitoring once or wait for the scheduler to persist the next workspace cycle.</p>
              </div>
            ) : (
              <div className="tableWrap">
                <table>
                  <thead>
                    <tr>
                      <th>Started</th>
                      <th>Status</th>
                      <th>Trigger</th>
                      <th>Systems</th>
                      <th>Assets</th>
                      <th>Detections</th>
                      <th>Alerts</th>
                      <th>Telemetry</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monitoringRuns.slice(0, 8).map((run) => (
                      <tr key={run.id}>
                        <td>{formatAbsoluteTime(run.started_at)}<span className="tableMeta">{formatRelativeTime(run.started_at)}</span></td>
                        <td><span className={`statusBadge statusBadge-${String(run.status || '').toLowerCase() === 'completed' ? 'healthy' : (String(run.status || '').toLowerCase() === 'error' ? 'attention' : 'offline')}`}>{String(run.status || 'unknown')}</span></td>
                        <td>{String(run.trigger_type || 'unknown')}</td>
                        <td>{Number(run.systems_checked_count ?? 0)}</td>
                        <td>{Number(run.assets_checked_count ?? 0)}</td>
                        <td>{Number(run.detections_created_count ?? 0)}</td>
                        <td>{Number(run.alerts_created_count ?? 0)}</td>
                        <td>{Number(run.telemetry_records_seen_count ?? 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </article>

          <article className="dataCard">
            <div className="listHeader">
              <div>
                <p className="sectionEyebrow">Alerts</p>
                <h3>Open alerts with linked detections</h3>
              </div>
              <Link href="/alerts" prefetch={false}>Open alert queue</Link>
            </div>
            {loadingSnapshot ? <p className="muted">Loading alerts…</p> : null}
            {!loadingSnapshot && linkedAlertRows.length === 0 ? (
              <div className="emptyStatePanel">
                <h4>No alerts recorded</h4>
                <p className="muted">No open alerts are currently linked to this workspace.</p>
              </div>
            ) : (
              <div className="stack compactStack">
                {linkedAlertRows.map(({ alert, linkedDetection }) => (
                  <div key={alert.id} className="overviewListItem">
                    <div>
                      <p>{alert.title}</p>
                      <p className="tableMeta">
                        <span className="statusBadge statusBadge-attention">Alert</span>{' '}
                        <span className="statusBadge statusBadge-high">Detection</span>{' '}
                        {formatAbsoluteTime(alert.created_at)}
                      </p>
                      <p className="tableMeta">
                        Linked detection: {linkedDetection?.title || linkedDetection?.id || 'Not linked'} · severity {severityLabel(alert.severity)}
                      </p>
                    </div>
                    <div className="signalActions">
                      <Link href="/alerts" prefetch={false}>Open</Link>
                      <button
                        type="button"
                        className="secondaryCta"
                        onClick={() => setEvidenceDrawer({
                          title: alert.title,
                          summary: linkedDetection?.evidence_summary || alert.explanation || 'Alert evidence available in raw payload.',
                          raw: linkedDetection?.raw_evidence_json ?? alert.payload ?? alert.findings ?? null,
                        })}
                      >
                        Open evidence
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </article>

          <article className="dataCard">
            <div className="listHeader">
              <div>
                <p className="sectionEyebrow">Incidents</p>
                <h3>Active incidents with timeline and audit history</h3>
              </div>
              <Link href="/incidents" prefetch={false}>Open incident queue</Link>
            </div>
            {loadingSnapshot ? <p className="muted">Loading incidents…</p> : null}
            {!loadingSnapshot && incidents.length === 0 ? (
              <div className="emptyStatePanel">
                <h4>No incidents recorded</h4>
                <p className="muted">Open incidents will include timeline and audit history entries here.</p>
              </div>
            ) : (
              <div className="stack compactStack">
                {incidents.slice(0, 6).map((incident) => (
                  <div key={incident.id} className="overviewListItem">
                    <div>
                      <p>{incident.title || incident.event_type || 'Incident opened'}</p>
                      <p className="tableMeta">
                        <span className="statusBadge statusBadge-attention">Incident</span>{' '}
                        <span className="statusBadge statusBadge-low">Audit</span>{' '}
                        {formatAbsoluteTime(incident.created_at)}
                      </p>
                    </div>
                    <Link href="/incidents" prefetch={false}>Open</Link>
                  </div>
                ))}
                <div className="stack compactStack">
                  {incidentTimelineItems.map((item) => (
                    <div key={item.id} className="overviewListItem">
                      <div>
                        <p>{item.description}</p>
                        <p className="tableMeta">
                          <span className={`statusBadge statusBadge-${categoryTone(item.category)}`}>{item.category}</span>{' '}
                          {formatAbsoluteTime(item.timestamp)}
                        </p>
                      </div>
                      <Link href={item.href} prefetch={false}>Open</Link>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </article>

          <article className="dataCard">
            <p className="sectionEyebrow">Response Actions</p>
            <h3>Operational actions</h3>
            <p className="muted">Use investigation and escalation workflows to restore healthy monitoring and resolve risk.</p>
            <div className="buttonRow">
              <Link href="/alerts" prefetch={false}>Review alerts</Link>
              <Link href="/incidents" prefetch={false}>Open incident queue</Link>
              <Link href="/history" prefetch={false}>View workspace history</Link>
              <Link href="/monitored-systems" prefetch={false}>Manage monitored systems</Link>
              <Link href="/compliance" prefetch={false}>Review governance actions</Link>
              <Link href="/integrations" prefetch={false}>Inspect integration health</Link>
            </div>
          </article>
        </div>
      </section>
      {evidenceDrawer ? (
        <article className="dataCard" role="dialog" aria-label="Evidence details">
          <div className="listHeader">
            <div>
              <p className="sectionEyebrow">Evidence</p>
              <h3>{evidenceDrawer.title}</h3>
            </div>
            <button type="button" className="secondaryCta" onClick={() => setEvidenceDrawer(null)}>Close</button>
          </div>
          <p className="muted">Summary: {evidenceDrawer.summary || 'No evidence summary available.'}</p>
          <pre className="tableMeta" style={{ whiteSpace: 'pre-wrap', overflowX: 'auto' }}>
            {JSON.stringify(evidenceDrawer.raw ?? { message: 'No raw evidence found.' }, null, 2)}
          </pre>
        </article>
      ) : null}
    </section>
  );
}
