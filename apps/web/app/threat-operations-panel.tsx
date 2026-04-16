'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import type { MonitoringPresentationStatus } from './monitoring-status-presentation';
import { usePilotAuth } from 'app/pilot-auth-context';
import { hasLiveTelemetry, monitoringHealthyCopyAllowed } from './workspace-monitoring-truth';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

type Props = { apiUrl: string };

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
};

type IncidentRow = {
  id: string;
  title?: string;
  event_type?: string;
  severity?: string;
  status?: string;
  created_at?: string;
};

type HistoryRun = {
  id: string;
  title: string;
  created_at: string;
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

type ThreatFeedState = 'Live' | 'Historical' | 'Test' | 'Stale' | 'Investigating' | 'Resolved';
type PageOperationalState =
  | 'healthy_live'
  | 'configured_no_signals'
  | 'degraded_partial'
  | 'offline_no_telemetry'
  | 'unconfigured_workspace'
  | 'fetch_error';

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
  category: 'Alert' | 'Incident' | 'Checkpoint' | 'Monitoring';
  description: string;
  href: string;
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

function displayIdentifier(target: TargetRow): string {
  if (target.wallet_address) {
    const value = target.wallet_address;
    return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
  }
  return target.contract_identifier || 'Identifier unavailable';
}

function derivePageState(params: {
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
  } = params;

  if (!loadingSnapshot && snapshotError && reportingSystems === 0) {
    return 'fetch_error';
  }

  if (!workspaceConfigured) {
    return 'unconfigured_workspace';
  }

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

function PageStateBanner({ state, telemetryLabel, pollLabel, reason, configurationReason }: { state: PageOperationalState; telemetryLabel: string; pollLabel: string; reason?: string | null; configurationReason?: string | null }) {
  if (state === 'healthy_live') {
    return <p className="explanation">Live monitoring is healthy. Telemetry freshness and threat detections reflect current workspace conditions.</p>;
  }
  if (state === 'configured_no_signals') {
    return <p className="explanation">Monitoring healthy. No active detections right now. Live telemetry remains current across reporting systems.</p>;
  }
  if (state === 'unconfigured_workspace') {
    return <p className="explanation">Workspace is not configured: {configurationReasonMessage(configurationReason)} Live threat detection starts only after persisted linkage is valid.</p>;
  }
  if (state === 'offline_no_telemetry') {
    return <p className="explanation">Monitoring is offline. Reason: {reason || 'no active reporting systems'}. Add one monitored system and confirm telemetry flow.</p>;
  }
  if (state === 'fetch_error') {
    return (
      <div className="emptyStatePanel">
        <h4>Monitoring data unavailable</h4>
        <p className="muted">The workspace is configured, but the latest telemetry could not be retrieved.</p>
        <p className="tableMeta">Last telemetry: {telemetryLabel} · Last successful poll: {pollLabel}</p>
        <div className="buttonRow">
          <Link href="/threat" prefetch={false}>Retry</Link>
          <Link href="/integrations" prefetch={false}>Inspect integration status</Link>
          <Link href="/monitored-systems" prefetch={false}>Manage monitored systems</Link>
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
  const [targets, setTargets] = useState<TargetRow[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [incidents, setIncidents] = useState<IncidentRow[]>([]);
  const [historyRuns, setHistoryRuns] = useState<HistoryRun[]>([]);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [monitoredSystems, setMonitoredSystems] = useState<MonitoredSystemRow[]>([]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function refreshSnapshot() {
      if (!active || !isAuthenticated || !user?.current_workspace?.id) {
        return;
      }
      try {
        const [targetsResponse, systemsResponse, alertsResponse, incidentsResponse, historyResponse, evidenceResponse] = await Promise.all([
          fetch(`${apiUrl}/monitoring/targets`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/monitoring/systems`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/alerts?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/incidents?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/pilot/history?limit=12`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/ops/monitoring/evidence?limit=50`, { headers: authHeaders(), cache: 'no-store' }),
        ]);
        if (!active) return;
        if (!targetsResponse.ok || !systemsResponse.ok || !alertsResponse.ok || !incidentsResponse.ok || !historyResponse.ok || !evidenceResponse.ok) {
          throw new Error('refresh_failed');
        }
        const targetsPayload = await targetsResponse.json();
        const systemsPayload = await systemsResponse.json();
        const alertsPayload = await alertsResponse.json();
        const incidentsPayload = await incidentsResponse.json();
        const historyPayload = await historyResponse.json();
        const evidencePayload = await evidenceResponse.json();

        setTargets((targetsPayload.targets ?? []) as TargetRow[]);
        setMonitoredSystems((systemsPayload.systems ?? []) as MonitoredSystemRow[]);
        setAlerts((alertsPayload.alerts ?? []) as AlertRow[]);
        setIncidents((incidentsPayload.incidents ?? []) as IncidentRow[]);
        setHistoryRuns((historyPayload.analysis_runs ?? []) as HistoryRun[]);
        setEvidence((evidencePayload.evidence ?? []) as EvidenceRow[]);
        setSnapshotError(null);
      } catch {
        if (active) {
          setSnapshotError('Monitoring snapshot refresh failed');
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
  const simulatorMode = truth.monitoring_mode === 'simulator' || truth.evidence_source === 'simulator';
  const protectedAssetCount = Number(truth.protected_assets_count ?? feed.counts.protectedAssets);
  const workspaceConfigured = truth.workspace_configured;
  const configuredSystems = truth.configured_systems;
  const reportingSystems = truth.reporting_systems;
  const monitoringMode = truth.monitoring_mode;
  const contradictionFlags = truth.contradiction_flags;
  const runtimeStatus = String(truth.runtime_status ?? '').toLowerCase();
  const presentationStatus = canonicalPresentation.status;
  const monitoringPresentation = {
    status: presentationStatus,
    tone: monitoringTone(presentationStatus),
    statusLabel: canonicalPresentation.statusLabel,
    summary: canonicalPresentation.summary,
    evidenceSourceLabel: String(truth.evidence_source || 'none'),
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
  const coverageTelemetryAt = truth.last_coverage_telemetry_at ?? monitoringPresentation.lastTelemetryAt;
  const hasTelemetryTimestamp = Boolean(coverageTelemetryAt);
  const telemetryDisplayLabel = formatRelativeTime(coverageTelemetryAt);
  const pollLabel = monitoringPresentation.pollLabel;
  const detectionEvalLabel = formatRelativeTime(truth.last_detection_at ?? monitoringPresentation.lastTelemetryAt ?? monitoringPresentation.lastPollAt);

  const baseDetections = useMemo<DetectionItem[]>(() => {
    const matchedAsset = targets[0];
    const fallbackAssetName = matchedAsset?.name || 'Unbound workspace asset';
    const fallbackAssetType = matchedAsset?.target_type || matchedAsset?.asset_type || 'system';

    const fromEvidence: DetectionItem[] = evidence.slice(0, 20).map((item) => ({
      id: `evidence-${item.id}`,
      timestamp: item.observed_at || new Date(0).toISOString(),
      severity: severityLabel(item.severity),
      title: item.summary || item.event_type || 'Observed monitoring event',
      assetName: item.asset_name || fallbackAssetName,
      assetType: fallbackAssetType,
      monitoringStatus: 'Monitored',
      evidenceSummary: item.summary || 'Evidence ingested from provider-backed monitoring.',
      txHash: item.tx_hash ?? null,
      blockNumber: item.block_number ?? null,
      counterparty: item.counterparty ?? null,
      amount: item.amount_text ?? null,
      tokenOrContract: item.contract_address ?? item.token_address ?? null,
      ruleId: item.rule_label ?? item.event_type ?? null,
      sourceProvider: item.source_provider ?? null,
      targetName: item.target_name ?? null,
      state: 'Live',
      href: '/alerts',
      source: 'evidence',
    }));

    const fromAlerts: DetectionItem[] = alerts.slice(0, 10).map((item) => {
      const isTest = isTestOrLabSignal(item.title) || isTestOrLabSignal(item.explanation);
      return {
        id: `alert-${item.id}`,
        timestamp: item.created_at || new Date(0).toISOString(),
        severity: severityLabel(item.severity),
        title: item.title,
        assetName: fallbackAssetName,
        assetType: fallbackAssetType,
        monitoringStatus: matchedAsset?.monitoring_enabled ? 'Monitored' : 'Status unavailable',
        evidenceSummary: item.explanation || 'Alert condition triggered and is waiting for operator review.',
        txHash: item.payload?.tx_hash ?? null,
        blockNumber: item.payload?.block_number ?? null,
        counterparty: item.payload?.counterparty ?? item.payload?.from ?? null,
        amount: item.payload?.amount ? String(item.payload?.amount) : null,
        tokenOrContract: item.payload?.contract_address ?? item.payload?.token_address ?? null,
        ruleId: item.findings?.rule_id ?? item.alert_type ?? null,
        sourceProvider: item.source_service ?? item.source ?? null,
        targetName: item.target_id ?? null,
        state: isTest ? ('Test' as const) : ('Live' as const),
        href: '/alerts',
        source: 'alert' as const,
      };
    });

    return [...fromEvidence, ...fromAlerts].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  }, [alerts, targets, evidence]);

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
    freshnessStatus: truth.freshness_status,
    contradictionFlags,
    reportingSystems,
    runtimeStatus,
    monitoredSystems: feed.counts.monitoredSystems,
    hasLiveTelemetry: showLiveTelemetry,
  });

  const timelineItems = useMemo<TimelineItem[]>(() => {
    const alertItems = alerts.slice(0, 5).map((item) => ({
      id: `alert-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Alert' as const,
      description: item.title,
      href: '/alerts',
    }));
    const incidentItems = incidents.slice(0, 5).map((item) => ({
      id: `incident-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Incident' as const,
      description: item.title || item.event_type || 'Incident opened',
      href: '/incidents',
    }));
    const historyItems = historyRuns.slice(0, 4).map((item) => ({
      id: `history-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Checkpoint' as const,
      description: item.title,
      href: '/history',
    }));
    const monitoringEvent = {
      id: `monitoring-${monitoringPresentation.lastPollAt ?? 'none'}`,
      timestamp: monitoringPresentation.lastPollAt || new Date().toISOString(),
      category: 'Monitoring' as const,
      description: pageState === 'offline_no_telemetry'
        ? 'Monitoring offline: no fresh telemetry available.'
        : runtimeStatus && runtimeStatus !== 'healthy'
          ? `Monitoring ${runtimeStatus}: ${truth.status_reason || 'runtime is not healthy'}.`
        : pageState === 'degraded_partial'
          ? 'Monitoring degraded: telemetry is partial or delayed.'
        : simulatorMode
          ? 'Simulator/dev mode active: records are persisted but are not live production telemetry.'
            : workspaceConfigured && reportingSystems > 0
              ? (monitoringHealthyCopyAllowed(truth) ? 'Monitoring healthy: telemetry and polling are current.' : 'Monitoring configured: waiting for reporting telemetry.')
              : (workspaceConfigured ? 'Monitoring configured: waiting for reporting telemetry.' : `Workspace not configured: ${configurationReasonMessage(truth.configuration_reason)}`),
      href: '/threat',
    };

    return [monitoringEvent, ...alertItems, ...incidentItems, ...historyItems]
      .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
      .slice(0, 12);
  }, [alerts, historyRuns, incidents, monitoringPresentation.lastPollAt, pageState, reportingSystems, runtimeStatus, simulatorMode, truth.status_reason, truth.runtime_status]);

  const coverageSummary = `${Math.max(reportingSystems, 0)} / ${Math.max(configuredSystems, 0)}`;
  const hasCoverageFromRuntime = workspaceConfigured && (protectedAssetCount > 0 || configuredSystems > 0);
  const hasTargetCoverageRows = targets.length > 0;
  const hasMonitoredSystemCoverageRows = !hasTargetCoverageRows && monitoredSystems.length > 0;
  const showRuntimeCoverageFallback = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && hasCoverageFromRuntime;
  const showCoverageEmptyState = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && !hasCoverageFromRuntime;
  const runtimeCoverageStatusNote = !workspaceConfigured
    ? `Workspace not configured: ${configurationReasonMessage(truth.configuration_reason)}`
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

  const feedHeading = pageState === 'healthy_live'
    ? 'Live detections available'
    : pageState === 'configured_no_signals'
      ? (categorizedDetections.historical.length > 0
          ? 'Historical detections only'
          : (reportingSystems > 0 ? 'No active detections, monitoring healthy' : 'No active detections, waiting for live telemetry'))
      : pageState === 'unconfigured_workspace'
          ? 'Workspace not configured'
          : 'Monitoring unavailable or partial';

  const detectionsToRender = pageState === 'healthy_live' ? categorizedDetections.live : categorizedDetections.historical;

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
          <span className="ruleChip">Operational state {pageState.replaceAll('_', ' ')}</span>
          <span className="ruleChip">{showLiveTelemetry ? `Live telemetry ${telemetryLabel}` : 'Current telemetry unavailable'}</span>
          <span className="ruleChip">Last poll {pollLabel}</span>
          <span className="ruleChip">Evidence source {monitoringPresentation.evidenceSourceLabel}</span>
          <span className="ruleChip">Protected assets {protectedAssetCount}</span>
          <span className="ruleChip">Monitored systems {configuredSystems}</span>
          <span className="ruleChip">Reporting systems {reportingSystems}</span>
          {!workspaceConfigured ? <span className="ruleChip">Workspace not configured</span> : null}
          {contradictionFlags.length > 0 ? <span className="statusBadge statusBadge-attention">Guarded fallback copy active</span> : null}
          <span className="ruleChip">Open alerts {openAlerts}</span>
          <span className="ruleChip">Active incidents {activeIncidents}</span>
        </div>
        <PageStateBanner state={pageState} telemetryLabel={telemetryLabel} pollLabel={pollLabel} reason={truth.status_reason} configurationReason={truth.configuration_reason} />
        <p className="tableMeta">
          Last telemetry: {hasTelemetryTimestamp ? telemetryDisplayLabel : 'Not available'} · Last detection evaluation: {detectionEvalLabel} · Last poll: {pollLabel} · Last heartbeat: {monitoringPresentation.heartbeatLabel} · Runtime freshness: {String(truth.freshness_status || 'unavailable')} · Runtime confidence: {String(truth.confidence_status || 'unavailable')}
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
            <p className="sectionEyebrow">Threat Feed</p>
            <h3>{feedHeading}</h3>
          </div>
          <Link href="/alerts" prefetch={false}>Review alerts</Link>
        </div>
        {loadingSnapshot ? <p className="muted">Loading threat feed…</p> : null}
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
              {pageState === 'configured_no_signals'
                ? 'Monitoring is healthy and no active detections are currently open.'
                : pageState === 'unconfigured_workspace'
                  ? `Workspace not configured: ${configurationReasonMessage(truth.configuration_reason)}`
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
                <Link href="/alerts" prefetch={false}>View alert</Link>
                <Link href="/incidents" prefetch={false}>Open incident</Link>
                <Link href="/alerts" prefetch={false}>Mute rule</Link>
                <Link href={signal.href} prefetch={false}>View raw evidence</Link>
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
                <p className="sectionEyebrow">Operational Timeline</p>
                <h3>Alerts, incidents, and monitoring transitions</h3>
              </div>
              <Link href="/history" prefetch={false}>Open full history</Link>
            </div>
            {timelineItems.length === 0 ? (
              <div className="emptyStatePanel">
                <h4>No activity recorded</h4>
                <p className="muted">No alerts, incidents, or health transitions have been recorded yet.</p>
              </div>
            ) : (
              <div className="stack compactStack">
                {timelineItems.map((item) => (
                  <div key={item.id} className="overviewListItem">
                    <div>
                      <p>{item.description}</p>
                      <p className="tableMeta">{item.category} · {formatAbsoluteTime(item.timestamp)}</p>
                    </div>
                    <Link href={item.href} prefetch={false}>Open</Link>
                  </div>
                ))}
              </div>
            )}
          </article>

          <article className="dataCard">
            <p className="sectionEyebrow">Action Center</p>
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
    </section>
  );
}
