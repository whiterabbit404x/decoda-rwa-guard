'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TableShell,
  TabStrip,
  type PillVariant,
} from './components/ui-primitives';
import { resolveApiUrl } from './dashboard-data';
import { usePilotAuth } from './pilot-auth-context';
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
  'Incident',
  'Date Created',
  'Includes',
  'Size',
  'Evidence Source',
  'Hash (SHA-256)',
  'Integrity',
  'Actions',
] as const;

// Integrity-status filter options. Values map to the backend integrity_status enum.
const INTEGRITY_FILTER_OPTIONS = [
  { value: '', label: 'All integrity states' },
  { value: 'verified', label: 'Verified' },
  { value: 'hash_generated', label: 'Hash generated' },
  { value: 'needs_evidence', label: 'Needs evidence' },
  { value: 'integrity_failed', label: 'Integrity failed' },
  { value: 'building', label: 'Building' },
  { value: 'failed', label: 'Failed' },
  { value: 'superseded', label: 'Superseded' },
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
  completeness_score?: number | null;
  manifest_download_url?: string | null;
  verified_at?: string | null;
  superseded?: boolean;
  files_hashed?: number;
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
  average_completeness?: number | null;
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

function isPackageReady(pkg: EvidencePackage): boolean {
  return (
    !!pkg.package_ready ||
    !!pkg.download_url ||
    packageStatusPill(pkg.status).label === 'Ready' ||
    packageStatusPill(pkg.status).label === 'Exported'
  );
}

// Integrity status is authoritative on the backend. This only maps the canonical
// enum to a truthful pill — it never invents a "Verified" state the backend
// didn't report. "Hash generated" is deliberately distinct from "Verified".
function integrityPill(status?: string): { label: string; variant: PillVariant } {
  switch ((status ?? '').toLowerCase()) {
    case 'verified':
      return { label: 'Verified', variant: 'success' };
    case 'hash_generated':
      return { label: 'Hash generated', variant: 'info' };
    case 'verifying':
      return { label: 'Verifying', variant: 'warning' };
    case 'building':
      return { label: 'Building', variant: 'warning' };
    case 'needs_evidence':
      return { label: 'Needs Evidence', variant: 'warning' };
    case 'integrity_failed':
      return { label: 'Integrity Failed', variant: 'danger' };
    case 'failed':
      return { label: 'Failed', variant: 'danger' };
    case 'superseded':
      return { label: 'Superseded', variant: 'neutral' };
    case 'draft':
      return { label: 'Draft', variant: 'neutral' };
    default:
      return { label: 'Pending', variant: 'neutral' };
  }
}

// Full SHA-256 stays available for copy/tooltip; the table shows a truncated form.
function packageHash(pkg: EvidencePackage): string | null {
  return pkg.integrity_hash ?? null;
}

function truncHash(hash?: string | null): string {
  if (!hash) return '-';
  const clean = hash.replace(/^sha256:/i, '');
  if (clean.length <= 16) return clean;
  return `${clean.slice(0, 10)}…${clean.slice(-6)}`;
}

// 95-100 Excellent · 80-94 Good · 60-79 Incomplete · <60 Critical
function completenessStatusLabel(score?: number | null): { label: string; variant: PillVariant } {
  if (typeof score !== 'number') return { label: 'Unknown', variant: 'neutral' };
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
  const apiUrl = resolveApiUrl();
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
  const [dataLoading, setDataLoading] = useState(false);
  const [auditUnavailable, setAuditUnavailable] = useState('');
  const [responseActionsCount, setResponseActionsCount] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<EvidenceMetrics | null>(null);
  const [search, setSearch] = useState('');
  const [integrityFilter, setIntegrityFilter] = useState('');
  const [selectedDetail, setSelectedDetail] = useState<PackageDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [verifyingId, setVerifyingId] = useState('');
  const [loadError, setLoadError] = useState('');
  const [lastRefreshAt, setLastRefreshAt] = useState<string>('');
  const [copiedHash, setCopiedHash] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

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
  const canCreatePackage = incidentOk && responseActionOk && !dataLoading && !runtimeLoading;

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
          const p = (await pkgRes.value.json()) as { exports?: EvidencePackage[]; metrics?: EvidenceMetrics };
          const loaded = p.exports ?? [];
          setPackages(loaded);
          setMetrics(p.metrics ?? null);
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
          };
          setAuditRows(a.events ?? a.audit_logs ?? []);
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
  }, [apiUrl, authHeaders, runtimeLoading, urlPackageId, urlActionId, urlIncidentId, reloadKey]);

  // Fetch the full package report for the selected package so the agent card can
  // show its real completeness checklist, files, and integrity — all backend values.
  useEffect(() => {
    if (!selectedPkgId) {
      setSelectedDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    (async () => {
      try {
        const res = await fetch(`/api/exports/${encodeURIComponent(selectedPkgId)}`, {
          headers: authHeaders(),
          cache: 'no-store',
        });
        if (!cancelled && res.ok) {
          const body = (await res.json()) as { export?: PackageDetail };
          setSelectedDetail(body.export ?? null);
        } else if (!cancelled) {
          setSelectedDetail(null);
        }
      } catch {
        if (!cancelled) setSelectedDetail(null);
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedPkgId, authHeaders, reloadKey]);

  async function verifyPackage(pkg: EvidencePackage) {
    if (!pkg.id) return;
    setMessage('');
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
        const detail = typeof body.detail === 'object' ? JSON.stringify(body.detail) : String(body.detail ?? 'Verification failed.');
        setMessage(`Verification failed: ${detail}`);
      }
    } catch {
      setMessage('Verification failed: network error.');
    } finally {
      setVerifyingId('');
    }
  }

  async function downloadManifest(pkg: EvidencePackage) {
    if (!pkg.id) return;
    setMessage('');
    let resp: Response;
    try {
      resp = await fetch(`/api/exports/${encodeURIComponent(pkg.id)}/manifest`, {
        headers: authHeaders(),
        cache: 'no-store',
      });
    } catch {
      setMessage('Manifest download failed: network error.');
      return;
    }
    if (!resp.ok) {
      const errBody = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
      setMessage(`Manifest download failed: ${String(errBody.detail ?? errBody.message ?? 'error')}`);
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

  async function copyHash(pkg: EvidencePackage) {
    const hash = packageHash(pkg);
    if (!hash) return;
    const ok = await copyToClipboard(hash);
    if (ok) {
      setCopiedHash(pkg.id);
      setTimeout(() => setCopiedHash(''), 1500);
    }
  }

  async function createPackage() {
    setMessage('');
    const linkedIncidentId =
      packages.find((pkg) => pkg.incident_id)?.incident_id ??
      ((runtime as Record<string, unknown> | undefined)?.latest_incident_id as string | undefined) ??
      ((summary as Record<string, unknown> | undefined)?.latest_incident_id as string | undefined) ??
      ((summary as Record<string, unknown> | undefined)?.last_incident_id as string | undefined);

    let incidentId = linkedIncidentId;
    if (!incidentId) {
      const incidentRes = await fetch(`${apiUrl}/incidents`, { headers: authHeaders(), cache: 'no-store' });
      if (incidentRes.ok) {
        const incidentsPayload = (await incidentRes.json()) as { incidents?: Array<{ id?: string }> };
        incidentId = incidentsPayload.incidents?.[0]?.id;
      }
    }

    if (!incidentId) {
      setMessage('Cannot create proof bundle yet: no incident is linked.');
      return;
    }

    const res = await fetch(`${apiUrl}/exports/proof-bundle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ incident_id: incidentId, include_raw_events: true }),
    });
    const payload = (await res.json()) as { status?: string; detail?: string };
    setMessage(
      res.ok
        ? `Evidence package ${payload.status ?? 'queued'}.`
        : (payload.detail ?? 'Export failed.'),
    );
    if (res.ok) {
      const pkgRes = await fetch('/api/exports', {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (pkgRes.ok) {
        const p = (await pkgRes.json()) as { exports?: EvidencePackage[] };
        setPackages(p.exports ?? []);
      }
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

  /* ── Derived metrics ─────────────────────────────────────────── */
  const exportReadyCount = packages.filter(isPackageReady).length;
  const retentionStatus = packages.length > 0
    ? (exportReadyCount > 0 ? 'Compliant' : 'Pending')
    : 'No packages';

  /* ── Search + integrity filtering (client-side over API rows) ── */
  const filteredPackages = useMemo(() => {
    const q = search.trim().toLowerCase();
    return packages.filter((pkg) => {
      if (integrityFilter && (pkg.integrity_status ?? '') !== integrityFilter) return false;
      if (!q) return true;
      const haystack = [pkg.id, pkg.incident_id, pkg.integrity_hash, pkg.integrity_status]
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
            Export incident evidence packages and review audit activity.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!canCreatePackage}
          title={
            !canCreatePackage
              ? 'Requires an incident and a response action before creating a package'
              : undefined
          }
          onClick={() => void createPackage()}
        >
          Create Evidence Package
        </button>
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
        <MetricTile label="Evidence Packages" value={packages.length} />
        <MetricTile label="Audit Events" value={auditRows.length} />
        <MetricTile
          label="Export Ready"
          value={exportReadyCount}
          meta={exportReadyCount > 0 ? 'packages ready' : 'none ready'}
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
        <p className="statusLine" style={{ marginBottom: '1rem' }}>
          {message}
        </p>
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
                  placeholder="Search package ID, incident, or hash"
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
                    const evSrc = evidenceSourcePill(resolvePackageEvidenceSource(pkg), workspaceEvidenceSource);
                    const ready = isPackageReady(pkg);
                    const isSelected = pkg.id === selectedPkgId;
                    const integ = integrityPill(pkg.integrity_status);
                    const hash = packageHash(pkg);
                    return (
                      <tr
                        key={pkg.id}
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
                          background: isSelected ? 'rgba(59,130,246,0.08)' : undefined,
                        }}
                      >
                        <td
                          style={{
                            fontFamily: 'monospace',
                            fontSize: '0.75rem',
                            whiteSpace: 'nowrap',
                            maxWidth: '130px',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                          title={pkg.id}
                        >
                          {pkg.id}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>{pkg.incident_id ?? '-'}</td>
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
                        <td>
                          <StatusPill label={evSrc.label} variant={evSrc.variant} />
                        </td>
                        {/* Hash (SHA-256): truncated display, full value in title + copy */}
                        <td style={{ fontSize: '0.72rem', whiteSpace: 'nowrap' }}>
                          {hash ? (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                              <span style={{ fontFamily: 'monospace' }} title={hash}>
                                {truncHash(hash)}
                              </span>
                              <span className="sr-only">Full SHA-256 hash {hash}</span>
                              <button
                                type="button"
                                className="btn btn-secondary"
                                aria-label={`Copy full SHA-256 hash for package ${pkg.id}`}
                                style={{ fontSize: '0.65rem', padding: '0.05rem 0.35rem' }}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void copyHash(pkg);
                                }}
                              >
                                {copiedHash === pkg.id ? 'Copied' : 'Copy'}
                              </button>
                            </span>
                          ) : (
                            <span className="muted">Pending</span>
                          )}
                        </td>
                        {/* Integrity status */}
                        <td>
                          <StatusPill label={integ.label} variant={integ.variant} />
                        </td>
                        {/* Actions — permission/readiness-aware */}
                        <td>
                          <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
                            <button
                              type="button"
                              disabled={!ready}
                              className="btn btn-secondary"
                              style={{ fontSize: '0.72rem', padding: '0.15rem 0.45rem' }}
                              onClick={(e) => {
                                e.stopPropagation();
                                void downloadPackage(pkg);
                              }}
                            >
                              Download JSON
                            </button>
                            <button
                              type="button"
                              disabled={!ready || verifyingId === pkg.id}
                              className="btn btn-secondary"
                              style={{ fontSize: '0.72rem', padding: '0.15rem 0.45rem' }}
                              onClick={(e) => {
                                e.stopPropagation();
                                void verifyPackage(pkg);
                              }}
                            >
                              {verifyingId === pkg.id ? 'Verifying…' : 'Verify Integrity'}
                            </button>
                            <button
                              type="button"
                              disabled={!ready}
                              className="btn btn-secondary"
                              style={{ fontSize: '0.72rem', padding: '0.15rem 0.45rem' }}
                              onClick={(e) => {
                                e.stopPropagation();
                                void downloadManifest(pkg);
                              }}
                            >
                              Download Manifest
                            </button>
                          </div>
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
                    workspaceEvidenceSource={workspaceEvidenceSource}
                    onDownload={downloadPackage}
                    onDownloadManifest={downloadManifest}
                    onVerify={verifyPackage}
                    verifying={verifyingId === selectedPkg.id}
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
    </section>
  );
}

/* ── Package detail panel ───────────────────────────────────────── */

function PackageDetailPanel({
  pkg,
  detail,
  detailLoading,
  workspaceEvidenceSource,
  onDownload,
  onDownloadManifest,
  onVerify,
  verifying,
}: {
  pkg: EvidencePackage;
  detail?: PackageDetail | null;
  detailLoading?: boolean;
  workspaceEvidenceSource: string;
  onDownload: (pkg: EvidencePackage) => Promise<void>;
  onDownloadManifest?: (pkg: EvidencePackage) => Promise<void>;
  onVerify?: (pkg: EvidencePackage) => Promise<void>;
  verifying?: boolean;
}) {
  const evSrc = evidenceSourcePill(resolvePackageEvidenceSource(pkg), workspaceEvidenceSource);
  const st = packageStatusPill(pkg.status);
  const ready = isPackageReady(pkg);
  // Detail is loaded from GET /exports/{id}; fall back to the list row while loading.
  const integ = integrityPill(detail?.integrity_status ?? pkg.integrity_status);
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
          marginBottom: '0.75rem',
          fontSize: '0.88rem',
          fontFamily: 'monospace',
          wordBreak: 'break-all',
        }}
      >
        {pkg.id}
      </h4>

      {/* ── Integrity summary (backend-authoritative) ─────────────── */}
      <div style={{ marginBottom: '0.75rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.2rem' }}>
          Integrity Status
        </p>
        <StatusPill label={integ.label} variant={integ.variant} />
        {(detail?.integrity_status ?? pkg.integrity_status) === 'integrity_failed' && (
          <div
            role="alert"
            style={{
              marginTop: '0.5rem',
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
        {verification ? (
          <div className="tableMeta" style={{ marginTop: '0.4rem', fontSize: '0.72rem' }}>
            {verification.valid
              ? `Integrity verified · ${verification.files_verified ?? 0}/${verification.files_total ?? 0} files matched`
              : 'Verification did not pass'}
            {verification.verified_at ? ` · ${fmt(verification.verified_at)}` : ''}
            {verification.seal_status ? ` · seal ${verification.seal_status}` : ''}
          </div>
        ) : (
          <p className="tableMeta" style={{ marginTop: '0.3rem', fontSize: '0.72rem' }}>
            Hashes generated. Run Verify Integrity to confirm content matches the generated package.
          </p>
        )}
      </div>

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
          Download JSON
        </button>
        {onVerify ? (
          <button
            type="button"
            className="btn btn-secondary"
            disabled={!ready || !!verifying}
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
            disabled={!ready}
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
        {packageScope ? 'Package evidence completeness' : 'AI Evidence Completeness'}
      </p>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
        <CompletenessRing score={score} />
        <div>
          <StatusPill label={statusLabel.label} variant={statusLabel.variant} />
          <p className="tableMeta" style={{ margin: '0.35rem 0 0', fontSize: '0.7rem' }}>
            {packageScope
              ? (detailLoading ? 'Calculating…' : 'For selected package')
              : `Workspace average across ${metrics?.total_packages ?? 0} package(s)`}
          </p>
          {lastRefreshAt ? (
            <p className="tableMeta" style={{ margin: '0.15rem 0 0', fontSize: '0.68rem' }}>
              Last calculated {fmt(lastRefreshAt)}
            </p>
          ) : null}
        </div>
      </div>

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
