'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TableShell,
  TabStrip,
  type PillVariant,
} from './components/ui-primitives';
import { usePilotAuth } from './pilot-auth-context';
import {
  evidenceDetailReducer,
  initEvidenceDetailState,
  isEvidenceProgressPollingState,
  resolveDetailActionState,
  selectAuthoritativeDetail,
  type EvidenceDetailEvent,
  type EvidenceDetailState,
} from './evidence-detail-state';
import { useRuntimeSummary } from './runtime-summary-context';

/* ── Constants ──────────────────────────────────────────────────── */

const REQUIRED_ARTIFACTS = [
  'Telemetry Snapshot',
  'Detection Event',
  'Alert',
  'Incident Timeline',
  'Response Action',
  'Audit Log',
] as const;

const PKG_TABLE_HEADERS = [
  'Package ID',
  'Incident / Scope',
  'Created',
  'Includes',
  'Size',
  'SHA-256',
  'Integrity',
  'Actions',
] as const;

// Integrity-status filter options. Values map to the backend integrity_status enum
// (the single canonical set of states — no AI badge on this ordinary data filter).
const INTEGRITY_FILTER_OPTIONS = [
  { value: '', label: 'All integrity states' },
  { value: 'draft', label: 'Draft' },
  { value: 'building', label: 'Building' },
  { value: 'hashing', label: 'Hashing' },
  { value: 'hash_generated', label: 'Hash Generated' },
  { value: 'verifying', label: 'Verifying' },
  { value: 'verified', label: 'Verified' },
  { value: 'needs_evidence', label: 'Needs Evidence' },
  { value: 'manifest_missing', label: 'Manifest Missing' },
  { value: 'integrity_failed', label: 'Integrity Failed' },
  { value: 'failed', label: 'Failed' },
  { value: 'superseded', label: 'Superseded' },
  { value: 'legacy_export', label: 'Legacy Export' },
] as const;

const AUDIT_TABLE_HEADERS = [
  'Time',
  'Actor',
  'Action',
  'Object',
  'Result',
  'Source IP or System',
  'Evidence Source',
] as const;

/* ── Types ──────────────────────────────────────────────────────── */

type EvidencePackage = {
  id: string;
  export_type?: string;
  format?: string;
  status?: string;
  created_at?: string;
  incident_id?: string;
  response_action_id?: string;
  alert_id?: string;
  detection_id?: string;
  asset_id?: string;
  asset_label?: string;
  evidence_source?: string;
  evidence_source_type?: string;
  size_bytes?: number;
  package_ready?: boolean;
  download_url?: string | null;
  created_by?: string;
  retention_policy?: string;
  integrity_hash?: string | null;
  includes?: string[];
  missing_artifacts?: string[];
  chain_complete?: boolean;
  export_status?: string;
  package_status?: string;
  source_truthfulness_status?: string;
  redactions_applied?: boolean;
  warnings?: string[];
  missing_sections?: string[];
  unavailable_sections?: string[];
  // Screen 9 integrity / completeness (backend-authoritative — never computed here)
  integrity_status?: string;
  integrity_label?: string;
  hash_state?: string;
  hash_display_state?: string;
  // Canonical manifest SHA-256 (== integrity_hash). Used by the SHA-256 column so
  // it never disagrees with the integrity pill about whether a manifest exists.
  manifest_sha256?: string | null;
  manifest_reference?: {
    exists?: boolean;
    retrievable?: boolean;
    sha256?: string | null;
    size_bytes?: number | null;
    file_count?: number | null;
    media_type?: string;
    source?: string;
  } | null;
  completeness_score?: number | null;
  manifest_download_url?: string | null;
  verified_at?: string | null;
  superseded?: boolean;
  files_hashed?: number;
  // Human-readable, backend-generated identifiers (raw UUIDs stay internal).
  package_number?: string;
  incident_short_id?: string | null;
  incident_number?: string | null;
  scope_type?: string;
  scope_label?: string | null;
  // Canonical export/action state — the UI never re-derives these.
  export_ready?: boolean;
  is_export_ready?: boolean;
  is_downloadable?: boolean;
  is_legacy_export?: boolean;
  is_manifest_missing?: boolean;
  ready_for_verification?: boolean;
  allowed_actions?: {
    view?: boolean;
    download?: boolean;
    download_manifest?: boolean;
    verify?: boolean;
    copy_hash?: boolean;
    // Recovery action for a Manifest-Missing package (no retrievable manifest).
    generate_manifest?: boolean;
    // Fallback recovery: regenerate a superseding package (preserves the original)
    // when an in-place manifest cannot safely be generated.
    regenerate_package?: boolean;
  };
};

// Full package report returned by GET /api/exports/{id}
type PackageDetail = EvidencePackage & {
  files?: Array<{
    logical_path?: string;
    media_type?: string;
    size_bytes?: number;
    sha256?: string;
    source_record_type?: string;
    verification_status?: string;
  }>;
  completeness?: Completeness | null;
  chain_evidence?: Array<Record<string, unknown>>;
  incident_trace?: Record<string, unknown>;
  verification?: {
    valid?: boolean;
    verified_at?: string;
    files_total?: number;
    files_verified?: number;
    files_failed?: string[];
    missing_files?: string[];
    manifest_ok?: boolean;
    seal_status?: string;
  } | null;
  agent_findings?: Array<{ type?: string; code?: string; message?: string }>;
  storage_available?: boolean;
};

type Completeness = {
  score?: number;
  status?: string;
  status_label?: string;
  required_count?: number;
  present_count?: number;
  missing_count?: number;
  unverifiable_count?: number;
  categories?: Array<{ code?: string; label?: string; required?: boolean; status?: string }>;
  missing_codes?: string[];
  checklist?: Array<{ code?: string; label?: string; present?: boolean }>;
};

type EvidenceMetrics = {
  total_packages?: number;
  total_size_bytes?: number;
  verified?: number;
  integrity_failures?: number;
  incidents_covered?: number;
  files_hashed?: number;
  ready?: number;
  export_ready?: number;
  ready_for_verification?: number;
  legacy_exports?: number;
  average_completeness?: number | null;
  retention_status?: string;
  last_calculated?: string;
};

type AuditRow = {
  id?: string;
  timestamp?: string;
  created_at?: string;
  actor?: string;
  system?: string;
  action?: string;
  event_type?: string;
  target?: string;
  target_id?: string;
  object_type?: string;
  object_id?: string;
  result?: string;
  status?: string;
  source?: string;
  origin?: string;
  source_ip?: string;
  user_agent?: string;
  workspace_id?: string;
  evidence_source?: string;
  evidence_source_type?: string;
};

/* ── Helpers ────────────────────────────────────────────────────── */

// Simulator evidence must always show evidence_source = simulator.
// Fallback evidence must be labeled unavailable, not simulator.
// Do not label simulator or fallback evidence as live_provider.
function evidenceSourcePill(
  rowSource?: string | null,
  workspaceSource?: string,
): { label: string; variant: PillVariant } {
  const raw = (rowSource ?? '').toLowerCase();
  if (raw === 'missing') {
    return { label: 'Evidence missing', variant: 'neutral' };
  }
  if (raw === 'unavailable' || raw === 'fallback') {
    return { label: 'Evidence unavailable', variant: 'warning' };
  }
  if (
    raw === 'simulator' ||
    raw === 'demo' ||
    raw === 'replay' ||
    raw === 'guided_simulator' ||
    workspaceSource === 'simulator'
  ) {
    return { label: 'Simulator/test evidence', variant: 'info' };
  }
  if (raw === 'live' || raw === 'live_provider') {
    return { label: 'Live evidence', variant: 'success' };
  }
  if (raw === 'response_action' || raw === 'proof_bundle') {
    return { label: 'Response action export', variant: 'success' };
  }
  // AI investigation recommendation decisions are grounded in the incident's AI
  // evidence snapshot — a truthful source, never "Unknown source" and never live-chain.
  if (raw === 'ai_investigation') {
    return { label: 'AI investigation', variant: 'info' };
  }
  if (raw === 'ai_evidence_snapshot') {
    return { label: 'AI evidence snapshot', variant: 'info' };
  }
  if (raw === 'ai_recommendation_review' || raw === 'human_recommendation_review') {
    return { label: 'Human recommendation review', variant: 'info' };
  }
  return { label: 'Unknown source', variant: 'neutral' };
}

// For proof_bundle packages that predate evidence_source_type being persisted to
// the DB, infer 'response_action' when no source is recorded but a response_action_id
// is present.  This avoids the misleading "Unknown source" label.
function resolvePackageEvidenceSource(pkg: EvidencePackage): string | undefined {
  const explicit = pkg.evidence_source_type ?? pkg.evidence_source;
  if (explicit) return explicit;
  if (pkg.export_type === 'proof_bundle' && pkg.response_action_id) {
    return 'response_action';
  }
  return undefined;
}

function packageStatusPill(status?: string): { label: string; variant: PillVariant } {
  const s = (status ?? '').toLowerCase();
  if (s === 'ready' || s === 'complete' || s === 'completed') return { label: 'Ready', variant: 'success' };
  if (s === 'exported') return { label: 'Exported', variant: 'info' };
  if (s === 'pending') return { label: 'Pending', variant: 'warning' };
  if (s === 'failed') return { label: 'Failed', variant: 'danger' };
  if (s === 'not_available' || s === 'not available') return { label: 'Not Available', variant: 'neutral' };
  return { label: 'Unknown', variant: 'neutral' };
}

function auditResultPill(result?: string): { label: string; variant: PillVariant } {
  const s = (result ?? '').toLowerCase();
  if (s === 'success' || s === 'succeeded') return { label: 'Success', variant: 'success' };
  if (s === 'failed' || s === 'failure') return { label: 'Failed', variant: 'danger' };
  if (s === 'denied') return { label: 'Denied', variant: 'danger' };
  if (s === 'pending') return { label: 'Pending', variant: 'warning' };
  return { label: 'Unknown', variant: 'neutral' };
}

function fmt(value?: string | null): string {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString();
}

