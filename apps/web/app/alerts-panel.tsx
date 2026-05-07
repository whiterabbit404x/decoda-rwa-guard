'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TableShell,
  type PillVariant,
} from './components/ui-primitives';
import { resolveApiUrl } from './dashboard-data';
import { usePilotAuth } from './pilot-auth-context';
import { useRuntimeSummary } from './runtime-summary-context';

/* ── Types ──────────────────────────────────────────────────────── */

type AlertRow = {
  id: string;
  title?: string | null;
  severity?: string | null;
  status?: string | null;
  target_id?: string | null;
  detection_id?: string | null;
  incident_id?: string | null;
  evidence_source?: string | null;
  evidence_origin?: string | null;
  source?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_seen_at?: string | null;
  payload?: {
    asset_label?: string | null;
    detection_type?: string | null;
    confidence?: string | null;
  } | null;
  detector_kind?: string | null;
  linked_evidence_count?: number | null;
  occurrence_count?: number | null;
  chain_linked_ids?: Record<string, string> | null;
};

/* ── Helpers ────────────────────────────────────────────────────── */

// Simulator evidence must always show evidence_source = simulator.
// Real provider evidence must show live_provider only when real data exists.
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

function severityPill(severity?: string | null): { label: string; variant: PillVariant } {
  const s = (severity ?? 'unknown').toLowerCase();
  if (s === 'critical') return { label: 'Critical', variant: 'danger' };
  if (s === 'high') return { label: 'High', variant: 'danger' };
  if (s === 'medium') return { label: 'Medium', variant: 'warning' };
  if (s === 'low') return { label: 'Low', variant: 'success' };
  if (s === 'info') return { label: 'Info', variant: 'info' };
  return { label: 'Unknown', variant: 'neutral' };
}

function statusPill(status?: string | null): { label: string; variant: PillVariant } {
  const s = (status ?? 'unknown').toLowerCase();
  if (s === 'open') return { label: 'Open', variant: 'danger' };
  if (s === 'investigating' || s === 'acknowledged') return { label: 'Investigating', variant: 'info' };
  if (s === 'linked_to_incident') return { label: 'Linked to Incident', variant: 'warning' };
  if (s === 'resolved') return { label: 'Resolved', variant: 'success' };
  if (s === 'suppressed') return { label: 'Suppressed', variant: 'neutral' };
  if (s === 'false_positive') return { label: 'False Positive', variant: 'neutral' };
  return { label: 'Unknown', variant: 'neutral' };
}

// Prevent "Linked to Incident" from appearing when no incident actually exists
function resolvedStatus(alert: AlertRow): string | null {
  if ((alert.status ?? '').toLowerCase() === 'linked_to_incident' && !alert.incident_id) {
    return 'open';
  }
  return alert.status ?? null;
}

function fmt(value?: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  const diff = Date.now() - parsed.getTime();
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return parsed.toLocaleDateString();
}

/* ── Constants ──────────────────────────────────────────────────── */

const ALERT_TABLE_HEADERS = ['Alert ID', 'Severity', 'Title', 'Asset', 'Status', 'Time', 'Action'];

/* ── Main panel ─────────────────────────────────────────────────── */

