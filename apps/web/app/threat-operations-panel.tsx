'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import type { MonitoringPresentationStatus } from './monitoring-status-presentation';
import type { MonitoringInvestigationTimeline, MonitoringRuntimeStatus } from './monitoring-status-contract';
import { usePilotAuth } from 'app/pilot-auth-context';
import { actionDisabledReason, capabilityMapFromPayload, isActionDisabledInMode, responseActionExecutionMessage, type ResponseActionCapability } from './response-action-capabilities';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import ThreatChainPanel from './threat-chain-panel';

type Props = { apiUrl: string };
// Temporary backoff while runtime-status latency is elevated; re-evaluate when p95 is back under threshold.
const THREAT_PAGE_POLL_VISIBLE_MS = 45000;
const THREAT_PAGE_POLL_HIDDEN_MS = 60000;

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
  summary?: string;
  detection_id?: string | null;
  incident_id?: string | null;
  evidence_summary?: string | null;
  explanation?: string;
  payload?: Record<string, any>;
  findings?: Record<string, any>;
  alert_type?: string;
  source?: string;
  source_service?: string;
  target_id?: string;
  response_action_mode?: string | null;
  linked_action_id?: string | null;
  linked_evidence_count?: number | null;
  last_evidence_at?: string | null;
  evidence_origin?: string | null;
  tx_hash?: string | null;
  block_number?: number | null;
  detector_kind?: string | null;
  chain_linked_ids?: {
    detection_id?: string | null;
    alert_id?: string | null;
    incident_id?: string | null;
    action_id?: string | null;
  } | null;
};

type IncidentRow = {
  id: string;
  title?: string;
  event_type?: string;
  severity?: string;
  status?: string;
  created_at?: string;
  source_alert_id?: string | null;
  response_action_mode?: string | null;
  linked_detection_id?: string | null;
  linked_action_id?: string | null;
  linked_evidence_count?: number | null;
  last_evidence_at?: string | null;
  evidence_origin?: string | null;
  tx_hash?: string | null;
  block_number?: number | null;
  detector_kind?: string | null;
  chain_linked_ids?: {
    detection_id?: string | null;
    alert_id?: string | null;
    incident_id?: string | null;
    action_id?: string | null;
  } | null;
};
type ActionHistoryRow = {
  id: string;
  actor_type?: string | null;
  actor_id?: string | null;
  object_type?: string | null;
  object_id?: string | null;
  action_type?: string | null;
  timestamp?: string | null;
  details_json?: Record<string, any> | null;
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
  target_id?: string | null;
  detection_id?: string | null;
  linked_detection_id?: string | null;
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
  linked_incident_id?: string | null;
  linked_action_id?: string | null;
  linked_evidence_count?: number | null;
  last_evidence_at?: string | null;
  tx_hash?: string | null;
  block_number?: number | null;
  detector_kind?: string | null;
  evidence_origin?: string | null;
  chain_linked_ids?: {
    detection_id?: string | null;
    alert_id?: string | null;
    incident_id?: string | null;
    action_id?: string | null;
  } | null;
};
type ThreatActionContextOption = {
  id: string;
  label: string;
  detectionId: string | null;
  alertId: string | null;
  incidentId: string | null;
};

type ThreatFeedState = 'Live' | 'Historical' | 'Test' | 'Stale' | 'Investigating' | 'Resolved';
export type PageOperationalState =
  | 'healthy_live'
  | 'configured_no_signals'
  | 'degraded_partial'
  | 'offline_no_telemetry'
  | 'unconfigured_workspace'
  | 'fetch_error';

type SnapshotFailureKey = 'runtime-status' | 'investigation-timeline';

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
  liveEvidenceEligible?: boolean;
  targetName?: string | null;
  state: ThreatFeedState;
  href: string;
  source: 'alert' | 'incident' | 'evidence' | 'detection';
  detectionId?: string | null;
  alertId?: string | null;
  incidentId?: string | null;
  actionId?: string | null;
};

type ThreatChainStep = {
  id: string;
  label: string;
  detail: string;
  timestamp: string | null;
  href: string;
};