function fmtSize(bytes?: number): string {
  if (typeof bytes !== 'number') return 'Pending';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

function includesLabel(pkg: EvidencePackage): string {
  const items = pkg.includes?.length
    ? pkg.includes
    : REQUIRED_ARTIFACTS.map((a) => a.toLowerCase());
  const preview = items.slice(0, 3).join(', ');
  return items.length > 3 ? `${preview} +${items.length - 3}` : preview;
}

// Whether the package artifact can be downloaded. Prefers the backend-authoritative
// is_downloadable flag; falls back to legacy signals only for older API responses.
function isPackageReady(pkg: EvidencePackage): boolean {
  if (typeof pkg.is_downloadable === 'boolean') return pkg.is_downloadable;
  return (
    !!pkg.package_ready ||
    !!pkg.download_url ||
    packageStatusPill(pkg.status).label === 'Ready' ||
    packageStatusPill(pkg.status).label === 'Exported'
  );
}

// A package is "in progress" while its generation lifecycle has not reached a
// terminal state. Job-level queued/pending/building/running and the integrity
// states Building/Hashing/Verifying are non-terminal; everything else (verified,
// hash_generated, integrity_failed, failed, legacy_export, superseded,
// needs_evidence AND manifest_missing) is a resting state the async poller must
// stop at. The predicate lives in evidence-detail-state as the ONE canonical,
// unit-tested definition so a Manifest-Missing package is never polled as though
// generation were still running (which would refetch and replace its detail).
function isPackageInProgress(pkg: EvidencePackage): boolean {
  return isEvidenceProgressPollingState(pkg);
}

// Integrity status is authoritative on the backend. This only maps the canonical
// enum to a truthful pill — it never invents a "Verified" state the backend
// didn't report. "Hash generated" is deliberately distinct from "Verified".
// Maps the canonical integrity_status to a pill variant. The label is taken from
// the backend integrity_label when present so wording stays authoritative; this
// only decides the colour. "Hash Generated" is deliberately distinct from
// "Verified", and a legacy/unhashed export is never shown as either.
const INTEGRITY_VARIANTS: Record<string, PillVariant> = {
  verified: 'success',
  hash_generated: 'info',
  verifying: 'warning',
  hashing: 'warning',
  building: 'warning',
  needs_evidence: 'warning',
  manifest_missing: 'danger',
  integrity_failed: 'danger',
  failed: 'danger',
  superseded: 'neutral',
  draft: 'neutral',
  legacy_export: 'warning',
};

function integrityPill(pkg: EvidencePackage): { label: string; variant: PillVariant } {
  const status = (pkg.integrity_status ?? '').toLowerCase();
  const variant = INTEGRITY_VARIANTS[status] ?? 'neutral';
  const label =
    pkg.integrity_label ??
    (status ? status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : 'Unknown');
  return { label, variant };
}

// The package details panel shows three explicit, non-overlapping states instead
// of one ambiguous "Integrity Status": Generation Status (did the bundle build),
// Hash Verification (its cryptographic state), and Evidence Completeness (score).
// Each is derived from backend-authoritative fields only.

// Generation Status — describes only whether the package artifact was produced,
// and whether it is a partial/incomplete bundle. Completeness and hashing are
// reported separately, so this never says "Verified" or "Manifest Missing".
function generationStatus(pkg: EvidencePackage): { label: string; variant: PillVariant } {
  const status = (pkg.status ?? '').toLowerCase();
  if (status === 'failed') return { label: 'Failed', variant: 'danger' };
  if (['queued', 'pending', 'building', 'running'].includes(status)) {
    return { label: 'Generating…', variant: 'warning' };
  }
  if (status !== 'completed') {
    return { label: status ? status.replace(/\b\w/g, (c) => c.toUpperCase()) : 'Unknown', variant: 'neutral' };
  }
  const packaging = (pkg.export_status ?? pkg.package_status ?? '').toLowerCase();
  if (pkg.superseded) return { label: 'Generated (Superseded)', variant: 'neutral' };
  if (['incomplete', 'blocked'].includes(packaging)) {
    return { label: 'Generated / Incomplete Package', variant: 'warning' };
  }
  if (['partial', 'degraded_diagnostic'].includes(packaging)) {
    return { label: 'Generated / Partial Package', variant: 'warning' };
  }
  return { label: 'Generated', variant: 'success' };
}

// Hash Verification — the cryptographic state of the package. It NEVER shows
// "Needs Evidence" (a completeness concern surfaced in its own field); a package
// with no retrievable manifest reads as "Manifest Missing", not as safe.
const HASH_VERIFICATION_STATES: Record<string, { label: string; variant: PillVariant }> = {
  verified: { label: 'Verified', variant: 'success' },
  verifying: { label: 'Verifying', variant: 'warning' },
  hash_generated: { label: 'Hash Generated', variant: 'info' },
  hashing: { label: 'Generating…', variant: 'warning' },
  building: { label: 'Generating…', variant: 'warning' },
  integrity_failed: { label: 'Integrity Failed', variant: 'danger' },
  manifest_missing: { label: 'Manifest Missing', variant: 'danger' },
  legacy_export: { label: 'No Manifest', variant: 'warning' },
  superseded: { label: 'Superseded', variant: 'neutral' },
};
function hashVerification(pkg: EvidencePackage): { label: string; variant: PillVariant } {
  const status = (pkg.integrity_status ?? '').toLowerCase();
  const mapped = HASH_VERIFICATION_STATES[status];
  if (mapped) return mapped;
  // Unknown / needs_evidence / draft: report only the manifest/hash truth, never
  // "Needs Evidence". Fail closed — an absent manifest is "Manifest Missing".
  const hashState = (pkg.hash_state ?? pkg.hash_display_state ?? '').toLowerCase();
  if (hashState === 'generating') return { label: 'Generating…', variant: 'warning' };
  const hasManifest = pkg.manifest_reference?.retrievable ?? Boolean(packageHash(pkg));
  if (hasManifest) return { label: 'Hash Generated', variant: 'info' };
  if (pkg.is_manifest_missing) return { label: 'Manifest Missing', variant: 'danger' };
  return { label: 'Not Generated', variant: 'neutral' };
}

// Full SHA-256 stays available for copy/tooltip; the table shows a truncated form.
// The manifest SHA-256 IS the integrity hash — one canonical field feeds both the
// SHA-256 column and the integrity pill so they can never disagree.
function packageHash(pkg: EvidencePackage): string | null {
  return pkg.integrity_hash ?? pkg.manifest_sha256 ?? pkg.manifest_reference?.sha256 ?? null;
}

function truncHash(hash?: string | null): string {
  if (!hash) return '-';
  const clean = hash.replace(/^sha256:/i, '');
  if (clean.length <= 16) return clean;
  return `${clean.slice(0, 10)}…${clean.slice(-6)}`;
}

// 95-100 Excellent · 80-94 Good · 60-79 Incomplete · <60 Critical · none → Not calculated.
// Never returns "Unknown": an absent score means the value has not been calculated,
// which is stated truthfully rather than implying an unknown-but-existing state.
function completenessStatusLabel(score?: number | null): { label: string; variant: PillVariant } {
  if (typeof score !== 'number') return { label: 'Not calculated', variant: 'neutral' };
  if (score >= 95) return { label: 'Excellent', variant: 'success' };
  if (score >= 80) return { label: 'Good', variant: 'success' };
  if (score >= 60) return { label: 'Incomplete', variant: 'warning' };
  return { label: 'Critical', variant: 'danger' };
}

function fmtBytesTotal(bytes?: number): string {
  if (typeof bytes !== 'number' || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

// Renders a backend error into a safe, human-readable string. FastAPI permission and
// validation errors arrive as `detail` objects ({code, message, …}); we surface only the
// message, never the raw object or an internal stack trace.
function safeErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
}

// Friendly, customer-facing rendering of a manifest/verify error. The raw
// backend envelope ({"error":"PACKAGE_NOT_READY",...}) is NEVER shown as text;
// the technical code is surfaced only in an expandable diagnostics disclosure.
const VERIFY_ERROR_COPY: Record<string, string> = {
  PACKAGE_NOT_READY:
    'Package is not ready for verification. This package does not have a retrievable manifest yet.',
  PACKAGE_STORAGE_UNAVAILABLE:
    'Evidence storage is temporarily unavailable. Package metadata is still available — please try again shortly.',
  PACKAGE_NOT_FOUND: 'This evidence package could not be found.',
  // Manifest-recovery failures — always customer-safe, never raw JSON.
  MANIFEST_GENERATION_FAILED: 'Manifest could not be generated. Please try again.',
  MANIFEST_UNRECOVERABLE:
    'This package cannot be recovered in place because its stored files are unavailable. Regenerate the package to supersede it.',
  MANIFEST_RECOVERY_NOT_APPLICABLE: 'This package is not eligible for manifest recovery.',
  PACKAGE_MANIFEST_IMMUTABLE:
    'This package already has a manifest and cannot be regenerated in place. Create a superseding package instead.',
  // Regenerate (superseding) recovery failures — customer-safe copy.
  PACKAGE_NOT_ELIGIBLE:
    'This package already has a usable manifest and cannot be regenerated. Verified evidence stays immutable.',
  PACKAGE_SUPERSEDED: 'This package has already been superseded by a newer package.',
  PACKAGE_NOT_INCIDENT_SCOPED: 'Only incident-scoped packages can be regenerated.',
};

function friendlyPackageError(
  body: unknown,
  fallback: string,
): { message: string; code: string | null } {
  const envelope = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const detail =
    envelope.detail && typeof envelope.detail === 'object'
      ? (envelope.detail as Record<string, unknown>)
      : envelope;
  const code =
    (typeof detail.error === 'string' && detail.error) ||
    (typeof envelope.error === 'string' && envelope.error) ||
    null;
  if (code && VERIFY_ERROR_COPY[code]) {
    return { message: VERIFY_ERROR_COPY[code], code };
  }
  // Unknown code: show the backend-safe message text only, never the raw object.
  return { message: safeErrorMessage(detail, fallback), code };
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  return false;
}

/* ── Main panel ─────────────────────────────────────────────────── */

export default function EvidenceAuditPanel() {
  const { summary, runtime, loading: runtimeLoading } = useRuntimeSummary();
  const { authHeaders } = usePilotAuth();
  const searchParams = useSearchParams();

  const urlPackageId = searchParams.get('package_id') ?? '';
  const urlActionId = searchParams.get('action_id') ?? '';
  const urlIncidentId = searchParams.get('incident_id') ?? '';

  const [packages, setPackages] = useState<EvidencePackage[]>([]);
  const [auditRows, setAuditRows] = useState<AuditRow[]>([]);
  const [activeTab, setActiveTab] = useState<'packages' | 'audit'>('packages');
  const [selectedPkgId, setSelectedPkgId] = useState(urlPackageId);
  const [selectedAuditId, setSelectedAuditId] = useState('');
  const [message, setMessage] = useState('');
  // Technical error code shown only inside an expandable diagnostics disclosure,
  // never inline as raw JSON in the customer-facing status line.
  const [diagnostics, setDiagnostics] = useState<string | null>(null);
  const [dataLoading, setDataLoading] = useState(false);
  const [auditUnavailable, setAuditUnavailable] = useState('');
  const [responseActionsCount, setResponseActionsCount] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<EvidenceMetrics | null>(null);
  const [search, setSearch] = useState('');
  const [integrityFilter, setIntegrityFilter] = useState('');
  // Selected-package DETAIL is a stale-while-refresh state machine (see
  // evidence-detail-state). The authoritative detail is retained across every
  // refresh and is NEVER overwritten by a list-row summary — the mixing of the
  // two models is what made "Generate Manifest" appear then vanish for a
  // Manifest-Missing package (EV-2026-004) whenever detail was briefly absent.
  const [detailState, dispatchDetail] = useReducer(
    evidenceDetailReducer as (
      s: EvidenceDetailState<PackageDetail>,
      e: EvidenceDetailEvent<PackageDetail>,
    ) => EvidenceDetailState<PackageDetail>,
    initEvidenceDetailState<PackageDetail>(urlPackageId),
  );
  const selectedDetail = selectAuthoritativeDetail(detailState);
  const detailRefreshing = detailState.refreshing;
  const detailError = detailState.error;
  // First-load spinner semantics preserved: "loading" means there is nothing
  // authoritative to show yet. During a stale refresh we keep the last detail,
  // so the checklist/metrics never blink back to "Calculating…".
  const detailLoading = detailState.refreshing && selectedDetail == null;
  const [verifyingId, setVerifyingId] = useState('');
  // Manifest-recovery (Generate Manifest) in-flight id + last per-package outcome,
  // so the recovery card can show Generating… / success / failure with a Retry.
  const [generatingId, setGeneratingId] = useState('');
  // "Regenerate Package" (fallback recovery) in-flight id — creates a superseding
  // package and leaves the Manifest-Missing original untouched.
  const [regeneratingId, setRegeneratingId] = useState('');
  const [recovery, setRecovery] = useState<{ id: string; phase: 'success' | 'error'; message: string } | null>(null);
  const [loadError, setLoadError] = useState('');
  const [lastRefreshAt, setLastRefreshAt] = useState<string>('');
  const [copiedHash, setCopiedHash] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  // Backend-authoritative summary state (never derived from raw rows in React).
  const [retentionStatus, setRetentionStatus] = useState<string>('Unknown');
  // Tri-state permission: undefined = not yet known / field absent from the API
  // response, true/false = the backend's explicit decision. Only an explicit `false`
  // denies the action — an absent field must never permanently lock out an authorized
  // user, because the POST is still enforced server-side (evidence.export).
  const [canExport, setCanExport] = useState<boolean | undefined>(undefined);
  const [auditTotal, setAuditTotal] = useState<number | null>(null);
  const [auditCapped, setAuditCapped] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [openMenuId, setOpenMenuId] = useState('');

  const counts = runtime?.counts as Record<string, number> | undefined;
  const workspaceEvidenceSource: string = summary.evidence_source_summary ?? '';

  /* ── Chain state ─────────────────────────────────────────────── */
  const telemetryOk = (counts?.telemetry_events ?? 0) > 0 || !!summary.last_telemetry_at;
  const detectionOk = (counts?.detections ?? 0) > 0 || !!summary.last_detection_at;
  const alertOk = summary.active_alerts_count > 0 || (counts?.active_alerts ?? 0) > 0;
  const incidentOk =
    summary.active_incidents_count > 0 || (counts?.open_incidents ?? 0) > 0;
  const responseActionOk = responseActionsCount !== null ? responseActionsCount > 0 : false;
  const packageExists = packages.length > 0;
  // NOTE: the old incident/response-action readiness gate has been removed from the
  // Create button entirely. Incident availability is a concern of the creation modal
  // (which shows a truthful empty state), never of whether the primary button can be
  // pressed. `incidentOk`/`responseActionOk` remain in use by getBlocker() below.

  /* ── Create-permission resolution (backend-authoritative) ────────
   * The permission is a tri-state. We deliberately distinguish four button states so a
   * faded button always has one truthful cause:
   *   - resolving   : still fetching runtime/exports; show a short loading state, never
   *                   a "denied" state, for an as-yet-unknown can_export.
   *   - undetermined: the exports request failed, so permission could not be read; block
   *                   submission behind the retry/error state (never call the user denied).
   *   - denied      : the backend explicitly returned can_export === false.
   *   - granted     : can_export === true, OR a successful response that simply omitted
   *                   the field (an absent field must not permanently deny an authorized
   *                   user — the POST is still enforced server-side and fails closed).
   */
  const permissionResolving = runtimeLoading || dataLoading;
  const permissionUndetermined = !permissionResolving && Boolean(loadError);
  const permissionDenied = !permissionResolving && !loadError && canExport === false;
  const createDisabled = creating || permissionResolving || permissionUndetermined || permissionDenied;

  // Focus returns to the Create button whenever the modal closes (cancel, Escape,
  // backdrop, success, or failure-then-cancel), satisfying the dialog focus contract.
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const modalWasOpenRef = useRef(false);
  useEffect(() => {
    if (showCreateModal) {
      modalWasOpenRef.current = true;
    } else if (modalWasOpenRef.current) {
      modalWasOpenRef.current = false;
      createButtonRef.current?.focus();
    }
  }, [showCreateModal]);

  /* ── Canonical exports refresh (single source of package rows) ──
   * One place that fetches the same-origin /api/exports proxy, applies the
   * backend-authoritative package rows + metrics + retention + permission, and
   * returns the loaded rows. Used by the create-confirm loop and the async
   * progress poller so both always reflect the real backend, never a synthetic
   * row. Fails soft (returns null) so callers can retry with bounded backoff. */
  const loadExportsOnce = useCallback(async (): Promise<EvidencePackage[] | null> => {
    const exportsParams = new URLSearchParams();
    if (urlPackageId) exportsParams.set('package_id', urlPackageId);
    if (urlActionId) exportsParams.set('action_id', urlActionId);
    if (urlIncidentId) exportsParams.set('incident_id', urlIncidentId);
    const qs = exportsParams.toString();
    const url = qs ? `/api/exports?${qs}` : '/api/exports';
    let res: Response;
    try {
      res = await fetch(url, { headers: authHeaders(), cache: 'no-store' });
    } catch {
      return null;
    }
    if (!res.ok) return null;
    const p = (await res.json().catch(() => ({}))) as {
      exports?: EvidencePackage[];
      metrics?: EvidenceMetrics;
      retention_status?: string;
      can_export?: boolean;
    };
    const loaded = p.exports ?? [];
    setPackages(loaded);
    setMetrics(p.metrics ?? null);
    setRetentionStatus(p.retention_status ?? p.metrics?.retention_status ?? 'Unknown');
    setCanExport(typeof p.can_export === 'boolean' ? p.can_export : undefined);
    setLoadError('');
    setLastRefreshAt(new Date().toISOString());
    return loaded;
  }, [authHeaders, urlPackageId, urlActionId, urlIncidentId]);

  /* ── Data loading ────────────────────────────────────────────── */
  useEffect(() => {
    if (runtimeLoading) return;
    setDataLoading(true);
    const hdrs = authHeaders();

    async function loadAll() {
      try {
        const exportsParams = new URLSearchParams();
      if (urlPackageId) exportsParams.set('package_id', urlPackageId);
      if (urlActionId) exportsParams.set('action_id', urlActionId);
      if (urlIncidentId) exportsParams.set('incident_id', urlIncidentId);
      const exportsParamStr = exportsParams.toString();
      const exportsUrl = exportsParamStr ? `/api/exports?${exportsParamStr}` : '/api/exports';

      const [pkgRes, auditRes, raRes] = await Promise.allSettled([
          fetch(exportsUrl, { headers: hdrs, cache: 'no-store' }),
          fetch('/api/events', { headers: hdrs, cache: 'no-store' }),
          fetch(`/api/response/actions?limit=50`, { headers: hdrs, cache: 'no-store' }),
        ]);

        if (pkgRes.status === 'fulfilled' && pkgRes.value.ok) {
          const p = (await pkgRes.value.json()) as {
            exports?: EvidencePackage[];
            metrics?: EvidenceMetrics;
            retention_status?: string;
            can_export?: boolean;
          };
          const loaded = p.exports ?? [];
          setPackages(loaded);
          setMetrics(p.metrics ?? null);
          // Retention + permission are backend-authoritative — never hardcoded.
          setRetentionStatus(p.retention_status ?? p.metrics?.retention_status ?? 'Unknown');
          // Preserve the tri-state: only an explicit boolean is a decision. An absent
          // field stays `undefined` so an authorized user is never permanently denied
          // by a missing key (the POST is still enforced server-side).
          setCanExport(typeof p.can_export === 'boolean' ? p.can_export : undefined);
          setLoadError('');
          setLastRefreshAt(new Date().toISOString());
          // Auto-select package from URL params
          if (urlPackageId) {
            const matched = loaded.find((pkg) => pkg.id === urlPackageId);
            if (matched) setSelectedPkgId(matched.id);
          } else if (urlActionId) {
            const matched = loaded.find((pkg) => pkg.response_action_id === urlActionId);
            if (matched) setSelectedPkgId(matched.id);
          } else if (urlIncidentId) {
            const matched = loaded.find((pkg) => pkg.incident_id === urlIncidentId);
            if (matched) setSelectedPkgId(matched.id);
          }
        } else {
          // Evidence packages could not be loaded — surface a retryable error state.
          setLoadError('Evidence packages could not be loaded.');
        }

        if (auditRes.status === 'fulfilled' && auditRes.value.ok) {
          const a = (await auditRes.value.json()) as {
            events?: AuditRow[];
            audit_logs?: AuditRow[];
            total?: number;
            capped?: boolean;
          };
          const rows = a.events ?? a.audit_logs ?? [];
          setAuditRows(rows);
          // Truthful lifetime total + capped flag from the backend so the summary
          // card never presents a 500-row page cap as the exact total.
          setAuditTotal(typeof a.total === 'number' ? a.total : rows.length);
          setAuditCapped(Boolean(a.capped));
          setAuditUnavailable('');
        } else {
          setAuditUnavailable('Audit log feed unavailable from current workspace endpoint.');
        }

        if (raRes.status === 'fulfilled' && raRes.value.ok) {
          const ra = (await raRes.value.json()) as {
            actions?: unknown[];
            response_actions?: unknown[];
          };
          const actions = ra.actions ?? ra.response_actions ?? [];
          // Count only real response actions. AI recommendation reviews are immutable
          // human-review records (never executed), so they must not flip the evidence
          // package readiness gate that expects an actual response_action to exist.
          const materialActions = Array.isArray(actions)
            ? actions.filter(
                (a) => (a as { record_type?: string })?.record_type !== 'ai_recommendation_review',
              )
            : [];
          setResponseActionsCount(materialActions.length);
        } else {
          setResponseActionsCount(0);
        }
      } finally {
        setDataLoading(false);
      }
    }

    void loadAll();
  }, [authHeaders, runtimeLoading, urlPackageId, urlActionId, urlIncidentId, reloadKey]);

  // Fetch the full package report for the selected package so the agent card can
  // show its real completeness checklist, files, and integrity — all backend values.
  //
  // Stale-while-refresh: this effect re-runs whenever `authHeaders` changes (its
  // identity flips as the async CSRF/session tokens resolve ~1s after mount) or a
  // recovery action bumps `reloadKey`. It must NOT clear the authoritative detail
  // on those refreshes — the reducer keeps the last detail visible and only drops
  // it when the SELECTED package actually changes. A failed refresh keeps the
  // last-known detail (+ a warning) rather than nulling valid recovery actions.
  useEffect(() => {
    const id = selectedPkgId;
    // Reconcile the reducer's selection identity. A no-op when `id` is unchanged
    // (an `authHeaders`/`reloadKey`-driven re-run), so the detail is preserved.
    dispatchDetail({ type: 'select', id });
    if (!id) return;
    let cancelled = false;
    dispatchDetail({ type: 'refreshStart', id });
    (async () => {
      try {
        const res = await fetch(`/api/exports/${encodeURIComponent(id)}`, {
          headers: authHeaders(),
          cache: 'no-store',
        });
        if (cancelled) return;
        if (res.ok) {
          const body = (await res.json()) as { export?: PackageDetail };
          dispatchDetail({ type: 'refreshSuccess', id, detail: body.export ?? null });
        } else {
          dispatchDetail({ type: 'refreshError', id, message: 'Package status could not be refreshed.' });
        }
      } catch {
        if (!cancelled) {
          dispatchDetail({ type: 'refreshError', id, message: 'Package status could not be refreshed.' });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedPkgId, authHeaders, reloadKey]);

  /* ── Asynchronous-progress polling ───────────────────────────────
   * Only runs while at least one package is still in a non-terminal generation
   * state (Queued/Building/Hashing/Verifying). The current backend generates
   * synchronously, so freshly created packages arrive terminal and this stays
   * dormant — but if a worker ever leaves a row in-progress, the row, metrics,
   * selected-package card and last-refreshed time advance truthfully instead of
   * going stale. Ticks pause while the tab is hidden and stop the moment every
   * package reaches a terminal state — never an unbounded-frequency loop. */
  const hasInFlightPackage = useMemo(
    () => packages.some(isPackageInProgress),
    [packages],
  );
  useEffect(() => {
    if (!hasInFlightPackage) return;
    let cancelled = false;
    let inFlight = false; // never overlap a refresh with the next tick
    const POLL_MS = 5000;
    const tick = async () => {
      if (cancelled || inFlight || (typeof document !== 'undefined' && document.hidden)) return;
      inFlight = true;
      try {
        await loadExportsOnce();
      } finally {
        inFlight = false;
      }
    };
    const intervalId = window.setInterval(() => {
      void tick();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [hasInFlightPackage, loadExportsOnce]);

  async function verifyPackage(pkg: EvidencePackage) {
    if (!pkg.id) return;
    setMessage('');
    setDiagnostics(null);
    setRecovery(null);
    setVerifyingId(pkg.id);
    try {
      const res = await fetch(`/api/exports/${encodeURIComponent(pkg.id)}/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        cache: 'no-store',
      });
      const body = (await res.json().catch(() => ({}))) as {
        integrity_status?: string;
        verification?: { valid?: boolean; files_failed?: string[] };
        detail?: unknown;
      };
      if (res.ok) {
        const ok = body.verification?.valid;
        setMessage(
          ok
            ? 'Integrity verified: content matches the generated package.'
            : `Integrity check failed: ${body.verification?.files_failed?.length ?? 0} file(s) did not match.`,
        );
        // Reload so the persisted integrity_status and metrics refresh from the backend.
        setReloadKey((k) => k + 1);
      } else {
        // Never surface the raw {"error":...} envelope — friendly text + a
        // technical code kept in the expandable diagnostics disclosure.
        const view = friendlyPackageError(body, 'Verification could not be completed.');
        setMessage(view.message);
        setDiagnostics(view.code);
      }
    } catch {
      setMessage('Verification could not be completed: network error.');
    } finally {
      setVerifyingId('');
    }
  }

  async function downloadManifest(pkg: EvidencePackage) {
    if (!pkg.id) return;
    setMessage('');
    setDiagnostics(null);
    setRecovery(null);
    let resp: Response;
    try {
      resp = await fetch(`/api/exports/${encodeURIComponent(pkg.id)}/manifest`, {
        headers: authHeaders(),
        cache: 'no-store',
      });
    } catch {
      setMessage('Manifest download could not be completed: network error.');
      return;
    }
    if (!resp.ok) {
      const errBody = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
      const view = friendlyPackageError(errBody, 'Manifest download could not be completed.');
      setMessage(view.message);
      setDiagnostics(view.code);
      return;
    }
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = `evidence-manifest-${pkg.id}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  }

  // Recovery flow for a Manifest-Missing package. The backend rebuilds a
  // retrievable manifest from the package's exact stored bytes, persists it, and
  // returns the canonical state; we then reload so every state (Hash Verification,
  // SHA-256, Verify/Download gating) refreshes from the backend, never guessed here.
  async function generateManifest(pkg: EvidencePackage) {
    if (!pkg.id) return;
    setMessage('');
    setDiagnostics(null);
    setRecovery(null);
    setGeneratingId(pkg.id);
    try {
      const res = await fetch(`/api/exports/${encodeURIComponent(pkg.id)}/manifest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        cache: 'no-store',
      });
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (res.ok) {
        setRecovery({
          id: pkg.id,
          phase: 'success',
          message: 'Manifest generated. This package is ready for integrity verification.',
        });
        setMessage('Manifest generated. This package is ready for integrity verification.');
        // Reload so the persisted manifest reference / integrity state refresh.
        setReloadKey((k) => k + 1);
      } else {
        const view = friendlyPackageError(body, 'Manifest could not be generated.');
        setRecovery({ id: pkg.id, phase: 'error', message: 'Manifest could not be generated.' });
        setMessage(view.message);
        setDiagnostics(view.code);
      }
    } catch {
      setRecovery({ id: pkg.id, phase: 'error', message: 'Manifest could not be generated.' });
      setMessage('Manifest could not be generated: network error.');
    } finally {
      setGeneratingId('');
    }
  }

  async function copyHash(pkg: EvidencePackage) {
    const hash = packageHash(pkg);
    if (!hash) return;
    const ok = await copyToClipboard(hash);
    if (ok) {
      setCopiedHash(pkg.id);
      setTimeout(() => setCopiedHash(''), 1500);
    }
  }

  // Confirm a freshly created/returned package id is actually present in the
  // canonical list (or directly fetchable by id) before we rely on the toast.
  // Bounded backoff absorbs read-replica / transaction-visibility / deployment
  // latency without an infinite poll: at most a handful of tries with growing
  // delays, then it gives up so the caller can show a truthful "accepted but not
  // yet loaded" message. Never synthesizes a row.
  const confirmPackageVisible = useCallback(
    async (newId: string): Promise<boolean> => {
      if (!newId) return false;
      const delaysMs = [0, 300, 600, 1200, 2400];
      for (const delay of delaysMs) {
        if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
        const loaded = await loadExportsOnce();
        if (loaded && loaded.some((pkg) => pkg.id === newId)) return true;
        // Fallback: fetch the package directly by id — it is visible even if the
        // list projection lags behind the just-committed row.
        try {
          const res = await fetch(`/api/exports/${encodeURIComponent(newId)}`, {
            headers: authHeaders(),
            cache: 'no-store',
          });
          if (res.ok) {
            const body = (await res.json().catch(() => ({}))) as { export?: PackageDetail };
            if (body.export?.id === newId) return true;
          }
        } catch {
          // keep retrying within the bounded loop
        }
      }
      return false;
    },
    [authHeaders, loadExportsOnce],
  );

  async function createPackage(incidentId?: string) {
    // Guard against accidental duplicate submissions and against an explicitly
    // denied user. The backend export is idempotent for the same incident+scope,
    // but we also disable re-entry here. `permissionDenied` is strict (=== false),
    // so an unknown/undefined permission does not block the attempt here — the POST
    // is still authorized server-side and fails closed if the user truly lacks it.
    if (creating || permissionDenied) return;
    setMessage('');
    setCreating(true);
    try {
      const resolvedIncidentId =
        incidentId ||
        packages.find((pkg) => pkg.incident_id)?.incident_id ||
        ((runtime as Record<string, unknown> | undefined)?.latest_incident_id as string | undefined) ||
        ((summary as Record<string, unknown> | undefined)?.latest_incident_id as string | undefined) ||
        ((summary as Record<string, unknown> | undefined)?.last_incident_id as string | undefined);

      if (!resolvedIncidentId) {
        // Truthful: nothing to package. The modal already surfaces this empty state;
        // this guards the direct-call path so we never POST without an incident.
        setMessage('No incidents are currently available for evidence packaging.');
        return;
      }

      // Route through the same-origin /api/exports proxy — the SAME transport the
      // list GET uses — so the created package is persisted in the backend and
      // workspace the list reads from (not a browser-exposed NEXT_PUBLIC_API_URL
      // that may be unset or a different deployment). Exactly the supported
      // proof-bundle contract: the incident id + include_raw_events. No
      // browser-generated ids, hashes, completeness, or client-asserted authz.
      let res: Response;
      try {
        res = await fetch('/api/exports/proof-bundle', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ incident_id: resolvedIncidentId, include_raw_events: true }),
          cache: 'no-store',
        });
      } catch {
        setMessage('Evidence package creation failed: network error.');
        return;
      }
      const payload = (await res.json().catch(() => ({}))) as {
        package_id?: string;
        export_job_id?: string;
        id?: string;
        status?: string;
        created?: boolean;
        detail?: unknown;
        error_message?: string;
        error?: string;
        code?: string;
      };

      if (!res.ok) {
        // Fail-closed messaging. A 503 means the generation subsystem (worker /
        // storage) is unavailable and NOTHING was persisted — never a success
        // toast, and never a claim that a package is export-ready.
        if (res.status === 503) {
          setMessage(
            'Package generation is waiting for an available worker. No evidence package was created yet — please retry shortly.',
          );
        } else {
          // Surface a backend-safe message only — never a raw object or stack trace.
          setMessage(safeErrorMessage(payload.detail ?? payload.error_message, 'Export failed.'));
        }
        return;
      }

      // Canonical identity: the real persisted package/export-job id. We never
      // synthesize a row — the list re-fetch is the single source of truth, so an
      // idempotent response cannot produce a duplicate.
      const newId = payload.package_id ?? payload.export_job_id ?? payload.id ?? '';
      const backendStatus = String(payload.status ?? '').toLowerCase();

      // A persisted-but-failed row is visible in the list, but must never read as
      // success. Show it truthfully and still select it so the user can inspect.
      if (backendStatus === 'failed') {
        setMessage('Evidence package generation failed. It is recorded as Failed — open it for details or retry.');
        setShowCreateModal(false);
        if (newId) setSelectedPkgId(newId);
        await confirmPackageVisible(newId);
        setReloadKey((k) => k + 1);
        return;
      }

      // Status-specific truthful success messages.
      if (payload.created === false) {
        setMessage('An evidence package already exists for this incident and evidence scope.');
      } else if (backendStatus === 'completed') {
        setMessage('Evidence package created.');
      } else {
        // Asynchronous generation (queued/building/hashing/verifying).
        setMessage('Evidence package queued.');
      }
      setShowCreateModal(false);
      // Select the created — or existing idempotent — package by the backend id.
      if (newId) setSelectedPkgId(newId);

      // Reliable refresh: confirm the returned id actually landed in the canonical
      // list (with a bounded direct-by-id fallback). If it genuinely cannot be
      // loaded yet, downgrade to a truthful "accepted but not loaded" message
      // rather than leaving a success toast with no visible row.
      const appeared = await confirmPackageVisible(newId);
      if (!appeared) {
        setMessage(
          'Evidence package was accepted, but its status could not yet be loaded. Refresh to check its progress.',
        );
      }
      // Full reload so the audit feed, response-action count, metrics, retention
      // and integrity all re-sync from the backend.
      setReloadKey((k) => k + 1);
    } finally {
      setCreating(false);
    }
  }

  // Fallback recovery for a Manifest-Missing package: create a NEW superseding
  // package for the same incident when an in-place manifest cannot safely be
  // generated. The original package is preserved untouched (it becomes superseded)
  // — historical evidence is never rewritten. Backend-authoritative: the browser
  // only triggers it, then selects and reloads the resulting package.
  async function regeneratePackage(pkg: EvidencePackage) {
    if (!pkg.id) return;
    setMessage('');
    setDiagnostics(null);
    setRecovery(null);
    setRegeneratingId(pkg.id);
    try {
      let res: Response;
      try {
        res = await fetch(`/api/exports/${encodeURIComponent(pkg.id)}/regenerate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          cache: 'no-store',
        });
      } catch {
        setMessage('Package could not be regenerated: network error.');
        return;
      }
      const body = (await res.json().catch(() => ({}))) as {
        package_id?: string;
        export_job_id?: string;
        id?: string;
        status?: string;
        superseded_package_id?: string;
        detail?: unknown;
        error_message?: string;
        error?: string;
        code?: string;
      };
      if (!res.ok) {
        const view = friendlyPackageError(body, 'Package could not be regenerated.');
        setMessage(view.message);
        setDiagnostics(view.code);
        return;
      }
      const newId = body.package_id ?? body.export_job_id ?? body.id ?? '';
      const backendStatus = String(body.status ?? '').toLowerCase();
      if (backendStatus === 'failed') {
        setMessage('Regenerated package generation failed. It is recorded as Failed — open it for details or retry.');
        if (newId) setSelectedPkgId(newId);
        await confirmPackageVisible(newId);
        setReloadKey((k) => k + 1);
        return;
      }
      // Truthful success: a new superseding package now exists; the original is kept.
      setMessage('Superseding evidence package created. The original package is preserved as historical evidence.');
      if (newId) setSelectedPkgId(newId);
      const appeared = await confirmPackageVisible(newId);
      if (!appeared) {
        setMessage('Regeneration was accepted, but the new package could not yet be loaded. Refresh to check its progress.');
      }
      setReloadKey((k) => k + 1);
    } finally {
      setRegeneratingId('');
    }
  }

  async function downloadPackage(pkg: EvidencePackage) {
    setMessage('');
    if (!pkg.id) return;
    let resp: Response;
    try {
      resp = await fetch(`/api/exports/${pkg.id}/download`, {
        headers: authHeaders(),
        cache: 'no-store',
      });
    } catch {
      setMessage('Download failed: network error.');
      return;
    }
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({})) as Record<string, unknown>;
      const msg = String(errBody.detail ?? errBody.error ?? 'Download failed.');
      setMessage(`Download failed: ${msg}`);
      return;
    }
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = `evidence-package-${pkg.id}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  }

  /* ── Derived metrics (backend-authoritative) ─────────────────── */
  // Export Ready counts only canonically export-ready (verified + retrievable)
  // packages — never every completed row. Falls back to the per-row canonical
  // flag if the metrics block is unavailable.
  const exportReadyCount =
    metrics?.export_ready ??
    packages.filter((p) => Boolean(p.is_export_ready ?? p.export_ready)).length;
  const readyForVerificationCount =
    metrics?.ready_for_verification ??
    packages.filter((p) => Boolean(p.ready_for_verification)).length;
  const auditEventsDisplay =
    auditCapped ? `${auditTotal ?? auditRows.length}+` : String(auditTotal ?? auditRows.length);

  /* ── Search + integrity filtering (client-side over API rows) ── */
  const filteredPackages = useMemo(() => {
    const q = search.trim().toLowerCase();
    return packages.filter((pkg) => {
      if (integrityFilter && (pkg.integrity_status ?? '') !== integrityFilter) return false;
      if (!q) return true;
      const haystack = [
        pkg.package_number,
        pkg.id,
        pkg.incident_short_id,
        pkg.incident_id,
        pkg.scope_label,
        pkg.integrity_hash,
        pkg.integrity_status,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [packages, search, integrityFilter]);
  const filtersActive = Boolean(search.trim() || integrityFilter);

  /* ── Selected rows ───────────────────────────────────────────── */
  const selectedPkg = useMemo(
    () => packages.find((p) => p.id === selectedPkgId) ?? null,
    [packages, selectedPkgId],
  );
  const selectedAudit = useMemo(
    () => auditRows.find((r, i) => (r.id ?? String(i)) === selectedAuditId) ?? null,
    [auditRows, selectedAuditId],
  );

  // Never keep a selection for a package the active filters have hidden from the
  // table. If the package still exists but is filtered out, drop the selection so
  // the agent card and detail panel don't describe an off-screen row. (A package
  // that no longer exists at all leaves selection to resolve to null naturally.)
  useEffect(() => {
    if (!selectedPkgId) return;
    const inPackages = packages.some((p) => p.id === selectedPkgId);
    const inFiltered = filteredPackages.some((p) => p.id === selectedPkgId);
    if (inPackages && !inFiltered) setSelectedPkgId('');
  }, [filteredPackages, packages, selectedPkgId]);

  /* ── Empty state / blocker ───────────────────────────────────── */
  type Blocker = {
    title: string;
    body: string;
    ctaHref?: string;
    ctaLabel?: string;
  };

  function getBlocker(): Blocker | null {
    if (dataLoading || runtimeLoading) return null;

    // A completed package means the full chain succeeded. Never show a chain-step
    // blocker when evidence already exists — the chain state counters (active alerts,
    // active incidents) may be zero after resolution even though the package is real.
    if (packageExists) return null;

    // When a URL param identifies a specific package/action/incident but it hasn't
    // loaded yet (e.g. first load before fetch completes), don't show a blocker.
    if (urlPackageId || urlActionId || urlIncidentId) return null;

    // When a response action exists the full chain (telemetry → detection → alert → incident → action)
    // must be present by definition. Skip lower-level blockers to avoid false negatives.
    if (!responseActionOk) {
      if (!telemetryOk) {
        return {
          title: 'No evidence packages yet',
          body: 'No evidence package can be created because no telemetry has been received.',
          ctaHref: '/threat',
          ctaLabel: 'View Threat Monitoring',
        };
      }
      if (!detectionOk) {
        return {
          title: 'No evidence packages yet',
          body: 'Telemetry has been received, but no detection has been generated yet.',
          ctaHref: '/threat',
          ctaLabel: 'Run Detection',
        };
      }
      if (!alertOk) {
        return {
          title: 'No evidence packages yet',
          body: 'Detections exist, but no alert has been opened yet.',
          ctaHref: '/alerts',
          ctaLabel: 'Open Alert',
        };
      }
      if (!incidentOk) {
        return {
          title: 'No evidence packages yet',
          body: 'Alerts exist, but no incident has been opened yet.',
          ctaHref: '/incidents',
          ctaLabel: 'Open Incident',
        };
      }
      return {
        title: 'Evidence package not ready',
        body: 'An incident exists, but no response action has been recommended or recorded yet.',
        ctaHref: '/response-actions',
        ctaLabel: 'Recommend Response',
      };
    }

    return {
      title: 'No evidence package exported yet',
      body: 'A response action exists but no evidence package has been exported yet. Click "Evidence Export" from a response action to create one.',
      ctaHref: '/response-actions',
      ctaLabel: 'Go to Response Actions',
    };
  }

  const blocker = getBlocker();
  const showBlocker = activeTab === 'packages' && !!blocker;

  /* ── Render ──────────────────────────────────────────────────── */
  return (
    <section className="featureSection">

      {/* ── Page header ─────────────────────────────────────────── */}
      <div
        className="listHeader"
        style={{
          marginBottom: '1.5rem',
          alignItems: 'flex-start',
          flexWrap: 'wrap',
          gap: '0.75rem',
        }}
      >
        <div>
          <h1 style={{ margin: 0, marginBottom: '0.25rem' }}>Evidence &amp; Audit</h1>
          <p className="muted" style={{ margin: 0 }}>
            Generate tamper-evident incident packages, verify file integrity, and review workspace audit activity.
          </p>
        </div>
        {/* Always rendered so a faded button can always explain itself. It disables ONLY
            for an explicit permission denial, an unresolved/failed permission read, or an
            in-flight submission — never for an empty incident list. Clicking opens the
            creation flow, which surfaces a truthful empty state when no incidents are
            available for packaging. */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.4rem' }}>
          <button
            ref={createButtonRef}
            type="button"
            className="btn btn-primary"
            disabled={createDisabled}
            aria-busy={creating || permissionResolving}
            title={
              permissionDenied
                ? 'You do not have permission to create evidence packages.'
                : creating
                  ? 'Creating…'
                  : permissionResolving
                    ? 'Checking your permissions…'
                    : permissionUndetermined
                      ? 'Evidence service is unavailable. Retry to continue.'
                      : undefined
            }
            onClick={() => setShowCreateModal(true)}
          >
            {permissionResolving ? 'Checking permission…' : creating ? 'Creating…' : 'Create Evidence Package'}
          </button>
          {/* Permission/API error is a state distinct from a denial: the backend never
              returned can_export === false — the permission simply could not be READ
              (exports request failed / service unreachable). It must be surfaced as an
              explicit, RECOVERABLE condition with a Retry, never rendered as a confirmed
              denial that permanently fades the button for an authorized admin. */}
          {permissionUndetermined ? (
            <div
              role="status"
              aria-live="polite"
              style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8rem' }}
            >
              <span style={{ color: '#fbbf24' }}>Permission could not be verified.</span>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ padding: '0.15rem 0.6rem', fontSize: '0.78rem' }}
                onClick={() => setReloadKey((k) => k + 1)}
              >
                Retry
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/* ── Metric row ──────────────────────────────────────────── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: '1rem',
          marginBottom: '1.5rem',
        }}
      >
        <MetricTile label="Evidence Packages" value={metrics?.total_packages ?? packages.length} />
        <MetricTile
          label="Audit Events"
          value={auditEventsDisplay}
          meta={auditCapped ? 'Latest 500 shown' : 'total events'}
        />
        <MetricTile
          label="Export Ready"
          value={exportReadyCount}
          meta={
            readyForVerificationCount > 0
              ? `${readyForVerificationCount} awaiting verification`
              : exportReadyCount > 0
                ? 'verified packages'
                : 'none verified'
          }
        />
        <MetricTile label="Retention Status" value={retentionStatus} />
      </div>

      {/* ── Tab strip ───────────────────────────────────────────── */}
      <TabStrip
        tabs={[
          { key: 'packages', label: 'Evidence Packages' },
          { key: 'audit', label: 'Audit Logs' },
        ]}
        active={activeTab}
        onChange={(k) => setActiveTab(k as 'packages' | 'audit')}
      />

      {message ? (
        <p className="statusLine" style={{ marginBottom: diagnostics ? '0.35rem' : '1rem' }}>
          {message}
        </p>
      ) : null}
      {diagnostics ? (
        <details style={{ marginBottom: '1rem' }}>
          <summary style={{ cursor: 'pointer', fontSize: '0.72rem', color: '#94a3b8' }}>
            Technical details
          </summary>
          <code style={{ fontSize: '0.72rem', color: '#94a3b8' }}>{diagnostics}</code>
        </details>
      ) : null}

      {/* ── Evidence Packages tab ────────────────────────────────── */}
      {activeTab === 'packages' &&
        (showBlocker && blocker ? (
          <EmptyStateBlocker
            title={blocker.title}
            body={blocker.body}
            ctaHref={blocker.ctaHref}
            ctaLabel={blocker.ctaLabel}
          />
        ) : loadError ? (
          <div className="dataCard sharedSurfaceCard" style={{ padding: '2rem', textAlign: 'center' }}>
            <h3 style={{ marginTop: 0 }}>Evidence packages could not be loaded.</h3>
            <p className="muted" style={{ marginBottom: '1rem' }}>
              The evidence service did not respond. No package data is shown to avoid presenting stale or missing evidence as complete.
            </p>
            <button type="button" className="btn btn-primary" onClick={() => setReloadKey((k) => k + 1)}>
              Retry
            </button>
          </div>
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 340px',
              gap: '1rem',
              alignItems: 'start',
            }}
          >
            <div>
              {/* ── Search + integrity filter toolbar ──────────────── */}
              <div
                style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.75rem', alignItems: 'center' }}
              >
                <input
                  type="search"
                  aria-label="Search evidence packages"
                  placeholder="Search package ID, incident, scope, or SHA-256"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="input"
                  style={{
                    flex: '1 1 220px',
                    minWidth: '180px',
                    padding: '0.35rem 0.6rem',
                    fontSize: '0.8rem',
                    background: 'rgba(15,23,42,0.6)',
                    border: '1px solid rgba(148,163,184,0.2)',
                    borderRadius: '6px',
                    color: 'inherit',
                  }}
                />
                <label className="sr-only" htmlFor="integrity-filter">
                  Filter by integrity status
                </label>
                <select
                  id="integrity-filter"
                  aria-label="Filter by integrity status"
                  value={integrityFilter}
                  onChange={(e) => setIntegrityFilter(e.target.value)}
                  style={{
                    padding: '0.35rem 0.6rem',
                    fontSize: '0.8rem',
                    background: 'rgba(15,23,42,0.6)',
                    border: '1px solid rgba(148,163,184,0.2)',
                    borderRadius: '6px',
                    color: 'inherit',
                  }}
                >
                  {INTEGRITY_FILTER_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                {lastRefreshAt ? (
                  <span className="tableMeta" style={{ marginLeft: 'auto', fontSize: '0.72rem' }}>
                    Last refreshed {fmt(lastRefreshAt)}
                  </span>
                ) : null}
              </div>

              <TableShell headers={[...PKG_TABLE_HEADERS]} compact>
                {dataLoading && packages.length === 0 ? (
                  [0, 1, 2].map((i) => (
                    <tr key={`skeleton-${i}`} aria-hidden="true">
                      {PKG_TABLE_HEADERS.map((h) => (
                        <td key={h} style={{ padding: '0.6rem 0.5rem' }}>
                          <span className="skeletonRow" style={{ display: 'block' }} />
                        </td>
                      ))}
                    </tr>
                  ))
                ) : filteredPackages.length === 0 ? (
                  <tr>
                    <td
                      colSpan={PKG_TABLE_HEADERS.length}
                      style={{
                        textAlign: 'center',
                        color: 'var(--color-muted, #94a3b8)',
                        padding: '2rem',
                      }}
                    >
                      {filtersActive ? 'No packages match these filters.' : 'No evidence packages yet.'}
                    </td>
                  </tr>
                ) : (
                  filteredPackages.map((pkg) => {
                    const isSelected = pkg.id === selectedPkgId;
                    const integ = integrityPill(pkg);
                    return (
                      <tr
                        key={pkg.id}
                        aria-selected={isSelected}
                        onClick={() => setSelectedPkgId(isSelected ? '' : pkg.id)}
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            setSelectedPkgId(isSelected ? '' : pkg.id);
                          }
                        }}
                        style={{
                          cursor: 'pointer',
                          background: isSelected ? 'rgba(59,130,246,0.12)' : undefined,
                          boxShadow: isSelected ? 'inset 3px 0 0 #3b82f6' : undefined,
                        }}
                      >
                        {/* Package ID — human-readable number; UUID stays in the tooltip */}
                        <td
                          style={{ fontFamily: 'monospace', fontSize: '0.78rem', whiteSpace: 'nowrap', fontWeight: 600 }}
                          title={`Internal ID ${pkg.id}`}
                        >
                          {pkg.package_number ?? '-'}
                          <span className="sr-only"> (internal id {pkg.id})</span>
                        </td>
                        {/* Incident / Scope — readable incident number, not a raw UUID */}
                        <td style={{ fontSize: '0.8rem', whiteSpace: 'nowrap' }} title={pkg.incident_id ?? undefined}>
                          {pkg.incident_short_id ?? pkg.scope_label ?? '-'}
                        </td>
                        <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>
                          {fmt(pkg.created_at)}
                        </td>
                        <td
                          style={{
                            fontSize: '0.75rem',
                            maxWidth: '180px',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {includesLabel(pkg)}
                        </td>
                        <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>
                          {fmtSize(pkg.size_bytes)}
                        </td>
                        {/* SHA-256 — truthful hash state; never "Pending" beside a hash pill */}
                        <td style={{ fontSize: '0.72rem', whiteSpace: 'nowrap' }}>
                          <HashCell
                            pkg={pkg}
                            copied={copiedHash === pkg.id}
                            onCopy={() => void copyHash(pkg)}
                          />
                        </td>
                        {/* Integrity status */}
                        <td>
                          <StatusPill label={integ.label} variant={integ.variant} />
                        </td>
                        {/* Actions — primary View + overflow menu, gated by allowed_actions */}
                        <td>
                          <RowActionMenu
                            pkg={pkg}
                            open={openMenuId === pkg.id}
                            verifying={verifyingId === pkg.id}
                            generating={generatingId === pkg.id}
                            copied={copiedHash === pkg.id}
                            onToggle={() => setOpenMenuId((cur) => (cur === pkg.id ? '' : pkg.id))}
                            onClose={() => setOpenMenuId('')}
                            onView={() => setSelectedPkgId(pkg.id)}
                            onDownload={() => void downloadPackage(pkg)}
                            onManifest={() => void downloadManifest(pkg)}
                            onGenerateManifest={() => void generateManifest(pkg)}
                            onVerify={() => void verifyPackage(pkg)}
                            onCopyHash={() => void copyHash(pkg)}
                          />
                        </td>
                      </tr>
                    );
                  })
                )}
              </TableShell>

              {selectedPkg && (
                <div style={{ marginTop: '1rem' }}>
                  <PackageDetailPanel
                    pkg={selectedPkg}
                    detail={selectedDetail}
                    detailLoading={detailLoading}
                    detailRefreshing={detailRefreshing}
                    detailError={detailError}
                    workspaceEvidenceSource={workspaceEvidenceSource}
                    onDownload={downloadPackage}
                    onDownloadManifest={downloadManifest}
                    onGenerateManifest={generateManifest}
                    onRegeneratePackage={regeneratePackage}
                    onVerify={verifyPackage}
                    verifying={verifyingId === selectedPkg.id}
                    generating={generatingId === selectedPkg.id}
                    regenerating={regeneratingId === selectedPkg.id}
                    recovery={recovery && recovery.id === selectedPkg.id ? recovery : null}
                  />
                </div>
              )}
            </div>

            {/* ── Crypto-Auditing Clerk agent sidebar ─────────────── */}
            <CryptoAuditingClerkPanel
              metrics={metrics}
              selectedPkg={selectedPkg}
              detail={selectedDetail}
              detailLoading={detailLoading}
              lastRefreshAt={lastRefreshAt}
              onViewReport={() => {
                if (selectedPkg) setSelectedPkgId(selectedPkg.id);
              }}
            />
          </div>
        ))}

      {/* ── Audit Logs tab ──────────────────────────────────────── */}
      {activeTab === 'audit' && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: selectedAudit ? '1fr 360px' : '1fr',
            gap: '1rem',
            alignItems: 'start',
          }}
        >
          <div>
            {auditUnavailable ? (
              <p className="statusLine" style={{ marginBottom: '0.75rem' }}>
                {auditUnavailable}
              </p>
            ) : null}
            <TableShell headers={[...AUDIT_TABLE_HEADERS]} compact>
              {auditRows.length === 0 ? (
                <tr>
                  <td
                    colSpan={AUDIT_TABLE_HEADERS.length}
                    style={{
                      textAlign: 'center',
                      color: 'var(--color-muted, #94a3b8)',
                      padding: '2rem',
                    }}
                  >
                    No audit events recorded yet.
                  </td>
                </tr>
              ) : (
                auditRows.map((row, index) => {
                  const rowId = row.id ?? String(index);
                  const isSelected = rowId === selectedAuditId;
                  const evSrc = evidenceSourcePill(row.evidence_source_type ?? row.evidence_source, workspaceEvidenceSource);
                  const result = auditResultPill(row.result ?? row.status);
                  return (
                    <tr
                      key={rowId}
                      onClick={() => setSelectedAuditId(isSelected ? '' : rowId)}
                      style={{
                        cursor: 'pointer',
                        background: isSelected ? 'rgba(59,130,246,0.08)' : undefined,
                      }}
                    >
                      <td style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                        {fmt(row.timestamp ?? row.created_at)}
                      </td>
                      <td style={{ fontSize: '0.8rem' }}>
                        {row.actor ?? row.system ?? 'system'}
                      </td>
                      <td style={{ fontSize: '0.8rem' }}>
                        {row.action ?? row.event_type ?? '-'}
                      </td>
                      <td
                        style={{
                          fontSize: '0.78rem',
                          maxWidth: '140px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {row.target ?? row.target_id ?? row.object_id ?? '-'}
                      </td>
                      <td>
                        <StatusPill label={result.label} variant={result.variant} />
                      </td>
                      <td style={{ fontSize: '0.75rem' }}>
                        {row.source_ip ?? row.source ?? row.origin ?? '-'}
                      </td>
                      <td>
                        <StatusPill label={evSrc.label} variant={evSrc.variant} />
                      </td>
                    </tr>
                  );
                })
              )}
            </TableShell>
          </div>

          {selectedAudit && (
            <AuditDetailPanel row={selectedAudit} workspaceEvidenceSource={workspaceEvidenceSource} />
          )}
        </div>
      )}

      {/* ── Create Evidence Package flow ─────────────────────────── */}
      {showCreateModal && (
        <EvidencePackageCreateModal
          authHeaders={authHeaders}
          permissionDenied={permissionDenied}
          creating={creating}
          preselectIncidentId={
            packages.find((pkg) => pkg.incident_id)?.incident_id ?? urlIncidentId ?? ''
          }
          onCreate={(incidentId) => createPackage(incidentId)}
          onClose={() => setShowCreateModal(false)}
        />
      )}
    </section>
  );
}

/* ── Create Evidence Package modal ───────────────────────────────── */

type CreateModalIncident = {
  id: string;
  title?: string | null;
  summary?: string | null;
  status?: string | null;
  workflow_status?: string | null;
  severity?: string | null;
  created_at?: string | null;
  // Human-readable identifiers when the API provides them; the modal falls back to the
  // same INC-<uuid8> short id the backend derives elsewhere so labels stay consistent.
  incident_number?: string | null;
  incident_short_id?: string | null;
};

// The proof-bundle endpoint always bundles every currently-available evidence category
// for the incident — there is no server-side category selection — so the modal states
// that truthfully instead of offering a fake per-category picker.
const PROOF_BUNDLE_EVIDENCE_CATEGORIES = REQUIRED_ARTIFACTS;

// Best available human-readable incident identity. Prefers a canonical number, then a
// backend short id, then the same INC-<first 8 of uuid> fallback the API uses. The raw
// UUID is only ever the submitted API identifier, never the label.
function incidentIdentity(incident: CreateModalIncident): string {
  return (
    incident.incident_number?.trim() ||
    incident.incident_short_id?.trim() ||
    `INC-${incident.id.slice(0, 8)}`
  );
}

function incidentOptionLabel(incident: CreateModalIncident): string {
  const parts = [incidentIdentity(incident)];
  const title = incident.title?.trim() || incident.summary?.trim();
  if (title) parts.push(title);
  if (incident.severity) parts.push(String(incident.severity).toUpperCase());
  if (incident.status) parts.push(String(incident.status));
  return parts.join(' · ');
}

// Normalizes the /api/incidents envelope. The list endpoint returns { incidents: [...] },
// but we defensively accept the other known shapes (items / raw array) rather than
// silently dropping valid data — while still rejecting a malformed body.
function normalizeIncidentsPayload(body: unknown): CreateModalIncident[] | null {
  const raw = Array.isArray(body)
    ? body
    : Array.isArray((body as { incidents?: unknown })?.incidents)
      ? (body as { incidents: unknown[] }).incidents
      : Array.isArray((body as { items?: unknown })?.items)
        ? (body as { items: unknown[] }).items
        : null;
  if (raw === null) return null; // malformed — caller surfaces an error, never a false empty state
  return raw.filter(
    (i): i is CreateModalIncident => Boolean(i) && typeof (i as { id?: unknown }).id === 'string',
  );
}

// The creation flow always opens (the primary button never gates on incident
// availability). It fetches the workspace's real incidents and, when none are
// eligible, shows a truthful empty state rather than pretending a package can be
// built. Actual authorization is still enforced by the backend POST.
function EvidencePackageCreateModal({
  authHeaders,
  permissionDenied,
  creating,
  preselectIncidentId,
  onCreate,
  onClose,
}: {
  authHeaders: () => Record<string, string>;
  permissionDenied: boolean;
  creating: boolean;
  preselectIncidentId: string;
  onCreate: (incidentId: string) => Promise<void>;
  onClose: () => void;
}) {
  const [incidents, setIncidents] = useState<CreateModalIncident[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState(preselectIncidentId);
  const [reloadKey, setReloadKey] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const selectRef = useRef<HTMLSelectElement>(null);

  // Escape closes (unless a submission is in flight, which we must not abandon) and Tab is
  // trapped so keyboard focus can never leave the open dialog.
  const closeIfIdle = () => {
    if (!creating) onClose();
  };
  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.stopPropagation();
      closeIfIdle();
      return;
    }
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), select:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  // Move focus into the dialog when it opens so keyboard + screen-reader users start
  // inside it. (Focus is returned to the Create button by the parent on close.)
  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    (async () => {
      try {
        const res = await fetch('/api/incidents?limit=50', {
          headers: authHeaders(),
          cache: 'no-store',
        });
        if (cancelled) return;
        if (res.ok) {
          const rows = normalizeIncidentsPayload(await res.json().catch(() => null));
          if (rows === null) {
            setError('Incidents could not be loaded.');
            return;
          }
          setIncidents(rows);
          // Preselect the linked incident if it is present, otherwise the first row.
          setSelectedId((cur) => (cur && rows.some((r) => r.id === cur) ? cur : rows[0]?.id ?? ''));
        } else {
          setError('Incidents could not be loaded.');
        }
      } catch {
        if (!cancelled) setError('Incidents could not be loaded.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authHeaders, reloadKey]);

  const hasIncidents = incidents.length > 0;
  // When the selector appears, move focus to it — but only if the user has not already
  // tabbed elsewhere in the dialog (focus is still on the dialog container).
  useEffect(() => {
    if (!loading && !error && hasIncidents && document.activeElement === dialogRef.current) {
      selectRef.current?.focus();
    }
  }, [loading, error, hasIncidents]);

  const selectedIncident = incidents.find((i) => i.id === selectedId) ?? null;
  // Only an EXPLICIT denial blocks submission here; the backend is the real gate.
  const canSubmit = !permissionDenied && !creating && !loading && !error && !!selectedId;

  return (
    <div className="modalOverlay" onClick={closeIfIdle} onKeyDown={handleKeyDown}>
      <section
        ref={dialogRef}
        className="modalCard"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-evidence-package-title"
        aria-describedby="create-evidence-package-desc"
        tabIndex={-1}
        style={{ maxWidth: '480px', outline: 'none' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.75rem' }}>
          <div>
            <p className="sectionEyebrow" style={{ marginBottom: '0.15rem' }}>
              Evidence &amp; Audit
            </p>
            <h2 id="create-evidence-package-title" style={{ margin: 0, fontSize: '1.15rem' }}>
              Create Evidence Package
            </h2>
          </div>
          <button
            type="button"
            className="btn btn-ghost"
            aria-label="Close create evidence package dialog"
            onClick={closeIfIdle}
          >
            ✕
          </button>
        </div>

        <p id="create-evidence-package-desc" className="muted" style={{ margin: '0.5rem 0 1rem', fontSize: '0.85rem' }}>
          Select an incident and generate a tamper-evident package containing its available
          investigation and response evidence.
        </p>

        {/* Live region so loading/empty/error transitions are announced. */}
        <div aria-live="polite">
          {permissionDenied ? (
            <p className="statusLine" role="alert" style={{ fontSize: '0.85rem' }}>
              You do not have permission to create evidence packages.
            </p>
          ) : loading ? (
            <div aria-busy="true">
              <p className="muted" style={{ fontSize: '0.82rem', marginBottom: '0.5rem' }}>
                Loading incidents…
              </p>
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="skeletonRow"
                  aria-hidden="true"
                  style={{ display: 'block', height: '1.1rem', marginBottom: '0.4rem' }}
                />
              ))}
            </div>
          ) : error ? (
            <div role="alert">
              <p className="fieldError" style={{ marginBottom: '0.6rem' }}>
                {error}
              </p>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ fontSize: '0.8rem' }}
                onClick={() => setReloadKey((k) => k + 1)}
              >
                Retry
              </button>
            </div>
          ) : !hasIncidents ? (
            // Truthful empty state — the flow opened, but there is genuinely nothing to
            // package yet. Never present this as a package or as an error.
            <div>
              <h3 style={{ margin: '0 0 0.4rem', fontSize: '0.98rem' }}>No incidents available</h3>
              <p className="muted" style={{ fontSize: '0.85rem', marginBottom: '0.9rem' }}>
                No incidents are currently available for evidence packaging. Create or open an
                incident first, then return here to generate its evidence package.
              </p>
              <Link href="/incidents" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.8rem' }}>
                View Incidents
              </Link>
            </div>
          ) : (
            <div className="formField">
              <label htmlFor="create-evidence-incident">Incident to package</label>
              <select
                id="create-evidence-incident"
                ref={selectRef}
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
              >
                {incidents.map((incident) => (
                  <option key={incident.id} value={incident.id}>
                    {incidentOptionLabel(incident)}
                  </option>
                ))}
              </select>

              {/* Selected-incident identity, shown as labelled fields (a native <option>
                  can only render plain text). Severity/status/created are canonical
                  backend values, never re-derived here. */}
              {selectedIncident ? (
                <div
                  className="drawerMetaGrid"
                  style={{ marginTop: '0.9rem', gridTemplateColumns: '1fr 1fr' }}
                >
                  <div>
                    <p className="tableMeta" style={{ margin: 0 }}>Incident</p>
                    <p style={{ margin: 0, fontSize: '0.82rem', fontFamily: 'monospace' }}>
                      {incidentIdentity(selectedIncident)}
                    </p>
                  </div>
                  <div>
                    <p className="tableMeta" style={{ margin: 0 }}>Severity</p>
                    <p style={{ margin: 0, fontSize: '0.82rem' }}>
                      {selectedIncident.severity ? String(selectedIncident.severity).toUpperCase() : '—'}
                    </p>
                  </div>
                  <div>
                    <p className="tableMeta" style={{ margin: 0 }}>Status</p>
                    <p style={{ margin: 0, fontSize: '0.82rem' }}>
                      {selectedIncident.status ?? selectedIncident.workflow_status ?? '—'}
                    </p>
                  </div>
                  <div>
                    <p className="tableMeta" style={{ margin: 0 }}>Created</p>
                    <p style={{ margin: 0, fontSize: '0.82rem' }}>{fmt(selectedIncident.created_at)}</p>
                  </div>
                </div>
              ) : null}

              {/* Truthful category statement — the endpoint bundles all available
                  categories; there is no per-category selection to offer. */}
              <div style={{ marginTop: '0.9rem' }}>
                <p className="tableMeta" style={{ marginBottom: '0.3rem' }}>
                  This package will include all currently available evidence associated with the
                  selected incident.
                </p>
                <ul style={{ margin: 0, paddingLeft: '1.1rem', fontSize: '0.78rem', color: 'var(--color-muted, #94a3b8)' }}>
                  {PROOF_BUNDLE_EVIDENCE_CATEGORIES.map((category) => (
                    <li key={category}>{category}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>

        <div className="buttonRow" style={{ justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1.25rem' }}>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: '0.82rem' }}
            disabled={creating}
            onClick={closeIfIdle}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            style={{ fontSize: '0.82rem' }}
            disabled={!canSubmit}
            aria-busy={creating}
            title={
              permissionDenied
                ? 'You do not have permission to create evidence packages.'
                : creating
                  ? 'Creating…'
                  : !hasIncidents
                    ? 'No incidents are currently available for evidence packaging.'
                    : !selectedId
                      ? 'Select an incident to package.'
                      : undefined
            }
            onClick={() => {
              if (!canSubmit) return;
              void onCreate(selectedId);
            }}
          >
            {creating ? 'Creating…' : 'Create Package'}
          </button>
        </div>
      </section>
    </div>
  );
}

/* ── SHA-256 hash cell (truthful, never contradicts the integrity pill) ── */

function HashCell({
  pkg,
  copied,
  onCopy,
}: {
  pkg: EvidencePackage;
  copied: boolean;
  onCopy: () => void;
}) {
  const hash = packageHash(pkg);
  const state = pkg.hash_display_state ?? pkg.hash_state ?? (hash ? 'present' : 'not_generated');
  if (hash) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
        <span style={{ fontFamily: 'monospace' }} title={`SHA-256 ${hash}`}>
          {truncHash(hash)}
        </span>
        <span className="sr-only">Full SHA-256 hash {hash}</span>
        <button
          type="button"
          className="btn btn-secondary"
          aria-label={`Copy full SHA-256 hash for package ${pkg.package_number ?? pkg.id}`}
          style={{ fontSize: '0.65rem', padding: '0.05rem 0.35rem' }}
          onClick={(e) => {
            e.stopPropagation();
            onCopy();
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </span>
    );
  }
  if (state === 'generating') {
    return <span className="muted">Generating…</span>;
  }
  return <span className="muted">Not generated</span>;
}

/* ── Row action menu (primary View + overflow, gated by allowed_actions) ── */

function RowActionMenu({
  pkg,
  open,
  verifying,
  generating,
  copied,
  onToggle,
  onClose,
  onView,
  onDownload,
  onManifest,
  onGenerateManifest,
  onVerify,
  onCopyHash,
}: {
  pkg: EvidencePackage;
  open: boolean;
  verifying: boolean;
  generating: boolean;
  copied: boolean;
  onToggle: () => void;
  onClose: () => void;
  onView: () => void;
  onDownload: () => void;
  onManifest: () => void;
  onGenerateManifest: () => void;
  onVerify: () => void;
  onCopyHash: () => void;
}) {
  const aa = pkg.allowed_actions ?? {};
  const ready = isPackageReady(pkg);
  // Prefer backend-authoritative gating; fall back to readiness for older responses.
  const canDownload = aa.download ?? ready;
  const canManifest = aa.download_manifest ?? (ready && !pkg.is_legacy_export);
  const canVerify = aa.verify ?? (ready && !pkg.is_legacy_export);
  const canCopy = aa.copy_hash ?? Boolean(pkg.integrity_hash);
  // Recovery is backend-authoritative: offered only for a Manifest-Missing package.
  const canGenerate = aa.generate_manifest ?? (ready && Boolean(pkg.is_manifest_missing));

  type Item = { label: string; onClick: () => void; disabled: boolean; title?: string; hidden?: boolean };
  const items: Item[] = [
    {
      label: 'Download Package',
      onClick: onDownload,
      disabled: !canDownload,
      title: canDownload ? undefined : 'Package artifact is not available for download.',
    },
    {
      // Recovery entry point — only shown for a Manifest-Missing package.
      label: generating ? 'Generating manifest…' : 'Generate Manifest',
      onClick: onGenerateManifest,
      disabled: !canGenerate || generating,
      hidden: !canGenerate,
      title: 'Rebuild a retrievable integrity manifest from this package’s stored bytes.',
    },
    {
      label: 'Download Manifest',
      onClick: onManifest,
      disabled: !canManifest,
      title: canManifest ? undefined : 'No signed manifest exists for this package.',
    },
    {
      label: verifying ? 'Verifying…' : 'Verify Integrity',
      onClick: onVerify,
      disabled: !canVerify || verifying,
      title: canVerify
        ? undefined
        : pkg.is_manifest_missing
          ? 'This package has no retrievable manifest. Generate a manifest before verifying integrity.'
          : 'Verification requires a signed manifest.',
    },
    {
      label: copied ? 'Hash Copied' : 'Copy Package Hash',
      onClick: onCopyHash,
      disabled: !canCopy,
      title: canCopy ? undefined : 'No authoritative hash to copy yet.',
    },
  ].filter((item) => !item.hidden);

  return (
    <div style={{ position: 'relative', display: 'inline-flex', gap: '0.3rem', alignItems: 'center' }}>
      <button
        type="button"
        className="btn btn-secondary"
        style={{ fontSize: '0.72rem', padding: '0.15rem 0.5rem' }}
        onClick={(e) => {
          e.stopPropagation();
          onView();
        }}
      >
        View Package
      </button>
      <button
        type="button"
        className="btn btn-secondary"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`More actions for package ${pkg.package_number ?? pkg.id}`}
        style={{ fontSize: '0.85rem', padding: '0.1rem 0.4rem', lineHeight: 1 }}
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onClose();
        }}
      >
        ⋯
      </button>
      {open ? (
        <>
          {/* Click-away backdrop */}
          <div
            aria-hidden="true"
            onClick={(e) => {
              e.stopPropagation();
              onClose();
            }}
            style={{ position: 'fixed', inset: 0, zIndex: 40 }}
          />
          <div
            role="menu"
            aria-label="Package actions"
            style={{
              position: 'absolute',
              top: 'calc(100% + 4px)',
              right: 0,
              zIndex: 41,
              minWidth: '180px',
              background: '#0f172a',
              border: '1px solid rgba(148,163,184,0.25)',
              borderRadius: '8px',
              boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
              padding: '0.25rem',
            }}
          >
            {items.map((item) => (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                disabled={item.disabled}
                title={item.title}
                onClick={(e) => {
                  e.stopPropagation();
                  if (item.disabled) return;
                  item.onClick();
                  onClose();
                }}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '0.4rem 0.6rem',
                  fontSize: '0.76rem',
                  background: 'transparent',
                  border: 'none',
                  borderRadius: '5px',
                  color: item.disabled ? '#64748b' : '#e2e8f0',
                  cursor: item.disabled ? 'not-allowed' : 'pointer',
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

/* ── Package detail panel ───────────────────────────────────────── */

function PackageDetailPanel({
  pkg,
  detail,
  detailLoading,
  detailRefreshing,
  detailError,
  workspaceEvidenceSource,
  onDownload,
  onDownloadManifest,
  onGenerateManifest,
  onRegeneratePackage,
  onVerify,
  verifying,
  generating,
  regenerating,
  recovery,
}: {
  pkg: EvidencePackage;
  detail?: PackageDetail | null;
  detailLoading?: boolean;
  /** A detail refresh is in flight while an authoritative detail is still shown. */
  detailRefreshing?: boolean;
  /** Last detail refresh failed; the detail on screen is the retained, stale one. */
  detailError?: string | null;
  workspaceEvidenceSource: string;
  onDownload: (pkg: EvidencePackage) => Promise<void>;
  onDownloadManifest?: (pkg: EvidencePackage) => Promise<void>;
  onGenerateManifest?: (pkg: EvidencePackage) => Promise<void>;
  onRegeneratePackage?: (pkg: EvidencePackage) => Promise<void>;
  onVerify?: (pkg: EvidencePackage) => Promise<void>;
  verifying?: boolean;
  generating?: boolean;
  regenerating?: boolean;
  recovery?: { id: string; phase: 'success' | 'error'; message: string } | null;
}) {
  const evSrc = evidenceSourcePill(resolvePackageEvidenceSource(pkg), workspaceEvidenceSource);
  const st = packageStatusPill(pkg.status);
  const ready = isPackageReady(pkg);
  // Detail is loaded from GET /exports/{id}; fall back to the list row for
  // DISPLAY-only fields (generation/hash pills, files, completeness) while the
  // detail loads. The ACTION gate below never uses this fallback.
  const source = detail ?? pkg;
  const genStatus = generationStatus(source);
  const hashStatus = hashVerification(source);
  const integrityStatus = (detail?.integrity_status ?? pkg.integrity_status ?? '').toLowerCase();
  // Whether the selected package's own storage-authoritative detail has loaded.
  // Until it has, the manifest/recovery/verify state is PENDING (refreshing), not
  // an authoritative denial — we never derive it from the list-row summary.
  const detailAvailable = detail != null;
  // The ONE authoritative action source (EV-2026-004 flicker fix, section 5): the
  // storage-authoritative DETAIL's allowed_actions — NEVER the list-row summary,
  // whose manifest state is metadata-derived and legitimately differs (list:
  // hash_generated / not-missing / no generate_manifest; detail: manifest_missing /
  // generate_manifest:true). Mixing the two via `detail ?? pkg` is what made
  // Generate Manifest appear then vanish whenever detail was briefly absent.
  const detailActions = detail?.allowed_actions;
  // Detail-AUTHORITATIVE, PENDING-aware action gate. `resolveDetailActionState`
  // treats a not-yet-loaded detail (null) as PENDING — every action withheld —
  // instead of coercing an ABSENT field into a summary-shaped decision. That is why
  // Verify Integrity / Download Manifest never blink enabled on first load, and
  // Generate Manifest never pops in from a summary row before the detail arrives.
  // Resolved through the ONE canonical gate (evidence-detail-state →
  // evidence-recovery-actions) the regression tests drive directly.
  //
  // The readiness fallback is DETAIL-derived too (never isPackageReady(pkg)): the
  // gate must read the DETAIL model ONLY, so no list-row/summary field — not even
  // `ready` — can influence recovery visibility. `ready` is consulted solely for
  // pre-`allowed_actions` responses; when detail is null the gate is PENDING and
  // `ready` is unused, so this can never fall back to the selectedPkg summary.
  const actionGate = resolveDetailActionState(detail ?? null, detail ? isPackageReady(detail) : false);
  const manifestMissing = actionGate.manifestMissing;
  const detailCanVerify = actionGate.canVerify;
  const detailCanManifest = actionGate.canDownloadManifest;
  // Recovery actions are backend-authoritative; the gate falls back to the
  // Manifest-Missing flag only for older responses without allowed_actions.
  const detailCanGenerate = actionGate.canGenerate;
  const detailCanRegenerate = actionGate.canRegenerate;
  // Button VISIBILITY comes from the SAME canonical gate, so the presence of the
  // recovery buttons and their enabled/disabled state can never diverge. Generate
  // Manifest is shown while the authoritative DETAIL still offers recovery — the
  // package is manifest_missing, OR it explicitly permits generation
  // (allowed_actions.generate_manifest === true). It is removed ONLY when a NEW
  // authoritative detail says otherwise (never on a background list/detail refresh).
  const showGenerateManifest = actionGate.showGenerate;
  const showRegeneratePackage = actionGate.showRegenerate;
  // Development-only guard (EV-2026-004): the recovery button MUST stay visible
  // while the authoritative DETAIL permits generation. If it is ever hidden while
  // detail says generate_manifest:true, a summary/pending value has leaked into the
  // gate — the exact flicker regression. Never ships to production logs.
  useEffect(() => {
    if (process.env.NODE_ENV === 'production') return;
    if (
      detail?.integrity_status === 'manifest_missing' &&
      detailActions?.generate_manifest === true &&
      !showGenerateManifest
    ) {
      // eslint-disable-next-line no-console
      console.warn(
        '[EV-2026-004] authoritative detail permits generate_manifest but the recovery button is hidden — a summary/pending value leaked into the action gate.',
        { id: pkg.id, integrity_status: detail?.integrity_status, allowed_actions: detailActions },
      );
    }
  }, [detail, detailActions, showGenerateManifest, pkg.id]);
  const detailCompletenessScore = detail?.completeness?.score ?? pkg.completeness_score ?? null;
  const verification = detail?.verification ?? null;
  const completeness = detail?.completeness ?? null;
  const files = detail?.files ?? [];
  const chainEvidence = detail?.chain_evidence ?? [];
  const agentFindings = detail?.agent_findings ?? [];

  const missingArtifacts: string[] = pkg.missing_artifacts ?? [];
  const providedArtifacts = new Set((pkg.includes ?? []).map((s) => s.toLowerCase()));

  function artifactPresent(name: string): boolean {
    if (missingArtifacts.some((m) => m.toLowerCase().includes(name.toLowerCase()))) return false;
    if (pkg.includes?.length) return providedArtifacts.has(name.toLowerCase());
    return ready;
  }

  const chainComplete = pkg.chain_complete ?? (missingArtifacts.length === 0 && ready);

  return (
    <aside
      className="dataCard sharedSurfaceCard"
      style={{ padding: '1rem', borderLeft: '1px solid rgba(148,163,184,0.15)' }}
      aria-label="Package detail"
    >
      <p className="eyebrow" style={{ marginBottom: '0.25rem', fontSize: '0.7rem' }}>
        Evidence Package
      </p>
      <h4
        style={{
          marginBottom: '0.15rem',
          fontSize: '0.95rem',
          fontFamily: 'monospace',
          wordBreak: 'break-all',
        }}
      >
        {pkg.package_number ?? pkg.id}
      </h4>
      {/* Internal UUID kept available for diagnostics/routing, not as the label. */}
      <p className="tableMeta" style={{ marginBottom: '0.75rem', fontSize: '0.66rem', wordBreak: 'break-all' }}>
        Internal ID {pkg.id}
      </p>

      {/* ── Integrity summary (three explicit, backend-authoritative states) ──
          Replaces the old ambiguous single "Integrity Status": Generation Status
          (did the artifact build), Hash Verification (its cryptographic state),
          and Evidence Completeness (score). They never contradict each other. */}
      <div
        role="group"
        aria-label="Package integrity states"
        style={{
          marginBottom: '0.75rem',
          display: 'grid',
          gridTemplateColumns: '1fr',
          gap: '0.5rem',
          padding: '0.6rem 0.7rem',
          background: 'rgba(148,163,184,0.05)',
          borderRadius: '6px',
          border: '1px solid rgba(148,163,184,0.12)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
          <span className="tableMeta">Generation Status</span>
          <StatusPill label={genStatus.label} variant={genStatus.variant} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
          <span className="tableMeta">Hash Verification</span>
          <StatusPill label={hashStatus.label} variant={hashStatus.variant} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
          <span className="tableMeta">Evidence Completeness</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
            <strong style={{ fontSize: '0.82rem' }}>
              {typeof detailCompletenessScore === 'number' ? `${detailCompletenessScore}%` : '—'}
            </strong>
            <StatusPill
              label={completenessStatusLabel(detailCompletenessScore).label}
              variant={completenessStatusLabel(detailCompletenessScore).variant}
            />
          </span>
        </div>
      </div>

      {(detail?.integrity_status ?? pkg.integrity_status) === 'integrity_failed' && (
        <div
          role="alert"
          style={{
            marginBottom: '0.75rem',
            padding: '0.5rem 0.6rem',
            background: 'rgba(239,68,68,0.08)',
            borderRadius: '4px',
            borderLeft: '3px solid #ef4444',
            fontSize: '0.75rem',
            color: '#fca5a5',
          }}
        >
          &#9888; Integrity Failed — this package content changed after generation. It is not verified and must not be presented as proof.
          {verification ? (
            <div style={{ marginTop: '0.25rem' }}>
              {(verification.files_failed?.length ?? 0)} file(s) failed,{' '}
              {(verification.missing_files?.length ?? 0)} missing.
            </div>
          ) : null}
        </div>
      )}

      {/* ── Manifest recovery (friendly, customer-facing) ─────────────
          Shown for a Manifest-Missing package. The primary action rebuilds a
          retrievable manifest from the package's exact stored bytes — the original
          package is preserved (only manifest.json is added). Verify Integrity is
          never enabled until a real manifest exists. */}
      {/* Detail-authoritative gating: until the selected package's own detail has
          loaded, its manifest/recovery state is PENDING — show a neutral,
          layout-stable placeholder instead of deriving a (false) "healthy" state
          from the list-row summary. This is why Generate Manifest no longer pops
          in and out during the first load / a background refresh. */}
      {!detailAvailable && (detailLoading || detailRefreshing) && (
        <div
          role="status"
          style={{
            marginBottom: '0.75rem',
            padding: '0.6rem 0.7rem',
            background: 'rgba(148,163,184,0.06)',
            borderRadius: '6px',
            border: '1px solid rgba(148,163,184,0.15)',
            fontSize: '0.75rem',
            color: '#94a3b8',
          }}
        >
          Refreshing package status…
        </div>
      )}
      {manifestMissing && (
        <div
          role="group"
          aria-label="Manifest recovery"
          style={{
            marginBottom: '0.75rem',
            padding: '0.65rem 0.7rem',
            background: 'rgba(239,68,68,0.07)',
            borderRadius: '6px',
            borderLeft: '3px solid #ef4444',
          }}
        >
          <p style={{ margin: 0, fontSize: '0.82rem', fontWeight: 700, color: '#fca5a5' }}>
            Manifest required
          </p>
          <p style={{ margin: '0.25rem 0 0.5rem', fontSize: '0.75rem', color: '#e2e8f0' }}>
            This package was generated without a retrievable integrity manifest. Generate a
            manifest before verifying its contents. Your original package is preserved — only
            the integrity manifest is added.
          </p>
          {/* Stale-while-refresh affordances: the recovery action stays put; we
              only annotate that a refresh is happening, or that the last refresh
              failed and the shown status is the retained last-known one. */}
          {detailError ? (
            <p role="status" style={{ margin: '0 0 0.5rem', fontSize: '0.72rem', color: '#fbbf24' }}>
              Couldn’t refresh package status — showing last known state.
            </p>
          ) : detailRefreshing ? (
            <p role="status" style={{ margin: '0 0 0.5rem', fontSize: '0.72rem', color: '#94a3b8' }}>
              Refreshing package status…
            </p>
          ) : null}
          <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {onGenerateManifest ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={!detailCanGenerate || !!generating || !!regenerating}
                title={
                  detailCanGenerate
                    ? 'Rebuild a retrievable integrity manifest from this package’s stored bytes.'
                    : 'Manifest recovery is not available for this package.'
                }
                style={{ fontSize: '0.75rem' }}
                onClick={() => void onGenerateManifest(pkg)}
              >
                {generating
                  ? 'Generating manifest…'
                  : recovery?.phase === 'error'
                    ? 'Retry'
                    : 'Generate Manifest'}
              </button>
            ) : null}
            {onRegeneratePackage ? (
              <button
                type="button"
                className="btn btn-secondary"
                // Fallback recovery: create a superseding package when an in-place
                // manifest cannot be generated. Never rewrites the original.
                disabled={!detailCanRegenerate || !!regenerating || !!generating}
                title={
                  detailCanRegenerate
                    ? 'Create a new superseding package for this incident. The original package is preserved unchanged.'
                    : 'Regeneration is not available for this package.'
                }
                style={{ fontSize: '0.75rem' }}
                onClick={() => void onRegeneratePackage(pkg)}
              >
                {regenerating ? 'Regenerating package…' : 'Regenerate Package'}
              </button>
            ) : null}
          </div>
          <p style={{ margin: '0.4rem 0 0', fontSize: '0.7rem', color: '#94a3b8' }}>
            If a manifest can’t be generated from the stored artifact, Regenerate Package
            creates a new superseding package for this incident and preserves this one as
            historical evidence.
          </p>
          {generating ? (
            <p style={{ margin: '0.4rem 0 0', fontSize: '0.72rem', color: '#cbd5e1' }} aria-live="polite">
              Generating manifest…
            </p>
          ) : regenerating ? (
            <p style={{ margin: '0.4rem 0 0', fontSize: '0.72rem', color: '#cbd5e1' }} aria-live="polite">
              Regenerating package…
            </p>
          ) : recovery?.phase === 'error' ? (
            <p style={{ margin: '0.4rem 0 0', fontSize: '0.72rem', color: '#fca5a5' }} role="alert">
              Manifest could not be generated. You can Regenerate Package to create a superseding package instead.
            </p>
          ) : null}
        </div>
      )}

      {recovery?.phase === 'success' && !manifestMissing ? (
        <div
          style={{
            marginBottom: '0.75rem',
            padding: '0.5rem 0.6rem',
            background: 'rgba(34,197,94,0.08)',
            borderRadius: '4px',
            borderLeft: '3px solid #22c55e',
            fontSize: '0.75rem',
            color: '#86efac',
          }}
          aria-live="polite"
        >
          Manifest generated. This package is ready for integrity verification.
        </div>
      ) : null}

      {verification ? (
        <div className="tableMeta" style={{ marginBottom: '0.75rem', fontSize: '0.72rem' }}>
          {verification.valid
            ? `Integrity verified · ${verification.files_verified ?? 0}/${verification.files_total ?? 0} files matched`
            : 'Verification did not pass'}
          {verification.verified_at ? ` · ${fmt(verification.verified_at)}` : ''}
          {verification.seal_status ? ` · seal ${verification.seal_status}` : ''}
        </div>
      ) : manifestMissing ? null : integrityStatus === 'hash_generated' ? (
        <p className="tableMeta" style={{ marginBottom: '0.75rem', fontSize: '0.72rem' }}>
          Hashes generated. Run Verify Integrity to confirm content matches the generated package.
        </p>
      ) : null}

      {detail?.storage_available === false && (
        <div
          style={{ marginBottom: '0.75rem', fontSize: '0.75rem', color: '#f59e0b' }}
          role="alert"
        >
          &#9888; Evidence storage is unavailable. Package metadata remains available, but downloads are disabled until storage recovers.
        </div>
      )}

      {/* ── Evidence completeness (deterministic score) ───────────── */}
      {completeness ? (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>
            Evidence Completeness
          </p>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.35rem' }}>
            <strong style={{ fontSize: '1.1rem' }}>
              {typeof completeness.score === 'number' ? `${completeness.score}%` : '—'}
            </strong>
            <StatusPill
              label={completenessStatusLabel(completeness.score).label}
              variant={completenessStatusLabel(completeness.score).variant}
            />
          </div>
          <div className="tableMeta" style={{ fontSize: '0.72rem' }}>
            {completeness.present_count ?? 0} present · {completeness.missing_count ?? 0} missing ·{' '}
            {completeness.unverifiable_count ?? 0} unverifiable of {completeness.required_count ?? 0} required
          </div>
          {(completeness.categories ?? [])
            .filter((c) => c.required)
            .map((c) => (
              <div
                key={c.code}
                style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.25rem', fontSize: '0.74rem' }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    color:
                      c.status === 'present' ? '#22c55e' : c.status === 'unverifiable' ? '#f59e0b' : '#ef4444',
                    fontWeight: 700,
                  }}
                >
                  {c.status === 'present' ? '✓' : c.status === 'unverifiable' ? '!' : '✗'}
                </span>
                <span>{c.label}</span>
                <span className="sr-only">{c.status}</span>
              </div>
            ))}
        </div>
      ) : detailLoading ? (
        <p className="tableMeta" style={{ marginBottom: '0.75rem' }}>Loading completeness…</p>
      ) : null}

      {/* ── Included files (from the signed manifest) ─────────────── */}
      {files.length > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>
            Included Files ({files.length})
          </p>
          {files.map((f) => (
            <div
              key={f.logical_path}
              style={{ marginBottom: '0.35rem', fontSize: '0.72rem', borderBottom: '1px solid rgba(148,163,184,0.1)', paddingBottom: '0.25rem' }}
            >
              <div style={{ fontFamily: 'monospace', display: 'flex', justifyContent: 'space-between', gap: '0.5rem' }}>
                <span>{f.logical_path}</span>
                <span className="muted">{fmtSize(f.size_bytes)}</span>
              </div>
              <div style={{ fontFamily: 'monospace', color: '#94a3b8', wordBreak: 'break-all' }} title={f.sha256 ?? ''}>
                {truncHash(f.sha256)}
                <span className="sr-only">SHA-256 {f.sha256}</span>
              </div>
              <div className="tableMeta" style={{ fontSize: '0.68rem' }}>
                {f.source_record_type} · {f.verification_status ?? 'hash_generated'}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Chain evidence ────────────────────────────────────────── */}
      {chainEvidence.length > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>
            Chain Evidence
          </p>
          {chainEvidence.map((c, i) => (
            <div key={i} style={{ fontSize: '0.7rem', fontFamily: 'monospace', wordBreak: 'break-all', marginBottom: '0.25rem' }}>
              {String((c as Record<string, unknown>).transaction_hash ?? (c as Record<string, unknown>).block_number ?? '—')}
            </div>
          ))}
        </div>
      )}

      {/* ── Agent findings (Crypto-Auditing Clerk) ────────────────── */}
      {agentFindings.length > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>
            Agent Findings
          </p>
          {agentFindings.map((f, i) => (
            <div key={i} style={{ fontSize: '0.72rem', marginBottom: '0.25rem', color: '#cbd5e1' }}>
              • {f.message}
            </div>
          ))}
        </div>
      )}

      {!chainComplete && (
        <div
          className="statusLine"
          style={{ marginBottom: '0.75rem', fontSize: '0.78rem', color: '#f59e0b' }}
        >
          &#9888; Evidence chain incomplete
        </div>
      )}

      {(pkg.warnings?.length ?? 0) > 0 && (
        <div
          style={{
            marginBottom: '0.75rem',
            padding: '0.5rem 0.6rem',
            background: 'rgba(245,158,11,0.07)',
            borderRadius: '4px',
            borderLeft: '3px solid #f59e0b',
          }}
        >
          <p className="tableMeta" style={{ marginBottom: '0.25rem', color: '#f59e0b' }}>
            Warnings
          </p>
          {pkg.warnings?.map((w, i) => (
            <p key={i} style={{ fontSize: '0.74rem', margin: '0.1rem 0', color: '#fcd34d' }}>
              {w}
            </p>
          ))}
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '0.5rem 1rem',
          marginBottom: '0.75rem',
        }}
      >
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Status
          </p>
          <StatusPill label={st.label} variant={st.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Evidence Source
          </p>
          <StatusPill label={evSrc.label} variant={evSrc.variant} />
        </div>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Linked Incident
        </p>
        {pkg.incident_id ? (
          <Link
            href="/incidents"
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.73rem', padding: '0.15rem 0.45rem' }}
          >
            {pkg.incident_id}
          </Link>
        ) : (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
            Not linked
          </p>
        )}
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Linked Alert
        </p>
        <p
          style={{
            fontSize: '0.78rem',
            margin: 0,
            fontFamily: pkg.alert_id ? 'monospace' : undefined,
          }}
        >
          {pkg.alert_id ?? '-'}
        </p>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Linked Detection
        </p>
        <p
          style={{
            fontSize: '0.78rem',
            margin: 0,
            fontFamily: pkg.detection_id ? 'monospace' : undefined,
          }}
        >
          {pkg.detection_id ?? '-'}
        </p>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Asset
        </p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>
          {pkg.asset_label ?? pkg.asset_id ?? '-'}
        </p>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Created At
        </p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>{fmt(pkg.created_at)}</p>
      </div>

      {pkg.created_by ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Created By
          </p>
          <p style={{ fontSize: '0.78rem', margin: 0 }}>{pkg.created_by}</p>
        </div>
      ) : null}

      {pkg.retention_policy ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Retention Policy
          </p>
          <p style={{ fontSize: '0.78rem', margin: 0 }}>{pkg.retention_policy}</p>
        </div>
      ) : null}

      {pkg.integrity_hash ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Integrity Hash
          </p>
          <p
            style={{
              fontSize: '0.7rem',
              fontFamily: 'monospace',
              margin: 0,
              wordBreak: 'break-all',
            }}
          >
            {pkg.integrity_hash}
          </p>
        </div>
      ) : null}

      {/* Included Artifacts checklist */}
      <div style={{ marginBottom: '0.75rem', marginTop: '0.75rem' }}>
        <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>
          Included Artifacts
        </p>
        {REQUIRED_ARTIFACTS.map((artifact) => {
          const present = artifactPresent(artifact);
          return (
            <div
              key={artifact}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                marginBottom: '0.28rem',
                fontSize: '0.78rem',
              }}
            >
              <span style={{ color: present ? '#22c55e' : '#ef4444', fontWeight: 700 }}>
                {present ? '✓' : '✗'}
              </span>
              <span style={{ color: present ? undefined : '#f87171' }}>{artifact}</span>
            </div>
          );
        })}
      </div>

      {/* Export Status */}
      <div style={{ marginBottom: '0.75rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.2rem' }}>
          Export Status
        </p>
        {pkg.export_status === 'incomplete' ? (
          <StatusPill label="Incomplete proof bundle" variant="danger" />
        ) : pkg.export_status === 'partial' ? (
          <StatusPill label="Partial proof bundle" variant="warning" />
        ) : ready ? (
          <StatusPill label="Complete proof bundle" variant="success" />
        ) : (
          <StatusPill label="Not Available" variant="neutral" />
        )}
      </div>

      {/* Missing chain sections */}
      {(pkg.missing_sections?.length ?? 0) > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>
            Missing Chain Sections
          </p>
          {pkg.missing_sections?.map((section) => (
            <div
              key={section}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.2rem', fontSize: '0.75rem' }}
            >
              <span style={{ color: '#ef4444', fontWeight: 700 }}>✗</span>
              <span style={{ color: '#f87171' }}>{section}</span>
            </div>
          ))}
        </div>
      )}


      {(pkg.unavailable_sections?.length ?? 0) > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.35rem' }}>
            Unavailable Sections
          </p>
          {pkg.unavailable_sections?.map((section) => (
            <div
              key={section}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.2rem', fontSize: '0.75rem' }}
            >
              <span style={{ color: '#f59e0b', fontWeight: 700 }}>!</span>
              <span style={{ color: '#fcd34d' }}>{section}</span>
            </div>
          ))}
        </div>
      )}
      {pkg.package_status && (
        <div style={{ marginBottom: '0.5rem', fontSize: '0.75rem' }}>
          <span style={{ color: '#94a3b8' }}>Package status: </span>
          <span style={{
            fontWeight: 600,
            color: pkg.package_status === 'complete' ? '#4ade80' : pkg.package_status === 'partial' ? '#fbbf24' : '#f87171',
          }}>
            {pkg.package_status.toUpperCase()}
          </span>
        </div>
      )}
      {(pkg.package_status === 'partial' || pkg.package_status === 'blocked') && (
        <div style={{ marginBottom: '0.5rem', fontSize: '0.74rem', color: pkg.package_status === 'blocked' ? '#f87171' : '#fbbf24' }}>
          &#9888;{' '}
          {pkg.package_status === 'blocked'
            ? 'No usable evidence — this package cannot be used as verification proof.'
            : 'Package is incomplete — some evidence sections are missing.'}
        </div>
      )}
      {pkg.source_truthfulness_status && pkg.source_truthfulness_status !== 'verified_live' && (
        <div style={{ marginBottom: '0.5rem', fontSize: '0.75rem', color: '#fbbf24' }}>
          Source truthfulness: {pkg.source_truthfulness_status.replace(/_/g, ' ')}
        </div>
      )}
      {pkg.redactions_applied && (
        <div style={{ marginBottom: '0.5rem', fontSize: '0.75rem', color: '#94a3b8' }}>
          Some fields were redacted for safe export.
        </div>
      )}
      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!ready}
          style={{ fontSize: '0.75rem' }}
          onClick={() => void onDownload(pkg)}
        >
          Download Package
        </button>
        {onGenerateManifest && showGenerateManifest ? (
          <button
            type="button"
            className="btn btn-secondary"
            // Recovery: rebuild a retrievable manifest from the package's stored
            // bytes. Disappears once a manifest exists (gated by the backend).
            disabled={!detailCanGenerate || !!generating}
            title={
              detailCanGenerate
                ? 'Rebuild a retrievable integrity manifest from this package’s stored bytes.'
                : 'Manifest recovery is not available for this package.'
            }
            style={{ fontSize: '0.75rem' }}
            onClick={() => void onGenerateManifest(pkg)}
          >
            {generating ? 'Generating manifest…' : 'Generate Manifest'}
          </button>
        ) : null}
        {onRegeneratePackage && showRegeneratePackage ? (
          <button
            type="button"
            className="btn btn-secondary"
            // Fallback recovery: create a superseding package (the original is
            // preserved). Backend-gated to the Manifest-Missing state.
            disabled={!detailCanRegenerate || !!regenerating || !!generating}
            title={
              detailCanRegenerate
                ? 'Create a new superseding package for this incident. The original package is preserved unchanged.'
                : 'Regeneration is not available for this package.'
            }
            style={{ fontSize: '0.75rem' }}
            onClick={() => void onRegeneratePackage(pkg)}
          >
            {regenerating ? 'Regenerating package…' : 'Regenerate Package'}
          </button>
        ) : null}
        {onVerify ? (
          <button
            type="button"
            className="btn btn-secondary"
            // Backend-authoritative: Verify is offered only when the canonical
            // manifest resolver reports a retrievable manifest — never guessed here.
            disabled={!detailCanVerify || !!verifying}
            title={
              detailCanVerify
                ? undefined
                : manifestMissing
                  ? 'This package has no retrievable manifest. Generate a manifest before verifying integrity.'
                  : 'Verification requires a retrievable signed manifest.'
            }
            style={{ fontSize: '0.75rem' }}
            onClick={() => void onVerify(pkg)}
          >
            {verifying ? 'Verifying…' : 'Verify Integrity'}
          </button>
        ) : null}
        {onDownloadManifest ? (
          <button
            type="button"
            className="btn btn-secondary"
            disabled={!detailCanManifest}
            title={detailCanManifest ? undefined : 'No retrievable manifest exists for this package.'}
            style={{ fontSize: '0.75rem' }}
            onClick={() => void onDownloadManifest(pkg)}
          >
            Download Manifest
          </button>
        ) : null}
        {pkg.incident_id ? (
          <Link
            href="/incidents"
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.75rem' }}
          >
            View Incident
          </Link>
        ) : null}
      </div>
    </aside>
  );
}

/* ── Crypto-Auditing Clerk agent sidebar ────────────────────────── */

function CompletenessRing({ score }: { score: number | null | undefined }) {
  const pct = typeof score === 'number' ? Math.max(0, Math.min(100, score)) : 0;
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - pct / 100);
  const color = pct >= 95 ? '#22c55e' : pct >= 80 ? '#4ade80' : pct >= 60 ? '#f59e0b' : '#ef4444';
  return (
    <svg width="88" height="88" viewBox="0 0 88 88" role="img" aria-label={`Evidence completeness ${typeof score === 'number' ? `${pct}%` : 'unknown'}`}>
      <circle cx="44" cy="44" r={radius} fill="none" stroke="rgba(148,163,184,0.2)" strokeWidth="8" />
      <circle
        cx="44"
        cy="44"
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth="8"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        transform="rotate(-90 44 44)"
      />
      <text x="44" y="49" textAnchor="middle" fontSize="18" fontWeight="700" fill="currentColor">
        {typeof score === 'number' ? `${pct}%` : '—'}
      </text>
    </svg>
  );
}

function CryptoAuditingClerkPanel({
  metrics,
  selectedPkg,
  detail,
  detailLoading,
  lastRefreshAt,
  onViewReport,
}: {
  metrics: EvidenceMetrics | null;
  selectedPkg: EvidencePackage | null;
  detail: PackageDetail | null;
  detailLoading: boolean;
  lastRefreshAt: string;
  onViewReport: () => void;
}) {
  // Package-scoped when a package is selected; otherwise workspace-scoped.
  const packageScope = Boolean(selectedPkg);
  const completeness = detail?.completeness ?? null;
  // Score comes from the backend — either the selected package's completeness or
  // the workspace average. Never a hardcoded value.
  const score: number | null =
    packageScope
      ? (typeof completeness?.score === 'number'
          ? completeness.score
          : (typeof selectedPkg?.completeness_score === 'number' ? selectedPkg.completeness_score : null))
      : (typeof metrics?.average_completeness === 'number' ? metrics.average_completeness : null);
  const statusLabel = completenessStatusLabel(score);
  const checklist = completeness?.checklist ?? [];

  return (
    <aside
      className="dataCard sharedSurfaceCard"
      style={{ padding: '1rem' }}
      aria-label="Crypto-Auditing Clerk"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
        <span aria-hidden="true" style={{ fontSize: '1.3rem' }}>🛡️</span>
        <div>
          <p className="eyebrow" style={{ margin: 0, fontSize: '0.68rem' }}>
            Autonomous Agent
          </p>
          <h3 style={{ margin: 0, fontSize: '0.95rem' }}>Crypto-Auditing Clerk</h3>
        </div>
      </div>

      <p className="tableMeta" style={{ marginBottom: '0.5rem' }}>
        {packageScope ? 'Package evidence completeness' : 'Evidence Completeness'}
      </p>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
        <CompletenessRing score={score} />
        <div>
          <StatusPill label={statusLabel.label} variant={statusLabel.variant} />
          <p className="tableMeta" style={{ margin: '0.35rem 0 0', fontSize: '0.7rem' }}>
            {packageScope
              ? (detailLoading ? 'Calculating…' : 'For selected package')
              : typeof score === 'number'
                ? `Workspace average across ${metrics?.total_packages ?? 0} package(s)`
                : 'No package has a calculable completeness score yet.'}
          </p>
          {(metrics?.last_calculated || lastRefreshAt) ? (
            <p className="tableMeta" style={{ margin: '0.15rem 0 0', fontSize: '0.68rem' }}>
              Last calculated {fmt(metrics?.last_calculated || lastRefreshAt)}
            </p>
          ) : null}
        </div>
      </div>

      {/* Package-mode evidence breakdown — real backend counts, never hardcoded. */}
      {packageScope && completeness ? (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>
            Selected Package
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem 0.75rem' }}>
            <ClerkMetric label="Package" value={selectedPkg?.package_number ?? '-'} />
            <ClerkMetric label="Required" value={completeness.required_count ?? 0} />
            <ClerkMetric label="Present" value={completeness.present_count ?? 0} />
            <ClerkMetric label="Missing" value={completeness.missing_count ?? 0} />
            <ClerkMetric label="Unverifiable" value={completeness.unverifiable_count ?? 0} />
            <ClerkMetric label="Files Hashed" value={selectedPkg?.files_hashed ?? 0} />
            <ClerkMetric label="Files Verified" value={detail?.verification?.files_verified ?? 0} />
            <ClerkMetric
              label="Integrity Failures"
              value={detail?.verification?.files_failed?.length ?? 0}
              danger={(detail?.verification?.files_failed?.length ?? 0) > 0}
            />
          </div>
          {detail?.verification?.verified_at ? (
            <p className="tableMeta" style={{ margin: '0.35rem 0 0', fontSize: '0.68rem' }}>
              Last verified {fmt(detail.verification.verified_at)}
            </p>
          ) : (
            <p className="tableMeta" style={{ margin: '0.35rem 0 0', fontSize: '0.68rem' }}>
              Not yet verified — run Verify Integrity.
            </p>
          )}
        </div>
      ) : null}

      {/* Verification checklist — only when a package is selected (real values). */}
      {packageScope && checklist.length > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>
            Verification Checklist
          </p>
          {checklist.map((item) => (
            <div
              key={item.code}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.25rem', fontSize: '0.75rem' }}
            >
              <span
                aria-hidden="true"
                style={{ color: item.present ? '#22c55e' : '#ef4444', fontWeight: 700 }}
              >
                {item.present ? '✓' : '✗'}
              </span>
              <span style={{ color: item.present ? undefined : '#f87171' }}>{item.label}</span>
              <span className="sr-only">{item.present ? 'present' : 'missing'}</span>
            </div>
          ))}
        </div>
      )}
      {packageScope && checklist.length === 0 && !detailLoading && (
        <p className="tableMeta" style={{ marginBottom: '0.75rem', fontSize: '0.72rem' }}>
          Select a completed package to see its verification checklist.
        </p>
      )}

      {/* Evidence Metrics (workspace-level) */}
      <div style={{ marginBottom: '0.75rem' }}>
        <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>
          Evidence Metrics
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem 0.75rem' }}>
          <ClerkMetric label="Total Packages" value={metrics?.total_packages ?? 0} />
          <ClerkMetric label="Total Size" value={fmtBytesTotal(metrics?.total_size_bytes)} />
          <ClerkMetric label="Verified" value={metrics?.verified ?? 0} />
          <ClerkMetric label="Incidents Covered" value={metrics?.incidents_covered ?? 0} />
          <ClerkMetric label="Files Hashed" value={metrics?.files_hashed ?? 0} />
          <ClerkMetric
            label="Integrity Failures"
            value={metrics?.integrity_failures ?? 0}
            danger={(metrics?.integrity_failures ?? 0) > 0}
          />
        </div>
      </div>

      <button
        type="button"
        className="btn btn-primary"
        style={{ width: '100%', fontSize: '0.8rem' }}
        disabled={!selectedPkg}
        onClick={onViewReport}
      >
        View Full Package Report
      </button>
      {!selectedPkg ? (
        <p className="tableMeta" style={{ marginTop: '0.4rem', fontSize: '0.68rem', textAlign: 'center' }}>
          Select a package to view its full report.
        </p>
      ) : null}
    </aside>
  );
}

function ClerkMetric({ label, value, danger }: { label: string; value: string | number; danger?: boolean }) {
  return (
    <div>
      <p className="tableMeta" style={{ margin: 0, fontSize: '0.66rem' }}>
        {label}
      </p>
      <p style={{ margin: 0, fontSize: '0.95rem', fontWeight: 600, color: danger ? '#f87171' : undefined }}>
        {value}
      </p>
    </div>
  );
}

/* ── Audit detail panel ─────────────────────────────────────────── */

function AuditDetailPanel({
  row,
  workspaceEvidenceSource,
}: {
  row: AuditRow;
  workspaceEvidenceSource: string;
}) {
  const evSrc = evidenceSourcePill(row.evidence_source_type ?? row.evidence_source, workspaceEvidenceSource);
  const result = auditResultPill(row.result ?? row.status);

  return (
    <aside
      className="dataCard sharedSurfaceCard"
      style={{ padding: '1rem', borderLeft: '1px solid rgba(148,163,184,0.15)' }}
      aria-label="Audit event detail"
    >
      <p className="eyebrow" style={{ marginBottom: '0.25rem', fontSize: '0.7rem' }}>
        Audit Event
      </p>
      <h4 style={{ marginBottom: '0.75rem', fontSize: '0.92rem' }}>
        {row.action ?? row.event_type ?? 'Audit Event'}
      </h4>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '0.5rem 1rem',
          marginBottom: '0.75rem',
        }}
      >
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Result
          </p>
          <StatusPill label={result.label} variant={result.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Evidence Source
          </p>
          <StatusPill label={evSrc.label} variant={evSrc.variant} />
        </div>
      </div>

      {row.id ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Event ID
          </p>
          <p style={{ fontSize: '0.72rem', fontFamily: 'monospace', margin: 0 }}>{row.id}</p>
        </div>
      ) : null}

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Actor
        </p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>
          {row.actor ?? row.system ?? 'system'}
        </p>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Object Type
        </p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>{row.object_type ?? '-'}</p>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Object ID
        </p>
        <p style={{ fontSize: '0.78rem', fontFamily: 'monospace', margin: 0 }}>
          {row.object_id ?? row.target_id ?? row.target ?? '-'}
        </p>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Source IP / System
        </p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>
          {row.source_ip ?? row.source ?? row.origin ?? '-'}
        </p>
      </div>

      {row.user_agent ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            User Agent
          </p>
          <p style={{ fontSize: '0.72rem', margin: 0, wordBreak: 'break-word' }}>
            {row.user_agent}
          </p>
        </div>
      ) : null}

      {row.workspace_id ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
            Workspace ID
          </p>
          <p style={{ fontSize: '0.72rem', fontFamily: 'monospace', margin: 0 }}>
            {row.workspace_id}
          </p>
        </div>
      ) : null}

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>
          Created At
        </p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>
          {fmt(row.timestamp ?? row.created_at)}
        </p>
      </div>
    </aside>
  );
}
