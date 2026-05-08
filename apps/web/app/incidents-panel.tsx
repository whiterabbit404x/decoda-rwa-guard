'use client';

import Link from 'next/link';
import { ReactNode, useEffect, useMemo, useState } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TabStrip,
  TableShell,
  type PillVariant,
} from './components/ui-primitives';
import { resolveApiUrl } from './dashboard-data';
import { usePilotAuth } from './pilot-auth-context';
import { useRuntimeSummary } from './runtime-summary-context';

/* ── Types ──────────────────────────────────────────────────────── */

type IncidentRow = {
  id: string;
  title?: string | null;
  severity?: string | null;
  workflow_status?: string | null;
  status?: string | null;
  owner_user_id?: string | null;
  assignee_user_id?: string | null;
  source_alert_id?: string | null;
  linked_alert_ids?: string[] | null;
  linked_detection_id?: string | null;
  linked_evidence_count?: number | null;
  linked_action_id?: string | null;
  target_id?: string | null;
  asset_label?: string | null;
  description?: string | null;
  impact?: string | null;
  risk_score?: number | null;
  normalized_risk?: string | null;
  evidence_source?: string | null;
  evidence_origin?: string | null;
  response_action_mode?: string | null;
  chain_linked_ids?: Record<string, string> | null;
  created_at?: string | null;
  resolved_at?: string | null;
  updated_at?: string | null;
};

type TimelineEntry = {
  id?: string;
  event_type?: string;
  message?: string;
  note?: string;
  actor?: string;
  system?: string;
  result?: string;
  evidence_source?: string;
  created_at?: string;
  timestamp?: string;
};

type AlertRow = {
  id: string;
  title?: string | null;
  severity?: string | null;
  status?: string | null;
  payload?: { detection_type?: string | null; confidence?: string | null; asset_label?: string | null } | null;
  detector_kind?: string | null;
  evidence_source?: string | null;
  evidence_origin?: string | null;
  source?: string | null;
};

type EvidenceRow = {
  id?: string;
  type?: string;
  source?: string;
  created_at?: string;
  included_in_package?: boolean;
  tx_hash?: string;
  block_number?: string | number;
};

type ResponseActionRow = {
  id?: string;
  action_type?: string;
  type?: string;
  status?: string;
  requires_approval?: boolean;
  evidence_source?: string;
  mode?: string;
};

/* ── Helpers ────────────────────────────────────────────────────── */

function severityPill(severity?: string | null): { label: string; variant: PillVariant } {
  const s = (severity ?? 'unknown').toLowerCase();
  if (s === 'critical') return { label: 'Critical', variant: 'danger' };
  if (s === 'high')     return { label: 'High',     variant: 'danger' };
  if (s === 'medium')   return { label: 'Medium',   variant: 'warning' };
  if (s === 'low')      return { label: 'Low',       variant: 'success' };
  if (s === 'info')     return { label: 'Info',      variant: 'info' };
  return { label: 'Unknown', variant: 'neutral' };
}

function incidentStatusPill(status?: string | null): { label: string; variant: PillVariant } {
  const s = (status ?? 'unknown').toLowerCase();
  if (s === 'open')               return { label: 'Open',               variant: 'danger' };
  if (s === 'investigating')      return { label: 'Investigating',      variant: 'info' };
  if (s === 'awaiting_response')  return { label: 'Awaiting Response',  variant: 'warning' };
  if (s === 'response_initiated') return { label: 'Response Initiated', variant: 'warning' };
  if (s === 'contained')          return { label: 'Awaiting Response',  variant: 'warning' };
  if (s === 'resolved')           return { label: 'Resolved',           variant: 'success' };
  if (s === 'closed')             return { label: 'Closed',             variant: 'neutral' };
  if (s === 'suppressed')         return { label: 'Suppressed',         variant: 'neutral' };
  if (s === 'reopened')           return { label: 'Reopened',           variant: 'warning' };
  return { label: 'Unknown', variant: 'neutral' };
}

// Simulator evidence must not be labeled as live_provider.
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

function fmtFull(value?: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleString();
}

function incidentAsset(incident: IncidentRow): string {
  return incident.asset_label ?? incident.target_id ?? '-';
}

function incidentStatus(incident: IncidentRow): string {
  return incident.workflow_status ?? incident.status ?? 'unknown';
}

