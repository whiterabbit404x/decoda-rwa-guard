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
} from '../components/ui-primitives';
import { resolveApiUrl } from '../dashboard-data';
import { usePilotAuth } from '../pilot-auth-context';
import { useRuntimeSummary } from '../runtime-summary-context';
import RuntimeSummaryPanel from '../runtime-summary-panel';
import { fetchRuntimeStatusDeduped } from '../runtime-status-client';
import {
  hasRealTelemetryBackedChain,
  resolveWorkspaceMonitoringTruth,
} from '../workspace-monitoring-truth';

type ActionRow = {
  id: string;
  action: string;
  type: string;
  impact: string;
  status: string;
  recommendedBy: string;
  linkedIncident: string | null;
  evidenceSource: string;
  requiresApproval: boolean;
  simulated: boolean;
  eta?: string | null;
  approvalState?: string | null;
  createdAt?: string | null;
};

type HistoryRow = {
  id: string;
  action: string;
  type: string;
  result: string;
  actorSystem: string;
  time: string | null;
  evidenceSource: string;
  simulated: boolean;
};

const RECOMMENDED_HEADERS = [
  'Action',
  'Type',
  'Impact',
  'Status',
  'Recommended By',
  'Linked Incident',
  'Evidence Source',
  'Requires Approval',
];

const HISTORY_HEADERS = [
  'Action ID',
  'Action',
  'Type',
  'Result',
  'Actor/System',
  'Time',
  'Evidence Source',
];

function evidenceSourcePill(
  rowSource?: string | null,
  workspaceSource?: string,
): { label: string; variant: PillVariant } {
  const raw = (rowSource ?? '').toLowerCase();
  const workspace = (workspaceSource ?? '').toLowerCase();

  // Do not label simulator evidence as live_provider.
  if (
    raw === 'simulator' ||
    raw === 'demo' ||
    raw === 'replay' ||
    raw === 'fallback' ||
    workspace === 'simulator'
  ) {
    return { label: 'simulator', variant: 'info' };
  }

  if (raw === 'live' || raw === 'live_provider') {
    return { label: 'live_provider', variant: 'success' };
  }

  return { label: 'none', variant: 'neutral' };
}

function actionStatusPill(status: string, simulated: boolean): { label: string; variant: PillVariant } {
  const base = status.toLowerCase().replace(/\s路\ssimulated$/, '').trim();
  const tag = simulated ? ' 路 SIMULATED' : '';

  if (base === 'recommended') return { label: `Recommended${tag}`, variant: 'info' };
  if (base === 'pending_approval' || base === 'pending approval') {
    return { label: `Pending Approval${tag}`, variant: 'warning' };
  }
  if (base === 'approved') return { label: `Approved${tag}`, variant: 'success' };
  if (base === 'simulated') return { label: `Simulated${tag}`, variant: 'info' };
  if (base === 'executed') return { label: `Executed${tag}`, variant: 'success' };
  if (base === 'failed') return { label: `Failed${tag}`, variant: 'danger' };
  if (base === 'cancelled') return { label: `Cancelled${tag}`, variant: 'neutral' };

  const display = base ? base.charAt(0).toUpperCase() + base.slice(1) : 'Unknown';
  return { label: `${display}${tag}`, variant: 'neutral' };
}

function impactPill(impact: string): { label: string; variant: PillVariant } {
  const i = impact.toLowerCase();

  if (i === 'critical') return { label: 'Critical', variant: 'danger' };
  if (i === 'high') return { label: 'High', variant: 'danger' };
  if (i === 'medium') return { label: 'Medium', variant: 'warning' };
  if (i === 'low') return { label: 'Low', variant: 'success' };
  if (i === 'informational' || i === 'info') return { label: 'Informational', variant: 'info' };

  return { label: 'Unknown', variant: 'neutral' };
}

