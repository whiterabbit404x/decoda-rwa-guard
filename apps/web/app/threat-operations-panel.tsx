'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { MonitoringPresentationStatus } from './monitoring-status-presentation';
import type { EnterpriseCriterionCheck, MonitoringInvestigationTimeline, MonitoringLoopHealth, MonitoringRuntimeStatus } from './monitoring-status-contract';
import { usePilotAuth } from 'app/pilot-auth-context';
import { actionDisabledReason, capabilityMapFromPayload, isActionDisabledInMode, responseActionExecutionMessage, type ResponseActionCapability } from './response-action-capabilities';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import { fetchRuntimeStatusDeduped } from './runtime-status-client';
import ThreatChainPanel from './threat-chain-panel';
import ThreatOverviewCard from './threat/threat-overview-card';
import MonitoringHealthCard from './threat/monitoring-health-card';
import DetectionFeed, { type DetectionRecord } from './threat/detection-feed';
import AlertIncidentChain from './threat/alert-incident-chain';
import ResponseActionPanel, { type ResponseAction } from './threat/response-action-panel';
import ThreatEmptyState from './threat/threat-empty-state';
import TechnicalRuntimeDetails from './threat/technical-runtime-details';
import ThreatPageHeader from './threat/threat-page-header';
import { buildDetectionRecords } from './threat/build-detection-records';
import { buildMonitoringHealthModel } from './threat/build-monitoring-health-model';
import { buildAlertIncidentChain } from './threat/build-alert-incident-chain';
import { buildResponseActionList, buildResponseActionsModel } from './threat/build-response-actions';
import { buildTechnicalRuntimeDetails } from './threat/build-technical-runtime-details';
import { THREAT_COPY, formatRawEvidenceReference } from './threat/threat-copy';

type Props = { apiUrl: string };
// Temporary backoff while runtime-status latency is elevated; re-evaluate when p95 is back under threshold.
const THREAT_PAGE_POLL_VISIBLE_MS = 45000;
const THREAT_PAGE_POLL_HIDDEN_MS = 60000;
const LOOP_DEGRADED_ALERT_THRESHOLD_SECONDS = 600;
const ENTERPRISE_GATE_LABELS: Record<string, string> = {
  continuity_slo_pass: 'Continuity SLO pass',
  linked_fresh_evidence: 'Linked fresh evidence',
  stable_monitored_systems: 'Stable monitored systems',
  live_action_capability_readiness: 'Live action capability readiness',
};
const ENTERPRISE_GATE_REMEDIATION_LINKS: Record<string, string> = {
  continuity_slo_pass: '/threat#continuity-slo',
  linked_fresh_evidence: '/threat#telemetry-freshness',
  stable_monitored_systems: '/threat#monitored-system-state',
  live_action_capability_readiness: '/threat#response-actions',
};
const ENTERPRISE_GATE_REMEDIATION_COPY: Record<string, string> = {
  continuity_slo_pass: 'Restore continuity SLO freshness across heartbeat, telemetry, ingestion, and detection.',
  linked_fresh_evidence: 'Relink and refresh evidence so the chain stays complete and current.',
  stable_monitored_systems: 'Bring monitored systems back to live reporting without contradiction or guard flags.',
  live_action_capability_readiness: 'Validate at least one live action path from threat to response execution.',
};
const CONTINUITY_REMEDIATION_COPY: Record<string, string> = {
  heartbeat_stale: 'Restart or recover the monitoring worker loop, then verify heartbeat freshness.',
  heartbeat_offline: 'Bring monitoring workers online and validate heartbeat telemetry resumes.',
  worker_not_live: 'Switch to live monitoring mode for this workspace and restart worker execution.',
  event_ingestion_stale: 'Verify telemetry provider reachability and re-run ingestion to refresh event timestamps.',
  event_ingestion_offline: 'Restore telemetry ingestion connectivity and confirm new events are persisted.',
  detection_pipeline_stale: 'Run detection evaluation and confirm recent detections are written for monitored systems.',
  detection_eval_stale: 'Run detection evaluation and confirm recent detections are written for monitored systems.',
  continuity_slo_failed: 'Review all continuity dimensions and remediate the first failed freshness check.',
};
const ENTERPRISE_CRITERIA_LABELS: Record<string, string> = {
  criterion_b_continuity_slos: 'Criterion B · Continuity SLOs',
  criterion_c_reconcile_stability: 'Criterion C · Reconcile stability',
  criterion_d_evidence_chain_hydration: 'Criterion D · Evidence-chain hydration',
  criterion_e_live_action_governance: 'Criterion E · Live action governance',
  criterion_f_state_model_ux: 'Criterion F · State-model UX',
  hidden_architecture: 'Hidden architecture',
};

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

type ThreatActionButtonId =
  | 'sim-notify-team'
  | 'sim-revoke-approval'
  | 'rec-freeze-wallet'
  | 'rec-disable-monitored-system'
  | 'live-freeze-wallet'
  | 'live-revoke-approval';

type ThreatActionButtonState = {
  disabled: boolean;
  reason: string;
  noOpMessage: string;
  nextStepLabel: string;
  nextStepHref: string;
};

type ThreatFeedState = 'Live' | 'Historical' | 'Test' | 'Stale' | 'Investigating' | 'Resolved';
export type PageOperationalState =
  | 'healthy_live'
  | 'configured_no_signals'
  | 'degraded_partial'
  | 'offline_no_telemetry'
  | 'unconfigured_workspace'
  | 'fetch_error';