/* ── Constants ──────────────────────────────────────────────────── */

const INCIDENT_TABLE_HEADERS = ['Incident ID', 'Severity', 'Title', 'Asset', 'Status', 'Created', 'Action'];

const DETAIL_TABS = [
  { key: 'overview',          label: 'Overview' },
  { key: 'timeline',          label: 'Timeline' },
  { key: 'alerts',            label: 'Alerts' },
  { key: 'evidence',          label: 'Evidence' },
  { key: 'response-actions',  label: 'Response Actions' },
] as const;

type TabKey = typeof DETAIL_TABS[number]['key'];

/* ── Main panel ─────────────────────────────────────────────────── */

export default function IncidentsPanel() {
  const { summary, runtime, loading: runtimeLoading } = useRuntimeSummary();
  const { authHeaders } = usePilotAuth();
  const apiUrl = resolveApiUrl();

  const [incidents, setIncidents] = useState<IncidentRow[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [search, setSearch] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [assigneeFilter, setAssigneeFilter] = useState('');
  const [dataLoading, setDataLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [activeTab, setActiveTab] = useState<TabKey>('overview');
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [linkedAlert, setLinkedAlert] = useState<AlertRow | null>(null);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [responseActions, setResponseActions] = useState<ResponseActionRow[]>([]);

  const counts = runtime?.counts as Record<string, number> | undefined;
  const workspaceEvidenceSource: string = summary.evidence_source_summary ?? '';
  const telemetryOk = (counts?.telemetry_events ?? 0) > 0 || !!summary.last_telemetry_at;
  const detectionOk = (counts?.detections ?? 0) > 0 || !!(summary as any).last_detection_at;
  const activeAlerts: number =
    (counts?.active_alerts as number | undefined) ?? summary.active_alerts_count ?? 0;

  useEffect(() => {
    if (runtimeLoading) return;
    let cancelled = false;
    setDataLoading(true);
    async function loadIncidents() {
      try {
        const params = new URLSearchParams();
        if (severityFilter) params.set('severity', severityFilter);
        if (statusFilter) params.set('status_value', statusFilter);
        if (assigneeFilter) params.set('assignee_user_id', assigneeFilter);
        const res = await fetch(`${apiUrl}/incidents?${params.toString()}`, {
          headers: authHeaders(),
          cache: 'no-store',
        });
        if (!res.ok || cancelled) return;
        const json = (await res.json()) as Record<string, unknown>;
        const rows = (json.incidents ?? []) as IncidentRow[];
        if (!cancelled) {
          setIncidents(rows);
          if (!selectedId && rows.length > 0) setSelectedId(rows[0].id);
        }
      } finally {
        if (!cancelled) setDataLoading(false);
      }
    }
    void loadIncidents();
    return () => { cancelled = true; };
  }, [apiUrl, authHeaders, runtimeLoading, severityFilter, statusFilter, assigneeFilter]);

  const filteredIncidents = useMemo(() => {
    return incidents.filter((inc) => {
      const q = search.toLowerCase();
      return (
        !q ||
        (inc.title ?? '').toLowerCase().includes(q) ||
        (inc.id ?? '').toLowerCase().includes(q) ||
        incidentAsset(inc).toLowerCase().includes(q)
      );
    });
  }, [incidents, search]);

  const selectedIncident = useMemo(
    () => filteredIncidents.find((i) => i.id === selectedId) ?? null,
    [filteredIncidents, selectedId],
  );

  /* ── Detail data loading ────────────────────────────────────── */
  useEffect(() => {
    if (!selectedId) {
      setTimeline([]);
      setLinkedAlert(null);
      setEvidence([]);
      setResponseActions([]);
      return;
    }
    void fetch(`${apiUrl}/incidents/${selectedId}/timeline`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => setTimeline((json as any)?.timeline ?? []))
      .catch(() => setTimeline([]));
  }, [apiUrl, authHeaders, selectedId]);

  useEffect(() => {
    const alertId = selectedIncident?.source_alert_id;
    if (!alertId) {
      setLinkedAlert(null);
      setEvidence([]);
      return;
    }
    void fetch(`${apiUrl}/alerts/${alertId}`, { headers: authHeaders(), cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => setLinkedAlert((json as any)?.alert ?? json ?? null))
      .catch(() => setLinkedAlert(null));
    void fetch(`${apiUrl}/alerts/${alertId}/evidence`, { headers: authHeaders(), cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        const ev = (json as any)?.evidence;
        if (!ev) { setEvidence([]); return; }
        setEvidence(Array.isArray(ev) ? ev : [ev]);
      })
      .catch(() => setEvidence([]));
  }, [apiUrl, authHeaders, selectedIncident?.source_alert_id]);

  useEffect(() => {
    if (!selectedId) { setResponseActions([]); return; }
    void fetch(`${apiUrl}/response/actions?incident_id=${selectedId}`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => setResponseActions((json as any)?.actions ?? []))
      .catch(() => setResponseActions([]));
  }, [apiUrl, authHeaders, selectedId]);

  /* ── Metrics ─────────────────────────────────────────────────── */
  const openCount = incidents.filter((i) =>
    ['open', 'reopened'].includes(incidentStatus(i).toLowerCase()),
  ).length;
  const criticalCount = incidents.filter(
    (i) => (i.severity ?? '').toLowerCase() === 'critical',
  ).length;
  const investigatingCount = incidents.filter(
    (i) => incidentStatus(i).toLowerCase() === 'investigating',
  ).length;
  const awaitingCount = incidents.filter((i) =>
    ['contained', 'awaiting_response'].includes(incidentStatus(i).toLowerCase()),
  ).length;

  /* ── Empty state ─────────────────────────────────────────────── */
  type Blocker = { title: string; body: string; ctaHref?: string; ctaLabel?: string };

  function getBlocker(): Blocker | null {
    if (!telemetryOk) {
      return {
        title: 'No incidents yet',
        body: 'No incidents can be opened because no telemetry has been received.',
        ctaHref: '/threat',
        ctaLabel: 'View Threat Monitoring',
      };
    }
    if (!detectionOk) {
      return {
        title: 'No incidents yet',
        body: 'Telemetry has been received, but no detection has been generated yet.',
      };
    }
    if (activeAlerts === 0) {
      return {
        title: 'No incidents yet',
        body: 'Detections exist, but no alert has been opened yet.',
        ctaHref: '/alerts',
        ctaLabel: 'Open Alert',
      };
    }
    if (incidents.length === 0) {
      return {
        title: 'No incidents opened',
        body: 'Alerts exist, but no incident has been opened yet.',
        ctaHref: '/alerts',
        ctaLabel: 'Open Incident',
      };
    }
    return null;
  }

  const blocker = dataLoading ? null : getBlocker();

  return (
    <section className="featureSection">
      {/* ── Metric row ──────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem', marginBottom: '1.5rem' }}>
        <MetricTile label="Open Incidents"    value={openCount} />
        <MetricTile label="Critical Incidents" value={criticalCount} />
        <MetricTile label="In Investigation"  value={investigatingCount} />
        <MetricTile label="Awaiting Response" value={awaitingCount} />
      </div>

      {/* ── Filter bar ──────────────────────────────────────────── */}
      <div className="buttonRow" style={{ marginBottom: '1rem', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
        <input placeholder="Search incidents..." value={search} onChange={(e) => setSearch(e.target.value)}
          style={{ flex: '1 1 200px', minWidth: '180px' }} aria-label="Search incidents" />
        <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)} aria-label="Severity filter">
          <option value="">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="info">Info</option>
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="Status filter">
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="investigating">Investigating</option>
          <option value="contained">Awaiting Response</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
          <option value="suppressed">Suppressed</option>
        </select>
        <input placeholder="Assignee user ID..." value={assigneeFilter} onChange={(e) => setAssigneeFilter(e.target.value)}
          style={{ width: '180px' }} aria-label="Assignee filter" />
        <button type="button" className="btn btn-primary" disabled style={{ opacity: 0.45 }}
          title="Incident creation from alert requires alert escalation — use View Alert → Open Incident">
          Create Incident
        </button>
      </div>

      {/* ── Content ─────────────────────────────────────────────── */}
      {blocker ? (
        <EmptyStateBlocker title={blocker.title} body={blocker.body} ctaHref={blocker.ctaHref} ctaLabel={blocker.ctaLabel} />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: selectedIncident ? '1fr 400px' : '1fr', gap: '1rem', alignItems: 'start' }}>
          {/* ── Incidents table ────────────────────────────────── */}
          <div>
            {filteredIncidents.length === 0 && !dataLoading ? (
              <div className="emptyStatePanel sharedEmptyStateBlocker">
                <h4>No incidents match current filters</h4>
                <p className="muted">Adjust the filters above to see more results.</p>
              </div>
            ) : (
              <TableShell headers={INCIDENT_TABLE_HEADERS} compact>
                {filteredIncidents.map((incident) => {
                  const sev = severityPill(incident.severity);
                  const st  = incidentStatusPill(incidentStatus(incident));
                  const isSelected = incident.id === selectedId;
                  return (
                    <tr key={incident.id} onClick={() => setSelectedId(incident.id)}
                      style={{ cursor: 'pointer', background: isSelected ? 'rgba(59,130,246,0.08)' : undefined }}>
                      <td style={{ fontFamily: 'monospace', fontSize: '0.75rem', whiteSpace: 'nowrap',
                        maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis' }} title={incident.id}>
                        {incident.id}
                      </td>
                      <td><StatusPill label={sev.label} variant={sev.variant} /></td>
                      <td style={{ maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {incident.title ?? '-'}
                      </td>
                      <td style={{ fontSize: '0.8rem' }}>{incidentAsset(incident)}</td>
                      <td><StatusPill label={st.label} variant={st.variant} /></td>
                      <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>{fmt(incident.created_at)}</td>
                      <td>
                        <button type="button" className="btn btn-secondary"
                          style={{ fontSize: '0.73rem', padding: '0.2rem 0.5rem' }}
                          onClick={(e) => { e.stopPropagation(); setSelectedId(incident.id); setActiveTab('overview'); }}>
                          View Incident
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </TableShell>
            )}
          </div>

          {/* ── Detail panel ────────────────────────────────────── */}
          {selectedIncident && (
            <IncidentDetailPanel
              incident={selectedIncident}
              timeline={timeline}
              linkedAlert={linkedAlert}
              evidence={evidence}
              responseActions={responseActions}
              activeTab={activeTab}
              onTabChange={(tab) => setActiveTab(tab as TabKey)}
              workspaceEvidenceSource={workspaceEvidenceSource}
              onMessage={setMessage}
            />
          )}
        </div>
      )}

      {message ? (
        <p className="statusLine" style={{ marginTop: '0.5rem' }}>{message}</p>
      ) : null}
    </section>
  );
}

/* ── Incident detail panel ──────────────────────────────────────── */

function IncidentDetailPanel({ incident, timeline, linkedAlert, evidence, responseActions,
  activeTab, onTabChange, workspaceEvidenceSource, onMessage: _onMessage }: {
  incident: IncidentRow; timeline: TimelineEntry[]; linkedAlert: AlertRow | null;
  evidence: EvidenceRow[]; responseActions: ResponseActionRow[];
  activeTab: string; onTabChange: (tab: string) => void;
  workspaceEvidenceSource: string; onMessage: (msg: string) => void;
}) {
  const sev = severityPill(incident.severity);
  const st  = incidentStatusPill(incidentStatus(incident));
  const ws  = incidentStatus(incident).toLowerCase();
  const evSrc = evidenceSourcePill(incident.evidence_source ?? incident.evidence_origin, workspaceEvidenceSource);
  const hasLinkedAlert = !!incident.source_alert_id;
  const linkedEvidenceCount = Number(incident.linked_evidence_count ?? 0);

  const progress = [
    { label: 'Alert Received', done: hasLinkedAlert },
    { label: 'Investigation Started', done: ['investigating', 'contained', 'resolved', 'closed', 'reopened'].includes(ws) },
    { label: 'Evidence Collected', done: linkedEvidenceCount > 0 || evidence.length > 0 },
    { label: 'Response Initiated', done: responseActions.length > 0 || ['response_initiated', 'contained', 'resolved', 'closed'].includes(ws) },
    { label: 'Resolution', done: ['resolved', 'closed'].includes(ws) },
  ];

  function recommendedNextAction(): string {
    if (!['investigating', 'contained', 'resolved', 'closed', 'reopened'].includes(ws)) return 'Start Investigation';
    if (linkedEvidenceCount === 0 && evidence.length === 0) return 'Collect Evidence';
    if (responseActions.length === 0 && !['resolved', 'closed'].includes(ws)) return 'Recommend Response';
    if (!['resolved', 'closed'].includes(ws)) return 'Resolve Incident';
    return 'Resolved';
  }

  return (
    <aside className="dataCard sharedSurfaceCard"
      style={{ padding: 0, borderLeft: '1px solid rgba(148,163,184,0.15)', overflow: 'hidden' }}
      aria-label="Incident detail">
      {/* ── Case file header ───────────────────────────────────── */}
      <div style={{ padding: '1rem', background: 'rgba(59,130,246,0.06)', borderBottom: '1px solid rgba(148,163,184,0.12)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
          <p className="sectionEyebrow" style={{ margin: 0 }}>Case File</p>
          <StatusPill label={sev.label} variant={sev.variant} />
        </div>
        <h4 style={{ margin: '0 0 0.75rem', fontSize: '0.95rem', lineHeight: 1.35 }}>
          {incident.title ?? 'Untitled Incident'}
        </h4>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem 1rem' }}>
          <DetailField label="Incident ID" value={<span style={{ fontFamily: 'monospace', fontSize: '0.72rem' }}>{incident.id}</span>} />
          <DetailField label="Status" value={<StatusPill label={st.label} variant={st.variant} />} />
          <DetailField label="Created" value={fmtFull(incident.created_at)} />
          <DetailField label="Asset" value={incidentAsset(incident)} />
          <DetailField label="Assigned To" value={incident.owner_user_id ?? incident.assignee_user_id ?? 'Unassigned'} />
          <DetailField label="Linked Alert" value={
            hasLinkedAlert
              ? <Link href="/alerts" prefetch={false} style={{ fontSize: '0.78rem', color: 'var(--text-accent)' }}>{incident.source_alert_id}</Link>
              : <span className="muted" style={{ fontSize: '0.78rem' }}>Linked alert unavailable</span>
          } />
          <DetailField label="Evidence Source" value={<StatusPill label={evSrc.label} variant={evSrc.variant} />} />
          <DetailField label="Next Action" value={recommendedNextAction()} />
        </div>
      </div>

      {/* ── Tabs ───────────────────────────────────────────────── */}
      <div style={{ padding: '0.75rem 1rem 0' }}>
        <TabStrip tabs={DETAIL_TABS.map((t) => ({ key: t.key, label: t.label }))} active={activeTab} onChange={onTabChange} />
      </div>

      {/* ── Tab content ────────────────────────────────────────── */}
      <div style={{ padding: '0.75rem 1rem 1rem' }}>
        {activeTab === 'overview' && <OverviewTab incident={incident} progress={progress} />}
        {activeTab === 'timeline' && <TimelineTab timeline={timeline} />}
        {activeTab === 'alerts' && <AlertsTab linkedAlert={linkedAlert} hasLinkedAlert={hasLinkedAlert} workspaceEvidenceSource={workspaceEvidenceSource} />}
        {activeTab === 'evidence' && <EvidenceTab evidence={evidence} workspaceEvidenceSource={workspaceEvidenceSource} />}
        {activeTab === 'response-actions' && <ResponseActionsTab actions={responseActions} incidentId={incident.id} />}
      </div>
    </aside>
  );
}

/* ── Detail field helper ─────────────────────────────────────────── */
function DetailField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="tableMeta" style={{ margin: '0 0 0.1rem', fontSize: '0.72rem' }}>{label}</p>
      <div style={{ fontSize: '0.8rem' }}>{value}</div>
    </div>
  );
}

/* ── Overview tab ────────────────────────────────────────────────── */
function OverviewTab({ incident, progress }: { incident: IncidentRow; progress: Array<{ label: string; done: boolean }> }) {
  return (
    <div>
      <div style={{ marginBottom: '0.75rem' }}>
        <p className="sectionEyebrow">Description</p>
        <p style={{ fontSize: '0.85rem', margin: 0, color: 'var(--text-secondary)' }}>
          {incident.description ?? 'No description provided.'}
        </p>
      </div>
      <div style={{ marginBottom: '0.75rem' }}>
        <p className="sectionEyebrow">Impact</p>
        <p style={{ fontSize: '0.85rem', margin: 0, color: 'var(--text-secondary)' }}>
          {incident.impact ?? 'Impact not assessed.'}
        </p>
      </div>
      {(incident.risk_score != null || incident.normalized_risk) && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow">Risk Score</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ fontSize: '1.4rem', fontWeight: 700 }}>
              {incident.risk_score != null ? `${Math.round(Number(incident.risk_score))} / 100` : '-'}
            </span>
            {incident.normalized_risk && (
              <StatusPill label={incident.normalized_risk}
                variant={['high', 'critical'].includes(incident.normalized_risk.toLowerCase()) ? 'danger' : 'warning'} />
            )}
          </div>
        </div>
      )}
      <div>
        <p className="sectionEyebrow">Incident Progress</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
          {progress.map(({ label, done }, i) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', fontSize: '0.83rem' }}>
              <span style={{
                width: '18px', height: '18px', borderRadius: '50%',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '0.65rem', fontWeight: 700,
                background: done ? 'rgba(34,197,94,0.18)' : 'rgba(148,163,184,0.12)',
                border: `1px solid ${done ? 'rgba(34,197,94,0.5)' : 'rgba(148,163,184,0.25)'}`,
                color: done ? 'var(--success-fg)' : 'var(--text-muted)',
                flexShrink: 0,
              }}>
                {done ? '✓' : i + 1}
              </span>
              <span style={{ color: done ? 'var(--text-primary)' : 'var(--text-muted)', flex: 1 }}>
                {label}
              </span>
              <StatusPill label={done ? 'Completed' : 'Pending'} variant={done ? 'success' : 'neutral'} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── Timeline tab ────────────────────────────────────────────────── */
const TIMELINE_HEADERS = ['Time', 'Event', 'Actor / System', 'Result', 'Evidence Source'];

function TimelineTab({ timeline }: { timeline: TimelineEntry[] }) {
  if (timeline.length === 0) {
    return <p className="muted" style={{ fontSize: '0.85rem' }}>No timeline events yet.</p>;
  }
  return (
    <TableShell headers={TIMELINE_HEADERS} compact>
      {timeline.map((entry, i) => (
        <tr key={entry.id ?? i}>
          <td style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{fmt(entry.created_at ?? entry.timestamp)}</td>
          <td style={{ fontSize: '0.8rem' }}>{entry.event_type ?? entry.message ?? entry.note ?? 'Event'}</td>
          <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{entry.actor ?? entry.system ?? 'System'}</td>
          <td style={{ fontSize: '0.78rem' }}>{entry.result ?? '-'}</td>
          <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{entry.evidence_source ?? '-'}</td>
        </tr>
      ))}
    </TableShell>
  );
}

/* ── Alerts tab ──────────────────────────────────────────────────── */
const ALERTS_TAB_HEADERS = ['Alert ID', 'Severity', 'Title', 'Detection Type', 'Confidence', 'Status'];

function AlertsTab({ linkedAlert, hasLinkedAlert, workspaceEvidenceSource }: {
  linkedAlert: AlertRow | null; hasLinkedAlert: boolean; workspaceEvidenceSource: string;
}) {
  if (!hasLinkedAlert) {
    return (
      <div className="emptyStatePanel sharedEmptyStateBlocker" style={{ padding: '0.75rem' }}>
        <h4 style={{ fontSize: '0.9rem', marginBottom: '0.35rem' }}>Linked alert unavailable</h4>
        <p className="muted" style={{ fontSize: '0.82rem', marginBottom: '0.5rem' }}>
          This incident has no linked alert. No alert link will be shown without a valid alert.
        </p>
        <Link href="/alerts" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.78rem' }}>View Alerts</Link>
      </div>
    );
  }
  if (!linkedAlert) return <p className="muted" style={{ fontSize: '0.85rem' }}>Loading linked alert…</p>;
  const sev = severityPill(linkedAlert.severity);
  const alertStatus = linkedAlert.status ?? 'unknown';
  return (
    <TableShell headers={ALERTS_TAB_HEADERS} compact>
      <tr>
        <td style={{ fontFamily: 'monospace', fontSize: '0.72rem', maxWidth: '90px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {linkedAlert.id}
        </td>
        <td><StatusPill label={sev.label} variant={sev.variant} /></td>
        <td style={{ fontSize: '0.8rem', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {linkedAlert.title ?? '-'}
        </td>
        <td style={{ fontSize: '0.78rem' }}>{linkedAlert.payload?.detection_type ?? linkedAlert.detector_kind ?? '-'}</td>
        <td style={{ fontSize: '0.78rem' }}>{linkedAlert.payload?.confidence ?? '-'}</td>
        <td><StatusPill label={alertStatus} variant="neutral" /></td>
      </tr>
    </TableShell>
  );
}

/* ── Evidence tab ────────────────────────────────────────────────── */
const EVIDENCE_HEADERS = ['Evidence ID', 'Type', 'Source', 'Created', 'In Package', 'Action'];

function EvidenceTab({ evidence, workspaceEvidenceSource }: { evidence: EvidenceRow[]; workspaceEvidenceSource: string }) {
  if (evidence.length === 0) {
    return (
      <div>
        <p className="muted" style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}>No evidence collected for this incident yet.</p>
        <Link href="/evidence" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.78rem' }}>View Evidence</Link>
      </div>
    );
  }
  return (
    <TableShell headers={EVIDENCE_HEADERS} compact>
      {evidence.map((ev, i) => {
        const rawSrc = (ev.source ?? '').toLowerCase();
        const isSimulator = rawSrc === 'simulator' || rawSrc === 'demo' || rawSrc === 'replay' || workspaceEvidenceSource === 'simulator';
        const srcLabel = isSimulator ? 'simulator' : rawSrc === 'live' || rawSrc === 'live_provider' ? 'live_provider' : (ev.source ?? '-');
        const srcVariant: PillVariant = isSimulator ? 'info' : rawSrc === 'live' || rawSrc === 'live_provider' ? 'success' : 'neutral';
        return (
          <tr key={ev.id ?? i}>
            <td style={{ fontFamily: 'monospace', fontSize: '0.72rem' }}>{ev.id ?? `EV-${i + 1}`}</td>
            <td style={{ fontSize: '0.78rem' }}>{ev.type ?? 'blockchain'}</td>
            <td><StatusPill label={srcLabel} variant={srcVariant} /></td>
            <td style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{fmt(ev.created_at)}</td>
            <td><StatusPill label={ev.included_in_package ? 'Yes' : 'No'} variant={ev.included_in_package ? 'success' : 'neutral'} /></td>
            <td>
              <Link href="/evidence" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.72rem', padding: '0.15rem 0.4rem' }}>
                Export Evidence
              </Link>
            </td>
          </tr>
        );
      })}
    </TableShell>
  );
}

/* ── Response Actions tab ────────────────────────────────────────── */
const RESPONSE_HEADERS = ['Action', 'Type', 'Status', 'Requires Approval', 'Evidence Source', 'Action'];

function ResponseActionsTab({ actions, incidentId: _incidentId }: { actions: ResponseActionRow[]; incidentId: string }) {
  if (actions.length === 0) {
    return (
      <div>
        <p className="muted" style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}>No response action recommended yet.</p>
        <Link href="/response-actions" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.78rem' }}>
          Recommend Response
        </Link>
      </div>
    );
  }
  return (
    <TableShell headers={RESPONSE_HEADERS} compact>
      {actions.map((action, i) => (
        <tr key={action.id ?? i}>
          <td style={{ fontSize: '0.8rem', maxWidth: '100px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {action.action_type ?? action.type ?? '-'}
          </td>
          <td><StatusPill label={action.mode ?? 'simulated'} variant="info" /></td>
          <td><StatusPill label={action.status ?? 'pending'}
            variant={action.status === 'succeeded' ? 'success' : action.status === 'failed' ? 'danger' : 'neutral'} /></td>
          <td><StatusPill label={action.requires_approval ? 'Yes' : 'No'} variant={action.requires_approval ? 'warning' : 'neutral'} /></td>
          <td style={{ fontSize: '0.75rem' }}>{action.evidence_source ?? '-'}</td>
          <td>
            <Link href="/response-actions" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.72rem', padding: '0.15rem 0.4rem' }}>
              View Response
            </Link>
          </td>
        </tr>
      ))}
    </TableShell>
  );
}