function fmt(value?: string | null): string {
  if (!value) return '-';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';

  const diff = Date.now() - parsed.getTime();

  if (diff < 60_000) return `${Math.max(0, Math.floor(diff / 1000))}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;

  return parsed.toLocaleDateString();
}

function normalizeActionRow(input: any, validIncidentIds: Set<string>): ActionRow {
  const mode = String(input?.mode || input?.response_action_mode || '').toLowerCase();
  const source = String(input?.source || input?.evidence_source || '').toLowerCase();

  const simulated =
    mode === 'simulated' ||
    mode === 'recommended' ||
    source === 'fallback' ||
    source === 'simulator' ||
    source === 'demo' ||
    source === 'replay';

  const rawStatus = String(input?.status || input?.workflow_status || 'recommended');
  const rawIncidentId = String(input?.incident_id || input?.linked_incident_id || '');

  // Do not show linked incident unless a valid incident exists in the system.
  const linkedIncident = rawIncidentId && validIncidentIds.has(rawIncidentId) ? rawIncidentId : null;

  return {
    id: String(input?.id || `${input?.action_type || 'action'}-${rawIncidentId || 'none'}`),
    action: String(input?.action_type || input?.action || 'Response action'),
    type: String(input?.category || input?.type || 'Other'),
    impact: String(input?.impact || input?.severity || 'medium'),
    status: simulated ? `${rawStatus} 路 SIMULATED` : rawStatus,
    recommendedBy: String(input?.recommended_by || input?.actor_type || 'Policy engine'),
    linkedIncident,
    evidenceSource: String(input?.evidence_source || input?.source || 'runtime'),
    requiresApproval: input?.requires_approval !== false,
    simulated,
    eta: input?.eta ?? input?.estimated_duration ?? input?.estimated_impact ?? null,
    approvalState:
      input?.approval_state ?? (input?.requires_approval === false ? 'not_required' : 'pending_approval'),
    createdAt: input?.created_at ?? input?.timestamp ?? null,
  };
}

function normalizeHistoryRow(input: any): HistoryRow {
  const source = String(
    input?.details_json?.source || input?.evidence_source || input?.source || '',
  ).toLowerCase();

  const simulated = source === 'fallback' || source === 'simulator' || source === 'demo';

  return {
    id: String(input?.id || '-'),
    action: String(input?.action_type || input?.action || '-'),
    type: String(input?.object_type || input?.type || '-'),
    result: String(input?.details_json?.result_summary || input?.result || input?.status || 'recorded'),
    actorSystem: String(input?.actor_type || input?.actor || 'system'),
    time: input?.created_at ?? input?.timestamp ?? null,
    evidenceSource: String(input?.details_json?.source || input?.evidence_source || input?.source || 'runtime'),
    simulated,
  };
}

type Blocker = {
  title: string;
  body: string;
  ctaHref?: string;
  ctaLabel?: string;
};

export default function ResponseActionsPageClient({ apiUrl: providedApiUrl }: { apiUrl: string }) {
  const { summary, runtime, loading: runtimeLoading } = useRuntimeSummary();
  const { authHeaders } = usePilotAuth();

  const apiUrl = providedApiUrl || resolveApiUrl();
  const summaryAny = summary as any;
  const counts = runtime?.counts as Record<string, number> | undefined;

  const [tab, setTab] = useState<'recommended' | 'history'>('recommended');
  const [recommendedRows, setRecommendedRows] = useState<ActionRow[]>([]);
  const [historyRows, setHistoryRows] = useState<HistoryRow[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [approvalFilter, setApprovalFilter] = useState('');
  const [dataLoading, setDataLoading] = useState(false);
  const [liveExecutionAllowed, setLiveExecutionAllowed] = useState(false);
  const [message, setMessage] = useState('');

  const workspaceEvidenceSource: string = summaryAny.evidence_source_summary ?? summaryAny.evidence_source ?? '';
  const telemetryOk = (counts?.telemetry_events ?? 0) > 0 || !!summaryAny.last_telemetry_at;
  const detectionOk = (counts?.detections ?? 0) > 0 || !!summaryAny.last_detection_at;
  const activeAlerts: number =
    (counts?.active_alerts as number | undefined) ?? summaryAny.active_alerts_count ?? 0;
  const activeIncidents: number =
    (counts?.open_incidents as number | undefined) ?? summaryAny.active_incidents_count ?? 0;

  useEffect(() => {
    if (runtimeLoading) return;

    let cancelled = false;
    setDataLoading(true);

    async function load() {
      try {
        const headers = authHeaders();

        const [actionsRes, historyRes, alertsRes, incidentsRes, runtimePayload] = await Promise.all([
          fetch(`${apiUrl}/response/actions?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
          fetch(`${apiUrl}/history/actions?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
          fetch(`${apiUrl}/alerts?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
          fetch(`${apiUrl}/incidents?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
          fetchRuntimeStatusDeduped(headers).catch(() => null),
        ]);

        if (cancelled) return;

        const actionsPayload = actionsRes?.ok ? await actionsRes.json() : {};
        const historyPayload = historyRes?.ok ? await historyRes.json() : {};
        const alertsPayload = alertsRes?.ok ? await alertsRes.json() : {};
        const incidentsPayload = incidentsRes?.ok ? await incidentsRes.json() : {};

        const incidentIds = new Set<string>(
          (Array.isArray(incidentsPayload?.incidents) ? incidentsPayload.incidents : [])
            .map((item: any) => String(item?.id || ''))
            .filter(Boolean),
        );

        const alertIncidentIds = new Set<string>(
          (Array.isArray(alertsPayload?.alerts) ? alertsPayload.alerts : [])
            .map((item: any) => String(item?.incident_id || ''))
            .filter(Boolean),
        );

        const validIncidentIds = new Set<string>([...incidentIds, ...alertIncidentIds]);

        const recommended = (Array.isArray(actionsPayload?.actions) ? actionsPayload.actions : []).map(
          (item: any) => normalizeActionRow(item, validIncidentIds),
        );

        const history = (Array.isArray(historyPayload?.history) ? historyPayload.history : [])
          .filter(
            (item: any) =>
              String(item?.object_type || '').includes('response_action') ||
              String(item?.action_type || '').includes('response'),
          )
          .map(normalizeHistoryRow);

        if (!cancelled) {
          setRecommendedRows(recommended);
          setHistoryRows(history);

          if (!selectedId && recommended.length > 0) {
            setSelectedId(recommended[0].id);
          }

          // Live execution claims are hidden until canonical runtime summary confirms a real telemetry-backed chain.
          setLiveExecutionAllowed(
            hasRealTelemetryBackedChain(resolveWorkspaceMonitoringTruth(runtimePayload)),
          );
        }
      } finally {
        if (!cancelled) setDataLoading(false);
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [apiUrl, authHeaders, runtimeLoading, selectedId]);

  const filteredRecommended = useMemo(() => {
    return recommendedRows.filter((row) => {
      const q = search.toLowerCase();
      const matchesSearch =
        !q ||
        row.action.toLowerCase().includes(q) ||
        row.id.toLowerCase().includes(q) ||
        (row.linkedIncident ?? '').toLowerCase().includes(q);

      const matchesType = !typeFilter || row.type.toLowerCase().includes(typeFilter.toLowerCase());
      const matchesStatus = !statusFilter || row.status.toLowerCase().includes(statusFilter.toLowerCase());
      const matchesApproval =
        !approvalFilter ||
        (approvalFilter === 'yes' && row.requiresApproval) ||
        (approvalFilter === 'no' && !row.requiresApproval);

      return matchesSearch && matchesType && matchesStatus && matchesApproval;
    });
  }, [recommendedRows, search, typeFilter, statusFilter, approvalFilter]);

  const filteredHistory = useMemo(() => {
    return historyRows.filter((row) => {
      const q = search.toLowerCase();
      return !q || row.action.toLowerCase().includes(q) || row.id.toLowerCase().includes(q);
    });
  }, [historyRows, search]);

  const activeRows = tab === 'recommended' ? filteredRecommended : filteredHistory;

  const selectedAction = useMemo(
    () => filteredRecommended.find((r) => r.id === selectedId) ?? filteredRecommended[0] ?? null,
    [filteredRecommended, selectedId],
  );

  const recommendedCount = recommendedRows.length;
  const pendingApprovalCount = recommendedRows.filter((r) =>
    r.status.toLowerCase().includes('pending'),
  ).length;
  const simulatedCount = recommendedRows.filter((r) => r.simulated).length;
  const executedCount = recommendedRows.filter((r) =>
    r.status.toLowerCase().includes('executed'),
  ).length;

  function getBlocker(): Blocker | null {
    if (!telemetryOk) {
      return {
        title: 'No response actions yet',
        body: 'No response action can be recommended because no telemetry has been received.',
        ctaHref: '/threat',
        ctaLabel: 'View Threat Monitoring',
      };
    }

    if (!detectionOk) {
      return {
        title: 'No response actions yet',
        body: 'Telemetry has been received, but no detection has been generated yet.',
        ctaHref: '/threat',
        ctaLabel: 'Run Detection',
      };
    }

    if (activeAlerts === 0) {
      return {
        title: 'No response actions yet',
        body: 'Detections exist, but no alert has been opened yet.',
        ctaHref: '/alerts',
        ctaLabel: 'Open Alert',
      };
    }

    if (activeIncidents === 0) {
      return {
        title: 'No response actions yet',
        body: 'Alerts exist, but no incident has been opened yet.',
        ctaHref: '/incidents',
        ctaLabel: 'Open Incident',
      };
    }

    if (recommendedRows.length === 0) {
      return {
        title: 'No response action recommended yet',
        body: 'An incident exists, but no response action has been recommended yet.',
        ctaLabel: 'Recommend Response',
      };
    }

    return null;
  }

  const blocker = dataLoading ? null : getBlocker();

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <h1>Response Actions</h1>
            <p className="muted">
              Review, approve, simulate, and track response actions linked to incidents.
            </p>
          </div>

          <button
            type="button"
            className="btn btn-primary"
            disabled
            style={{ opacity: 0.45 }}
            title="Response action recommendation requires incident-linked workflow - use Incidents to open a response action"
            aria-label="Recommend Action"
          >
            Recommend Action
          </button>
        </div>

        <div
          className="buttonRow"
          style={{
            marginBottom: '1rem',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '0.5rem',
          }}
        >
          <input
            placeholder="Search actions..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ flex: '1 1 200px', minWidth: '180px' }}
            aria-label="Search actions"
          />

          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="Type filter">
            <option value="">All Types</option>
            <option value="freeze">Freeze Asset</option>
            <option value="revoke">Revoke Access</option>
            <option value="notify">Notify Stakeholders</option>
            <option value="escalate">Escalate Incident</option>
            <option value="compliance">Apply Compliance Rule</option>
            <option value="rotate">Rotate Key</option>
            <option value="pause">Pause Transfer</option>
            <option value="simulate">Simulate Action</option>
            <option value="other">Other</option>
          </select>

          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="Status filter">
            <option value="">All Statuses</option>
            <option value="recommended">Recommended</option>
            <option value="pending_approval">Pending Approval</option>
            <option value="approved">Approved</option>
            <option value="simulated">Simulated</option>
            <option value="executed">Executed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
            <option value="unknown">Unknown</option>
          </select>

          <select
            value={approvalFilter}
            onChange={(e) => setApprovalFilter(e.target.value)}
            aria-label="Approval filter"
          >
            <option value="">All Approvals</option>
            <option value="yes">Requires Approval</option>
            <option value="no">No Approval Required</option>
          </select>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
            gap: '1rem',
            marginBottom: '1.5rem',
          }}
        >
          <MetricTile label="Recommended Actions" value={recommendedCount} />
          <MetricTile label="Pending Approval" value={pendingApprovalCount} />
          <MetricTile label="Simulated Actions" value={simulatedCount} />
          <MetricTile label="Executed Actions" value={executedCount} />
        </div>

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
              gridTemplateColumns: selectedAction ? 'minmax(0, 1fr) 380px' : '1fr',
              gap: '1rem',
              alignItems: 'start',
            }}
          >
            <div>
              <TabStrip
                tabs={[
                  { key: 'recommended', label: 'Recommended Actions' },
                  { key: 'history', label: 'Action History' },
                ]}
                active={tab}
                onChange={(value) => setTab(value as 'recommended' | 'history')}
              />

              {activeRows.length === 0 && !dataLoading ? (
                <div className="emptyStatePanel sharedEmptyStateBlocker">
                  <h4>No actions match current filters</h4>
                  <p className="muted">Adjust the filters above to see more results.</p>
                </div>
              ) : tab === 'recommended' ? (
                <TableShell headers={RECOMMENDED_HEADERS}>
                  {filteredRecommended.map((row) => {
                    const st = actionStatusPill(row.status, row.simulated);
                    const imp = impactPill(row.impact);
                    const evSrc = evidenceSourcePill(row.evidenceSource, workspaceEvidenceSource);
                    const isSelected = row.id === selectedId;

                    return (
                      <tr
                        key={row.id}
                        onClick={() => setSelectedId(row.id)}
                        style={{
                          cursor: 'pointer',
                          background: isSelected ? 'rgba(59,130,246,0.08)' : undefined,
                        }}
                      >
                        <td style={{ maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500 }}>
                          {row.action}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>{row.type}</td>
                        <td><StatusPill label={imp.label} variant={imp.variant} /></td>
                        <td><StatusPill label={st.label} variant={st.variant} /></td>
                        <td style={{ fontSize: '0.8rem' }}>{row.recommendedBy}</td>
                        <td style={{ fontSize: '0.8rem' }}>
                          {row.linkedIncident ? (
                            <Link href="/incidents" prefetch={false} onClick={(e) => e.stopPropagation()} style={{ fontSize: '0.78rem' }}>
                              {row.linkedIncident}
                            </Link>
                          ) : (
                            <span className="muted" style={{ fontSize: '0.78rem' }}>
                              Linked incident unavailable
                            </span>
                          )}
                        </td>
                        <td><StatusPill label={evSrc.label} variant={evSrc.variant} /></td>
                        <td>
                          {row.requiresApproval ? (
                            <StatusPill label="Requires Approval" variant="warning" />
                          ) : (
                            <span className="muted" style={{ fontSize: '0.78rem' }}>No</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </TableShell>
              ) : (
                <TableShell headers={HISTORY_HEADERS}>
                  {filteredHistory.map((row) => {
                    const evSrc = evidenceSourcePill(row.evidenceSource, workspaceEvidenceSource);

                    return (
                      <tr key={row.id}>
                        <td style={{ fontFamily: 'monospace', fontSize: '0.75rem', whiteSpace: 'nowrap', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis' }} title={row.id}>
                          {row.id}
                        </td>
                        <td style={{ maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {row.action}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>{row.type}</td>
                        <td style={{ fontSize: '0.8rem', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {row.result}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>{row.actorSystem}</td>
                        <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>{fmt(row.time)}</td>
                        <td><StatusPill label={evSrc.label} variant={evSrc.variant} /></td>
                      </tr>
                    );
                  })}
                </TableShell>
              )}
            </div>

            {selectedAction ? (
              <ActionDetailPanel
                action={selectedAction}
                workspaceEvidenceSource={workspaceEvidenceSource}
                liveExecutionAllowed={liveExecutionAllowed}
                onMessage={setMessage}
              />
            ) : null}
          </div>
        )}

        {message ? (
          <p className="statusLine" style={{ marginTop: '0.5rem' }}>
            {message}
          </p>
        ) : null}
      </section>
    </main>
  );
}

function ActionDetailPanel({
  action,
  workspaceEvidenceSource,
  liveExecutionAllowed,
  onMessage,
}: {
  action: ActionRow;
  workspaceEvidenceSource: string;
  liveExecutionAllowed: boolean;
  onMessage: (msg: string) => void;
}) {
  const st = actionStatusPill(action.status, action.simulated);
  const imp = impactPill(action.impact);
  const evSrc = evidenceSourcePill(action.evidenceSource, workspaceEvidenceSource);
  const isSimulatorAction = action.simulated || evSrc.label === 'simulator';

  // Do not show Execute Action when backend only supports simulator mode.
  const canExecute = liveExecutionAllowed && !isSimulatorAction;

  const approvalBlocked =
    action.requiresApproval &&
    !['approved', 'executed'].some((s) => action.status.toLowerCase().includes(s));

  async function simulateAction() {
    onMessage('Simulation initiated. Action marked as SIMULATED.');
  }

  return (
    <aside
      className="dataCard sharedSurfaceCard"
      style={{ padding: '1rem', borderLeft: '1px solid rgba(148,163,184,0.15)' }}
      aria-label="Action detail panel"
    >
      <p className="eyebrow" style={{ marginBottom: '0.25rem', fontSize: '0.7rem' }}>
        Action Detail
      </p>

      <h4 style={{ marginBottom: '0.75rem', fontSize: '0.95rem', lineHeight: 1.35 }}>
        {action.action}
      </h4>

      {isSimulatorAction ? (
        <div style={{ marginBottom: '0.6rem' }}>
          <StatusPill label="SIMULATED" variant="info" />
          <span className="muted" style={{ fontSize: '0.75rem', marginLeft: '0.4rem' }}>
            Simulator action only
          </span>
        </div>
      ) : null}

      {approvalBlocked ? (
        <div style={{ marginBottom: '0.6rem' }}>
          <StatusPill label="Requires Approval" variant="warning" />
          <span className="muted" style={{ fontSize: '0.75rem', marginLeft: '0.4rem' }}>
            Requires approval before execution
          </span>
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem 1rem', marginBottom: '0.75rem' }}>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Status</p>
          <StatusPill label={st.label} variant={st.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Impact</p>
          <StatusPill label={imp.label} variant={imp.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Type</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>{action.type}</p>
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Recommended By</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>{action.recommendedBy}</p>
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Evidence Source</p>
          <StatusPill label={evSrc.label} variant={evSrc.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Requires Approval</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>{action.requiresApproval ? 'Yes' : 'No'}</p>
        </div>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Action ID</p>
        <p style={{ fontFamily: 'monospace', fontSize: '0.73rem', margin: 0, wordBreak: 'break-all' }}>
          {action.id}
        </p>
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Linked Incident</p>
        {action.linkedIncident ? (
          <Link href="/incidents" prefetch={false} style={{ fontSize: '0.78rem' }}>
            {action.linkedIncident}
          </Link>
        ) : (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
            Linked incident unavailable
          </p>
        )}
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Approval State</p>
        <p style={{ fontSize: '0.8rem', margin: 0 }}>
          {action.approvalState ?? (action.requiresApproval ? 'Pending approval' : 'Not required')}
        </p>
      </div>

      {action.eta ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>ETA</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>{action.eta}</p>
        </div>
      ) : null}

      <div style={{ marginBottom: '0.75rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Audit Trail</p>
        <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
          {action.createdAt ? `Action recorded ${fmt(action.createdAt)}.` : 'Audit trail recorded in evidence.'}
          {isSimulatorAction ? ' Simulator record only.' : ''}
        </p>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {canExecute ? (
          <button
            type="button"
            className="btn btn-primary"
            style={{ fontSize: '0.8rem' }}
            disabled={approvalBlocked}
            onClick={() => onMessage('Execution initiated.')}
          >
            Execute Action
          </button>
        ) : (
          <button type="button" className="btn btn-primary" style={{ fontSize: '0.8rem' }} onClick={() => void simulateAction()}>
            Simulate Action
          </button>
        )}

        <Link href="/incidents" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.8rem' }}>
          {action.linkedIncident ? 'View Incident' : 'View Incidents'}
        </Link>

        <Link href="/evidence" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.8rem' }}>
          Evidence Export
        </Link>
      </div>
    </aside>
  );
}