type EvidenceDrawerState = {
  detectionId?: string | null;
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

function severityClass(severity?: string | null) {
  const normalized = String(severity ?? '').toLowerCase();
  if (normalized.includes('critical')) return 'critical';
  if (normalized.includes('high')) return 'high';
  if (normalized.includes('medium')) return 'medium';
  return 'low';
}

function severityLabel(severity?: string | null) {
  const normalized = String(severity ?? '').toLowerCase();
  if (normalized.includes('critical')) return 'Critical';
  if (normalized.includes('high')) return 'High';
  if (normalized.includes('medium')) return 'Medium';
  return 'Low';
}

function isTestOrLabSignal(text: string | null | undefined): boolean {
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

function timelineLinkTone(linkName: string) {
  if (linkName === 'alert' || linkName === 'incident') return 'attention';
  if (linkName === 'detection' || linkName === 'detection_evidence') return 'high';
  if (linkName === 'response_action') return 'healthy';
  return 'low';
}

function timelineLinkHref(linkName: string): string {
  if (linkName === 'alert' || linkName === 'detection') return '/alerts';
  if (linkName === 'incident') return '/incidents';
  if (linkName === 'response_action' || linkName === 'monitoring_run') return '/history';
  return '/threat';
}

function displayIdentifier(target: TargetRow): string {
  if (target.wallet_address) {
    const value = target.wallet_address;
    return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
  }
  return target.contract_identifier || 'Identifier unavailable';
}

type CoverageIndexes = {
  alertsById: Map<string, AlertRow>;
  alertsByTargetId: Map<string, AlertRow[]>;
  alertsByIncidentId: Map<string, AlertRow[]>;
  incidentsBySourceAlertId: Map<string, IncidentRow[]>;
  incidentsByLinkedAlertId: Map<string, IncidentRow[]>;
  detectionsByMonitoredSystemId: Map<string, DetectionRow[]>;
  detectionsByLinkedAlertId: Map<string, DetectionRow[]>;
  evidenceByTargetId: Map<string, EvidenceRow[]>;
  evidenceByTargetName: Map<string, EvidenceRow[]>;
  evidenceByDetectionId: Map<string, EvidenceRow[]>;
};

type LinkedCoverageResolution = {
  latestDetection: DetectionRow | null;
  latestAlert: AlertRow | null;
  latestIncident: IncidentRow | null;
  latestEvidence: EvidenceRow | null;
};

function normalizeLookup(value?: string | null): string {
  return String(value ?? '').trim().toLowerCase();
}

function parseTimestamp(value?: string | null): number {
  if (!value) return 0;
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function pickLatestByTime<T>(items: T[], getTimestamp: (item: T) => string | null | undefined): T | null {
  if (items.length === 0) return null;
  return items.slice().sort((a, b) => parseTimestamp(getTimestamp(b)) - parseTimestamp(getTimestamp(a)))[0] ?? null;
}

export function buildCoverageIndexes(params: {
  alerts: AlertRow[];
  incidents: IncidentRow[];
  detections: DetectionRow[];
  evidenceRows: EvidenceRow[];
}): CoverageIndexes {
  const {
    alerts,
    incidents,
    detections,
    evidenceRows,
  } = params;
  const alertsByTargetId = new Map<string, AlertRow[]>();
  const alertsByIncidentId = new Map<string, AlertRow[]>();
  const alertsById = new Map<string, AlertRow>();
  alerts.forEach((alert) => {
    const alertId = normalizeLookup(alert.id);
    if (alertId) alertsById.set(alertId, alert);
    const targetId = normalizeLookup(alert.target_id);
    if (targetId) alertsByTargetId.set(targetId, [...(alertsByTargetId.get(targetId) ?? []), alert]);
    const incidentId = normalizeLookup(alert.incident_id);
    if (incidentId) alertsByIncidentId.set(incidentId, [...(alertsByIncidentId.get(incidentId) ?? []), alert]);
  });

  const incidentsBySourceAlertId = new Map<string, IncidentRow[]>();
  const incidentsByLinkedAlertId = new Map<string, IncidentRow[]>();
  incidents.forEach((incident) => {
    const sourceAlertId = normalizeLookup(incident.source_alert_id);
    if (sourceAlertId) incidentsBySourceAlertId.set(sourceAlertId, [...(incidentsBySourceAlertId.get(sourceAlertId) ?? []), incident]);
    const linkedAlertIds = [
      ...((incident as IncidentRow & { linked_alert_ids?: string[] | null }).linked_alert_ids ?? []),
      ...((incident as IncidentRow & { alert_ids?: string[] | null }).alert_ids ?? []),
    ].map((value) => normalizeLookup(value)).filter(Boolean);
    linkedAlertIds.forEach((alertId) => {
      incidentsByLinkedAlertId.set(alertId, [...(incidentsByLinkedAlertId.get(alertId) ?? []), incident]);
    });
  });

  const detectionsByMonitoredSystemId = new Map<string, DetectionRow[]>();
  const detectionsByLinkedAlertId = new Map<string, DetectionRow[]>();
  detections.forEach((detection) => {
    const monitoredSystemId = normalizeLookup(detection.monitored_system_id);
    if (monitoredSystemId) detectionsByMonitoredSystemId.set(monitoredSystemId, [...(detectionsByMonitoredSystemId.get(monitoredSystemId) ?? []), detection]);
    const linkedAlertId = normalizeLookup(detection.linked_alert_id);
    if (linkedAlertId) detectionsByLinkedAlertId.set(linkedAlertId, [...(detectionsByLinkedAlertId.get(linkedAlertId) ?? []), detection]);
  });

  const evidenceByTargetId = new Map<string, EvidenceRow[]>();
  const evidenceByTargetName = new Map<string, EvidenceRow[]>();
  const evidenceByDetectionId = new Map<string, EvidenceRow[]>();
  evidenceRows.forEach((evidence) => {
    const targetId = normalizeLookup(evidence.target_id);
    if (targetId) evidenceByTargetId.set(targetId, [...(evidenceByTargetId.get(targetId) ?? []), evidence]);
    const targetName = normalizeLookup(evidence.target_name ?? evidence.asset_name);
    if (targetName) evidenceByTargetName.set(targetName, [...(evidenceByTargetName.get(targetName) ?? []), evidence]);
    const detectionId = normalizeLookup(evidence.detection_id ?? evidence.linked_detection_id);
    if (detectionId) evidenceByDetectionId.set(detectionId, [...(evidenceByDetectionId.get(detectionId) ?? []), evidence]);
  });

  return {
    alertsById,
    alertsByTargetId,
    alertsByIncidentId,
    incidentsBySourceAlertId,
    incidentsByLinkedAlertId,
    detectionsByMonitoredSystemId,
    detectionsByLinkedAlertId,
    evidenceByTargetId,
    evidenceByTargetName,
    evidenceByDetectionId,
  };
}

function isRealEvidence(evidence: EvidenceRow | null, detection: DetectionRow | null): boolean {
  const source = normalizeLookup(evidence?.source_provider ?? detection?.evidence_source);
  if (!source) return false;
  return !['simulator', 'synthetic', 'demo', 'fallback', 'test', 'lab', 'replay'].some((flag) => source.includes(flag));
}

function severityFromLinked(params: LinkedCoverageResolution): string | null {
  const severities = [params.latestIncident?.severity, params.latestAlert?.severity, params.latestDetection?.severity];
  return severities.find((value) => normalizeLookup(value)) ?? null;
}

export function resolveLinkedCoverageForTarget(params: {
  target: TargetRow;
  systemIds: string[];
  indexes: CoverageIndexes;
}): LinkedCoverageResolution {
  const { target, systemIds, indexes } = params;
  const targetId = normalizeLookup(target.id);
  const detectionPool = systemIds.flatMap((id) => indexes.detectionsByMonitoredSystemId.get(normalizeLookup(id)) ?? []);
  const latestDetection = pickLatestByTime(detectionPool, (item) => item.detected_at);
  const targetAlerts = indexes.alertsByTargetId.get(targetId) ?? [];
  const alertPool = [
    ...targetAlerts,
    ...detectionPool
      .map((detection) => (detection.linked_alert_id ? indexes.alertsById.get(normalizeLookup(detection.linked_alert_id)) ?? null : null))
      .filter((alert): alert is AlertRow => Boolean(alert)),
  ];
  const latestAlert = pickLatestByTime(alertPool, (item) => item.created_at);
  const latestIncident = latestAlert
    ? pickLatestByTime([
      ...(indexes.incidentsBySourceAlertId.get(normalizeLookup(latestAlert.id)) ?? []),
      ...(indexes.incidentsByLinkedAlertId.get(normalizeLookup(latestAlert.id)) ?? []),
    ], (item) => item.created_at)
    : null;
  const evidencePool = [
    ...(indexes.evidenceByTargetId.get(targetId) ?? []),
    ...(indexes.evidenceByTargetName.get(normalizeLookup(target.name)) ?? []),
    ...detectionPool.flatMap((detection) => indexes.evidenceByDetectionId.get(normalizeLookup(detection.id)) ?? []),
  ];
  const latestEvidence = pickLatestByTime(evidencePool, (item) => item.observed_at);
  return { latestDetection, latestAlert, latestIncident, latestEvidence };
}

export function destinationForLinked(resolution: LinkedCoverageResolution): string {
  if (resolution.latestIncident) return '/incidents';
  if (resolution.latestAlert) return '/alerts';
  if (resolution.latestDetection) return '/detections';
  return '/monitored-systems';
}

function linkedSignalLabel(resolution: LinkedCoverageResolution): string {
  if (resolution.latestIncident?.title) return resolution.latestIncident.title;
  if (resolution.latestAlert?.title) return resolution.latestAlert.title;
  if (resolution.latestDetection?.title) return resolution.latestDetection.title;
  return 'No linked real evidence yet';
}

export function linkedRiskLabel(resolution: LinkedCoverageResolution): { label: string; tone: string } {
  const severity = severityFromLinked(resolution);
  if (!severity) return { label: 'No linked severity', tone: 'offline' };
  const normalized = severityClass(severity);
  return { label: severityLabel(severity), tone: normalized === 'medium' ? 'attention' : normalized };
}

export function evidenceStatusCopy(params: {
  resolution: LinkedCoverageResolution;
  fallback: string;
}): string {
  const { resolution, fallback } = params;
  if (!resolution.latestEvidence && !resolution.latestDetection) return 'No linked real evidence yet';
  if (!isRealEvidence(resolution.latestEvidence, resolution.latestDetection)) return 'Degraded evidence';
  if (resolution.latestEvidence?.summary) return resolution.latestEvidence.summary;
  if (resolution.latestDetection?.evidence_summary) return resolution.latestDetection.evidence_summary;
  return fallback;
}

export function derivePageState(params: {
  loadingSnapshot: boolean;
  snapshotError: boolean;
  targets: TargetRow[];
  liveDetections: DetectionItem[];
  workspaceConfigured: boolean;
  freshnessStatus: string;
  monitoringStatus: 'live' | 'limited' | 'offline';
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
  continuityStatus?: 'continuous_live' | 'continuous_no_evidence' | 'degraded' | 'offline' | 'idle_no_telemetry' | null;
}): PageOperationalState {
  const {
    loadingSnapshot,
    snapshotError,
    targets,
    liveDetections,
    workspaceConfigured,
    freshnessStatus,
    monitoringStatus,
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
    continuityStatus,
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

  // State precedence:
  // 1) runtime query failure payloads (backend/runtime endpoint is unhealthy)
  // 2) explicit structural misconfiguration (workspace unconfigured by design)
  // 3) explicit offline continuity/runtime status (no telemetry continuity)
  // 4) snapshot endpoint fetch failure (runtime/investigation snapshot unavailable)
  // 5) continuity + runtime derived operational states
  if (!workspaceConfigured && structuralUnconfiguredReason && !runtimeQueryFailure) {
    return 'unconfigured_workspace';
  }

  if (continuityStatus === 'offline' || runtimeStatus === 'offline') {
    return 'offline_no_telemetry';
  }

  if (snapshotError) {
    return 'fetch_error';
  }

  if (!workspaceConfigured) return 'fetch_error';
  if (continuityStatus === 'idle_no_telemetry') {
    return 'configured_no_signals';
  }
  if (continuityStatus === 'continuous_no_evidence') {
    return 'configured_no_signals';
  }
  if (continuityStatus === 'degraded') {
    return 'degraded_partial';
  }
  if (continuityStatus === 'continuous_live') {
    return liveDetections.length > 0 ? 'healthy_live' : 'configured_no_signals';
  }

  if (
    runtimeStatus === 'degraded'
    || runtimeStatus === 'failed'
    || runtimeStatus === 'disabled'
    || runtimeStatus === 'provisioning'
    || monitoringStatus === 'limited'
    || freshnessStatus === 'stale'
  ) {
    return 'degraded_partial';
  }

  return 'degraded_partial';
}

function formatSnapshotErrorMessage(failedEndpoints: SnapshotFailureKey[]): string | null {
  if (failedEndpoints.length === 0) return null;
  return `Monitoring snapshot partially unavailable (${failedEndpoints.length} endpoint${failedEndpoints.length === 1 ? '' : 's'} failed).`;
}

export function formatSystemsPanelWarning(failedEndpoints: SnapshotFailureKey[]): string | null {
  if (failedEndpoints.includes('runtime-status')) {
    return 'Runtime status unavailable';
  }
  if (failedEndpoints.includes('investigation-timeline')) {
    return 'Investigation timeline unavailable';
  }
  return null;
}

export function pageStatePrimaryCopy(
  state: PageOperationalState,
  configurationReason?: string | null,
  continuityStatus?: 'continuous_live' | 'continuous_no_evidence' | 'degraded' | 'offline' | 'idle_no_telemetry' | null,
): string {
  if (state === 'healthy_live') {
    return 'Live monitoring is healthy. Telemetry freshness and threat detections reflect current workspace conditions.';
  }
  if (state === 'configured_no_signals') {
    if (continuityStatus === 'continuous_no_evidence') {
      return 'Live polling active. No recent anomaly evidence.';
    }
    if (continuityStatus === 'continuous_live') {
      return 'Telemetry continuity is live and continuous. No active detections are currently open.';
    }
    return 'No telemetry continuity is currently proven for this workspace. Active detections are not currently available.';
  }
  if (state === 'unconfigured_workspace') {
    return `Workspace is not configured: ${configurationReasonMessage(configurationReason)} Live threat detection starts only after persisted linkage is valid.`;
  }
  if (state === 'offline_no_telemetry') {
    return 'Monitoring continuity is offline with no current telemetry.';
  }
  if (state === 'fetch_error') {
    return 'Backend telemetry/runtime retrieval failed, so monitoring data is temporarily unavailable.';
  }
  return 'Monitoring is partially degraded. Threat outcomes may be delayed or incomplete.';
}

function PageStateBanner({ state, telemetryLabel, pollLabel, reason, configurationReason, continuityStatus }: { state: PageOperationalState; telemetryLabel: string; pollLabel: string; reason?: string | null; configurationReason?: string | null; continuityStatus?: 'continuous_live' | 'continuous_no_evidence' | 'degraded' | 'offline' | 'idle_no_telemetry' | null }) {
  if (state === 'healthy_live') {
    return <p className="explanation">{pageStatePrimaryCopy(state, configurationReason, continuityStatus)}</p>;
  }
  if (state === 'configured_no_signals') {
    return <p className="explanation">{pageStatePrimaryCopy(state, configurationReason, continuityStatus)}</p>;
  }
  if (state === 'unconfigured_workspace') {
    return <p className="explanation">{pageStatePrimaryCopy(state, configurationReason, continuityStatus)}</p>;
  }
  if (state === 'offline_no_telemetry') {
    return <p className="explanation">{pageStatePrimaryCopy(state, configurationReason, continuityStatus)} Reason: {reason || 'no active reporting systems'}. Add one monitored system and confirm telemetry flow.</p>;
  }
  if (state === 'fetch_error') {
    return (
      <div className="emptyStatePanel">
        <h4>Telemetry retrieval degraded</h4>
        <p className="muted">{pageStatePrimaryCopy(state, configurationReason, continuityStatus)}</p>
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
  const feed = useLiveWorkspaceFeed(THREAT_PAGE_POLL_VISIBLE_MS);
  const [loadingSnapshot, setLoadingSnapshot] = useState(true);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [systemsPanelWarning, setSystemsPanelWarning] = useState<string | null>(null);
  const [targets, setTargets] = useState<TargetRow[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [incidents, setIncidents] = useState<IncidentRow[]>([]);
  const [actionHistory, setActionHistory] = useState<ActionHistoryRow[]>([]);
  const [monitoringRuns, setMonitoringRuns] = useState<MonitoringRunRow[]>([]);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [detections, setDetections] = useState<DetectionRow[]>([]);
  const [monitoredSystems, setMonitoredSystems] = useState<MonitoredSystemRow[]>([]);
  const [evidenceDrawer, setEvidenceDrawer] = useState<EvidenceDrawerState | null>(null);
  const [responseToast, setResponseToast] = useState<string | null>(null);
  const [actionCapabilities, setActionCapabilities] = useState<Record<string, ResponseActionCapability>>({});
  const [runtimeStatusSnapshot, setRuntimeStatusSnapshot] = useState<MonitoringRuntimeStatus | null>(null);
  const [investigationTimeline, setInvestigationTimeline] = useState<MonitoringInvestigationTimeline | null>(null);
  const [ensuringProofChain, setEnsuringProofChain] = useState(false);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function refreshSnapshot() {
      if (!active || !isAuthenticated || !user?.current_workspace?.id) {
        return;
      }
      try {
        const [runtimeStatusResult, investigationTimelineResult] = await Promise.allSettled([
          fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/ops/monitoring/investigation-timeline`, { headers: authHeaders(), cache: 'no-store' }),
        ]);
        if (!active) return;
        const responseEntries: [SnapshotFailureKey, PromiseSettledResult<Response>][] = [
          ['runtime-status', runtimeStatusResult],
          ['investigation-timeline', investigationTimelineResult],
        ];
        const failedEndpoints = responseEntries
          .filter(([, result]) => !(result.status === 'fulfilled' && result.value.ok))
          .map(([key]) => key);
        const responses = responseEntries.map(([, result]) => (
          result.status === 'fulfilled' && result.value.ok ? result.value : null
        ));
        const [runtimeStatusResponse, investigationTimelineResponse] = responses;
        const runtimeStatusPayload = runtimeStatusResponse ? await runtimeStatusResponse.json().catch(() => ({})) : {};
        const investigationTimelinePayload = investigationTimelineResponse ? await investigationTimelineResponse.json().catch(() => ({})) : {};

        // Runtime-status + investigation-timeline are the canonical monitoring sources for this panel.
        setTargets([]);
        setMonitoredSystems([]);
        setAlerts([]);
        setIncidents([]);
        setEvidence([]);
        setMonitoringRuns([]);
        setDetections([]);
        setActionHistory([]);
        if (runtimeStatusResponse) {
          setRuntimeStatusSnapshot(runtimeStatusPayload as MonitoringRuntimeStatus);
        }
        if (investigationTimelineResponse) {
          setInvestigationTimeline({
            ...investigationTimelinePayload,
            items: Array.isArray(investigationTimelinePayload?.items) ? investigationTimelinePayload.items : [],
            missing: Array.isArray(investigationTimelinePayload?.missing) ? investigationTimelinePayload.missing : [],
          } as MonitoringInvestigationTimeline);
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
      return document.visibilityState === 'hidden' ? THREAT_PAGE_POLL_HIDDEN_MS : THREAT_PAGE_POLL_VISIBLE_MS;
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

  const runtimeSummary = runtimeStatusSnapshot?.workspace_monitoring_summary;
  const openAlerts = Number(runtimeStatusSnapshot?.open_alerts ?? runtimeSummary?.active_alerts_count ?? 0);
  const activeIncidents = Number(runtimeStatusSnapshot?.active_incidents ?? runtimeSummary?.active_incidents_count ?? 0);
  const truth = feed.monitoring.truth;
  const canonicalPresentation = feed.monitoring.presentation;
  const runtimeEvidenceSource = String(
    runtimeStatusSnapshot?.evidence_source
    ?? runtimeStatusSnapshot?.monitoring_mode
    ?? runtimeSummary?.evidence_source_summary
    ?? 'none',
  ).toLowerCase();
  const simulatorMode = runtimeEvidenceSource === 'simulator';
  const protectedAssetCount = Number(runtimeStatusSnapshot?.protected_assets_count ?? runtimeSummary?.protected_assets_count ?? 0);
  const workspaceConfigured = Boolean(runtimeStatusSnapshot?.workspace_configured ?? runtimeSummary?.workspace_configured ?? false);
  const configuredSystems = Number(runtimeStatusSnapshot?.monitored_systems_count ?? runtimeSummary?.monitored_systems_count ?? 0);
  const reportingSystems = Number(runtimeStatusSnapshot?.reporting_systems ?? runtimeSummary?.reporting_systems_count ?? 0);
  const monitoringMode = runtimeEvidenceSource;
  const runtimeStatus = String(runtimeStatusSnapshot?.runtime_status ?? runtimeSummary?.runtime_status ?? '').toLowerCase();
  const continuityLive = runtimeStatus === 'live';
  const presentationStatus: MonitoringPresentationStatus = runtimeStatus === 'live'
    ? 'live'
    : runtimeStatus === 'offline'
      ? 'offline'
      : 'degraded';
  const presentationStatusLabel = presentationStatus === 'live'
    ? 'LIVE'
    : presentationStatus === 'offline'
      ? 'OFFLINE'
      : 'DEGRADED';
  const monitoringPresentation = {
    status: presentationStatus,
    tone: monitoringTone(presentationStatus),
    statusLabel: presentationStatusLabel,
    summary: canonicalPresentation.summary,
    evidenceSourceLabel: runtimeEvidenceSource,
    lastTelemetryAt: runtimeStatusSnapshot?.last_telemetry_at ?? runtimeSummary?.last_telemetry_at ?? null,
    lastHeartbeatAt: runtimeStatusSnapshot?.last_heartbeat_at ?? runtimeSummary?.last_heartbeat_at ?? null,
    lastPollAt: runtimeStatusSnapshot?.last_poll_at ?? runtimeSummary?.last_poll_at ?? null,
    telemetryLabel: formatRelativeTime(runtimeStatusSnapshot?.last_telemetry_at ?? runtimeSummary?.last_telemetry_at ?? null),
    heartbeatLabel: formatRelativeTime(runtimeStatusSnapshot?.last_heartbeat_at ?? runtimeSummary?.last_heartbeat_at ?? null),
    pollLabel: formatRelativeTime(runtimeStatusSnapshot?.last_poll_at ?? runtimeSummary?.last_poll_at ?? null),
    hasLiveTelemetry: continuityLive
      && String(runtimeSummary?.telemetry_freshness ?? runtimeStatusSnapshot?.freshness_status ?? 'unavailable') === 'fresh'
      && reportingSystems > 0
      && runtimeEvidenceSource === 'live',
  };
  const showLiveTelemetry = monitoringPresentation.hasLiveTelemetry;
  const dbPersistenceOutageReason = truth.db_failure_reason || null;
  const dbPersistenceOutageActive = Boolean(dbPersistenceOutageReason);
  const telemetryLabel = monitoringPresentation.telemetryLabel;
  const coverageTelemetryAt = monitoringPresentation.lastTelemetryAt;
  const hasTelemetryTimestamp = Boolean(coverageTelemetryAt);
  const telemetryDisplayLabel = formatRelativeTime(coverageTelemetryAt);
  const pollLabel = monitoringPresentation.pollLabel;
  const detectionEvalLabel = formatRelativeTime(runtimeStatusSnapshot?.last_detection_at ?? monitoringPresentation.lastTelemetryAt);

  const targetById = useMemo(() => {
    return new Map(targets.map((target) => [target.id, target] as const));
  }, [targets]);

  const monitoredSystemById = useMemo(() => {
    return new Map(monitoredSystems.map((system) => [system.id, system] as const));
  }, [monitoredSystems]);
  const monitoredSystemIdsByTargetId = useMemo(() => {
    const map = new Map<string, string[]>();
    monitoredSystems.forEach((system) => {
      const targetId = normalizeLookup(system.target_id);
      if (!targetId) return;
      map.set(targetId, [...(map.get(targetId) ?? []), system.id]);
    });
    return map;
  }, [monitoredSystems]);
  const coverageIndexes = useMemo(() => {
    return buildCoverageIndexes({
      alerts,
      incidents,
      detections,
      evidenceRows: evidence,
    });
  }, [alerts, detections, evidence, incidents]);

  const baseDetections = useMemo<DetectionItem[]>(() => {
    return detections.slice(0, 50).map((item) => {
      const rawEvidence = item.raw_evidence_json || {};
      const rawEvent = rawEvidence.event || {};
      const responsePayload = rawEvidence.response || {};
      const isTest = isTestOrLabSignal(item.title ?? undefined) || isTestOrLabSignal(item.evidence_summary ?? undefined);
      const monitoredSystem = item.monitored_system_id ? monitoredSystemById.get(item.monitored_system_id) : null;
      const matchedTarget = monitoredSystem?.target_id ? targetById.get(monitoredSystem.target_id) : null;
      const fallbackAssetName = monitoredSystem?.asset_name || monitoredSystem?.target_name || matchedTarget?.name || 'Unbound workspace asset';
      const fallbackAssetType = matchedTarget?.target_type || matchedTarget?.asset_type || 'system';
      const monitoringStatus = monitoredSystem?.is_enabled
        ? 'Monitored'
        : monitoredSystem
        ? 'Disabled'
        : (matchedTarget?.monitoring_enabled ? 'Monitored' : 'Status unavailable');
      const normalizedEvidenceSource = String(item.evidence_source ?? '').toLowerCase();
      const simulatorEvidence = ['simulator', 'demo', 'synthetic', 'fallback', 'replay'].includes(normalizedEvidenceSource);
      const evidenceSourceLabel = simulatorEvidence ? 'simulator/demo' : 'live';
      return {
        id: `detection-${item.id}`,
        timestamp: item.detected_at || new Date(0).toISOString(),
        severity: severityLabel(item.severity),
        title: item.title || 'Detection matched',
        assetName: fallbackAssetName,
        assetType: fallbackAssetType,
        monitoringStatus,
        evidenceSummary: item.evidence_summary || 'Rule matched from monitored evidence.',
        txHash: rawEvent.tx_hash ?? responsePayload.observed_evidence?.tx_hash ?? null,
        blockNumber: rawEvent.block_number ?? responsePayload.observed_evidence?.block_number ?? null,
        counterparty: rawEvent.counterparty ?? rawEvent.from ?? null,
        amount: rawEvent.amount ? String(rawEvent.amount) : null,
        tokenOrContract: rawEvent.contract_address ?? rawEvent.token_address ?? null,
        ruleId: item.source_rule ?? responsePayload.findings?.rule_id ?? item.detection_type ?? null,
        sourceProvider: evidenceSourceLabel,
        liveEvidenceEligible: !simulatorEvidence,
        targetName: monitoredSystem?.target_name ?? matchedTarget?.name ?? null,
        state: isTest ? ('Test' as const) : ('Live' as const),
        href: item.linked_alert_id ? '/alerts' : '/threat',
        source: 'detection' as const,
        detectionId: item.id,
        alertId: item.linked_alert_id ?? null,
        incidentId: item.linked_incident_id ?? null,
        actionId: item.linked_action_id ?? null,
      };
    }).sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  }, [detections, monitoredSystemById, targetById]);

  const categorizedDetections = useMemo(() => {
    const now = Date.now();
    const live: DetectionItem[] = [];
    const historical: DetectionItem[] = [];

    baseDetections.forEach((item) => {
      const ageMs = now - new Date(item.timestamp).getTime();
      const telemetryFresh = monitoringPresentation.status === 'live' && monitoringPresentation.hasLiveTelemetry;
      const liveCandidate = telemetryFresh
        && !dbPersistenceOutageActive
        && item.liveEvidenceEligible !== false
        && ageMs <= DETECTION_LIVE_MS
        && item.state !== 'Test';
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
  }, [baseDetections, dbPersistenceOutageActive, monitoringPresentation.hasLiveTelemetry, monitoringPresentation.status]);

  const pageState = derivePageState({
    loadingSnapshot,
    snapshotError: Boolean(snapshotError),
    targets,
    liveDetections: categorizedDetections.live,
    workspaceConfigured,
    freshnessStatus: runtimeSummary?.telemetry_freshness ?? runtimeStatusSnapshot?.freshness_status ?? 'unavailable',
    monitoringStatus: runtimeStatusSnapshot?.monitoring_status ?? runtimeSummary?.monitoring_status ?? 'offline',
    reportingSystems,
    runtimeStatus,
    monitoredSystems: configuredSystems,
    hasLiveTelemetry: showLiveTelemetry,
    statusReason: runtimeStatusSnapshot?.status_reason ?? runtimeSummary?.status_reason ?? null,
    configurationReason: null,
    configurationReasonCodes: [],
    runtimeMonitoringStatus: runtimeStatusSnapshot?.monitoring_status ?? runtimeSummary?.monitoring_status ?? 'offline',
    runtimeErrorCode: null,
    runtimeDegradedReason: null,
    fieldReasonCodes: null,
    summaryStatusReason: runtimeStatusSnapshot?.status_reason ?? runtimeSummary?.status_reason ?? null,
    summaryConfigurationReason: null,
    summaryConfigurationReasonCodes: [],
    continuityStatus: runtimeSummary?.continuity_status ?? null,
  });

  const runtimeReason = String(runtimeStatusSnapshot?.status_reason ?? runtimeSummary?.status_reason ?? 'not_reported');
  const proofChainStatus = String(runtimeStatusSnapshot?.proof_chain_status ?? investigationTimeline?.proof_chain_status ?? 'incomplete');
  const timelineItems = Array.isArray(investigationTimeline?.items) ? investigationTimeline.items : [];
  const missingTimelineLinks = Array.isArray(investigationTimeline?.missing) ? investigationTimeline.missing : [];
  const timelineLinkNames = new Set(timelineItems.map((item) => String(item.link_name || '')));
  const hasDetectionTimelineLink = timelineLinkNames.has('detection');
  const hasEvidenceTimelineLink = timelineLinkNames.has('telemetry_event') || timelineLinkNames.has('detection_evidence');
  const showEvidenceLinkedSignals = hasDetectionTimelineLink && hasEvidenceTimelineLink;

  const coverageSummary = `${Math.max(reportingSystems, 0)} / ${Math.max(configuredSystems, 0)}`;
  const hasCoverageFromRuntime = workspaceConfigured && (protectedAssetCount > 0 || configuredSystems > 0);
  const hasTargetCoverageRows = targets.length > 0;
  const hasMonitoredSystemCoverageRows = !hasTargetCoverageRows && monitoredSystems.length > 0;
  const showRuntimeCoverageFallback = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && hasCoverageFromRuntime;
  const showCoverageEmptyState = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && !hasCoverageFromRuntime;
  const runtimeCoverageStatusNote = !workspaceConfigured
    ? 'Workspace not configured: monitoring setup is incomplete.'
    : monitoringPresentation.status === 'offline'
    ? 'Runtime reports coverage, but telemetry is currently offline.'
    : monitoringPresentation.status === 'degraded'
      ? 'Runtime reports partial or stale telemetry. Detailed protected system rows are still syncing.'
      : 'Runtime reports healthy coverage. Detailed protected system rows are still syncing.';
  const targetCoverageRows = useMemo(() => {
    return targets.slice(0, 10).map((target) => {
      const coverage = normalizeCoverageStatus(target);
      const linked = resolveLinkedCoverageForTarget({
        target,
        systemIds: monitoredSystemIdsByTargetId.get(normalizeLookup(target.id)) ?? [],
        indexes: coverageIndexes,
      });
      const risk = linkedRiskLabel(linked);
      return {
        target,
        coverage,
        linked,
        risk,
        destinationHref: destinationForLinked(linked),
        latestSignal: linkedSignalLabel(linked),
        evidenceCopy: evidenceStatusCopy({ resolution: linked, fallback: 'No linked real evidence yet' }),
      };
    });
  }, [coverageIndexes, monitoredSystemIdsByTargetId, targets]);
  const monitoredSystemCoverageRows = useMemo(() => {
    return monitoredSystems.slice(0, 10).map((system) => {
      const runtimeStatusValue = String(system.runtime_status || 'idle').toLowerCase();
      const statusTone = runtimeStatusValue === 'failed' || runtimeStatusValue === 'degraded'
        ? 'attention'
        : (runtimeStatusValue === 'disabled' || !system.is_enabled ? 'offline' : 'healthy');
      const statusLabel = runtimeStatusValue === 'healthy'
        ? 'Monitored'
        : runtimeStatusValue === 'failed'
          ? 'Error'
          : runtimeStatusValue === 'degraded'
            ? 'Degraded'
            : runtimeStatusValue === 'disabled'
              ? 'Offline'
              : 'Idle';
      const coverage: ReturnType<typeof normalizeCoverageStatus> = runtimeStatusValue === 'healthy'
        ? 'Full'
        : runtimeStatusValue === 'idle'
          ? 'Partial'
          : runtimeStatusValue === 'disabled'
            ? 'Offline'
            : 'Stale';
      const linkedDetections = coverageIndexes.detectionsByMonitoredSystemId.get(normalizeLookup(system.id)) ?? [];
      const latestDetection = pickLatestByTime(linkedDetections, (item) => item.detected_at);
      const linkedAlert = latestDetection?.linked_alert_id
        ? coverageIndexes.alertsById.get(normalizeLookup(latestDetection.linked_alert_id)) ?? null
        : null;
      const linkedIncident = linkedAlert
        ? pickLatestByTime([
          ...(coverageIndexes.incidentsBySourceAlertId.get(normalizeLookup(linkedAlert.id)) ?? []),
          ...(coverageIndexes.incidentsByLinkedAlertId.get(normalizeLookup(linkedAlert.id)) ?? []),
        ], (item) => item.created_at)
        : null;
      const linkedEvidence = latestDetection
        ? pickLatestByTime(coverageIndexes.evidenceByDetectionId.get(normalizeLookup(latestDetection.id)) ?? [], (item) => item.observed_at)
        : null;
      const linked = {
        latestDetection,
        latestAlert: linkedAlert,
        latestIncident: linkedIncident,
        latestEvidence: linkedEvidence,
      };
      const hasHeartbeat = Boolean(system.last_heartbeat);
      const hasTelemetry = Boolean(system.last_event_at);
      const statusText = !hasTelemetry && hasHeartbeat && !linkedEvidence
        ? 'No recent telemetry for this protected system'
        : evidenceStatusCopy({
          resolution: linked,
          fallback: system.last_error_text || system.coverage_reason || 'No linked real evidence yet',
        });
      return {
        system,
        statusTone,
        statusLabel,
        coverage,
        latestSignal: linkedSignalLabel(linked),
        risk: linkedRiskLabel(linked),
        statusText,
        destinationHref: destinationForLinked(linked),
      };
    });
  }, [coverageIndexes, monitoredSystems]);
  const latestRiskScore = useMemo(() => {
    if (activeIncidents > 0) return { value: 'High', tier: `${activeIncidents} active incident${activeIncidents === 1 ? '' : 's'}` };
    if (openAlerts > 0) return { value: 'Elevated', tier: `${openAlerts} open alert${openAlerts === 1 ? '' : 's'}` };
    if (runtimeStatus === 'live') return { value: 'Low', tier: 'No active alerts or incidents' };
    if (runtimeStatus === 'degraded') return { value: 'Guarded', tier: 'Runtime degraded; investigate telemetry continuity' };
    if (runtimeStatus === 'offline') return { value: 'Unknown', tier: 'Runtime offline; live risk score unavailable' };
    return { value: 'Unknown', tier: 'Awaiting runtime signal' };
  }, [activeIncidents, openAlerts, runtimeStatus]);

  const riskFreshness = pageState === 'healthy_live' || (pageState === 'configured_no_signals' && reportingSystems > 0)
    ? `last evaluated ${detectionEvalLabel} across ${Math.max(configuredSystems, 0)} monitored systems`
    : `last known score from ${detectionEvalLabel}; current telemetry unavailable`;

  const detectionsToRender = pageState === 'healthy_live' ? categorizedDetections.live : categorizedDetections.historical;
  const linkedAlertRows = alerts.slice(0, 10).map((alert) => {
    const linkedDetection = detections.find((item) => item.linked_alert_id === alert.id) ?? null;
    return { alert, linkedDetection };
  });
  const investigationTimelineItems = useMemo(() => (
    timelineItems.slice().sort((a, b) => new Date(b.timestamp || 0).getTime() - new Date(a.timestamp || 0).getTime())
  ), [timelineItems]);
  const threatChainSteps = useMemo<ThreatChainStep[]>(() => {
    const recentDetection = detections
      .slice()
      .sort((a, b) => new Date(b.detected_at || 0).getTime() - new Date(a.detected_at || 0).getTime())
      .find((item) => item.linked_alert_id || alerts.some((alert) => alert.detection_id === item.id));
    const relatedAlert = recentDetection
      ? alerts.find((alert) => alert.id === recentDetection.linked_alert_id || alert.detection_id === recentDetection.id) ?? null
      : null;
    const relatedIncident = relatedAlert
      ? incidents.find((incident) => incident.id === relatedAlert.incident_id || incident.source_alert_id === relatedAlert.id) ?? null
      : null;
    const relatedRun = monitoringRuns
      .slice()
      .sort((a, b) => new Date((b.completed_at || b.started_at) || 0).getTime() - new Date((a.completed_at || a.started_at) || 0).getTime())[0] ?? null;
    const relatedAction = actionHistory
      .slice()
      .sort((a, b) => new Date(b.timestamp || 0).getTime() - new Date(a.timestamp || 0).getTime())
      .find((item) => (
        (relatedIncident && ((item.object_type === 'incident' && item.object_id === relatedIncident.id) || item.details_json?.incident_id === relatedIncident.id))
        || (relatedAlert && ((item.object_type === 'alert' && item.object_id === relatedAlert.id) || item.details_json?.alert_id === relatedAlert.id))
      )) ?? null;

    return [
      {
        id: 'chain-detection',
        label: 'Detection created',
        detail: recentDetection?.title || recentDetection?.evidence_summary || 'No linked detection yet.',
        timestamp: recentDetection?.detected_at ?? null,
        href: '/alerts',
      },
      {
        id: 'chain-alert',
        label: 'Alert created',
        detail: relatedAlert?.title || relatedAlert?.summary || 'No linked alert yet.',
        timestamp: relatedAlert?.created_at ?? null,
        href: '/alerts',
      },
      {
        id: 'chain-incident',
        label: 'Incident opened',
        detail: relatedIncident?.title || relatedIncident?.event_type || 'No linked incident yet.',
        timestamp: relatedIncident?.created_at ?? null,
        href: '/incidents',
      },
      {
        id: 'chain-action',
        label: 'Action logged',
        detail: relatedAction
          ? `${String(relatedAction.action_type || 'workflow.action_recorded')} by ${String(relatedAction.actor_type || 'system')}`
          : relatedRun
            ? `${String(relatedRun.trigger_type || 'unknown')} run ${String(relatedRun.status || 'unknown')} (monitoring evidence recorded)`
            : 'No action history recorded yet.',
        timestamp: relatedAction?.timestamp ?? relatedRun?.completed_at ?? relatedRun?.started_at ?? null,
        href: '/history',
      },
    ];
  }, [actionHistory, alerts, detections, incidents, monitoringRuns]);
  const chainPanelSelection = useMemo(() => {
    const latestDetection = detections
      .slice()
      .sort((a, b) => new Date(b.detected_at || 0).getTime() - new Date(a.detected_at || 0).getTime())[0] ?? null;
    const linkedAlert = latestDetection?.linked_alert_id
      ? alerts.find((item) => item.id === latestDetection.linked_alert_id) ?? null
      : null;
    const linkedIncident = linkedAlert?.incident_id
      ? incidents.find((item) => item.id === linkedAlert.incident_id) ?? null
      : null;
    return {
      detectionId: latestDetection?.id ?? null,
      alertId: latestDetection?.linked_alert_id ?? linkedAlert?.id ?? null,
      incidentId: latestDetection?.linked_incident_id ?? linkedAlert?.incident_id ?? linkedIncident?.id ?? null,
      actionId: latestDetection?.linked_action_id ?? linkedAlert?.linked_action_id ?? linkedIncident?.linked_action_id ?? null,
      linkedEvidenceCount: latestDetection?.linked_evidence_count ?? linkedAlert?.linked_evidence_count ?? linkedIncident?.linked_evidence_count ?? null,
      lastEvidenceAt: latestDetection?.last_evidence_at ?? linkedAlert?.last_evidence_at ?? linkedIncident?.last_evidence_at ?? null,
      evidenceOrigin: latestDetection?.evidence_origin ?? linkedAlert?.evidence_origin ?? linkedIncident?.evidence_origin ?? null,
      txHash: latestDetection?.tx_hash ?? linkedAlert?.tx_hash ?? linkedIncident?.tx_hash ?? null,
      blockNumber: latestDetection?.block_number ?? linkedAlert?.block_number ?? linkedIncident?.block_number ?? null,
      detectorKind: latestDetection?.detector_kind ?? linkedAlert?.detector_kind ?? linkedIncident?.detector_kind ?? null,
      chainLinkedIds: latestDetection?.chain_linked_ids ?? linkedAlert?.chain_linked_ids ?? linkedIncident?.chain_linked_ids ?? null,
    };
  }, [alerts, detections, incidents]);

  const threatActionContextOptions = useMemo<ThreatActionContextOption[]>(() => {
    const options: ThreatActionContextOption[] = [];
    const seen = new Set<string>();
    detections.forEach((detection) => {
      const alertId = detection.linked_alert_id ?? null;
      const incidentId = detection.linked_incident_id ?? null;
      if (!alertId && !incidentId) {
        return;
      }
      const key = `${detection.id}:${alertId ?? 'none'}:${incidentId ?? 'none'}`;
      if (seen.has(key)) return;
      seen.add(key);
      options.push({
        id: key,
        label: `Detection ${detection.id.slice(0, 8)}${alertId ? ` · Alert ${alertId.slice(0, 8)}` : ''}${incidentId ? ` · Incident ${incidentId.slice(0, 8)}` : ''}`,
        detectionId: detection.id,
        alertId,
        incidentId,
      });
    });
    return options;
  }, [detections]);
  const [selectedThreatActionContextId, setSelectedThreatActionContextId] = useState<string>('');
  useEffect(() => {
    setSelectedThreatActionContextId((current) => {
      if (!current) return '';
      return threatActionContextOptions.some((option) => option.id === current)
        ? current
        : '';
    });
  }, [threatActionContextOptions]);
  const selectedThreatActionContext = useMemo(() => (
    threatActionContextOptions.find((option) => option.id === selectedThreatActionContextId) ?? null
  ), [selectedThreatActionContextId, threatActionContextOptions]);
  const noLinkedActionContextAvailable = threatActionContextOptions.length === 0;
  const shouldBlockThreatActionCreation = noLinkedActionContextAvailable || !selectedThreatActionContext;

  useEffect(() => {
    void fetch(`${apiUrl}/response/action-capabilities`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setActionCapabilities(capabilityMapFromPayload(payload)))
      .catch(() => setActionCapabilities({}));
  }, [apiUrl, authHeaders]);

  function responseActionModeLabel(value: unknown): string {
    const normalized = String(value ?? '').trim().toLowerCase();
    if (normalized === 'live') return 'LIVE';
    if (normalized === 'recommended') return 'RECOMMENDED';
    if (normalized === 'simulated') return 'SIMULATED';
    return 'SIMULATED';
  }

  function responseActionModeDetail(value: unknown): string {
    const mode = responseActionModeLabel(value);
    if (mode === 'LIVE') return 'LIVE (integration required; not executed from simulator mode)';
    if (mode === 'RECOMMENDED') return 'RECOMMENDED (approval/manual execution required)';
    return 'SIMULATED (no live execution)';
  }

  async function openDetectionEvidence(signal: DetectionItem) {
    const detectionId = signal.id.replace('detection-', '');
    const fallback = detections.find((item) => item.id === detectionId) ?? null;
    const fallbackRaw = fallback?.raw_evidence_json ?? null;
    const fallbackSummary = fallback?.evidence_summary || signal.evidenceSummary;
    try {
      const response = await fetch(`${apiUrl}/detections/${detectionId}/evidence`, { headers: authHeaders(), cache: 'no-store' });
      if (!response.ok) {
        setEvidenceDrawer({
          detectionId,
          title: signal.title,
          summary: fallbackSummary,
          raw: fallbackRaw,
        });
        return;
      }
      const payload = await response.json().catch(() => ({}));
      setEvidenceDrawer({
        detectionId,
        title: signal.title,
        summary: String(payload?.summary || fallbackSummary || 'No evidence summary available.'),
        raw: (payload?.raw_evidence_json ?? fallbackRaw ?? null) as Record<string, any> | null,
      });
    } catch {
      setEvidenceDrawer({
        detectionId,
        title: signal.title,
        summary: fallbackSummary,
        raw: fallbackRaw,
      });
    }
  }

  async function runSimulatedThreatAction(actionType: string, label: string) {
    if (shouldBlockThreatActionCreation) {
      setResponseToast('No linked alert/incident context available.');
      return;
    }
    const contextLabel = selectedThreatActionContext
      ? `Linked context: ${selectedThreatActionContext.label}`
      : 'No linked alert/incident context available.';
    const create = await fetch(`${apiUrl}/response/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        action_type: actionType,
        mode: 'simulated',
        status: 'pending',
        incident_id: selectedThreatActionContext.incidentId,
        alert_id: selectedThreatActionContext.alertId,
        result_summary: `SIMULATED ${label} created from threat client (${contextLabel})`,
      }),
    });
    if (!create.ok) {
      setResponseToast(`SIMULATED ${label} failed to create.`);
      return;
    }
    const action = await create.json();
    const execute = await fetch(`${apiUrl}/response/actions/${action.id}/execute`, { method: 'POST', headers: authHeaders() });
    const executePayload = await execute.json().catch(() => ({}));
    const backendMode = String(executePayload?.mode || executePayload?.requested_mode || 'simulated');
    const modeLabel = responseActionModeLabel(backendMode);
    const executionResult = responseActionExecutionMessage(executePayload);
    if (execute.ok && executionResult.isSuccess) {
      if (modeLabel === 'LIVE') {
        setResponseToast('LIVE mode was returned by backend, but this panel only executes simulated actions. No live action was executed.');
      } else if (modeLabel === 'RECOMMENDED') {
        setResponseToast('RECOMMENDED action recorded. Manual/live follow-up is still required.');
      } else {
        setResponseToast('SIMULATED action completed (no live execution).');
      }
      return;
    }
    setResponseToast(executionResult.text || `${modeLabel} ${label} could not be completed.`);
  }

  async function ensureSimulatorProofChain() {
    setEnsuringProofChain(true);
    try {
      const ensureResponse = await fetch(`${apiUrl}/ops/monitoring/proof-chain/ensure`, { method: 'POST', headers: authHeaders() });
      if (!ensureResponse.ok) {
        setResponseToast('Failed to generate simulator proof chain.');
        return;
      }
      const [runtimeStatusResponse, investigationTimelineResponse] = await Promise.all([
        fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' }),
        fetch(`${apiUrl}/ops/monitoring/investigation-timeline`, { headers: authHeaders(), cache: 'no-store' }),
      ]);
      if (runtimeStatusResponse.ok) {
        const runtimePayload = await runtimeStatusResponse.json().catch(() => ({}));
        setRuntimeStatusSnapshot(runtimePayload as MonitoringRuntimeStatus);
      }
      if (investigationTimelineResponse.ok) {
        const timelinePayload = await investigationTimelineResponse.json().catch(() => ({}));
        setInvestigationTimeline({
          ...timelinePayload,
          items: Array.isArray(timelinePayload?.items) ? timelinePayload.items : [],
          missing: Array.isArray(timelinePayload?.missing) ? timelinePayload.missing : [],
        } as MonitoringInvestigationTimeline);
      }
      setResponseToast('Simulator proof chain generated and monitoring status refreshed.');
    } catch {
      setResponseToast('Failed to generate simulator proof chain.');
    } finally {
      setEnsuringProofChain(false);
    }
  }

  return (
    <section className="stack monitoringConsoleStack">
      <article className="dataCard monitoringHeaderCard">
        <div className="monitoringHeaderTop">
          <div>
            <p className="sectionEyebrow">Threat monitoring command center</p>
            <h2>{user?.current_workspace?.name ?? 'Workspace monitoring console'}</h2>
          </div>
          <div className="monitoringHeaderActions">
            <button
              type="button"
              className="secondaryCta"
              onClick={() => window.dispatchEvent(new Event('pilot-history-refresh'))}
            >
              Refresh now
            </button>
            <button
              type="button"
              className="secondaryCta"
              disabled={ensuringProofChain}
              onClick={() => void ensureSimulatorProofChain()}
            >
              {ensuringProofChain ? 'Generating simulator proof chain…' : 'Generate simulator proof chain'}
            </button>
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
          {systemsPanelWarning ? <span className="statusBadge statusBadge-attention">{systemsPanelWarning}</span> : null}
          <span className="ruleChip">Open alerts {openAlerts}</span>
          <span className="ruleChip">Active incidents {activeIncidents}</span>
        </div>
        <PageStateBanner state={pageState} telemetryLabel={telemetryLabel} pollLabel={pollLabel} reason={runtimeReason} configurationReason={null} continuityStatus={truth.continuity_status} />
        {dbPersistenceOutageActive ? (
          <p className="statusLine">
            Persistence outage active: {dbPersistenceOutageReason}. Simulator/demo rows remain visible but are excluded from live-evidence claims.
          </p>
        ) : null}
        <p className="tableMeta">
          Last telemetry: {hasTelemetryTimestamp ? telemetryDisplayLabel : 'Not available'} · Last detection evaluation: {detectionEvalLabel} · Last poll: {pollLabel} · Last heartbeat: {monitoringPresentation.heartbeatLabel} · Runtime freshness: {String(runtimeSummary?.telemetry_freshness ?? runtimeStatusSnapshot?.freshness_status ?? 'unavailable')} · Runtime confidence: {String(runtimeSummary?.confidence ?? runtimeStatusSnapshot?.confidence_status ?? 'unavailable')}
        </p>
        {feed.loading ? <p className="statusLine">Loading monitoring state…</p> : null}
        {feed.refreshing ? <p className="statusLine">Refreshing monitoring state…</p> : null}
      </article>

      <section className="monitoringKpiGrid" aria-label="Monitoring KPIs">
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Monitoring Status</p>
          <p className="kpiValue">{monitoringPresentation.statusLabel}</p>
          <p className="tableMeta">{runtimeReason}</p>
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
          <p className="kpiValue">{latestRiskScore.value}</p>
          <p className="tableMeta">{latestRiskScore.tier} · {riskFreshness}</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Coverage State</p>
          <p className="kpiValue">{coverageSummary}</p>
          <p className="tableMeta">Systems reporting telemetry.</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Proof Chain Status</p>
          <p className="kpiValue">{proofChainStatus.toUpperCase()}</p>
          <p className="tableMeta">Missing links: {missingTimelineLinks.length === 0 ? 'none' : missingTimelineLinks.join(', ')}</p>
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
        {!loadingSnapshot && !showEvidenceLinkedSignals ? (
          <div className="emptyStatePanel">
            <h4>No evidence-linked threat signals</h4>
            <p className="muted">missing[] links: [{missingTimelineLinks.join(', ')}]</p>
            <div className="buttonRow">
              <button
                type="button"
                className="secondaryCta"
                onClick={() => void ensureSimulatorProofChain()}
                disabled={ensuringProofChain}
              >
                {ensuringProofChain ? 'Generating simulator proof chain…' : 'Generate simulator proof chain'}
              </button>
            </div>
          </div>
        ) : null}
        {!loadingSnapshot && showEvidenceLinkedSignals && detectionsToRender.length === 0 ? (
          <div className="emptyStatePanel">
            <h4>
              {pageState === 'configured_no_signals'
                ? 'No evidence-linked threat signals'
                : pageState === 'unconfigured_workspace'
                  ? 'No monitored systems configured'
                  : 'No detections available'}
            </h4>
            <p className="muted">
              {pageState === 'configured_no_signals'
                ? (monitoringPresentation.evidenceSourceLabel === 'live' || monitoringPresentation.evidenceSourceLabel === 'hybrid')
                  ? 'LIVE/HYBRID degraded state: monitoring is configured, but no persisted evidence is linked to active detections yet.'
                  : 'Monitoring is configured, but no persisted evidence is currently linked to active detections.'
                : pageState === 'unconfigured_workspace'
                  ? 'Workspace not configured: monitoring setup is incomplete.'
                  : 'No persisted or linked detections are available for display at this time.'}
            </p>
            <div className="buttonRow">
              <Link href="/monitored-systems" prefetch={false}>Manage monitored systems</Link>
              <Link href="/history" prefetch={false}>View workspace history</Link>
              <Link href="/integrations" prefetch={false}>Inspect integration health</Link>
            </div>
          </div>
        ) : null}
        {showEvidenceLinkedSignals ? (
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
                  {signal.liveEvidenceEligible === false ? ' · Simulator/demo evidence (not live)' : ''}
                  {dbPersistenceOutageActive ? ' · Excluded from live evidence during persistence outage' : ''}
                </p>
                <p className="tableMeta">
                  {formatAbsoluteTime(signal.timestamp)} · {formatRelativeTime(signal.timestamp)} · Source: {signal.source}
                </p>
                <p className="tableMeta">tx: {signal.txHash || 'n/a'} · block: {signal.blockNumber || 'n/a'} · counterparty: {signal.counterparty || 'n/a'} · amount: {signal.amount || 'n/a'} · contract/token: {signal.tokenOrContract || 'n/a'} · rule: {signal.ruleId || 'n/a'} · target: {signal.targetName || 'n/a'} · provider: {signal.sourceProvider || 'n/a'}</p>
                <p className="tableMeta">Chain: detection {signal.detectionId || 'n/a'} · alert {signal.alertId || 'n/a'} · incident {signal.incidentId || 'n/a'} · action {signal.actionId || 'n/a'}</p>
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
                  onClick={() => void openDetectionEvidence(signal)}
                >
                  Open evidence drawer
                </button>
                {signal.alertId ? <Link href="/alerts" prefetch={false}>Open alert link</Link> : null}
                {signal.incidentId ? <Link href="/incidents" prefetch={false}>Open incident link</Link> : null}
                {signal.actionId ? <Link href="/history" prefetch={false}>Open action link</Link> : null}
                <Link href={signal.href} prefetch={false}>View destination</Link>
              </div>
            </div>
          ))}
        </div>
        ) : null}
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
                  {hasTargetCoverageRows ? targetCoverageRows.map(({ target, coverage, risk, latestSignal, evidenceCopy, destinationHref }) => {
                    return (
                      <tr key={target.id}>
                        <td>{target.name}<span className="tableMeta">{displayIdentifier(target)}</span></td>
                        <td>{target.target_type || target.asset_type || 'System'}</td>
                        <td><span className={`statusBadge statusBadge-${target.health_status === 'broken' ? 'attention' : (target.monitoring_enabled ? 'healthy' : 'offline')}`}>{target.health_status === 'broken' ? 'Broken' : (target.monitoring_enabled ? 'Monitored' : 'Offline')}</span></td>
                        <td><span className={`statusBadge statusBadge-${coverageTone(coverage)}`}>{coverage}</span></td>
                        <td>{hasTelemetryTimestamp ? telemetryDisplayLabel : 'Not available'}</td>
                        <td>{pollLabel}</td>
                        <td>{monitoringPresentation.heartbeatLabel}</td>
                        <td>{latestSignal}<span className="tableMeta">{evidenceCopy}</span></td>
                        <td><span className={`statusBadge statusBadge-${risk.tone}`}>{risk.label}</span></td>
                        <td><Link href={destinationHref} prefetch={false}>Open linked destination</Link></td>
                      </tr>
                    );
                  }) : monitoredSystemCoverageRows.map(({ system, statusTone, statusLabel, coverage, latestSignal, risk, statusText, destinationHref }) => {
                    return (
                      <tr key={system.id}>
                        <td>{system.target_name || system.asset_name || 'Monitored system'}<span className="tableMeta">{system.chain || 'Unknown chain'}</span></td>
                        <td>System</td>
                        <td><span className={`statusBadge statusBadge-${statusTone}`}>{statusLabel}</span></td>
                        <td><span className={`statusBadge statusBadge-${coverageTone(coverage)}`}>{coverage}</span></td>
                        <td>{system.last_event_at ? formatRelativeTime(system.last_event_at) : 'Not available'}</td>
                        <td>{pollLabel}</td>
                        <td>{formatRelativeTime(system.last_heartbeat)}</td>
                        <td>{latestSignal}<span className="tableMeta">{statusText}</span></td>
                        <td><span className={`statusBadge statusBadge-${risk.tone}`}>{risk.label}</span></td>
                        <td><Link href={destinationHref} prefetch={false}>Open linked destination</Link></td>
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
                <h4>Monitoring run details not loaded in this panel</h4>
                <p className="muted">This view is runtime-status driven. Open history for detailed run records.</p>
              </div>
            ) : (
              <div className="tableWrap">
                <table>
                  <thead>
                    <tr>
                      <th>Started</th>
                      <th>Completed</th>
                      <th>Status</th>
                      <th>Trigger</th>
                      <th>Systems</th>
                      <th>Assets</th>
                      <th>Detections</th>
                      <th>Alerts created in this cycle</th>
                      <th>Telemetry</th>
                      <th>Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monitoringRuns.slice(0, 8).map((run) => (
                      <tr key={run.id}>
                        <td>{formatAbsoluteTime(run.started_at)}<span className="tableMeta">{formatRelativeTime(run.started_at)}</span></td>
                        <td>{run.completed_at ? formatRelativeTime(run.completed_at) : 'In progress'}</td>
                        <td><span className={`statusBadge statusBadge-${String(run.status || '').toLowerCase() === 'completed' ? 'healthy' : (String(run.status || '').toLowerCase() === 'error' ? 'attention' : 'offline')}`}>{String(run.status || 'unknown')}</span></td>
                        <td>{String(run.trigger_type || 'unknown')}</td>
                        <td>{Number(run.systems_checked_count ?? 0)}</td>
                        <td>{Number(run.assets_checked_count ?? 0)}</td>
                        <td>{Number(run.detections_created_count ?? 0)}</td>
                        <td>{Number(run.alerts_created_count ?? 0)}</td>
                        <td>{Number(run.telemetry_records_seen_count ?? 0)}</td>
                        <td>{run.notes || '—'}</td>
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
                <h4>Alert details not loaded in this panel</h4>
                <p className="muted">
                  Runtime status reports {openAlerts} open alert{openAlerts === 1 ? '' : 's'}. Open the alert queue to inspect full records.
                </p>
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
                      <p className="tableMeta">Chain: detection {alert.detection_id || linkedDetection?.id || 'n/a'} · alert {alert.id} · incident {alert.incident_id || 'n/a'} · action {alert.linked_action_id || 'n/a'}</p>
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
                        Open evidence drawer
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
                <h3>Active incidents with timeline and run evidence</h3>
              </div>
              <Link href="/incidents" prefetch={false}>Open incident queue</Link>
            </div>
            {loadingSnapshot ? <p className="muted">Loading incidents…</p> : null}
            {!loadingSnapshot && incidents.length === 0 ? (
              <div className="emptyStatePanel">
                <h4>Incident details not loaded in this panel</h4>
                <p className="muted">Runtime status reports {activeIncidents} active incident{activeIncidents === 1 ? '' : 's'}. Open the incident queue for full timeline records.</p>
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
                      <p className="tableMeta">Chain: detection {incident.linked_detection_id || 'n/a'} · alert {incident.source_alert_id || 'n/a'} · incident {incident.id} · action {incident.linked_action_id || 'n/a'}</p>
                    </div>
                    <Link href="/incidents" prefetch={false}>Open</Link>
                  </div>
                ))}
                <div className="stack compactStack">
                  <ThreatChainPanel
                    chainLinkedIds={chainPanelSelection.chainLinkedIds}
                    detectionId={chainPanelSelection.detectionId}
                    alertId={chainPanelSelection.alertId}
                    incidentId={chainPanelSelection.incidentId}
                    actionId={chainPanelSelection.actionId}
                    linkedEvidenceCount={chainPanelSelection.linkedEvidenceCount}
                    lastEvidenceAt={chainPanelSelection.lastEvidenceAt}
                    evidenceOrigin={chainPanelSelection.evidenceOrigin}
                    txHash={chainPanelSelection.txHash}
                    blockNumber={chainPanelSelection.blockNumber}
                    detectorKind={chainPanelSelection.detectorKind}
                    liveLikeMode={monitoringPresentation.evidenceSourceLabel === 'live' || monitoringPresentation.evidenceSourceLabel === 'hybrid'}
                    evidenceDrawerLabel="Open evidence drawer"
                    onOpenEvidence={() => {
                      const detection = chainPanelSelection.detectionId ? detections.find((item) => item.id === chainPanelSelection.detectionId) : null;
                      setEvidenceDrawer({
                        detectionId: chainPanelSelection.detectionId || undefined,
                        title: detection?.title || 'Threat chain evidence',
                        summary: detection?.evidence_summary || 'No evidence summary available.',
                        raw: detection?.raw_evidence_json ?? null,
                      });
                    }}
                  />
                  {threatChainSteps.map((step) => (
                    <div key={step.id} className="overviewListItem">
                      <div>
                        <p>{step.label}</p>
                        <p className="tableMeta">{step.detail}</p>
                        <p className="tableMeta">
                          <span className="statusBadge statusBadge-low">Threat chain</span>{' '}
                          {formatAbsoluteTime(step.timestamp)}
                        </p>
                      </div>
                      <Link href={step.href} prefetch={false}>Open</Link>
                    </div>
                  ))}
                </div>
                <div className="stack compactStack">
                  <div className="listHeader">
                    <p className="sectionEyebrow">Chain proof</p>
                    <h4>Detection created → Alert created → Incident opened → Action logged</h4>
                  </div>
                  {actionHistory.slice(0, 4).map((entry) => {
                    const entryAlertId = typeof entry.details_json?.alert_id === 'string' ? entry.details_json.alert_id : null;
                    const entryIncidentId = typeof entry.details_json?.incident_id === 'string' ? entry.details_json.incident_id : null;
                    const entryMode = typeof entry.details_json?.mode === 'string' ? entry.details_json.mode : null;
                    const modeLabel = responseActionModeDetail(entryMode);
                    const href = entry.object_type === 'alert' || entryAlertId ? '/alerts' : entry.object_type === 'incident' || entryIncidentId ? '/incidents' : '/history';
                    return (
                      <div key={entry.id} className="overviewListItem">
                        <div>
                          <p>{String(entry.action_type || 'workflow.action_recorded')}</p>
                          <p className="tableMeta">
                            object {String(entry.object_type || 'unknown')}:{String(entry.object_id || 'n/a')} · actor {String(entry.actor_type || 'system')}
                          </p>
                          <p className="tableMeta"><strong>Mode:</strong> {modeLabel}</p>
                          <p className="tableMeta">{formatAbsoluteTime(entry.timestamp)}</p>
                        </div>
                        <Link href={href} prefetch={false}>Open</Link>
                      </div>
                    );
                  })}
                  {actionHistory.length === 0 ? <p className="muted">Workflow action detail rows are not loaded in this runtime-status view. Open history for complete records.</p> : null}
                </div>
                <div className="stack compactStack">
                  {investigationTimelineItems.length === 0 ? (
                    <p className="muted">No investigation timeline records currently returned. Missing links: {missingTimelineLinks.length === 0 ? 'none' : missingTimelineLinks.join(', ')}.</p>
                  ) : null}
                  {investigationTimelineItems.map((item) => {
                    const linkName = String(item.link_name || 'unknown');
                    const sourceLabel = String(item.evidence_source || 'simulator');
                    return (
                      <div key={item.id} className="overviewListItem">
                        <div>
                          <p>{linkName.replaceAll('_', ' ')}</p>
                          <p className="tableMeta">id {item.id} · table {String(item.table_name || 'unknown')} · evidence {sourceLabel}</p>
                          <p className="tableMeta">
                            <span className={`statusBadge statusBadge-${timelineLinkTone(linkName)}`}>{linkName}</span>{' '}
                            {formatAbsoluteTime(item.timestamp)}
                          </p>
                        </div>
                        <Link href={timelineLinkHref(linkName)} prefetch={false}>Open</Link>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </article>

          <article className="dataCard">
            <p className="sectionEyebrow">Response Actions</p>
            <h3>Operational actions</h3>
            <p className="muted">Use investigation and escalation workflows to restore healthy monitoring and resolve risk. Modes are labeled as SIMULATED, RECOMMENDED, or LIVE; live execution requires real integration and is never implied by simulator flows.</p>
            <label className="fieldLabel" htmlFor="threat-action-context-select">Launch action context</label>
            <select
              id="threat-action-context-select"
              value={selectedThreatActionContext ? selectedThreatActionContext.id : ''}
              onChange={(event) => setSelectedThreatActionContextId(event.target.value)}
            >
              <option value="" disabled>Select linked detection/alert/incident context</option>
              {threatActionContextOptions.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
            {shouldBlockThreatActionCreation
              ? <p className="statusLine">No linked alert/incident context available.</p>
              : <p className="statusLine">Linked detection/alert/incident context selected for action creation.</p>}
            <div className="buttonRow">
              <button type="button" className="secondaryCta" disabled={ensuringProofChain} onClick={() => void ensureSimulatorProofChain()}>
                {ensuringProofChain ? 'Generating simulator proof chain…' : 'Generate simulator proof chain'}
              </button>
              <button type="button" disabled={shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.notify_team, 'simulated')} title={actionDisabledReason(actionCapabilities.notify_team, 'simulated') || ''} onClick={() => void runSimulatedThreatAction('notify_team', 'Run simulated response')}>Run simulated response (SIMULATED)</button>
              <button type="button" disabled={shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.block_transaction, 'simulated')} title={actionDisabledReason(actionCapabilities.block_transaction, 'simulated') || ''} onClick={() => void runSimulatedThreatAction('block_transaction', 'Block transaction')}>Block transaction (SIMULATED)</button>
              <button type="button" disabled={shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.revoke_approval, 'simulated')} title={actionDisabledReason(actionCapabilities.revoke_approval, 'simulated') || ''} onClick={() => void runSimulatedThreatAction('revoke_approval', 'Revoke approval')}>Revoke approval (SIMULATED)</button>
              <button type="button" disabled={shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.freeze_wallet, 'simulated')} title={actionDisabledReason(actionCapabilities.freeze_wallet, 'simulated') || ''} onClick={() => void runSimulatedThreatAction('freeze_wallet', 'Freeze wallet')}>Freeze wallet (SIMULATED)</button>
              <button type="button" disabled={shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.disable_monitored_system, 'simulated')} title={actionDisabledReason(actionCapabilities.disable_monitored_system, 'simulated') || ''} onClick={() => void runSimulatedThreatAction('disable_monitored_system', 'Disable monitored system')}>Disable monitored system (SIMULATED)</button>
              <button type="button" disabled={shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.suppress_rule, 'simulated')} title={actionDisabledReason(actionCapabilities.suppress_rule, 'simulated') || ''} onClick={() => void runSimulatedThreatAction('suppress_rule', 'Suppress/mute rule')}>Suppress/mute rule (SIMULATED)</button>
              <Link href="/alerts" prefetch={false}>Review alerts</Link>
              <Link href="/incidents" prefetch={false}>Open incident queue</Link>
              <Link href="/history" prefetch={false}>View workspace history</Link>
              <Link href="/monitored-systems" prefetch={false}>Manage monitored systems</Link>
              <Link href="/compliance" prefetch={false}>Review governance actions</Link>
              <Link href="/integrations" prefetch={false}>Inspect integration health</Link>
            </div>
            {responseToast ? <p className="statusLine">{responseToast}</p> : null}
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
