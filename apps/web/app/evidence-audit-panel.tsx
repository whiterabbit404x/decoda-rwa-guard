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
  'Actions',
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
          const p = (await pkgRes.value.json()) as { exports?: EvidencePackage[] };
          const loaded = p.exports ?? [];
          setPackages(loaded);
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
  }, [apiUrl, authHeaders, runtimeLoading, urlPackageId, urlActionId, urlIncidentId]);

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
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: selectedPkg ? '1fr 380px' : '1fr',
              gap: '1rem',
              alignItems: 'start',
            }}
          >
            <div>
              <TableShell headers={[...PKG_TABLE_HEADERS]} compact>
                {packages.length === 0 ? (
                  <tr>
                    <td
                      colSpan={PKG_TABLE_HEADERS.length}
                      style={{
                        textAlign: 'center',
                        color: 'var(--color-muted, #94a3b8)',
                        padding: '2rem',
                      }}
                    >
                      No evidence packages yet.
                    </td>
                  </tr>
                ) : (
                  packages.map((pkg) => {
                    const evSrc = evidenceSourcePill(resolvePackageEvidenceSource(pkg), workspaceEvidenceSource);
                    const ready = isPackageReady(pkg);
                    const isSelected = pkg.id === selectedPkgId;
                    return (
                      <tr
                        key={pkg.id}
                        onClick={() => setSelectedPkgId(isSelected ? '' : pkg.id)}
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
                        <td>
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
                        </td>
                      </tr>
                    );
                  })
                )}
              </TableShell>
            </div>

            {selectedPkg && (
              <PackageDetailPanel
                pkg={selectedPkg}
                workspaceEvidenceSource={workspaceEvidenceSource}
                onDownload={downloadPackage}
              />
            )}
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
  workspaceEvidenceSource,
  onDownload,
}: {
  pkg: EvidencePackage;
  workspaceEvidenceSource: string;
  onDownload: (pkg: EvidencePackage) => Promise<void>;
}) {
  const evSrc = evidenceSourcePill(resolvePackageEvidenceSource(pkg), workspaceEvidenceSource);
  const st = packageStatusPill(pkg.status);
  const ready = isPackageReady(pkg);

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
