/**
 * Screen 5 — Threat Monitoring presentation helpers (pure logic, no React).
 *
 * The backend's canonical summary is the source of truth for every count. These
 * helpers only format/label it. Truthfulness rules enforced here:
 *   - a trend is rendered ONLY when a change percent exists (never fabricated),
 *   - MORE detections/anomalies is a WORSENING security metric, so an increase is
 *     never styled as "good" (green),
 *   - confidence and severity are formatted as distinct concepts,
 *   - zero / unavailable / no-telemetry / stale / degraded are distinct states.
 */

import type { PillVariant } from '../components/ui-primitives';

// --------------------------------------------------------------------------
// Types (loose — mirror the backend JSON shape)
// --------------------------------------------------------------------------
export type DetectorSupport = Record<string, { supported: boolean; evidence_quality?: string; reason?: string }>;

export type DetectionByType = {
  type: string;
  label: string;
  count: number;
  supported: boolean;
  unsupported_reason?: string | null;
};

export type VolumeBucket = { bucket_epoch: number; count: number; live_count: number; other_count: number; bucket_seconds: number };

export type WatchlistMatch = {
  actor_address: string | null;
  asset_id: string | null;
  asset_name: string | null;
  match_reason: string | null;
  detection_type: string | null;
  severity: string | null;
  confidence: number | null;
  status: string | null;
  evidence_source: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  hits: number;
  detection_id: string;
};

export type TopDetection = {
  detection_id: string;
  detection_type: string;
  title: string;
  summary: string | null;
  severity: string;
  confidence: number | null;
  status: string;
  affected_asset_names: string[];
  actor_addresses: string[];
  transaction_hashes: string[];
  chain_id: number | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  detected_at: string | null;
  event_count: number;
  evidence_count: number;
  evidence_quality: string;
  evidence_source: string;
  recommended_next_step: string | null;
  ai_summary: string | null;
  ai_summary_source: string | null;
  alert_eligible: boolean;
  alert_id: string | null;
  incident_id: string | null;
};

export type EnginePanel = {
  state: string;
  headline: string;
  detection: TopDetection | null;
  ai_summary_source: string;
  can_investigate: boolean;
};

export type ThreatSummary = {
  workspace_id: string;
  generated_at: string | null;
  window_days: number;
  window_start: string | null;
  window_end: string | null;
  telemetry_events_count: number;
  previous_telemetry_events_count: number;
  telemetry_change_percent: number | null;
  detection_count: number;
  previous_detection_count: number;
  detection_change_percent: number | null;
  anomaly_count: number;
  previous_anomaly_count: number;
  anomaly_change_percent: number | null;
  mean_time_to_detect_seconds: number | null;
  telemetry_volume_buckets: VolumeBucket[];
  detections_by_type: DetectionByType[];
  active_watchlist_matches: WatchlistMatch[];
  top_detection: TopDetection | null;
  ingestion_health: {
    last_telemetry_at: string | null;
    data_freshness: string;
    telemetry_events_window: number;
    source_breakdown: { live?: number; simulator?: number; replay?: number };
    worker: { enabled: boolean; healthy: boolean; last_heartbeat_at: string | null; last_completed_at: string | null };
  };
  evidence_coverage: { active_detections: number; live_backed: number; non_live_backed: number };
  data_freshness: string;
  degraded_reasons: string[];
  detector_support: DetectorSupport;
  detector_version: string;
  engine_panel: EnginePanel;
};

export type DetectionRow = {
  id: string;
  detection_type: string;
  detection_type_label: string;
  title: string;
  severity: string;
  confidence: number | null;
  status: string;
  primary_asset_id: string | null;
  asset_name: string | null;
  evidence_source: string;
  evidence_quality: string;
  event_count: number;
  actor_count: number;
  evidence_count: number;
  explanation: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  detected_at: string | null;
  linked_alert_id: string | null;
  linked_incident_id: string | null;
};

// --------------------------------------------------------------------------
// Tooltips (customer-facing, precise)
// --------------------------------------------------------------------------
export const TELEMETRY_TOOLTIP = 'Normalized on-chain telemetry events ingested in the selected window. Live, simulator, and replay sources are counted and labelled separately.';
export const DETECTIONS_TOOLTIP = 'Promoted, evidence-grounded threat detections that crossed detection criteria in the window. Resolved and dismissed detections are excluded.';
export const ANOMALIES_TOOLTIP = 'Sub-threshold deviations that have NOT yet crossed detection criteria. An anomaly is not a confirmed threat.';
export const MTTD_TOOLTIP = 'Mean time to detect: average time from the earliest supporting on-chain event to when a detection was raised, across promoted detections in the window.';
export const CONFIDENCE_TOOLTIP = 'How strongly the available evidence supports this detection. Independent of impact — thin evidence yields low confidence even when severity is high.';
export const SEVERITY_TOOLTIP = 'Potential impact if the detection is valid. Independent of confidence.';
export const EVIDENCE_QUALITY_TOOLTIP = 'Strength of the underlying evidence: transaction trace > decoded call > event logs > normalized telemetry > partial metadata.';