type SnapshotFailureKey = 'runtime-status';
type SnapshotCollectionKey = 'detections' | 'alerts' | 'incidents' | 'evidence' | 'history' | 'monitoring-runs';
type SnapshotFreshnessState = 'fresh' | 'stale' | 'unavailable';
type ReconcileJobStatus = 'queued' | 'running' | 'completed' | 'failed';
type ReconcileJobSnapshot = {
  id: string;
  status: ReconcileJobStatus;
  idempotency_key?: string | null;
  retry_count?: number;
  counts?: Record<string, number>;
  reason_codes?: string[];
  reason_code?: string | null;
  reason_detail?: string | null;
  progress_state?: Record<string, unknown> | null;
  affected_systems?: string[];
  last_event_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

type MonitoringProvenanceLabel = 'live' | 'degraded' | 'stale_snapshot' | 'partial_failure';
type EndpointProvenanceState = 'live' | 'degraded' | 'stale_snapshot' | 'partial_failure';

type PageBannerModel = {
  variant: 'explanation' | 'fetch_error';
  headline?: string;
  primaryCopy: string;
  metaLines: string[];
};

type CanonicalCollectionPayload = {
  detections?: DetectionRow[];
  alerts?: AlertRow[];
  incidents?: IncidentRow[];
  evidence?: EvidenceRow[];
  history?: ActionHistoryRow[];
  actions?: ActionHistoryRow[];
  monitoring_runs?: MonitoringRunRow[];
  runs?: MonitoringRunRow[];
};

type MonitoringViewModel = {
  presentationStatus: MonitoringPresentationStatus;
  presentationStatusLabel: string;
  presentationTone: 'healthy' | 'offline' | 'attention';
  pageState: PageOperationalState;
  continuitySlo: ContinuitySloEvaluation;
  telemetryLabel: string;
  pollLabel: string;
  heartbeatLabel: string;
  telemetryState: SnapshotFreshnessState;
  pollState: SnapshotFreshnessState;
  heartbeatState: SnapshotFreshnessState;
  provenanceLabel: MonitoringProvenanceLabel;
  provenanceExplanation: string;
  endpointProvenance: {
    runtimeStatus: EndpointProvenanceState;
  };
  lastSuccessfulRuntimeRefreshAt: string | null;
  lastSuccessfulTimelineRefreshAt: string | null;
  lastSuccessfulRefreshAt: string | null;
  runtimeReason: string;
  configurationReason: string | null;
  continuityStatus: 'continuous_live' | 'continuous_no_evidence' | 'degraded' | 'offline' | 'idle_no_telemetry' | null;
  evidenceSourceLabel: string;
  protectedAssetCount: number;
  configuredSystems: number;
  reportingSystems: number;
  evidenceCount: number;
  openAlerts: number;
  activeIncidents: number;
  headerStatusChips: Array<{ label: string; tone: 'chip' | 'status'; className?: string }>;
  contradictions: string[];
  pageBanner: PageBannerModel;
  ctas: {
    generateSimulatorProofChain: ThreatActionButtonState;
  };
  actionButtons: Record<ThreatActionButtonId, ThreatActionButtonState>;
  confirmLiveAction: ThreatActionButtonState;
  disabledActionGuidance: Array<{
    key: string;
    action: string;
    reason: string;
    nextStepLabel: string;
    nextStepHref: string;
  }>;
};

type MonitoringStatusViewModel = Omit<MonitoringViewModel, 'actionButtons' | 'confirmLiveAction' | 'disabledActionGuidance'>;

const STRUCTURAL_CONFIGURATION_REASON_CODES = new Set([
  'no_valid_protected_assets',
  'no_linked_monitored_systems',
  'no_persisted_enabled_monitoring_config',
  'target_system_linkage_invalid',
]);

type ContinuitySloDimension = {
  key: 'heartbeat' | 'telemetry' | 'detection_eval';
  label: string;
  ageSeconds: number | null;
  thresholdSeconds: number | null;
  pass: boolean;
  reason: string | null;
};

type ContinuitySloEvaluation = {
  pass: boolean;
  statusLabel: 'PASS' | 'FAIL';
  dimensions: ContinuitySloDimension[];
};

type ContinuityFailedCheck = {
  code: string;
  label: string;
  detail?: string;
};

function formatSloDuration(value: number | null): string {
  if (value === null || Number.isNaN(value)) return 'missing';
  if (value < 60) return `${value}s`;
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

export function evaluateContinuitySlo(
  summary?: MonitoringRuntimeStatus['workspace_monitoring_summary'] | null,
  continuitySloPayload?: MonitoringRuntimeStatus['continuity_slo'] | null,
): ContinuitySloEvaluation {
  const continuityContractChecks = (summary?.continuity_contract as Record<string, any> | undefined)?.checks
    ?? (continuitySloPayload as Record<string, any> | undefined)?.checks
    ?? {};
  const thresholds: {
    heartbeat?: number;
    telemetry?: number;
    event_ingestion?: number;
    detection_eval?: number;
  } = {
    ...(continuitySloPayload?.required_thresholds_seconds ?? {}),
    ...(continuitySloPayload?.thresholds_seconds ?? {}),
    ...(summary?.required_thresholds_seconds ?? {}),
    ...(summary?.thresholds_seconds ?? {}),
  };
  const normalizedTopLevelPass = continuitySloPayload?.pass === true || summary?.continuity_slo_pass === true;
  const baseDimensions: ContinuitySloDimension[] = [
    {
      key: 'heartbeat',
      label: 'Worker heartbeat',
      ageSeconds: continuitySloPayload?.worker_heartbeat_age_seconds
        ?? continuitySloPayload?.heartbeat_age_seconds
        ?? summary?.worker_heartbeat_age_seconds
        ?? summary?.heartbeat_age_seconds
        ?? null,
      thresholdSeconds: typeof thresholds.heartbeat === 'number' ? thresholds.heartbeat : null,
      pass: false,
      reason: null,
    },
    {
      key: 'telemetry',
      label: 'Telemetry ingestion',
      ageSeconds: continuitySloPayload?.telemetry_age_seconds ?? summary?.telemetry_age_seconds ?? summary?.event_ingestion_age_seconds ?? null,
      thresholdSeconds: typeof thresholds.telemetry === 'number'
        ? thresholds.telemetry
        : (typeof thresholds.event_ingestion === 'number' ? thresholds.event_ingestion : null),
      pass: false,
      reason: null,
    },
    {
      key: 'detection_eval',
      label: 'Detection evaluation',
      ageSeconds: continuitySloPayload?.detection_age_seconds
        ?? continuitySloPayload?.detection_pipeline_age_seconds
        ?? continuitySloPayload?.detection_eval_age_seconds
        ?? summary?.detection_age_seconds
        ?? summary?.detection_pipeline_age_seconds
        ?? summary?.detection_eval_age_seconds
        ?? null,
      thresholdSeconds: typeof thresholds.detection_eval === 'number' ? thresholds.detection_eval : null,
      pass: false,
      reason: null,
    },
  ];
  const dimensions: ContinuitySloDimension[] = baseDimensions.map((dimension): ContinuitySloDimension => {
    const contractCheck = continuityContractChecks[`${dimension.key === 'detection_eval' ? 'detection' : dimension.key}_freshness`];
    if (contractCheck && typeof contractCheck === 'object' && typeof contractCheck.pass === 'boolean') {
      return {
        ...dimension,
        ageSeconds: typeof contractCheck.age_seconds === 'number' ? contractCheck.age_seconds : dimension.ageSeconds,
        thresholdSeconds: typeof contractCheck.threshold_seconds === 'number' ? contractCheck.threshold_seconds : dimension.thresholdSeconds,
        pass: Boolean(contractCheck.pass),
        reason: contractCheck.pass ? null : `${String(contractCheck.state || 'failed')}`,
      };
    }
    if (dimension.ageSeconds === null) {
      return { ...dimension, pass: false, reason: 'timestamp missing' };
    }
    if (dimension.thresholdSeconds === null) {
      return { ...dimension, pass: false, reason: 'threshold missing' };
    }
    if (dimension.ageSeconds > dimension.thresholdSeconds) {
      return {
        ...dimension,
        pass: false,
        reason: `${formatSloDuration(dimension.ageSeconds)} exceeds ${formatSloDuration(dimension.thresholdSeconds)}`,
      };
    }
    return { ...dimension, pass: true, reason: null };
  });

  const calculatedPass = dimensions.every((dimension) => dimension.pass);
  const pass = Boolean(normalizedTopLevelPass && calculatedPass);
  return {
    pass,
    statusLabel: pass ? 'PASS' : 'FAIL',
    dimensions,
  };
}

function continuitySloFailureReasons(continuitySlo: ContinuitySloEvaluation): string {
  const reasons = continuitySlo.dimensions
    .filter((dimension) => !dimension.pass)
    .map((dimension) => `${dimension.label}: ${dimension.reason || 'failed'}`);
  if (reasons.length === 0) {
    return 'All continuity timestamps are within SLO.';
  }
  return reasons.join('; ');
}

function continuitySloFailingDimensions(continuitySlo: ContinuitySloEvaluation): string {
  const failed = continuitySlo.dimensions.filter((dimension) => !dimension.pass).map((dimension) => dimension.label);
  return failed.length > 0 ? failed.join(', ') : 'none';
}

function continuityFailureLabelFromCode(code: string): string {
  if (code.startsWith('heartbeat_')) return 'Worker heartbeat';
  if (code.startsWith('event_ingestion_') || code.startsWith('telemetry_')) return 'Telemetry ingestion';
  if (code.startsWith('detection_pipeline_') || code.startsWith('detection_eval_') || code.startsWith('detection_')) return 'Detection evaluation';
  return code.replaceAll('_', ' ');
}

export function continuityFailedChecks(
  summary?: MonitoringRuntimeStatus['workspace_monitoring_summary'] | null,
  continuitySloPayload?: MonitoringRuntimeStatus['continuity_slo'] | null,
  continuitySlo?: ContinuitySloEvaluation,
): ContinuityFailedCheck[] {
  const breachReasons = [
    ...(continuitySloPayload?.breach_reasons ?? []),
    ...(summary?.continuity_breach_reasons ?? []),
  ].filter((item) => Boolean(item && typeof item === 'object'));
  if (breachReasons.length > 0) {
    const mapped = breachReasons.map((item: any) => {
      const code = String(item.code ?? item.check ?? 'continuity_breach').trim();
      const label = continuityFailureLabelFromCode(code);
      const ageSeconds = typeof item.age_seconds === 'number' ? item.age_seconds : null;
      const thresholdSeconds = typeof item.threshold_seconds === 'number' ? item.threshold_seconds : null;
      const state = String(item.state ?? '').trim();
      const detail = [
        ageSeconds !== null ? `age ${formatSloDuration(ageSeconds)}` : null,
        thresholdSeconds !== null ? `threshold ${formatSloDuration(thresholdSeconds)}` : null,
        state ? `state ${state}` : null,
      ].filter(Boolean).join(' · ');
      return { code, label, detail };
    });
    return Array.from(new Map(mapped.map((item) => [`${item.code}:${item.detail ?? ''}`, item])).values());
  }
  const reasonCodes = Array.from(new Set(
    [
      ...(continuitySloPayload?.reason_codes ?? []),
      ...(summary?.continuity_reason_codes ?? []),
    ]
      .map((code) => String(code ?? '').trim())
      .filter(Boolean),
  ));
  if (reasonCodes.length > 0) {
    return reasonCodes.map((code) => ({ code, label: continuityFailureLabelFromCode(code) }));
  }
  return (continuitySlo?.dimensions ?? [])
    .filter((dimension) => !dimension.pass)
    .map((dimension) => ({ code: dimension.key, label: dimension.label }));
}

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
  rawEvidenceReference?: string;
  rawEvidenceObservedAt?: string | null;
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
const POLL_STALE_MS = 10 * 60 * 1000;
const HEARTBEAT_STALE_MS = 10 * 60 * 1000;
const DETECTION_LIVE_MS = 15 * 60 * 1000;

function deriveSnapshotFreshnessState(value: string | null | undefined, staleMs: number): SnapshotFreshnessState {
  if (!value) return 'unavailable';
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return 'unavailable';
  if (Date.now() - timestamp > staleMs) return 'stale';
  return 'fresh';
}

function configurationReasonMessage(reason: string | null | undefined): string {
  switch (reason) {
    case 'no_valid_protected_assets':
      return 'No valid protected assets are linked to enabled monitoring yet.';
    case 'no_linked_monitored_systems':
      return 'No linked monitored systems exist for enabled workspace targets.';
    case 'no_persisted_enabled_monitoring_config':
      return 'No persisted enabled monitoring configuration exists yet.';
    case 'target_system_linkage_invalid':
      return 'Target/system linkage is invalid. Run monitored systems reconcile and verify the reconcile status badge reaches COMPLETED.';
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

function mostRecentTimestamp(...values: Array<string | null | undefined>): string | null {
  let latest: number | null = null;
  let latestIso: string | null = null;
  values.forEach((value) => {
    if (!value) return;
    const timestamp = new Date(value).getTime();
    if (Number.isNaN(timestamp)) return;
    if (latest === null || timestamp > latest) {
      latest = timestamp;
      latestIso = value;
    }
  });
  return latestIso;
}

function deterministicDisabledReason(reason: string | null | undefined, fallback: string): string {
  const normalized = String(reason || '').trim();
  return normalized.length > 0 ? normalized : fallback;
}

export function collectMonitoringContradictions(model: Pick<MonitoringViewModel, 'provenanceLabel' | 'telemetryState' | 'pollState' | 'heartbeatState' | 'endpointProvenance' | 'presentationStatus'>): string[] {
  const contradictions: string[] = [];
  if (model.provenanceLabel === 'live' && (model.telemetryState !== 'fresh' || model.pollState !== 'fresh' || model.heartbeatState !== 'fresh')) {
    contradictions.push('Live provenance cannot be shown while telemetry, poll, or heartbeat freshness is stale/unavailable.');
  }
  if (model.provenanceLabel === 'live' && model.endpointProvenance.runtimeStatus !== 'live') {
    contradictions.push('Live provenance requires runtime endpoint provenance to be live.');
  }
  if (model.provenanceLabel === 'live' && model.presentationStatus !== 'live') {
    contradictions.push('Live provenance requires presentation status to be live.');
  }
  if (model.provenanceLabel === 'partial_failure' && model.endpointProvenance.runtimeStatus !== 'partial_failure') {
    contradictions.push('Partial failure provenance requires at least one endpoint to report partial_failure.');
  }
  if (model.provenanceLabel === 'stale_snapshot'
    && model.telemetryState !== 'stale'
    && model.pollState !== 'stale'
    && model.heartbeatState !== 'stale'
    && model.endpointProvenance.runtimeStatus !== 'stale_snapshot') {
    contradictions.push('stale_snapshot provenance requires stale freshness telemetry or an endpoint stale_snapshot marker.');
  }
  return contradictions;
}

function reconcileStatusBadgeTone(status?: ReconcileJobStatus | null): 'healthy' | 'attention' | 'offline' {
  if (status === 'completed') return 'healthy';
  if (status === 'running' || status === 'queued') return 'attention';
  return 'offline';
}

export function formatOperationalStateLabel(value: unknown): string {
  const normalized = String(value ?? '').trim();
  return normalized ? normalized.replaceAll('_', ' ') : 'unknown';
}

export function resolveLoopHealthSignal(
  loopHealth: MonitoringLoopHealth | null | undefined,
  nowMs = Date.now(),
  degradedThresholdSeconds = LOOP_DEGRADED_ALERT_THRESHOLD_SECONDS,
): { state: 'healthy' | 'degraded' | 'recovering'; degradedSeconds: number | null; shouldAlert: boolean } {
  const lastSuccessful = loopHealth?.last_successful_cycle ? Date.parse(loopHealth.last_successful_cycle) : Number.NaN;
  const degradedSeconds = Number.isFinite(lastSuccessful) ? Math.max(0, Math.floor((nowMs - lastSuccessful) / 1000)) : null;
  const hasFailures = Number(loopHealth?.consecutive_failures ?? 0) > 0;
  const loopRunning = Boolean(loopHealth?.loop_running);
  const hasRetry = Boolean(loopHealth?.next_retry_at);
  const degraded = !loopRunning && (hasFailures || hasRetry);
  const recovering = loopRunning && hasFailures;
  const state: 'healthy' | 'degraded' | 'recovering' = degraded ? 'degraded' : (recovering ? 'recovering' : 'healthy');
  const shouldAlert = state === 'degraded' && degradedSeconds !== null && degradedSeconds >= degradedThresholdSeconds;
  return { state, degradedSeconds, shouldAlert };
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

function monitoringTone(status: MonitoringPresentationStatus): 'healthy' | 'offline' | 'attention' {
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

type PersistedThreatChain = {
  detection: DetectionRow | null;
  alert: AlertRow | null;
  incident: IncidentRow | null;
  action: ActionHistoryRow | null;
  linkedIds: {
    detectionId: string | null;
    alertId: string | null;
    incidentId: string | null;
    actionId: string | null;
  };
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

function isSimulatorEvidenceMode(value: unknown): boolean {
  const source = normalizeLookup(String(value ?? ''));
  if (!source) return false;
  return ['simulator', 'synthetic', 'demo', 'fallback', 'test', 'lab', 'replay'].some((flag) => source.includes(flag));
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

function resolvePersistedThreatChain(params: {
  detections: DetectionRow[];
  alerts: AlertRow[];
  incidents: IncidentRow[];
  actionHistory: ActionHistoryRow[];
}): PersistedThreatChain {
  const { detections, alerts, incidents, actionHistory } = params;
  const detection = detections
    .slice()
    .sort((a, b) => parseTimestamp(b.detected_at) - parseTimestamp(a.detected_at))[0] ?? null;
  const detectionAlertId = detection?.chain_linked_ids?.alert_id ?? detection?.linked_alert_id ?? null;
  const alert = detectionAlertId
    ? alerts.find((item) => normalizeLookup(item.id) === normalizeLookup(detectionAlertId)) ?? null
    : detection
      ? alerts.find((item) => normalizeLookup(item.detection_id) === normalizeLookup(detection.id)) ?? null
      : null;
  const alertIncidentId = alert?.chain_linked_ids?.incident_id ?? alert?.incident_id ?? null;
  const detectionIncidentId = detection?.chain_linked_ids?.incident_id ?? detection?.linked_incident_id ?? null;
  const incident = (alertIncidentId || detectionIncidentId)
    ? incidents.find((item) => normalizeLookup(item.id) === normalizeLookup(alertIncidentId ?? detectionIncidentId)) ?? null
    : alert
      ? incidents.find((item) => normalizeLookup(item.source_alert_id) === normalizeLookup(alert.id)) ?? null
      : null;
  const actionIdFromChain = detection?.chain_linked_ids?.action_id
    ?? detection?.linked_action_id
    ?? alert?.chain_linked_ids?.action_id
    ?? alert?.linked_action_id
    ?? incident?.chain_linked_ids?.action_id
    ?? incident?.linked_action_id
    ?? null;
  const action = actionHistory
    .slice()
    .sort((a, b) => parseTimestamp(b.timestamp) - parseTimestamp(a.timestamp))
    .find((entry) => (
      (actionIdFromChain && normalizeLookup(entry.id) === normalizeLookup(actionIdFromChain))
      || (incident && (
        (normalizeLookup(entry.object_type) === 'incident' && normalizeLookup(entry.object_id) === normalizeLookup(incident.id))
        || normalizeLookup(entry.details_json?.incident_id as string | null) === normalizeLookup(incident.id)
      ))
      || (alert && (
        (normalizeLookup(entry.object_type) === 'alert' && normalizeLookup(entry.object_id) === normalizeLookup(alert.id))
        || normalizeLookup(entry.details_json?.alert_id as string | null) === normalizeLookup(alert.id)
      ))
    )) ?? null;

  return {
    detection,
    alert,
    incident,
    action,
    linkedIds: {
      detectionId: detection?.chain_linked_ids?.detection_id ?? detection?.id ?? alert?.chain_linked_ids?.detection_id ?? incident?.chain_linked_ids?.detection_id ?? null,
      alertId: detectionAlertId ?? detection?.chain_linked_ids?.alert_id ?? alert?.chain_linked_ids?.alert_id ?? alert?.id ?? incident?.chain_linked_ids?.alert_id ?? null,
      incidentId: detectionIncidentId ?? alertIncidentId ?? alert?.chain_linked_ids?.incident_id ?? incident?.chain_linked_ids?.incident_id ?? incident?.id ?? null,
      actionId: actionIdFromChain ?? action?.id ?? null,
    },
  };
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
  const structuralReasonValues = [configurationReason, summaryConfigurationReason]
    .map((value) => String(value ?? '').toLowerCase())
    .filter((value) => value.length > 0);
  const structuralReasonCodeValues = [...configurationReasonCodes, ...summaryConfigurationReasonCodes]
    .map((value) => String(value ?? '').toLowerCase())
    .filter((value) => value.length > 0);
  const structuralUnconfiguredReason = [...structuralReasonValues, ...structuralReasonCodeValues]
    .some((value) => STRUCTURAL_CONFIGURATION_REASON_CODES.has(value));

  if (runtimeQueryFailure) {
    return 'fetch_error';
  }

  if (snapshotError) {
    return 'fetch_error';
  }

  if (structuralUnconfiguredReason) {
    return 'unconfigured_workspace';
  }

  if (continuityStatus === 'offline' || runtimeStatus === 'offline') {
    return 'offline_no_telemetry';
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
  return `Monitoring snapshot is running on stale fallback data (${failedEndpoints.length} endpoint${failedEndpoints.length === 1 ? '' : 's'} failed this cycle).`;
}

export function formatSystemsPanelWarning(failedEndpoints: SnapshotFailureKey[]): string | null {
  if (failedEndpoints.includes('runtime-status')) {
    return 'Runtime status unavailable';
  }
  return null;
}

export function pageStatePrimaryCopy(
  state: PageOperationalState,
  configurationReason?: string | null,
  continuityStatus?: 'continuous_live' | 'continuous_no_evidence' | 'degraded' | 'offline' | 'idle_no_telemetry' | null,
  continuitySlo?: ContinuitySloEvaluation,
  continuityFailedCheckList: ContinuityFailedCheck[] = [],
  remediationLinks: Record<string, string> = {},
): string {
  if (state === 'healthy_live') {
    return 'Live monitoring is healthy. Telemetry freshness and threat detections reflect current workspace conditions.';
  }
  if (state === 'configured_no_signals') {
    const sloReasons = continuitySlo ? continuitySloFailureReasons(continuitySlo) : 'Continuity reasons unavailable.';
    const failedReasons = continuityFailedCheckList.length > 0
      ? continuityFailedCheckList.map((item) => item.label).join(', ')
      : sloReasons;
    const remediationTargets = continuityFailedCheckList
      .map((item) => remediationLinks[item.code] ?? ENTERPRISE_GATE_REMEDIATION_LINKS.continuity_slo_pass)
      .filter(Boolean);
    const remediationCopy = remediationTargets.length > 0
      ? ` Remediation: ${Array.from(new Set(remediationTargets)).join(' · ')}.`
      : '';
    if (continuityStatus === 'continuous_no_evidence') {
      return `Monitoring continuity needs attention: ${failedReasons}.${remediationCopy}`;
    }
    if (continuityStatus === 'continuous_live') {
      return 'Continuous live monitoring proven. No active detections are currently open.';
    }
    return `Monitoring continuity needs attention: ${failedReasons}.${remediationCopy}`;
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

function PageStateBanner({ viewModel }: { viewModel: MonitoringViewModel }) {
  if (viewModel.pageBanner.variant === 'fetch_error') {
    return (
      <ThreatEmptyState>
        <h4>{viewModel.pageBanner.headline || 'Telemetry retrieval degraded'}</h4>
        <p className="muted">{viewModel.pageBanner.primaryCopy}</p>
        {viewModel.pageBanner.metaLines.map((line) => <p key={line} className="tableMeta">{line}</p>)}
        <div className="buttonRow">
          <Link href="/threat" prefetch={false}>Retry</Link>
          <Link href="/integrations" prefetch={false}>Inspect backend integration status</Link>
          <Link href="/history" prefetch={false}>Review recent runtime history</Link>
        </div>
      </ThreatEmptyState>
    );
  }
  return <p className="explanation">{viewModel.pageBanner.primaryCopy}</p>;
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
  const [snapshotFailedEndpoints, setSnapshotFailedEndpoints] = useState<SnapshotFailureKey[]>([]);
  const [snapshotStaleCollections, setSnapshotStaleCollections] = useState<SnapshotCollectionKey[]>([]);
  const [collectionLastSuccessfulRefreshAt, setCollectionLastSuccessfulRefreshAt] = useState<Record<SnapshotCollectionKey, string | null>>({
    detections: null,
    alerts: null,
    incidents: null,
    evidence: null,
    history: null,
    'monitoring-runs': null,
  });
  const collectionCacheRef = useRef<{
    detections: DetectionRow[];
    alerts: AlertRow[];
    incidents: IncidentRow[];
    evidence: EvidenceRow[];
    history: ActionHistoryRow[];
    monitoringRuns: MonitoringRunRow[];
  }>({
    detections: [],
    alerts: [],
    incidents: [],
    evidence: [],
    history: [],
    monitoringRuns: [],
  });
  const [latestReconcileJob, setLatestReconcileJob] = useState<ReconcileJobSnapshot | null>(null);
  const [activeReconcileId, setActiveReconcileId] = useState<string | null>(null);
  const [ensuringProofChain, setEnsuringProofChain] = useState(false);
  const reconcileStateStorageKey = useMemo(
    () => (user?.current_workspace?.id ? `pilot.reconcile.active.${user.current_workspace.id}` : null),
    [user?.current_workspace?.id],
  );

  useEffect(() => {
    if (!reconcileStateStorageKey) return;
    try {
      const cached = window.localStorage.getItem(reconcileStateStorageKey);
      setActiveReconcileId(cached || null);
    } catch {
      setActiveReconcileId(null);
    }
  }, [reconcileStateStorageKey]);

  useEffect(() => {
    if (!reconcileStateStorageKey) return;
    try {
      if (activeReconcileId) {
        window.localStorage.setItem(reconcileStateStorageKey, activeReconcileId);
      } else {
        window.localStorage.removeItem(reconcileStateStorageKey);
      }
    } catch {
      // Best effort storage only.
    }
  }, [activeReconcileId, reconcileStateStorageKey]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function refreshSnapshot() {
      if (!active || !isAuthenticated || !user?.current_workspace?.id) {
        return;
      }
      async function safeJson(response: Response | null): Promise<any> {
        if (!response?.ok) {
          return {};
        }
        try {
          return await response.json();
        } catch {
          return {};
        }
      }

      function payloadRows<T>(payload: any, keys: string[]): T[] | null {
        for (const key of keys) {
          const rows = payload?.[key];
          if (Array.isArray(rows)) {
            return rows as T[];
          }
        }
        return null;
      }

      function payloadRowsWithAvailability<T>(payload: any, keys: string[]): { rows: T[] | null; available: boolean } {
        for (const key of keys) {
          if (Object.prototype.hasOwnProperty.call(payload ?? {}, key)) {
            const rows = payload?.[key];
            if (Array.isArray(rows)) {
              return { rows: rows as T[], available: true };
            }
            return { rows: null, available: false };
          }
        }
        return { rows: null, available: false };
      }

      function updateCollection<T>({
        key,
        result,
        payload,
        payloadKeys,
        canonical,
        setter,
        stale,
        cacheKey,
        markRefreshed,
      }: {
        key: SnapshotCollectionKey;
        result: PromiseSettledResult<Response>;
        payload: any;
        payloadKeys: string[];
        canonical: { rows: T[] | null; available: boolean };
        setter: (rows: T[]) => void;
        stale: SnapshotCollectionKey[];
        cacheKey: keyof typeof collectionCacheRef.current;
        markRefreshed: (collectionKey: SnapshotCollectionKey) => void;
      }) {
        const endpointOk = result.status === 'fulfilled' && result.value.ok;
        const applyRows = (rows: T[], allowEmpty: boolean): boolean => {
          if (rows.length === 0 && !allowEmpty) {
            return false;
          }
          setter(rows);
          collectionCacheRef.current[cacheKey] = rows as any;
          markRefreshed(key);
          return true;
        };
        const endpoint = payloadRowsWithAvailability<T>(payload, payloadKeys);
        if (canonical.available && canonical.rows) {
          const rows = canonical.rows;
          if (applyRows(rows, endpointOk)) {
            if (!endpointOk) {
              stale.push(key);
            }
            return;
          }
          if (!endpointOk) {
            stale.push(key);
          }
        }
        if (endpointOk && endpoint.available && endpoint.rows) {
          const rows = endpoint.rows;
          if (applyRows(rows, true)) return;
        }
        const cachedRows = collectionCacheRef.current[cacheKey] as T[];
        if (cachedRows.length > 0) {
          setter(cachedRows);
        }
        stale.push(key);
      }

      try {
        const [
          runtimeStatusResult,
          investigationTimelineResult,
          detectionsResult,
          alertsResult,
          incidentsResult,
          evidenceResult,
          historyResult,
          monitoringRunsResult,
          reconcileLatestResult,
          activeReconcileStatusResult,
        ] = await Promise.allSettled([
          Promise.resolve(fetchRuntimeStatusDeduped(authHeaders(), { forceRefresh: true })),
          fetch(`${apiUrl}/ops/monitoring/investigation-timeline`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/detections?limit=50`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/alerts?limit=50`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/incidents?limit=50`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/ops/monitoring/evidence?limit=50`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/history/actions?limit=50`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/monitoring/runs?limit=20`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/monitoring/systems/reconcile/latest`, { headers: authHeaders(), cache: 'no-store' }),
          activeReconcileId ? fetch(`${apiUrl}/monitoring/systems/reconcile/${encodeURIComponent(activeReconcileId)}`, { headers: authHeaders(), cache: 'no-store' }) : Promise.resolve(new Response('{}', { status: 204 })),
        ]);
        if (!active) return;
        const runtimeStatusPayload = runtimeStatusResult.status === 'fulfilled' ? runtimeStatusResult.value : null;
        const investigationTimelineResponse = investigationTimelineResult.status === 'fulfilled' && investigationTimelineResult.value.ok
          ? investigationTimelineResult.value
          : null;
        const failedEndpoints: SnapshotFailureKey[] = [];
        if (!runtimeStatusPayload) failedEndpoints.push('runtime-status');
        const investigationTimelinePayload = await safeJson(investigationTimelineResponse);

        const detectionsResponse = detectionsResult.status === 'fulfilled' && detectionsResult.value.ok ? detectionsResult.value : null;
        const alertsResponse = alertsResult.status === 'fulfilled' && alertsResult.value.ok ? alertsResult.value : null;
        const incidentsResponse = incidentsResult.status === 'fulfilled' && incidentsResult.value.ok ? incidentsResult.value : null;
        const evidenceResponse = evidenceResult.status === 'fulfilled' && evidenceResult.value.ok ? evidenceResult.value : null;
        const historyResponse = historyResult.status === 'fulfilled' && historyResult.value.ok ? historyResult.value : null;
        const monitoringRunsResponse = monitoringRunsResult.status === 'fulfilled' && monitoringRunsResult.value.ok ? monitoringRunsResult.value : null;
        const reconcileLatestResponse = reconcileLatestResult.status === 'fulfilled' && reconcileLatestResult.value.ok ? reconcileLatestResult.value : null;
        const activeReconcileStatusResponse = activeReconcileStatusResult.status === 'fulfilled' && activeReconcileStatusResult.value.ok ? activeReconcileStatusResult.value : null;
        const [
          detectionsPayload,
          alertsPayload,
          incidentsPayload,
          evidencePayload,
          historyPayload,
          monitoringRunsPayload,
          reconcileLatestPayload,
          activeReconcileStatusPayload,
        ] = await Promise.all([
          safeJson(detectionsResponse),
          safeJson(alertsResponse),
          safeJson(incidentsResponse),
          safeJson(evidenceResponse),
          safeJson(historyResponse),
          safeJson(monitoringRunsResponse),
          safeJson(reconcileLatestResponse),
          safeJson(activeReconcileStatusResponse),
        ]);

        const canonicalCollections: CanonicalCollectionPayload = {
          ...(runtimeStatusPayload?.canonical_collections ?? {}),
          ...(investigationTimelinePayload?.canonical_collections ?? {}),
          ...(investigationTimelinePayload?.collections ?? {}),
          detections: payloadRows<DetectionRow>(detectionsPayload, ['detections']) ?? payloadRows<DetectionRow>(runtimeStatusPayload?.canonical_collections, ['detections']) ?? payloadRows<DetectionRow>(investigationTimelinePayload?.canonical_collections, ['detections']) ?? undefined,
          alerts: payloadRows<AlertRow>(alertsPayload, ['alerts']) ?? payloadRows<AlertRow>(runtimeStatusPayload?.canonical_collections, ['alerts']) ?? payloadRows<AlertRow>(investigationTimelinePayload?.canonical_collections, ['alerts']) ?? undefined,
          incidents: payloadRows<IncidentRow>(incidentsPayload, ['incidents']) ?? payloadRows<IncidentRow>(runtimeStatusPayload?.canonical_collections, ['incidents']) ?? payloadRows<IncidentRow>(investigationTimelinePayload?.canonical_collections, ['incidents']) ?? undefined,
          evidence: payloadRows<EvidenceRow>(evidencePayload, ['evidence']) ?? payloadRows<EvidenceRow>(runtimeStatusPayload?.canonical_collections, ['evidence']) ?? payloadRows<EvidenceRow>(investigationTimelinePayload?.canonical_collections, ['evidence']) ?? undefined,
          history: payloadRows<ActionHistoryRow>(historyPayload, ['history', 'actions']) ?? payloadRows<ActionHistoryRow>(runtimeStatusPayload?.canonical_collections, ['history', 'actions']) ?? payloadRows<ActionHistoryRow>(investigationTimelinePayload?.canonical_collections, ['history', 'actions']) ?? undefined,
        };
        const timelineAlertId = investigationTimelinePayload?.chain_linked_ids?.alert_id;
        const linkedAlertEvidencePayload = timelineAlertId
          ? await fetch(`${apiUrl}/alerts/${encodeURIComponent(String(timelineAlertId))}/evidence?limit=50`, { headers: authHeaders(), cache: 'no-store' })
            .then((response) => safeJson(response.ok ? response : null))
            .catch(() => ({}))
          : {};
        const canonicalEvidence = payloadRows<EvidenceRow>(canonicalCollections, ['evidence']);
        const alertEvidenceRows = payloadRows<EvidenceRow>(linkedAlertEvidencePayload, ['evidence']) ?? [];
        const mergedCanonicalEvidence = [
          ...(canonicalEvidence ?? []),
          ...alertEvidenceRows,
        ];
        const canonicalEvidenceById = new Map<string, EvidenceRow>();
        mergedCanonicalEvidence.forEach((row) => {
          if (!row?.id) return;
          canonicalEvidenceById.set(row.id, row);
        });

        // Runtime-status + investigation-timeline are canonical; collection APIs are refreshed in parallel and kept stale-safe.
        const staleCollections: SnapshotCollectionKey[] = [];
        const refreshedCollectionTimestamps: Partial<Record<SnapshotCollectionKey, string>> = {};
        const markCollectionRefreshed = (collectionKey: SnapshotCollectionKey) => {
          refreshedCollectionTimestamps[collectionKey] = new Date().toISOString();
        };
        updateCollection<DetectionRow>({
          key: 'detections',
          result: detectionsResult,
          payload: detectionsPayload,
          payloadKeys: ['detections'],
          canonical: payloadRowsWithAvailability(canonicalCollections, ['detections']),
          setter: setDetections,
          stale: staleCollections,
          cacheKey: 'detections',
          markRefreshed: markCollectionRefreshed,
        });
        updateCollection<AlertRow>({
          key: 'alerts',
          result: alertsResult,
          payload: alertsPayload,
          payloadKeys: ['alerts'],
          canonical: payloadRowsWithAvailability(canonicalCollections, ['alerts']),
          setter: setAlerts,
          stale: staleCollections,
          cacheKey: 'alerts',
          markRefreshed: markCollectionRefreshed,
        });
        updateCollection<IncidentRow>({
          key: 'incidents',
          result: incidentsResult,
          payload: incidentsPayload,
          payloadKeys: ['incidents'],
          canonical: payloadRowsWithAvailability(canonicalCollections, ['incidents']),
          setter: setIncidents,
          stale: staleCollections,
          cacheKey: 'incidents',
          markRefreshed: markCollectionRefreshed,
        });
        updateCollection<EvidenceRow>({
          key: 'evidence',
          result: evidenceResult,
          payload: evidencePayload,
          payloadKeys: ['evidence'],
          canonical: {
            rows: canonicalEvidence || alertEvidenceRows.length > 0 ? [...canonicalEvidenceById.values()] : null,
            available: Boolean(canonicalEvidence) || alertEvidenceRows.length > 0,
          },
          setter: setEvidence,
          stale: staleCollections,
          cacheKey: 'evidence',
          markRefreshed: markCollectionRefreshed,
        });
        updateCollection<ActionHistoryRow>({
          key: 'history',
          result: historyResult,
          payload: historyPayload,
          payloadKeys: ['history', 'actions'],
          canonical: payloadRowsWithAvailability(canonicalCollections, ['history', 'actions']),
          setter: setActionHistory,
          stale: staleCollections,
          cacheKey: 'history',
          markRefreshed: markCollectionRefreshed,
        });
        updateCollection<MonitoringRunRow>({
          key: 'monitoring-runs',
          result: monitoringRunsResult,
          payload: monitoringRunsPayload,
          payloadKeys: ['runs', 'monitoring_runs'],
          canonical: payloadRowsWithAvailability(canonicalCollections, ['runs', 'monitoring_runs']),
          setter: setMonitoringRuns,
          stale: staleCollections,
          cacheKey: 'monitoringRuns',
          markRefreshed: markCollectionRefreshed,
        });
        if (Object.keys(refreshedCollectionTimestamps).length > 0) {
          setCollectionLastSuccessfulRefreshAt((current) => ({
            ...current,
            ...refreshedCollectionTimestamps,
          }));
        }
        setSnapshotStaleCollections(staleCollections);
        const activeJob = activeReconcileStatusPayload?.job;
        const reconcileJob = activeJob && typeof activeJob === 'object' ? activeJob : reconcileLatestPayload?.job;
        setLatestReconcileJob(reconcileJob && typeof reconcileJob === 'object' ? reconcileJob as ReconcileJobSnapshot : null);
        const nextReconcileId = (reconcileJob?.id && typeof reconcileJob.id === 'string') ? reconcileJob.id : null;
        const terminal = reconcileJob?.status === 'completed' || reconcileJob?.status === 'failed';
        setActiveReconcileId(terminal ? null : nextReconcileId);
        if (runtimeStatusPayload) {
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
        setSnapshotFailedEndpoints(failedEndpoints);
      } catch {
        if (active) {
          setSnapshotError('Monitoring snapshot refresh failed');
          setSystemsPanelWarning('Systems list unavailable');
          setSnapshotFailedEndpoints(['runtime-status']);
          setSnapshotStaleCollections(['detections', 'alerts', 'incidents', 'evidence', 'history', 'monitoring-runs']);
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
  const enterpriseReadyPass = Boolean(runtimeStatusSnapshot?.enterprise_ready_pass ?? runtimeSummary?.enterprise_ready_pass ?? false);
  const failedEnterpriseChecks = Array.isArray(runtimeStatusSnapshot?.failed_checks)
    ? runtimeStatusSnapshot.failed_checks
    : Array.isArray(runtimeSummary?.failed_checks)
      ? runtimeSummary.failed_checks
      : [];
  const remediationLinks: Record<string, string> = {
    ...ENTERPRISE_GATE_REMEDIATION_LINKS,
    ...(runtimeSummary?.remediation_links ?? {}),
    ...(runtimeStatusSnapshot?.remediation_links ?? {}),
    heartbeat_stale: '/threat#continuity-slo',
    heartbeat_offline: '/threat#continuity-slo',
    worker_not_live: '/threat#continuity-slo',
    event_ingestion_stale: '/threat#telemetry-freshness',
    event_ingestion_offline: '/threat#telemetry-freshness',
    detection_pipeline_stale: '/threat#continuity-slo',
    detection_eval_stale: '/threat#continuity-slo',
  };
  const remediationChecks = failedEnterpriseChecks.length > 0
    ? failedEnterpriseChecks
    : Object.keys(ENTERPRISE_GATE_LABELS);
  const enterpriseCriteriaChecks: EnterpriseCriterionCheck[] = Array.isArray(runtimeStatusSnapshot?.enterprise_criteria)
    ? runtimeStatusSnapshot.enterprise_criteria
    : Array.isArray(runtimeSummary?.enterprise_criteria)
      ? (runtimeSummary.enterprise_criteria ?? [])
      : [];
  const openAlerts = Number(runtimeStatusSnapshot?.open_alerts ?? runtimeSummary?.active_alerts_count ?? 0);
  const activeIncidents = Number(runtimeStatusSnapshot?.active_incidents ?? runtimeSummary?.active_incidents_count ?? 0);
  const truth = feed.monitoring.truth;
  const canonicalPresentation = feed.monitoring.presentation;
  const runtimeEvidenceSource = String(runtimeStatusSnapshot?.evidence_source ?? 'none').toLowerCase();
  const simulatorMode = isSimulatorEvidenceMode(runtimeEvidenceSource);
  const simulatorProofChainCapability = Boolean(
    (runtimeStatusSnapshot as { can_generate_simulator_proof_chain?: boolean } | null)?.can_generate_simulator_proof_chain
    ?? (runtimeStatusSnapshot as { capabilities?: { can_generate_simulator_proof_chain?: boolean } } | null)?.capabilities?.can_generate_simulator_proof_chain,
  );
  const canGenerateSimulatorProofChain = simulatorMode || simulatorProofChainCapability;
  const simulatorProofChainUnavailableCopy = 'Simulator-only action unavailable in live mode';
  const protectedAssetCount = Number(runtimeStatusSnapshot?.protected_assets_count ?? runtimeSummary?.protected_assets_count ?? 0);
  const workspaceConfigured = Boolean(runtimeStatusSnapshot?.workspace_configured ?? runtimeSummary?.workspace_configured ?? false);
  const configuredSystems = Number(runtimeStatusSnapshot?.monitored_systems_count ?? runtimeSummary?.monitored_systems_count ?? 0);
  const reportingSystems = Number(runtimeStatusSnapshot?.reporting_systems ?? 0);
  const summaryConfigurationReason = runtimeSummary?.configuration_reason ?? null;
  const summaryConfigurationReasonCodes = Array.isArray(runtimeSummary?.configuration_reason_codes)
    ? runtimeSummary.configuration_reason_codes
    : [];
  const runtimeConfigurationReason = runtimeStatusSnapshot?.configuration_reason ?? null;
  const runtimeConfigurationReasonCodes = Array.isArray(runtimeStatusSnapshot?.configuration_reason_codes)
    ? runtimeStatusSnapshot.configuration_reason_codes
    : [];
  const configurationReason = runtimeStatusSnapshot?.configuration_reason ?? summaryConfigurationReason;
  const configurationReasonCodes = Array.isArray(runtimeStatusSnapshot?.configuration_reason_codes)
    ? runtimeStatusSnapshot.configuration_reason_codes
    : summaryConfigurationReasonCodes;
  const monitoringMode = runtimeEvidenceSource;
  const runtimeStatus = String(runtimeStatusSnapshot?.runtime_status ?? '').toLowerCase();
  const runtimeMonitoringStatusForPageState: 'live' | 'limited' | 'offline' = runtimeStatus === 'live'
    ? 'live'
    : runtimeStatus === 'offline'
      ? 'offline'
      : 'limited';
  const continuityLive = runtimeStatus === 'live';
  const runtimeContradictionFlags = Array.isArray(runtimeStatusSnapshot?.contradiction_flags)
    ? runtimeStatusSnapshot.contradiction_flags
    : [];
  const hasRuntimeContradictionFlags = runtimeContradictionFlags.length > 0;
  const presentationStatus: MonitoringPresentationStatus = runtimeStatus === 'live'
    ? (hasRuntimeContradictionFlags ? 'degraded' : 'live')
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
    lastTelemetryAt: runtimeStatusSnapshot?.last_telemetry_at ?? null,
    lastHeartbeatAt: runtimeStatusSnapshot?.last_heartbeat_at ?? null,
    lastPollAt: runtimeStatusSnapshot?.last_poll_at ?? null,
    telemetryLabel: formatRelativeTime(runtimeStatusSnapshot?.last_telemetry_at ?? null),
    heartbeatLabel: formatRelativeTime(runtimeStatusSnapshot?.last_heartbeat_at ?? null),
    pollLabel: formatRelativeTime(runtimeStatusSnapshot?.last_poll_at ?? null),
    hasLiveTelemetry: continuityLive
      && String(runtimeStatusSnapshot?.freshness_status ?? 'unavailable') === 'fresh'
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
  const continuitySlo = evaluateContinuitySlo(runtimeSummary, runtimeStatusSnapshot?.continuity_slo);
  const continuityFailedCheckList = continuityFailedChecks(runtimeSummary, runtimeStatusSnapshot?.continuity_slo, continuitySlo);
  const continuityRemediationActions = Array.from(new Map(
    continuityFailedCheckList.map((item) => {
      const href = remediationLinks[item.code] ?? '/threat#continuity-slo';
      const label = CONTINUITY_REMEDIATION_COPY[item.code] ?? `Remediate ${item.label.toLowerCase()}.`;
      return [`${href}:${label}`, { href, label }];
    }),
  ).values());
  const telemetryState = deriveSnapshotFreshnessState(monitoringPresentation.lastTelemetryAt, TELEMETRY_STALE_MS);
  const pollState = deriveSnapshotFreshnessState(monitoringPresentation.lastPollAt, POLL_STALE_MS);
  const heartbeatState = deriveSnapshotFreshnessState(monitoringPresentation.lastHeartbeatAt, HEARTBEAT_STALE_MS);
  const hasCanonicalSnapshot = Boolean(runtimeStatusSnapshot || investigationTimeline);
  const monitoringHealthModel = useMemo(() => buildMonitoringHealthModel({
    runtimeStatusSnapshot,
    detections,
    alerts,
    incidents,
    evidence,
    telemetryAt: monitoringPresentation.lastTelemetryAt,
    heartbeatAt: monitoringPresentation.lastHeartbeatAt,
    pollAt: monitoringPresentation.lastPollAt,
    contradictionFlags: runtimeContradictionFlags,
    continuityChecks: continuityFailedCheckList.map((item) => item.code),
  }), [alerts, continuityFailedCheckList, detections, evidence, incidents, monitoringPresentation.lastHeartbeatAt, monitoringPresentation.lastPollAt, monitoringPresentation.lastTelemetryAt, runtimeContradictionFlags, runtimeStatusSnapshot]);

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
      const reconcileTerminal = latestReconcileJob?.status === 'completed' || latestReconcileJob?.status === 'failed';
      const monitoringStatus = monitoredSystem?.is_enabled
        ? 'Monitored'
        : monitoredSystem
        ? 'Disabled'
        : (reconcileTerminal ? 'Status unavailable' : (matchedTarget?.monitoring_enabled ? 'Monitored' : 'Status unavailable'));
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
        rawEvidenceReference: `raw evidence refs: detection ${item.id} · tx ${rawEvent.tx_hash ?? responsePayload.observed_evidence?.tx_hash ?? item.tx_hash ?? 'n/a'} · block ${rawEvent.block_number ?? responsePayload.observed_evidence?.block_number ?? item.block_number ?? 'n/a'} · provider ${item.evidence_origin ?? item.evidence_source ?? 'n/a'}`,
        rawEvidenceObservedAt: item.last_evidence_at ?? item.detected_at ?? null,
        state: isTest ? ('Test' as const) : ('Live' as const),
        href: item.linked_alert_id ? '/alerts' : '/threat',
        source: 'detection' as const,
        detectionId: item.id,
        alertId: item.linked_alert_id ?? null,
        incidentId: item.linked_incident_id ?? null,
        actionId: item.linked_action_id ?? null,
      };
    }).sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  }, [detections, latestReconcileJob?.status, monitoredSystemById, targetById]);

  const categorizedDetections = useMemo(() => {
    const now = Date.now();
    const live: DetectionItem[] = [];
    const historical: DetectionItem[] = [];

    baseDetections.forEach((item) => {
      const row = detections.find((detection) => detection.id === item.detectionId) ?? null;
      const linkedAlertId = row?.chain_linked_ids?.alert_id ?? row?.linked_alert_id ?? null;
      const linkedIncidentId = row?.chain_linked_ids?.incident_id ?? row?.linked_incident_id ?? null;
      const linkedAlert = linkedAlertId ? coverageIndexes.alertsById.get(normalizeLookup(linkedAlertId)) ?? null : null;
      const linkedIncident = linkedAlert?.incident_id
        ? incidents.find((incident) => incident.id === linkedAlert.incident_id) ?? null
        : linkedIncidentId
          ? incidents.find((incident) => incident.id === linkedIncidentId) ?? null
          : null;
      const linkedEvidenceRows = coverageIndexes.evidenceByDetectionId.get(normalizeLookup(item.detectionId)) ?? [];
      const linkedEvidence = pickLatestByTime(linkedEvidenceRows, (entry) => entry.observed_at);
      const rawEvidenceReference = `raw evidence refs: evidence_id ${linkedEvidence?.id || 'n/a'} · tx ${linkedEvidence?.tx_hash || item.txHash || 'n/a'} · block ${linkedEvidence?.block_number ?? item.blockNumber ?? 'n/a'} · provider ${linkedEvidence?.source_provider || row?.evidence_origin || row?.evidence_source || 'n/a'}`;
      const rawEvidenceObservedAt = linkedEvidence?.observed_at ?? row?.last_evidence_at ?? item.rawEvidenceObservedAt ?? null;
      const chainLinkedIds = row?.chain_linked_ids
        ?? linkedAlert?.chain_linked_ids
        ?? linkedIncident?.chain_linked_ids
        ?? null;
      const linkedEvidenceCount = Number(
        row?.linked_evidence_count
        ?? linkedAlert?.linked_evidence_count
        ?? linkedIncident?.linked_evidence_count
        ?? linkedEvidenceRows.length,
      );
      const hasLinkedChainIds = Boolean(
        chainLinkedIds?.detection_id
        && chainLinkedIds?.alert_id
        && chainLinkedIds?.incident_id
        && chainLinkedIds?.action_id,
      );
      const hasPersistedEvidenceRecord = Boolean(linkedEvidence?.id);
      const hasRealLinkedEvidence = hasPersistedEvidenceRecord && linkedEvidenceCount > 0 && isRealEvidence(linkedEvidence, row);
      const ageMs = now - new Date(item.timestamp).getTime();
      const telemetryFresh = monitoringPresentation.status === 'live' && monitoringPresentation.hasLiveTelemetry;
      const liveCandidate = telemetryFresh
        && !dbPersistenceOutageActive
        && item.liveEvidenceEligible !== false
        && hasLinkedChainIds
        && hasRealLinkedEvidence
        && ageMs <= DETECTION_LIVE_MS
        && item.state !== 'Test';
      if (liveCandidate) {
        live.push({
          ...item,
          rawEvidenceReference,
          rawEvidenceObservedAt,
        });
        return;
      }
      historical.push({
        ...item,
        rawEvidenceReference,
        rawEvidenceObservedAt,
        state: item.state === 'Test' ? 'Test' : ageMs > DETECTION_LIVE_MS ? 'Historical' : 'Stale',
      });
    });

    return { live, historical };
  }, [baseDetections, coverageIndexes.alertsById, coverageIndexes.evidenceByDetectionId, dbPersistenceOutageActive, detections, incidents, monitoringPresentation.hasLiveTelemetry, monitoringPresentation.status]);

  const pageState = derivePageState({
    loadingSnapshot,
    snapshotError: Boolean(snapshotError) && !hasCanonicalSnapshot,
    targets,
    liveDetections: categorizedDetections.live,
    workspaceConfigured,
    freshnessStatus: runtimeStatusSnapshot?.freshness_status ?? 'unavailable',
    monitoringStatus: runtimeMonitoringStatusForPageState,
    reportingSystems,
    runtimeStatus,
    monitoredSystems: configuredSystems,
    hasLiveTelemetry: showLiveTelemetry,
    statusReason: runtimeStatusSnapshot?.status_reason ?? null,
    configurationReason: runtimeConfigurationReason,
    configurationReasonCodes: runtimeConfigurationReasonCodes,
    runtimeMonitoringStatus: runtimeStatus,
    runtimeErrorCode: null,
    runtimeDegradedReason: null,
    fieldReasonCodes: null,
    summaryStatusReason: runtimeStatusSnapshot?.status_reason ?? null,
    summaryConfigurationReason,
    summaryConfigurationReasonCodes,
    continuityStatus: runtimeSummary?.continuity_status ?? null,
  });

  const runtimeReason = String(runtimeStatusSnapshot?.status_reason ?? 'not_reported');
  const proofChainStatus = String(runtimeStatusSnapshot?.proof_chain_status ?? 'incomplete');
  const timelineItems = Array.isArray(investigationTimeline?.items) ? investigationTimeline.items : [];
  const missingTimelineLinks = Array.isArray(investigationTimeline?.missing) ? investigationTimeline.missing : [];
  const timelineLinkNames = new Set(timelineItems.map((item) => String(item.link_name || '')));
  const hasDetectionTimelineLink = timelineLinkNames.has('detection');
  const hasEvidenceTimelineLink = timelineLinkNames.has('telemetry_event') || timelineLinkNames.has('detection_evidence') || timelineLinkNames.has('telemetry') || timelineLinkNames.has('evidence');
  const timelineChainLinkedIds = investigationTimeline?.chain_linked_ids ?? null;
  const hasCompleteTimelineLinkedIds = Boolean(
    timelineChainLinkedIds?.detection_id
    && timelineChainLinkedIds?.alert_id
    && timelineChainLinkedIds?.incident_id
    && timelineChainLinkedIds?.action_id,
  );
  const hasTimelineLinkedEvidence = Number(investigationTimeline?.linked_evidence_count ?? 0) > 0;
  const hasPersistedTimelineEvidence = Boolean(
    timelineChainLinkedIds?.detection_id
    && (coverageIndexes.evidenceByDetectionId.get(normalizeLookup(timelineChainLinkedIds.detection_id))?.length ?? 0) > 0,
  );
  const showEvidenceLinkedSignals = hasDetectionTimelineLink
    && hasEvidenceTimelineLink
    && hasCompleteTimelineLinkedIds
    && hasTimelineLinkedEvidence
    && hasPersistedTimelineEvidence;

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
  const loopHealth = (runtimeStatusSnapshot?.background_loop_health ?? runtimeSummary?.background_loop_health ?? null) as MonitoringLoopHealth | null;
  const loopHealthSignal = resolveLoopHealthSignal(loopHealth);
  const monitoringStatusViewModel = useMemo<MonitoringStatusViewModel>(() => {
    const runtimeEndpointState: EndpointProvenanceState = snapshotFailedEndpoints.includes('runtime-status')
      ? 'partial_failure'
      : (telemetryState === 'stale' || pollState === 'stale' || heartbeatState === 'stale')
        ? 'stale_snapshot'
        : (pageState === 'degraded_partial' || monitoringPresentation.status === 'degraded')
          ? 'degraded'
        : 'live';
    const lastSuccessfulRuntimeRefreshAt = runtimeStatusSnapshot?.last_poll_at
      ?? runtimeStatusSnapshot?.last_telemetry_at
      ?? null;
    const lastSuccessfulTimelineRefreshAt = (investigationTimeline as Record<string, any> | null)?.generated_at
      ?? (investigationTimeline as Record<string, any> | null)?.created_at
      ?? null;
    const lastSuccessfulRefreshAt = mostRecentTimestamp(lastSuccessfulRuntimeRefreshAt, lastSuccessfulTimelineRefreshAt);
    const derivedProvenanceLabel: MonitoringProvenanceLabel = snapshotFailedEndpoints.length > 0
      ? 'partial_failure'
      : (telemetryState === 'stale' || pollState === 'stale' || heartbeatState === 'stale')
        ? 'stale_snapshot'
        : (pageState === 'degraded_partial' || monitoringPresentation.status === 'degraded')
          ? 'degraded'
          : 'live';
    const provenanceExplanation = derivedProvenanceLabel === 'partial_failure'
      ? `Monitoring snapshot fallback is active because ${snapshotFailedEndpoints.join(', ')} failed in the most recent refresh.`
      : derivedProvenanceLabel === 'stale_snapshot'
        ? 'Runtime snapshot is visible, but at least one freshness timestamp is stale or unavailable; serving stale_snapshot provenance.'
        : derivedProvenanceLabel === 'degraded'
          ? 'Runtime and continuity contract report degraded monitoring health.'
          : 'Runtime and continuity contract confirm live monitoring health.';
    const staleCollectionNotes = snapshotStaleCollections.map((collection) => (
      `${collection}: last successful refresh ${formatAbsoluteTime(collectionLastSuccessfulRefreshAt[collection])}`
    ));

    const bannerPrimaryCopy = pageState === 'offline_no_telemetry'
      ? `${pageStatePrimaryCopy(pageState, configurationReason, runtimeSummary?.continuity_status ?? null, continuitySlo, continuityFailedCheckList, remediationLinks)} Reason: ${runtimeReason || 'no active reporting systems'}. Provenance: ${derivedProvenanceLabel}. Add one monitored system and confirm telemetry flow.`
      : `${pageStatePrimaryCopy(pageState, configurationReason, runtimeSummary?.continuity_status ?? null, continuitySlo, continuityFailedCheckList, remediationLinks)} Provenance: ${derivedProvenanceLabel}.`;
    const pageBanner: PageBannerModel = pageState === 'fetch_error'
      ? {
        variant: 'fetch_error',
        headline: 'Telemetry retrieval degraded',
        primaryCopy: pageStatePrimaryCopy(pageState, configurationReason, runtimeSummary?.continuity_status ?? null, continuitySlo, continuityFailedCheckList, remediationLinks),
        metaLines: [
          `Provenance: ${derivedProvenanceLabel} · ${provenanceExplanation}`,
          ...(runtimeReason ? [`Backend reason: ${runtimeReason}`] : []),
          `Last telemetry: ${telemetryLabel} · Last successful poll: ${pollLabel}`,
          ...staleCollectionNotes,
        ],
      }
      : {
        variant: 'explanation',
        primaryCopy: bannerPrimaryCopy,
        metaLines: [],
      };

    const headerStatusChips: MonitoringViewModel['headerStatusChips'] = [
      {
        label: `Loop ${loopHealthSignal.state}${loopHealth?.consecutive_failures ? ` (${loopHealth.consecutive_failures} failures)` : ''}`,
        tone: 'status',
        className: `statusBadge statusBadge-${loopHealthSignal.state === 'healthy' ? 'healthy' : 'attention'}`,
      },
      { label: monitoringPresentation.statusLabel, tone: 'status', className: `statusBadge statusBadge-${monitoringPresentation.tone}` },
      { label: `Operational state ${formatOperationalStateLabel(pageState)}`, tone: 'chip' },
      { label: `Telemetry ${telemetryState}`, tone: 'chip' },
      { label: `Poll ${pollState}`, tone: 'chip' },
      { label: `Heartbeat ${heartbeatState}`, tone: 'chip' },
      { label: `Last successful refresh ${formatAbsoluteTime(lastSuccessfulRefreshAt)}`, tone: 'chip' },
      { label: `Provenance ${derivedProvenanceLabel}`, tone: 'status', className: 'statusBadge statusBadge-attention' },
      { label: `Evidence source ${monitoringPresentation.evidenceSourceLabel}`, tone: 'chip' },
      { label: `Protected assets ${protectedAssetCount}`, tone: 'chip' },
      { label: `Monitored systems ${configuredSystems}`, tone: 'chip' },
      { label: `Reporting systems ${reportingSystems}`, tone: 'chip' },
      { label: `Evidence records ${evidence.length}`, tone: 'chip' },
      { label: `Open alerts ${openAlerts}`, tone: 'chip' },
      { label: `Active incidents ${activeIncidents}`, tone: 'chip' },
    ];
    if (loopHealth?.last_successful_cycle) {
      headerStatusChips.push({ label: `Loop last success ${formatAbsoluteTime(loopHealth.last_successful_cycle)}`, tone: 'chip' });
    }
    if (loopHealth?.next_retry_at) {
      headerStatusChips.push({ label: `Loop next retry ${formatAbsoluteTime(loopHealth.next_retry_at)}`, tone: 'chip' });
    }
    if (monitoringMode === 'simulator' || simulatorMode) {
      headerStatusChips.push({ label: 'SIMULATOR MODE', tone: 'status', className: 'statusBadge statusBadge-attention' });
    }
    if (!workspaceConfigured) {
      headerStatusChips.push({ label: 'Workspace not configured', tone: 'chip' });
    }
    if (systemsPanelWarning) {
      headerStatusChips.push({ label: systemsPanelWarning, tone: 'status', className: 'statusBadge statusBadge-attention' });
    }
    const contradictions = collectMonitoringContradictions({
      provenanceLabel: derivedProvenanceLabel,
      telemetryState,
      pollState,
      heartbeatState,
      endpointProvenance: {
        runtimeStatus: runtimeEndpointState,
      },
      presentationStatus: monitoringPresentation.status,
    });
    return {
      presentationStatus: monitoringPresentation.status,
      presentationStatusLabel: monitoringPresentation.statusLabel,
      presentationTone: monitoringPresentation.tone,
      pageState,
      continuitySlo,
      telemetryLabel,
      pollLabel,
      heartbeatLabel: monitoringPresentation.heartbeatLabel,
      telemetryState,
      pollState,
      heartbeatState,
      provenanceLabel: derivedProvenanceLabel,
      provenanceExplanation,
      endpointProvenance: {
        runtimeStatus: runtimeEndpointState,
      },
      lastSuccessfulRuntimeRefreshAt,
      lastSuccessfulTimelineRefreshAt,
      lastSuccessfulRefreshAt,
      runtimeReason,
      configurationReason,
      continuityStatus: runtimeSummary?.continuity_status ?? null,
      evidenceSourceLabel: monitoringPresentation.evidenceSourceLabel,
      protectedAssetCount,
      configuredSystems,
      reportingSystems,
      evidenceCount: evidence.length,
      openAlerts,
      activeIncidents,
      headerStatusChips,
      contradictions,
      pageBanner,
      ctas: {
        generateSimulatorProofChain: {
          disabled: ensuringProofChain || !canGenerateSimulatorProofChain,
          reason: deterministicDisabledReason(
            ensuringProofChain
              ? THREAT_COPY.evidencePackageAlreadyRunning
              : simulatorProofChainUnavailableCopy,
            THREAT_COPY.evidencePackageUnavailable,
          ),
          noOpMessage: THREAT_COPY.generateEvidencePackageUnavailable,
          nextStepLabel: 'Inspect integration health',
          nextStepHref: '/integrations',
        },
      },
    };
  }, [
    activeIncidents,
    configuredSystems,
    continuitySlo,
    evidence.length,
    heartbeatState,
    monitoringPresentation.evidenceSourceLabel,
    monitoringPresentation.heartbeatLabel,
    monitoringPresentation.status,
    monitoringPresentation.statusLabel,
    monitoringPresentation.tone,
    loopHealth,
    loopHealthSignal.state,
    openAlerts,
    pageState,
    pollLabel,
    pollState,
    protectedAssetCount,
    reportingSystems,
    runtimeReason,
    runtimeSummary?.continuity_status,
    runtimeStatusSnapshot?.last_poll_at,
    runtimeStatusSnapshot?.last_telemetry_at,
    investigationTimeline,
    latestReconcileJob,
    monitoringMode,
    simulatorMode,
    snapshotFailedEndpoints,
    snapshotStaleCollections,
    collectionLastSuccessfulRefreshAt,
    systemsPanelWarning,
    telemetryLabel,
    telemetryState,
    workspaceConfigured,
    configurationReason,
    canGenerateSimulatorProofChain,
    ensuringProofChain,
    simulatorProofChainUnavailableCopy,
  ]);
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
    if (monitoringHealthModel.securityStatus.activeIncidents > 0) {
      return { value: 'High', tier: `${monitoringHealthModel.securityStatus.activeIncidents} active incident${monitoringHealthModel.securityStatus.activeIncidents === 1 ? '' : 's'}` };
    }
    if (monitoringHealthModel.securityStatus.openAlerts > 0) {
      return { value: 'Elevated', tier: `${monitoringHealthModel.securityStatus.openAlerts} open alert${monitoringHealthModel.securityStatus.openAlerts === 1 ? '' : 's'}` };
    }
    if (monitoringHealthModel.securityStatus.posture === 'healthy') return { value: 'Low', tier: 'No active alerts or incidents' };
    if (monitoringHealthModel.securityStatus.posture === 'degraded') return { value: 'Guarded', tier: 'Runtime degraded; investigate telemetry continuity' };
    if (monitoringHealthModel.securityStatus.posture === 'offline') return { value: 'Unknown', tier: 'Runtime offline; live risk score unavailable' };
    return { value: 'Unknown', tier: 'Awaiting runtime signal' };
  }, [monitoringHealthModel.securityStatus]);

  const riskFreshness = pageState === 'healthy_live' || (pageState === 'configured_no_signals' && reportingSystems > 0)
    ? `last evaluated ${detectionEvalLabel} across ${Math.max(configuredSystems, 0)} monitored systems`
    : `last known score from ${detectionEvalLabel}; current telemetry unavailable`;

  const detectionsToRender = pageState === 'healthy_live' ? categorizedDetections.live : categorizedDetections.historical;
  const detectionRecords = useMemo<DetectionRecord[]>(() => (
    buildDetectionRecords(detectionsToRender)
  ), [detectionsToRender]);
  const investigationTimelineItems = useMemo(() => (
    timelineItems.slice().sort((a, b) => new Date(b.timestamp || 0).getTime() - new Date(a.timestamp || 0).getTime())
  ), [timelineItems]);
  const persistedThreatChain = useMemo(() => resolvePersistedThreatChain({
    detections,
    alerts,
    incidents,
    actionHistory,
  }), [actionHistory, alerts, detections, incidents]);
  const threatChainSteps = useMemo<ThreatChainStep[]>(() => {
    const relatedRun = monitoringRuns
      .slice()
      .sort((a, b) => new Date((b.completed_at || b.started_at) || 0).getTime() - new Date((a.completed_at || a.started_at) || 0).getTime())[0] ?? null;
    const relatedDetection = persistedThreatChain.detection;
    const relatedAlert = persistedThreatChain.alert;
    const relatedIncident = persistedThreatChain.incident;
    const relatedAction = persistedThreatChain.action;

    return [
      {
        id: 'chain-detection',
        label: 'Detection created',
        detail: relatedDetection?.title || relatedDetection?.evidence_summary || 'No linked detection yet.',
        timestamp: relatedDetection?.detected_at ?? null,
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
  }, [monitoringRuns, persistedThreatChain]);
  const chainPanelSelection = useMemo(() => {
    const latestDetection = persistedThreatChain.detection;
    const linkedAlert = persistedThreatChain.alert;
    const linkedIncident = persistedThreatChain.incident;
    return {
      detectionId: persistedThreatChain.linkedIds.detectionId,
      alertId: persistedThreatChain.linkedIds.alertId,
      incidentId: persistedThreatChain.linkedIds.incidentId,
      actionId: persistedThreatChain.linkedIds.actionId,
      linkedEvidenceCount: latestDetection?.linked_evidence_count ?? linkedAlert?.linked_evidence_count ?? linkedIncident?.linked_evidence_count ?? (latestDetection ? (coverageIndexes.evidenceByDetectionId.get(normalizeLookup(latestDetection.id))?.length ?? 0) : null),
      lastEvidenceAt: latestDetection?.last_evidence_at ?? linkedAlert?.last_evidence_at ?? linkedIncident?.last_evidence_at ?? null,
      evidenceOrigin: latestDetection?.evidence_origin ?? linkedAlert?.evidence_origin ?? linkedIncident?.evidence_origin ?? null,
      txHash: latestDetection?.tx_hash ?? linkedAlert?.tx_hash ?? linkedIncident?.tx_hash ?? null,
      blockNumber: latestDetection?.block_number ?? linkedAlert?.block_number ?? linkedIncident?.block_number ?? null,
      detectorKind: latestDetection?.detector_kind ?? linkedAlert?.detector_kind ?? linkedIncident?.detector_kind ?? null,
      chainLinkedIds: latestDetection?.chain_linked_ids ?? linkedAlert?.chain_linked_ids ?? linkedIncident?.chain_linked_ids ?? null,
    };
  }, [coverageIndexes.evidenceByDetectionId, persistedThreatChain]);
  const threatChainTimeline = useMemo(() => {
    const latestEvidence = chainPanelSelection.detectionId
      ? pickLatestByTime(coverageIndexes.evidenceByDetectionId.get(normalizeLookup(chainPanelSelection.detectionId)) ?? [], (entry) => entry.observed_at)
      : null;
    const orderedTimeline = [
      { key: 'detection', label: 'Detection', id: chainPanelSelection.detectionId, timestamp: detections.find((item) => item.id === chainPanelSelection.detectionId)?.detected_at ?? null, href: '/alerts' },
      { key: 'alert', label: 'Alert', id: chainPanelSelection.alertId, timestamp: alerts.find((item) => item.id === chainPanelSelection.alertId)?.created_at ?? null, href: '/alerts' },
      { key: 'incident', label: 'Incident', id: chainPanelSelection.incidentId, timestamp: incidents.find((item) => item.id === chainPanelSelection.incidentId)?.created_at ?? null, href: '/incidents' },
      { key: 'action', label: 'Action', id: chainPanelSelection.actionId, timestamp: persistedThreatChain.action?.timestamp ?? null, href: '/history' },
    ];
    const rawEvidenceReference = formatRawEvidenceReference({
      evidenceId: latestEvidence?.id || 'n/a',
      txHash: latestEvidence?.tx_hash || chainPanelSelection.txHash || 'n/a',
      blockNumber: latestEvidence?.block_number ?? chainPanelSelection.blockNumber ?? null,
      provider: latestEvidence?.source_provider || chainPanelSelection.evidenceOrigin || 'n/a',
    });
    return { orderedTimeline, rawEvidenceReference };
  }, [alerts, chainPanelSelection.alertId, chainPanelSelection.blockNumber, chainPanelSelection.detectionId, chainPanelSelection.evidenceOrigin, chainPanelSelection.incidentId, chainPanelSelection.txHash, coverageIndexes.evidenceByDetectionId, detections, incidents, persistedThreatChain.action?.timestamp]);

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
  const [liveActionConfirm, setLiveActionConfirm] = useState<{ actionType: string; label: string } | null>(null);
  const [liveActionConfirmationText, setLiveActionConfirmationText] = useState<string>('');
  const [liveActionAcknowledged, setLiveActionAcknowledged] = useState<boolean>(false);
  const [liveActionConfirmationToken, setLiveActionConfirmationToken] = useState<string | null>(null);
  useEffect(() => {
    setSelectedThreatActionContextId((current) => {
      if (!current) return '';
      return threatActionContextOptions.some((option) => option.id === current)
        ? current
        : '';
    });
  }, [threatActionContextOptions]);
  useEffect(() => {
    if (!liveActionConfirm) {
      setLiveActionAcknowledged(false);
    }
  }, [liveActionConfirm]);
  const selectedThreatActionContext = useMemo(() => (
    threatActionContextOptions.find((option) => option.id === selectedThreatActionContextId) ?? null
  ), [selectedThreatActionContextId, threatActionContextOptions]);
  const liveActionConfirmationPhrase = useMemo(() => {
    const incidentId = selectedThreatActionContext?.incidentId;
    return incidentId ? `LIVE ${incidentId}` : 'LIVE';
  }, [selectedThreatActionContext?.incidentId]);
  const confirmLiveActionDisabledReason = !selectedThreatActionContext?.incidentId
    ? 'Confirm LIVE action is disabled because no incident context is linked.'
    : !liveActionAcknowledged
      ? 'Confirm LIVE action is disabled until acknowledgement is checked.'
      : liveActionConfirmationText.trim().toUpperCase() !== liveActionConfirmationPhrase.toUpperCase()
        ? `Confirm LIVE action is disabled until the exact phrase "${liveActionConfirmationPhrase}" is entered.`
        : '';
  const noLinkedActionContextAvailable = threatActionContextOptions.length === 0;
  const shouldBlockThreatActionCreation = noLinkedActionContextAvailable || !selectedThreatActionContext;
  const missingIncidentContextReason = selectedThreatActionContext && !selectedThreatActionContext.incidentId
    ? 'Selected context has no incident link.'
    : null;
  const actionButtonStates = useMemo<Record<ThreatActionButtonId, ThreatActionButtonState>>(() => {
    const baseContextReason = 'No linked alert/incident context is selected.';
    const buildState = (
      disabled: boolean,
      reason: string | null | undefined,
      noOpMessage: string,
      nextStepLabel: string,
      nextStepHref: string,
    ): ThreatActionButtonState => ({
      disabled,
      reason: deterministicDisabledReason(reason, 'Unavailable due to current monitoring state'),
      noOpMessage,
      nextStepLabel,
      nextStepHref,
    });
    return {
      'sim-notify-team': buildState(
        shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.notify_team, 'simulated'),
        shouldBlockThreatActionCreation ? baseContextReason : actionDisabledReason(actionCapabilities.notify_team, 'simulated'),
        'Run simulated response is currently unavailable. No action was executed.',
        'Review alerts',
        '/alerts',
      ),
      'sim-revoke-approval': buildState(
        shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.revoke_approval, 'simulated'),
        shouldBlockThreatActionCreation ? baseContextReason : actionDisabledReason(actionCapabilities.revoke_approval, 'simulated'),
        'Revoke approval (simulated) is currently unavailable. No action was executed.',
        'Review alerts',
        '/alerts',
      ),
      'rec-freeze-wallet': buildState(
        shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.freeze_wallet, 'recommended'),
        shouldBlockThreatActionCreation ? baseContextReason : actionDisabledReason(actionCapabilities.freeze_wallet, 'recommended'),
        'Freeze wallet (recommended) is currently unavailable. No action was created.',
        'Open incident queue',
        '/incidents',
      ),
      'rec-disable-monitored-system': buildState(
        shouldBlockThreatActionCreation || isActionDisabledInMode(actionCapabilities.disable_monitored_system, 'recommended'),
        shouldBlockThreatActionCreation ? baseContextReason : actionDisabledReason(actionCapabilities.disable_monitored_system, 'recommended'),
        'Disable monitored system (recommended) is currently unavailable. No action was created.',
        'Manage monitored systems',
        '/monitored-systems',
      ),
      'live-freeze-wallet': buildState(
        shouldBlockThreatActionCreation || !selectedThreatActionContext?.incidentId || isActionDisabledInMode(actionCapabilities.freeze_wallet, 'live'),
        shouldBlockThreatActionCreation ? baseContextReason : (missingIncidentContextReason ?? actionDisabledReason(actionCapabilities.freeze_wallet, 'live')),
        'Freeze wallet (live) is currently unavailable. No live workflow was started.',
        'Open incident queue',
        '/incidents',
      ),
      'live-revoke-approval': buildState(
        shouldBlockThreatActionCreation || !selectedThreatActionContext?.incidentId || isActionDisabledInMode(actionCapabilities.revoke_approval, 'live'),
        shouldBlockThreatActionCreation ? baseContextReason : (missingIncidentContextReason ?? actionDisabledReason(actionCapabilities.revoke_approval, 'live')),
        'Revoke approval (live) is currently unavailable. No live workflow was started.',
        'Open incident queue',
        '/incidents',
      ),
    };
  }, [
    actionCapabilities.disable_monitored_system,
    actionCapabilities.freeze_wallet,
    actionCapabilities.notify_team,
    actionCapabilities.revoke_approval,
    missingIncidentContextReason,
    selectedThreatActionContext?.incidentId,
    shouldBlockThreatActionCreation,
  ]);
  const monitoringViewModel = useMemo<MonitoringViewModel>(() => {
    const confirmLiveAction: ThreatActionButtonState = {
      disabled: Boolean(confirmLiveActionDisabledReason),
      reason: deterministicDisabledReason(confirmLiveActionDisabledReason, 'Confirm LIVE action is available.'),
      noOpMessage: 'Confirm LIVE action is currently unavailable. No live workflow was started.',
      nextStepLabel: !selectedThreatActionContext?.incidentId ? 'Open incident queue' : 'Review confirmation steps',
      nextStepHref: !selectedThreatActionContext?.incidentId ? '/incidents' : '/threat#response-actions',
    };
    const disabledActionGuidance: MonitoringViewModel['disabledActionGuidance'] = [];
    const registerDisabledAction = (
      key: string,
      action: string,
      state: ThreatActionButtonState,
    ) => {
      if (!state.disabled) return;
      disabledActionGuidance.push({
        key,
        action,
        reason: deterministicDisabledReason(state.reason, 'Unavailable due to current monitoring state'),
        nextStepLabel: state.nextStepLabel,
        nextStepHref: state.nextStepHref,
      });
    };
    registerDisabledAction('repair-proof-chain', THREAT_COPY.generateEvidencePackage, monitoringStatusViewModel.ctas.generateSimulatorProofChain);
    registerDisabledAction('sim-notify-team', 'Run simulated response', actionButtonStates['sim-notify-team']);
    registerDisabledAction('sim-revoke-approval', 'Revoke approval', actionButtonStates['sim-revoke-approval']);
    registerDisabledAction('rec-freeze-wallet', 'Freeze wallet (RECOMMENDED)', actionButtonStates['rec-freeze-wallet']);
    registerDisabledAction('rec-disable-monitored-system', 'Disable monitored system (RECOMMENDED)', actionButtonStates['rec-disable-monitored-system']);
    registerDisabledAction('live-freeze-wallet', 'Freeze wallet (LIVE)', actionButtonStates['live-freeze-wallet']);
    registerDisabledAction('live-revoke-approval', 'Revoke approval (LIVE)', actionButtonStates['live-revoke-approval']);
    registerDisabledAction('confirm-live-action', 'Confirm LIVE action', confirmLiveAction);
    return {
      ...monitoringStatusViewModel,
      actionButtons: actionButtonStates,
      confirmLiveAction,
      disabledActionGuidance,
    };
  }, [
    actionButtonStates,
    confirmLiveActionDisabledReason,
    monitoringStatusViewModel,
    selectedThreatActionContext?.incidentId,
  ]);

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
      setResponseToast(THREAT_COPY.noLinkedContext);
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

  async function runThreatAction(actionType: string, label: string, mode: 'simulated' | 'recommended' | 'live', confirmationToken?: string) {
    const guardId: ThreatActionButtonId = mode === 'simulated'
      ? (actionType === 'notify_team' ? 'sim-notify-team' : 'sim-revoke-approval')
      : mode === 'recommended'
        ? (actionType === 'freeze_wallet' ? 'rec-freeze-wallet' : 'rec-disable-monitored-system')
        : (actionType === 'freeze_wallet' ? 'live-freeze-wallet' : 'live-revoke-approval');
    const guardState = actionButtonStates[guardId];
    if (guardState?.disabled) {
      setResponseToast(`${guardState.noOpMessage} Reason: ${guardState.reason}`);
      return;
    }
    if (mode === 'simulated') {
      await runSimulatedThreatAction(actionType, label);
      return;
    }
    if (mode === 'live') {
      if (!confirmationToken || !liveActionConfirmationToken || confirmationToken !== liveActionConfirmationToken) {
        setResponseToast('LIVE action was blocked because explicit confirmation was not completed in this session.');
        return;
      }
      setLiveActionConfirmationToken(null);
    }
    const create = await fetch(`${apiUrl}/response/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        action_type: actionType,
        mode,
        status: 'pending',
        incident_id: selectedThreatActionContext?.incidentId,
        alert_id: selectedThreatActionContext?.alertId,
        result_summary: `${mode.toUpperCase()} ${label} created from threat operations panel`,
      }),
    });
    if (!create.ok) {
      setResponseToast(`${mode.toUpperCase()} ${label} failed to create.`);
      return;
    }
    const action = await create.json();
    if (mode === 'live') {
      const approve = await fetch(`${apiUrl}/response/actions/${action.id}/approve`, { method: 'POST', headers: authHeaders() });
      if (!approve.ok) {
        setResponseToast('LIVE action approval failed.');
        return;
      }
      const execute = await fetch(`${apiUrl}/response/actions/${action.id}/execute`, { method: 'POST', headers: authHeaders() });
      const executePayload = await execute.json().catch(() => ({}));
      const executionResult = responseActionExecutionMessage(executePayload);
      const provenance = executePayload?.execution_provenance ?? executePayload?.execution_evidence ?? {};
      const txHash = String(provenance?.tx_hash || provenance?.safe_tx_hash || '').trim();
      const resultCode = provenance?.result_code;
      if (execute.ok && executionResult.isSuccess) {
        const receiptLabel = txHash ? ` tx=${txHash}` : '';
        const codeLabel = typeof resultCode === 'number' ? ` code=${resultCode}` : '';
        setResponseToast(`LIVE action submitted through enterprise workflow.${receiptLabel}${codeLabel}`);
      } else {
        setResponseToast(executionResult.text || 'LIVE action execution failed.');
      }
      return;
    }
    setResponseToast('RECOMMENDED action recorded. Approval and live execution workflow required.');
  }

  async function ensureSimulatorProofChain() {
    if (!canGenerateSimulatorProofChain) {
      setResponseToast(simulatorProofChainUnavailableCopy);
      return;
    }
    setEnsuringProofChain(true);
    try {
      const ensureResponse = await fetch(`${apiUrl}/ops/monitoring/proof-chain/ensure`, { method: 'POST', headers: authHeaders() });
      if (!ensureResponse.ok) {
        setResponseToast(THREAT_COPY.failedToGenerateEvidencePackage);
        return;
      }
      const [runtimePayload, investigationTimelineResponse] = await Promise.all([
        Promise.resolve(fetchRuntimeStatusDeduped(authHeaders(), { forceRefresh: true })),
        fetch(`${apiUrl}/ops/monitoring/investigation-timeline`, { headers: authHeaders(), cache: 'no-store' }),
      ]);
      if (runtimePayload) {
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
      setResponseToast(THREAT_COPY.evidencePackageGenerated);
    } catch {
      setResponseToast(THREAT_COPY.failedToGenerateEvidencePackage);
    } finally {
      setEnsuringProofChain(false);
    }
  }

  const reconcileTimeoutExceeded = useMemo(() => {
    if (!latestReconcileJob?.started_at || latestReconcileJob.status !== 'running') return false;
    const started = Date.parse(latestReconcileJob.started_at);
    return Number.isFinite(started) && (Date.now() - started) > 120000;
  }, [latestReconcileJob?.started_at, latestReconcileJob?.status]);
  const reconcileProgressLabel = useMemo(() => {
    if (!latestReconcileJob) return null;
    const scanned = Number(latestReconcileJob.counts?.targets_scanned ?? 0);
    const updated = Number(latestReconcileJob.counts?.created_or_updated ?? 0);
    const invalid = Number(latestReconcileJob.counts?.invalid_targets ?? 0);
    const skipped = Number(latestReconcileJob.counts?.skipped_targets ?? 0);
    return `Reconcile progress: scanned ${scanned} targets · updated ${updated} · invalid ${invalid} · skipped ${skipped}`;
  }, [latestReconcileJob]);
  const reconcileUiState = useMemo<ReconcileJobStatus>(() => {
    if (latestReconcileJob?.status === 'queued' || latestReconcileJob?.status === 'running' || latestReconcileJob?.status === 'completed' || latestReconcileJob?.status === 'failed') {
      return latestReconcileJob.status;
    }
    return 'queued';
  }, [latestReconcileJob?.status]);
  const reconcileActionableError = useMemo(() => {
    if (latestReconcileJob?.status !== 'failed') return null;
    const reasonCodes = latestReconcileJob.reason_codes ?? [];
    const reasonCodeList = reasonCodes.length > 0 ? ` Reason codes: ${reasonCodes.join(', ')}.` : '';
    return `Action required: resolve ${latestReconcileJob.reason_detail || latestReconcileJob.reason_code || 'the backend reconcile failure'} and rerun reconcile from Monitored Systems.${reasonCodeList}`;
  }, [latestReconcileJob]);
  const lastSuccessfulReconcileAt = useMemo(() => {
    if (!latestReconcileJob || latestReconcileJob.status !== 'completed') return null;
    return latestReconcileJob.completed_at || latestReconcileJob.last_event_at || latestReconcileJob.started_at || null;
  }, [latestReconcileJob]);

  const threatOperationsViewModel = useMemo(() => ({
    monitoring: monitoringViewModel,
  }), [monitoringViewModel]);
  const reconcileStateReasonLabel = useMemo(() => {
    if (!latestReconcileJob) return 'No reconcile job has run yet.';
    const reason = latestReconcileJob.reason_detail || latestReconcileJob.reason_code || 'No reason provided.';
    return `State ${reconcileUiState.toUpperCase()} · Reason: ${reason}`;
  }, [latestReconcileJob, reconcileUiState]);


  const chainSummary = useMemo(() => buildAlertIncidentChain({
    alerts,
    incidents,
    actionHistory,
    detections,
  }), [alerts, incidents, actionHistory, detections]);

  const technicalDetails = useMemo(() => buildTechnicalRuntimeDetails({
    provenanceLabel: monitoringViewModel.provenanceLabel,
    provenanceExplanation: monitoringViewModel.provenanceExplanation,
    lastSuccessfulRefreshAt: monitoringViewModel.lastSuccessfulRefreshAt,
    lastSuccessfulRuntimeRefreshAt: monitoringViewModel.lastSuccessfulRuntimeRefreshAt,
    lastSuccessfulTimelineRefreshAt: monitoringViewModel.lastSuccessfulTimelineRefreshAt,
    continuityChecks: continuityFailedCheckList.map((item) => item.label),
    customerContinuitySummary: pageStatePrimaryCopy(pageState, configurationReason, runtimeSummary?.continuity_status ?? null, continuitySlo, continuityFailedCheckList, remediationLinks),
    reconcileUiState,
    activeReconcileId,
    lastSuccessfulReconcileAt,
    loopState: loopHealthSignal.state,
    consecutiveFailures: loopHealth?.consecutive_failures ?? 0,
    lastSuccessfulCycle: loopHealth?.last_successful_cycle ?? null,
    ensuringProofChain,
    proofChainEnabled: !monitoringViewModel.ctas.generateSimulatorProofChain.disabled,
    formatAbsoluteTime,
  }), [activeReconcileId, continuityFailedCheckList, ensuringProofChain, lastSuccessfulReconcileAt, loopHealth, loopHealthSignal.state, monitoringViewModel, reconcileUiState]);

  const responseActionsModel = useMemo(() => buildResponseActionsModel(actionCapabilities), [actionCapabilities]);
  const responseActionCapabilities = responseActionsModel.responseActionCapabilities;
  const responseActions = useMemo<ResponseAction[]>(() => (
    buildResponseActionList({ actionButtons: monitoringViewModel.actionButtons }).map((action) => ({
      ...action,
      onClick: action.id === 'sim-notify-team'
        ? () => void runThreatAction('notify_team', 'Run simulated response', 'simulated')
        : () => void runThreatAction('revoke_approval', 'Revoke approval', 'simulated'),
    }))
  ), [monitoringViewModel.actionButtons]);

  return (
    <section className="stack monitoringConsoleStack">
      <ThreatPageHeader
        showLiveTelemetry={showLiveTelemetry}
        ensuringProofChain={ensuringProofChain}
        proofChainDisabled={monitoringViewModel.ctas.generateSimulatorProofChain.disabled}
        proofChainReason={monitoringViewModel.ctas.generateSimulatorProofChain.reason}
        onRefreshNow={() => window.dispatchEvent(new Event('pilot-history-refresh'))}
        onGenerateProofChain={() => void ensureSimulatorProofChain()}
      />
      <ThreatOverviewCard securityStatus={monitoringHealthModel.securityStatus} />
      <MonitoringHealthCard
        heartbeatLabel={monitoringPresentation.heartbeatLabel}
        pollLabel={monitoringViewModel.pollLabel}
        telemetryLabel={hasTelemetryTimestamp ? telemetryDisplayLabel : 'Not available'}
        reportingSystems={reportingSystems}
        configuredSystems={configuredSystems}
        freshnessStatus={String(runtimeStatusSnapshot?.freshness_status ?? 'unavailable')}
        confidenceStatus={String(runtimeStatusSnapshot?.confidence_status ?? 'unavailable')}
      />
      <DetectionFeed detections={detectionRecords} loading={loadingSnapshot} />
      <AlertIncidentChain
        alert={chainSummary.alert}
        incident={chainSummary.incident}
        responseAction={chainSummary.responseAction}
      />
      <ResponseActionPanel
        capabilities={responseActionCapabilities}
        actions={responseActions}
        loading={loadingSnapshot}
      />
      <TechnicalRuntimeDetails
        summaryLine={technicalDetails.summaryLine}
        runtimeStatus={runtimeStatusSnapshot?.runtime_status}
        monitoringStatus={monitoringPresentation.status}
        telemetryFreshness={runtimeStatusSnapshot?.freshness_status}
        confidence={runtimeStatusSnapshot?.confidence_status}
        contradictionFlags={monitoringHealthModel.contradictionFlags}
        guardFlags={Array.isArray(runtimeStatusSnapshot?.guard_flags) ? runtimeStatusSnapshot.guard_flags : []}
        dbFailureClassification={runtimeStatusSnapshot?.db_failure_classification}
        statusReason={runtimeStatusSnapshot?.status_reason}
        failedEndpoints={snapshotFailedEndpoints}
        staleCollections={snapshotStaleCollections}
        diagnostics={technicalDetails.diagnostics}
        customerContinuitySummary={technicalDetails.customerContinuitySummary}
        continuityChecks={technicalDetails.continuityChecks}
        reconcileInternals={technicalDetails.reconcileInternals}
        loopHealthInternals={technicalDetails.loopHealthInternals}
        proofChainInternals={technicalDetails.proofChainInternals}
      />
    </section>
  );
}
