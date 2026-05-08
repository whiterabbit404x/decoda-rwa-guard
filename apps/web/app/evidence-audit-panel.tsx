'use client';

import Link from 'next/link';
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
  alert_id?: string;
  detection_id?: string;
  asset_id?: string;
  asset_label?: string;
  evidence_source?: string;
  size_bytes?: number;
  package_ready?: boolean;
  download_url?: string | null;
  created_by?: string;
  retention_policy?: string;
  integrity_hash?: string | null;
  includes?: string[];
  missing_artifacts?: string[];
  chain_complete?: boolean;
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
};

/* ── Helpers ────────────────────────────────────────────────────── */

// Simulator evidence must always show evidence_source = simulator.
// Do not label simulator evidence as live_provider.
function evidenceSourcePill(
  rowSource?: string | null,
  workspaceSource?: string,
): { label: string; variant: PillVariant } {
  const raw = (rowSource ?? '').toLowerCase();
  if (
    raw === 'simulator' ||
    raw === 'demo' ||
    raw === 'replay' ||
    workspaceSource === 'simulator'
  ) {
    return { label: 'simulator', variant: 'info' };
  }
  if (raw === 'live' || raw === 'live_provider') {
    return { label: 'live_provider', variant: 'success' };
  }
  return { label: 'none', variant: 'neutral' };
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

  const [packages, setPackages] = useState<EvidencePackage[]>([]);
  const [auditRows, setAuditRows] = useState<AuditRow[]>([]);
  const [activeTab, setActiveTab] = useState<'packages' | 'audit'>('packages');
  const [selectedPkgId, setSelectedPkgId] = useState('');
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
        const [pkgRes, auditRes, raRes] = await Promise.allSettled([
          fetch(`${apiUrl}/exports`, { headers: hdrs, cache: 'no-store' }),
          fetch(`${apiUrl}/events`, { headers: hdrs, cache: 'no-store' }),
          fetch(`${apiUrl}/response-actions`, { headers: hdrs, cache: 'no-store' }),
        ]);

        if (pkgRes.status === 'fulfilled' && pkgRes.value.ok) {
          const p = (await pkgRes.value.json()) as { exports?: EvidencePackage[] };
          setPackages(p.exports ?? []);
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
          setResponseActionsCount(Array.isArray(actions) ? actions.length : 0);
        } else {
          setResponseActionsCount(0);
        }
      } finally {
        setDataLoading(false);
      }
    }

    void loadAll();
  }, [apiUrl, authHeaders, runtimeLoading]);

  async function createPackage() {
    setMessage('');
    const res = await fetch(`${apiUrl}/exports/history`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ format: 'csv' }),
    });
    const payload = (await res.json()) as { status?: string; detail?: string };
    setMessage(
      res.ok
        ? `Evidence package ${payload.status ?? 'queued'}.`
        : (payload.detail ?? 'Export failed.'),
    );
    if (res.ok) {
      const pkgRes = await fetch(`${apiUrl}/exports`, {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (pkgRes.ok) {
        const p = (await pkgRes.json()) as { exports?: EvidencePackage[] };
        setPackages(p.exports ?? []);
      }
    }
  }

  async function exportPackage(pkg: EvidencePackage, format: 'json' | 'csv') {
    const res = await fetch(`${apiUrl}/exports/${pkg.id ?? 'report'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ format }),
    });
    const payload = (await res.json()) as { status?: string; detail?: string };
    setMessage(
      res.ok
        ? `Export ${payload.status ?? 'queued'}.`
        : (payload.detail ?? 'Export failed.'),
    );
  }

  /* ── Derived metrics ─────────────────────────────────────────── */
  const exportReadyCount = packages.filter(isPackageReady).length;
  const retentionStatus = packages.length > 0 ? 'Compliant' : 'No packages';

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
    if (!responseActionOk) {
      return {
        title: 'Evidence package not ready',
        body: 'An incident exists, but no response action has been recommended or recorded yet.',
        ctaHref: '/response-actions',
        ctaLabel: 'Recommend Response',
      };
    }
    if (!packageExists) {
      return {
        title: 'Evidence package can be created',
        body: 'The incident chain is ready for exportable evidence.',
        ctaLabel: 'Create Evidence Package',
      };
    }
    return null;
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
                    const evSrc = evidenceSourcePill(pkg.evidence_source, workspaceEvidenceSource);
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
                            style={{
                              fontSize: '0.72rem',
                              padding: '0.15rem 0.45rem',
                              marginRight: '0.3rem',
                            }}
                            onClick={(e) => {
                              e.stopPropagation();
                              void exportPackage(pkg, 'json');
                            }}
                          >
                            Export JSON
                          </button>
                          {pkg.download_url ? (
                            <a
                              href={`${apiUrl}${pkg.download_url}`}
                              onClick={(e) => {
                                if (!ready) e.preventDefault();
                              }}
                            >
                              <button
                                type="button"
                                disabled={!ready}
                                className="btn btn-secondary"
                                style={{ fontSize: '0.72rem', padding: '0.15rem 0.45rem' }}
                              >
                                Download
                              </button>
                            </a>
                          ) : (
                            <button
                              type="button"
                              disabled={!ready}
                              className="btn btn-secondary"
                              style={{ fontSize: '0.72rem', padding: '0.15rem 0.45rem' }}
                            >
                              Download
                            </button>
                          )}
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
                apiUrl={apiUrl}
                authHeaders={authHeaders}
                onExport={exportPackage}
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
                  const evSrc = evidenceSourcePill(row.evidence_source, workspaceEvidenceSource);
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
  apiUrl,
  authHeaders,
  onExport,
}: {
  pkg: EvidencePackage;
  workspaceEvidenceSource: string;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  onExport: (pkg: EvidencePackage, format: 'json' | 'csv') => Promise<void>;
}) {
  const evSrc = evidenceSourcePill(pkg.evidence_source, workspaceEvidenceSource);
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
        {ready ? (
          <StatusPill label="Ready" variant="success" />
        ) : (
          <StatusPill label="Not Available" variant="neutral" />
        )}
      </div>

      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!ready}
          style={{ fontSize: '0.75rem' }}
          onClick={() => void onExport(pkg, 'json')}
        >
          Export JSON
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={!ready}
          style={{ fontSize: '0.75rem' }}
          onClick={() => void onExport(pkg, 'csv')}
        >
          Export CSV
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
  const evSrc = evidenceSourcePill(row.evidence_source, workspaceEvidenceSource);
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