export default function AlertsPanel() {
  const { summary, runtime, loading: runtimeLoading } = useRuntimeSummary();
  const { authHeaders } = usePilotAuth();
  const apiUrl = resolveApiUrl();

  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [search, setSearch] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [evidenceSourceFilter, setEvidenceSourceFilter] = useState('');
  const [dataLoading, setDataLoading] = useState(false);
  const [message, setMessage] = useState('');

  const counts = runtime?.counts as Record<string, number> | undefined;
  const workspaceEvidenceSource: string = summary.evidence_source_summary ?? '';
  const telemetryOk = (counts?.telemetry_events ?? 0) > 0 || !!summary.last_telemetry_at;
  const detectionOk = (counts?.detections ?? 0) > 0 || !!(summary as any).last_detection_at;
  const activeAlerts: number = (counts?.active_alerts as number | undefined) ?? summary.active_alerts_count ?? 0;

  useEffect(() => {
    if (runtimeLoading) return;
    let cancelled = false;
    setDataLoading(true);
    async function loadAlerts() {
      try {
        const params = new URLSearchParams();
        if (severityFilter) params.set('severity', severityFilter);
        if (statusFilter) params.set('status_value', statusFilter);
        const res = await fetch(`${apiUrl}/alerts?${params.toString()}`, {
          headers: authHeaders(),
          cache: 'no-store',
        });
        if (!res.ok || cancelled) return;
        const json = (await res.json()) as Record<string, unknown>;
        const rows = (json.alerts ?? []) as AlertRow[];
        if (!cancelled) {
          setAlerts(rows);
          if (!selectedId && rows.length > 0) setSelectedId(rows[0].id);
        }
      } finally {
        if (!cancelled) setDataLoading(false);
      }
    }
    void loadAlerts();
    return () => {
      cancelled = true;
    };
  }, [apiUrl, authHeaders, runtimeLoading, severityFilter, statusFilter]);

  const filteredAlerts = useMemo(() => {
    return alerts.filter((a) => {
      const q = search.toLowerCase();
      const matchesSearch =
        !q ||
        (a.title ?? '').toLowerCase().includes(q) ||
        (a.id ?? '').toLowerCase().includes(q) ||
        (a.payload?.asset_label ?? '').toLowerCase().includes(q);
      const rawSrc = (a.evidence_source ?? a.evidence_origin ?? a.source ?? '').toLowerCase();
      const isSimulator =
        rawSrc === 'simulator' ||
        rawSrc === 'demo' ||
        rawSrc === 'replay' ||
        workspaceEvidenceSource === 'simulator';
      const isLive = rawSrc === 'live' || rawSrc === 'live_provider';
      const matchesEvidenceSource =
        !evidenceSourceFilter ||
        (evidenceSourceFilter === 'simulator' && isSimulator) ||
        (evidenceSourceFilter === 'live_provider' && isLive);
      return matchesSearch && matchesEvidenceSource;
    });
  }, [alerts, search, evidenceSourceFilter, workspaceEvidenceSource]);

  const selectedAlert = useMemo(
    () => filteredAlerts.find((a) => a.id === selectedId) ?? null,
    [filteredAlerts, selectedId],
  );

  const criticalCount = alerts.filter(
    (a) => (a.severity ?? '').toLowerCase() === 'critical',
  ).length;
  const highConfidenceCount = alerts.filter((a) => {
    const conf = (a.payload?.confidence ?? '').toLowerCase();
    return conf === 'high' || conf === 'critical';
  }).length;
  const linkedIncidentCount = alerts.filter((a) => !!a.incident_id).length;

  /* ── Empty state blocker ────────────────────────────────────────── */
  type Blocker = { title: string; body: string; ctaHref: string; ctaLabel: string };

  function getBlocker(): Blocker | null {
    if (!telemetryOk) {
      return {
        title: 'No alerts yet',
        body: 'No alerts yet because no telemetry has been received.',
        ctaHref: '/threat',
        ctaLabel: 'View Threat Monitoring',
      };
    }
    if (!detectionOk) {
      return {
        title: 'No alerts yet',
        body: 'Telemetry has been received, but no detection has been generated yet.',
        ctaHref: '/threat',
        ctaLabel: 'Run Detection',
      };
    }
    if (alerts.length === 0) {
      return {
        title: 'No alerts opened',
        body: 'Detections exist, but no alert has been opened yet.',
        ctaHref: '/threat',
        ctaLabel: 'Open Alert',
      };
    }
    return null;
  }

  const blocker = dataLoading ? null : getBlocker();

  return (
    <section className="featureSection">
      {/* ── Metric row ─────────────────────────────────────────────── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: '1rem',
          marginBottom: '1.5rem',
        }}
      >
        <MetricTile label="Active Alerts" value={activeAlerts} />
        <MetricTile label="Critical Alerts" value={criticalCount} />
        <MetricTile label="High Confidence" value={highConfidenceCount} />
        <MetricTile label="Linked Incidents" value={linkedIncidentCount} />
      </div>

      {/* ── Filter bar ─────────────────────────────────────────────── */}
      <div
        className="buttonRow"
        style={{ marginBottom: '1rem', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}
      >
        <input
          placeholder="Search alerts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ flex: '1 1 200px', minWidth: '180px' }}
          aria-label="Search alerts"
        />
        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          aria-label="Severity filter"
        >
          <option value="">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="info">Info</option>
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="Status filter"
        >
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="acknowledged">Investigating</option>
          <option value="resolved">Resolved</option>
          <option value="suppressed">Suppressed</option>
        </select>
        <select
          value={evidenceSourceFilter}
          onChange={(e) => setEvidenceSourceFilter(e.target.value)}
          aria-label="Evidence Source filter"
        >
          <option value="">All Sources</option>
          <option value="simulator">Simulator</option>
          <option value="live_provider">Live Provider</option>
        </select>
        <button
          type="button"
          className="btn btn-primary"
          disabled
          style={{ opacity: 0.45 }}
          title="Alert creation from detection is not yet configured"
        >
          Create Alert
        </button>
      </div>
      {/* ── Content ────────────────────────────────────────────────── */}
      {blocker ? (
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
            gridTemplateColumns: selectedAlert ? '1fr 380px' : '1fr',
            gap: '1rem',
            alignItems: 'start',
          }}
        >
          {/* ── Alert table ─────────────────────────────────────────── */}
          <div>
            {filteredAlerts.length === 0 && !dataLoading ? (
              <div className="emptyStatePanel sharedEmptyStateBlocker">
                <h4>No alerts match current filters</h4>
                <p className="muted">Adjust the filters above to see more results.</p>
              </div>
            ) : (
              <TableShell headers={ALERT_TABLE_HEADERS} compact>
                {filteredAlerts.map((alert) => {
                  const sev = severityPill(alert.severity);
                  const st = statusPill(resolvedStatus(alert));
                  const isSelected = alert.id === selectedId;
                  return (
                    <tr
                      key={alert.id}
                      onClick={() => setSelectedId(alert.id)}
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
                          maxWidth: '120px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                        title={alert.id}
                      >
                        {alert.id}
                      </td>
                      <td>
                        <StatusPill label={sev.label} variant={sev.variant} />
                      </td>
                      <td
                        style={{
                          maxWidth: '240px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {alert.title ?? '-'}
                      </td>
                      <td style={{ fontSize: '0.8rem' }}>
                        {alert.payload?.asset_label ?? alert.target_id ?? '-'}
                      </td>
                      <td>
                        <StatusPill label={st.label} variant={st.variant} />
                      </td>
                      <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>
                        {fmt(alert.created_at)}
                      </td>
                      <td>
                        {alert.incident_id ? (
                          <Link
                            href="/incidents"
                            prefetch={false}
                            className="btn btn-secondary"
                            style={{ fontSize: '0.73rem', padding: '0.2rem 0.5rem' }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            View Incident
                          </Link>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-secondary"
                            style={{ fontSize: '0.73rem', padding: '0.2rem 0.5rem' }}
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedId(alert.id);
                            }}
                          >
                            Open Incident
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </TableShell>
            )}
          </div>

          {/* ── Detail panel ────────────────────────────────────────── */}
          {selectedAlert && (
            <AlertDetailPanel
              alert={selectedAlert}
              apiUrl={apiUrl}
              authHeaders={authHeaders}
              workspaceEvidenceSource={workspaceEvidenceSource}
              onMessage={setMessage}
            />
          )}
        </div>
      )}

      {message ? (
        <p className="statusLine" style={{ marginTop: '0.5rem' }}>
          {message}
        </p>
      ) : null}
    </section>
  );
}

/* ── Alert detail panel ─────────────────────────────────────────── */

function AlertDetailPanel({
  alert,
  apiUrl,
  authHeaders,
  workspaceEvidenceSource,
  onMessage,
}: {
  alert: AlertRow;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  workspaceEvidenceSource: string;
  onMessage: (msg: string) => void;
}) {
  const [timeline, setTimeline] = useState<any[]>([]);

  useEffect(() => {
    if (!alert.incident_id) {
      setTimeline([]);
      return;
    }
    void fetch(`${apiUrl}/incidents/${alert.incident_id}/timeline`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => setTimeline((json as any)?.timeline ?? []))
      .catch(() => setTimeline([]));
  }, [apiUrl, authHeaders, alert.incident_id]);

  const sev = severityPill(alert.severity);
  const st = statusPill(resolvedStatus(alert));
  const evSrc = evidenceSourcePill(
    alert.evidence_source ?? alert.evidence_origin ?? alert.source,
    workspaceEvidenceSource,
  );

  async function openIncident() {
    const res = await fetch(`${apiUrl}/alerts/${alert.id}/escalate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        title: `Escalated alert: ${alert.title ?? alert.id}`,
        summary: alert.title ?? alert.id,
      }),
    });
    onMessage(res.ok ? 'Incident opened.' : 'Unable to open incident.');
  }

  return (
    <aside
      className="dataCard sharedSurfaceCard"
      style={{ padding: '1rem', borderLeft: '1px solid rgba(148,163,184,0.15)' }}
      aria-label="Alert detail"
    >
      <p className="eyebrow" style={{ marginBottom: '0.25rem', fontSize: '0.7rem' }}>
        Alert Detail
      </p>
      <h4 style={{ marginBottom: '0.75rem', fontSize: '0.95rem', lineHeight: 1.35 }}>
        {alert.title ?? 'Untitled Alert'}
      </h4>

      <div
        style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem 1rem', marginBottom: '0.75rem' }}
      >
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Severity</p>
          <StatusPill label={sev.label} variant={sev.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Status</p>
          <StatusPill label={st.label} variant={st.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Asset</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {alert.payload?.asset_label ?? alert.target_id ?? '-'}
          </p>
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Evidence Source</p>
          <StatusPill label={evSrc.label} variant={evSrc.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Detection Type</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {alert.payload?.detection_type ?? alert.detector_kind ?? '-'}
          </p>
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Confidence</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {alert.payload?.confidence ?? '-'}
          </p>
        </div>
      </div>

      {/* Linked Detection */}
      <div style={{ marginBottom: '0.6rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.2rem' }}>Linked Detection</p>
        {alert.detection_id ? (
          <p style={{ fontFamily: 'monospace', fontSize: '0.73rem', margin: 0, wordBreak: 'break-all' }}>
            {alert.detection_id}
          </p>
        ) : (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
            Detection link unavailable
          </p>
        )}
      </div>

      {/* Linked Incident */}
      <div style={{ marginBottom: '0.6rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.2rem' }}>Linked Incident</p>
        {alert.incident_id ? (
          <Link
            href="/incidents"
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.75rem', padding: '0.2rem 0.6rem' }}
          >
            View Incident
          </Link>
        ) : (
          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: '0.75rem', padding: '0.2rem 0.6rem' }}
            onClick={() => void openIncident()}
          >
            Open Incident
          </button>
        )}
      </div>

      {/* Timestamps */}
      <div style={{ marginBottom: '0.6rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Created At</p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>
          {alert.created_at ? new Date(alert.created_at).toLocaleString() : '-'}
        </p>
        <p className="tableMeta" style={{ marginBottom: '0.1rem', marginTop: '0.3rem' }}>
          Updated At
        </p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>
          {alert.updated_at ?? alert.last_seen_at
            ? new Date((alert.updated_at ?? alert.last_seen_at)!).toLocaleString()
            : '-'}
        </p>
      </div>

      {/* Timeline */}
      {timeline.length > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.3rem' }}>Timeline</p>
          {timeline.slice(0, 6).map((entry: any, i: number) => (
            <div
              key={i}
              style={{
                fontSize: '0.75rem',
                marginBottom: '0.3rem',
                paddingLeft: '0.5rem',
                borderLeft: '2px solid rgba(148,163,184,0.2)',
              }}
            >
              <span className="muted">{fmt(entry.created_at ?? entry.timestamp)}</span>
              {' · '}
              {entry.note ?? entry.event_type ?? entry.type ?? 'Event'}
            </div>
          ))}
        </div>
      )}

      {/* Next action */}
      <div style={{ marginTop: '0.75rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.3rem' }}>Recommended Next Action</p>
        {!alert.incident_id ? (
          <button
            type="button"
            className="btn btn-primary"
            style={{ fontSize: '0.78rem', marginRight: '0.4rem' }}
            onClick={() => void openIncident()}
          >
            Open Incident
          </button>
        ) : (
          <Link
            href="/incidents"
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.78rem', marginRight: '0.4rem' }}
          >
            View Incident
          </Link>
        )}
        <Link
          href="/response-actions"
          prefetch={false}
          className="btn btn-secondary"
          style={{ fontSize: '0.78rem' }}
        >
          Response Actions
        </Link>
      </div>
    </aside>
  );
}