// --------------------------------------------------------------------------
// Severity / status / confidence / evidence-quality labels
// --------------------------------------------------------------------------
export function severityLabel(severity: string | null | undefined): string {
  const s = String(severity ?? '').toLowerCase();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Unknown';
}

export function severityVariant(severity: string | null | undefined): PillVariant {
  switch (String(severity ?? '').toLowerCase()) {
    case 'critical':
    case 'high':
      return 'danger';
    case 'medium':
      return 'warning';
    case 'low':
      return 'info';
    default:
      return 'neutral';
  }
}

export function detectionStatusLabel(status: string | null | undefined): string {
  const s = String(status ?? '').toLowerCase();
  const map: Record<string, string> = {
    anomaly: 'Anomaly',
    open: 'Open',
    investigating: 'Investigating',
    resolved: 'Resolved',
    dismissed: 'Dismissed',
  };
  return map[s] ?? (s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Unknown');
}

export function detectionStatusVariant(status: string | null | undefined): PillVariant {
  switch (String(status ?? '').toLowerCase()) {
    case 'open':
      return 'danger';
    case 'investigating':
      return 'info';
    case 'anomaly':
      return 'warning';
    case 'resolved':
      return 'success';
    case 'dismissed':
      return 'neutral';
    default:
      return 'neutral';
  }
}

export function confidencePercent(confidence: number | null | undefined): string {
  if (confidence === null || confidence === undefined || Number.isNaN(confidence)) return '—';
  return `${Math.round(confidence * 100)}%`;
}

export function confidenceBand(confidence: number | null | undefined): 'High' | 'Medium' | 'Low' | 'Unknown' {
  if (confidence === null || confidence === undefined || Number.isNaN(confidence)) return 'Unknown';
  if (confidence >= 0.7) return 'High';
  if (confidence >= 0.4) return 'Medium';
  return 'Low';
}

export function confidenceVariant(confidence: number | null | undefined): PillVariant {
  const band = confidenceBand(confidence);
  if (band === 'High') return 'success';
  if (band === 'Medium') return 'warning';
  if (band === 'Low') return 'neutral';
  return 'neutral';
}

const EVIDENCE_QUALITY_LABELS: Record<string, string> = {
  transaction_trace: 'Transaction trace',
  decoded_call: 'Decoded call',
  event_logs: 'Event logs',
  normalized_telemetry: 'Normalized telemetry',
  partial_metadata: 'Partial metadata',
};

export function evidenceQualityLabel(quality: string | null | undefined): string {
  const q = String(quality ?? '').toLowerCase();
  return EVIDENCE_QUALITY_LABELS[q] ?? (q ? q.replace(/_/g, ' ') : 'Unknown');
}

const DETECTION_TYPE_LABELS: Record<string, string> = {
  unusual_transfer: 'Unusual Transfer',
  coordinated_activity: 'Coordinated Activity',
  mint_burn_irregularity: 'Mint/Burn Irregularity',
  privileged_action: 'Privileged/Admin Action',
  oracle_deviation: 'Oracle Deviation',
  reentrancy_pattern: 'Reentrancy Pattern',
  flash_loan_pattern: 'Flash-Loan Pattern',
  other: 'Other',
};

export function detectionTypeLabel(type: string | null | undefined): string {
  const t = String(type ?? '').toLowerCase();
  return DETECTION_TYPE_LABELS[t] ?? (t ? t.replace(/_/g, ' ') : 'Unknown');
}

// --------------------------------------------------------------------------
// Evidence source (live vs simulator vs replay) — truthful labelling
// --------------------------------------------------------------------------
export function evidenceSourceLabel(source: string | null | undefined): string {
  const s = String(source ?? '').toLowerCase();
  if (s === 'live') return 'live';
  if (s === 'simulator') return 'simulator';
  if (s === 'replay') return 'replay';
  return 'unknown';
}

export function evidenceSourceVariant(source: string | null | undefined): PillVariant {
  const s = String(source ?? '').toLowerCase();
  if (s === 'live') return 'success';
  if (s === 'simulator') return 'info';
  if (s === 'replay') return 'warning';
  return 'neutral';
}

// --------------------------------------------------------------------------
// MTTD + data freshness
// --------------------------------------------------------------------------
export function formatMttd(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

export function dataFreshnessLabel(state: string | null | undefined): string {
  switch (String(state ?? '').toLowerCase()) {
    case 'fresh':
      return 'Fresh';
    case 'stale':
      return 'Stale';
    case 'no_telemetry':
      return 'No telemetry';
    case 'unavailable':
      return 'Unavailable';
    default:
      return 'Unknown';
  }
}

export function dataFreshnessVariant(state: string | null | undefined): PillVariant {
  switch (String(state ?? '').toLowerCase()) {
    case 'fresh':
      return 'success';
    case 'stale':
      return 'warning';
    case 'no_telemetry':
      return 'neutral';
    case 'unavailable':
      return 'neutral';
    default:
      return 'neutral';
  }
}

// --------------------------------------------------------------------------
// Trend rendering — security-aware. Only rendered when a change exists.
// --------------------------------------------------------------------------
export type TrendMetric = 'telemetry' | 'detections' | 'anomalies' | 'mttd';
export type Trend = { arrow: '▲' | '▼'; label: string; tone: 'good' | 'bad' | 'neutral' } | null;

export function trend(metric: TrendMetric, changePercent: number | null | undefined): Trend {
  // No valid comparison base -> no trend (never fabricate one).
  if (changePercent === null || changePercent === undefined || Number.isNaN(changePercent)) return null;
  if (changePercent === 0) return { arrow: '▲', label: '0%', tone: 'neutral' };
  const up = changePercent > 0;
  const arrow = up ? '▲' : '▼';
  const label = `${up ? '+' : ''}${changePercent}%`;
  // For detections/anomalies, an INCREASE is a worsening security posture.
  // For MTTD, an increase (slower detection) is worse. For telemetry volume,
  // change is informational, never "good/bad".
  let tone: 'good' | 'bad' | 'neutral';
  if (metric === 'telemetry') {
    tone = 'neutral';
  } else if (metric === 'mttd') {
    tone = up ? 'bad' : 'good';
  } else {
    tone = up ? 'bad' : 'good';
  }
  return { arrow, label, tone };
}

export function trendColor(tone: 'good' | 'bad' | 'neutral'): string {
  if (tone === 'good') return 'var(--success-fg, #4ade80)';
  if (tone === 'bad') return 'var(--danger-fg, #f87171)';
  return 'var(--text-muted, #5a6478)';
}

// --------------------------------------------------------------------------
// Engine panel tone — never a scary alert when evidence is thin
// --------------------------------------------------------------------------
export type EnginePanelPresentation = { eyebrow: string; variant: PillVariant; scary: boolean };

export function enginePanelPresentation(state: string, severity?: string | null): EnginePanelPresentation {
  switch (state) {
    case 'active_detection':
      return { eyebrow: 'Threat detected', variant: severityVariant(severity), scary: true };
    case 'investigation_open':
      return { eyebrow: 'Investigation open', variant: 'info', scary: false };
    case 'anomalies_only':
      return { eyebrow: 'Anomalies observed', variant: 'warning', scary: false };
    case 'telemetry_stale':
      return { eyebrow: 'Telemetry stale', variant: 'warning', scary: false };
    case 'provider_degraded':
      return { eyebrow: 'Provider degraded', variant: 'warning', scary: false };
    case 'no_telemetry':
      return { eyebrow: 'Awaiting telemetry', variant: 'neutral', scary: false };
    case 'unavailable':
      return { eyebrow: 'Provisioning', variant: 'neutral', scary: false };
    case 'no_material_threat':
    default:
      return { eyebrow: 'No material threat', variant: 'success', scary: false };
  }
}

// --------------------------------------------------------------------------
// Relative time + short address/hash
// --------------------------------------------------------------------------
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '—';
  const diff = Date.now() - parsed.getTime();
  if (diff < 0) return 'just now';
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return parsed.toLocaleDateString();
}

export function shortHex(value: string | null | undefined, lead = 6, tail = 4): string {
  const v = String(value ?? '');
  if (!v) return '—';
  if (v.length <= lead + tail + 2) return v;
  return `${v.slice(0, lead)}…${v.slice(-tail)}`;
}

// --------------------------------------------------------------------------
// Degraded-reason copy
// --------------------------------------------------------------------------
const DEGRADED_REASON_COPY: Record<string, string> = {
  telemetry_stale: 'Telemetry is stale — detections may be based on older data.',
  no_telemetry: 'No telemetry has arrived yet.',
  detection_storage_provisioning: 'Detection storage is provisioning.',
  worker_unhealthy: 'The detection worker heartbeat is stale; new detections may be delayed.',
};

export function degradedReasonCopy(reason: string): string {
  return DEGRADED_REASON_COPY[reason] ?? reason.replace(/_/g, ' ');
}

// --------------------------------------------------------------------------
// Investigate result -> destination + toast copy
// --------------------------------------------------------------------------
export type InvestigateResult = {
  status?: string;
  destination?: string;
  alert_id?: string | null;
  incident_id?: string | null;
  detail?: string;
};

export function investigateOutcomeMessage(result: InvestigateResult): string {
  switch (result.status) {
    case 'created':
      return 'Investigation opened. Redirecting to alert triage…';
    case 'existing_alert':
      return 'An investigation is already open for this detection. Redirecting…';
    case 'existing_incident':
      return 'An incident already exists for this detection. Redirecting…';
    default:
      return result.detail ?? 'Unable to start the investigation.';
  }
}
