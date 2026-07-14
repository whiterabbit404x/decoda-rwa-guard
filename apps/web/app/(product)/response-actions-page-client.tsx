'use client';

// fallback examples remain clearly marked as SIMULATED
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
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
  linkedAlert: string | null;
  evidenceSource: string;
  requiresApproval: boolean;
  simulated: boolean;
  eta?: string | null;
  approvalState?: string | null;
  createdAt?: string | null;
  // AI recommendation-review record fields (record_type === 'ai_recommendation_review').
  recordType?: string;
  sourceType?: string;
  decision?: string | null;
  executed?: boolean;
  reviewer?: string | null;
  provider?: string | null;
  model?: string | null;
  evidenceSnapshotId?: string | null;
  evidenceRefsCount?: number;
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
  // AI recommendation-review extensions. Legacy audit rows leave these undefined.
  recordType?: string;
  sourceType?: string;
  decision?: string | null;
  executed?: boolean;
  linkedIncident?: string | null;
  evidenceSnapshotId?: string | null;
  evidenceRefsCount?: number;
  provider?: string | null;
  model?: string | null;
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
  'Decision',
  'Executed',
  'Links',
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

  // AI investigation recommendation reviews carry AI evidence — never simulator, never live-chain.
  if (raw === 'ai_investigation' || raw === 'ai_evidence_snapshot') {
    return { label: 'AI investigation', variant: 'info' };
  }

  return { label: 'none', variant: 'neutral' };
}

function actionStatusPill(status: string, simulated: boolean): { label: string; variant: PillVariant } {
  const base = status.toLowerCase().replace(/\s路\ssimulated$/, '').trim();
  const tag = simulated ? ' 路 SIMULATED' : '';

  if (base === 'recommended') return { label: `Recommended${tag}`, variant: 'info' };
  if (base === 'pending_approval' || base === 'pending approval' || base === 'pending_review') {
    return { label: `Pending Approval${tag}`, variant: 'warning' };
  }
  if (base === 'accepted') return { label: `Accepted${tag}`, variant: 'success' };
  if (base === 'rejected') return { label: `Rejected${tag}`, variant: 'neutral' };
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

  // incident_id directly on the action row is authoritative (from the DB record itself).
  // chain_linked_ids.incident_id is also from the DB response_action payload.
  // Only fall back to validIncidentIds cross-check for IDs from other inferred sources.
  const directIncidentId = String(input?.incident_id || input?.chain_linked_ids?.incident_id || '');
  const rawIncidentId = directIncidentId || String(input?.linked_incident_id || '');
  const rawAlertId = String(input?.alert_id || input?.chain_linked_ids?.alert_id || '');

  // Trust the action's own incident_id from the backend. For IDs inferred from external
  // sources only, require confirmation via validIncidentIds.
  const linkedIncident = directIncidentId
    ? directIncidentId
    : (rawIncidentId && validIncidentIds.has(rawIncidentId) ? rawIncidentId : null);
  const linkedAlert = rawAlertId || null;

  const isAiReview = String(input?.record_type || '') === 'ai_recommendation_review';
  // AI review records carry a human-readable title; legacy rows keep action_type/action.
  const displayAction = isAiReview
    ? String(input?.title || input?.action_type || 'AI recommendation')
    : String(input?.action_type || input?.action || 'Response action');

  return {
    id: String(input?.id || `${input?.action_type || 'action'}-${rawIncidentId || 'none'}`),
    action: displayAction,
    type: String(input?.category || input?.type || 'Other'),
    impact: String(input?.impact || input?.severity || 'medium'),
    status: simulated ? `${rawStatus} 路 SIMULATED` : rawStatus,
    recommendedBy: String(input?.recommended_by || input?.actor_type || 'Policy engine'),
    linkedIncident,
    linkedAlert,
    evidenceSource: String(input?.evidence_source || input?.source || 'runtime'),
    requiresApproval: input?.requires_approval !== false,
    simulated,
    eta: input?.eta ?? input?.estimated_duration ?? input?.estimated_impact ?? null,
    approvalState:
      input?.approval_state ?? (input?.requires_approval === false ? 'not_required' : 'pending_approval'),
    createdAt: input?.created_at ?? input?.timestamp ?? null,
    recordType: input?.record_type ?? undefined,
    sourceType: input?.source_type ?? undefined,
    decision: input?.decision ?? null,
    // A recommendation review is never an executed action.
    executed: isAiReview ? false : input?.executed === true,
    reviewer: input?.reviewer_email ?? input?.reviewer_id ?? null,
    provider: input?.provider ?? null,
    model: input?.model ?? null,
    evidenceSnapshotId: input?.evidence_snapshot_id ?? null,
    evidenceRefsCount:
      typeof input?.evidence_refs_count === 'number'
        ? input.evidence_refs_count
        : Array.isArray(input?.evidence_refs)
          ? input.evidence_refs.length
          : 0,
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

// Accepted / rejected AI recommendation reviews are immutable human-review records,
// not executed actions. They render in Action History with a truthful AI source, the
// decision, executed=No, the reviewer, and links to the incident and its evidence.
function normalizeAiReviewHistoryRow(input: any): HistoryRow {
  const decision = String(input?.decision || input?.review_state || '').toLowerCase();
  return {
    id: String(input?.recommendation_id || input?.id || '-'),
    action: String(input?.title || input?.action_type || 'AI recommendation'),
    type: 'AI recommendation review',
    result: decision === 'accepted' ? 'Accepted' : decision === 'rejected' ? 'Rejected' : 'Reviewed',
    actorSystem: String(input?.reviewer_email || input?.reviewer_id || 'Reviewer'),
    time: input?.reviewed_at ?? input?.created_at ?? null,
    // AI investigation evidence — never simulator, never live-chain.
    evidenceSource: String(input?.evidence_source || 'ai_investigation'),
    simulated: false,
    recordType: 'ai_recommendation_review',
    sourceType: String(input?.source_type || 'ai_investigation'),
    decision: decision === 'accepted' ? 'accepted' : decision === 'rejected' ? 'rejected' : null,
    executed: false,
    linkedIncident: input?.incident_id ? String(input.incident_id) : null,
    evidenceSnapshotId: input?.evidence_snapshot_id ? String(input.evidence_snapshot_id) : null,
    evidenceRefsCount:
      typeof input?.evidence_refs_count === 'number'
        ? input.evidence_refs_count
        : Array.isArray(input?.evidence_refs)
          ? input.evidence_refs.length
          : 0,
    provider: input?.provider ?? null,
    model: input?.model ?? null,
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
  const { authHeaders, refreshCsrfToken } = usePilotAuth();
  const searchParams = useSearchParams();

  const apiUrl = providedApiUrl || resolveApiUrl();
  // incident_id from URL: when the user clicks "Recommend Response" we navigate here with this param
  const incidentIdFilter = searchParams.get('incident_id') ?? '';
  const summaryAny = summary as any;
  const counts = runtime?.counts as Record<string, number> | undefined;

  const actionIdParam = searchParams.get('action_id') ?? '';

  const [tab, setTab] = useState<'recommended' | 'history'>('recommended');
  const [recommendedRows, setRecommendedRows] = useState<ActionRow[]>([]);
  const [historyRows, setHistoryRows] = useState<HistoryRow[]>([]);
  const [selectedId, setSelectedId] = useState(actionIdParam);
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

        const actionsQsParams = new URLSearchParams({ limit: '50' });
        if (incidentIdFilter) actionsQsParams.set('incident_id', incidentIdFilter);
        if (actionIdParam) actionsQsParams.set('action_id', actionIdParam);
        const actionsQs = `?${actionsQsParams.toString()}`;
        const [actionsRes, historyRes, alertsRes, incidentsRes, runtimePayload] = await Promise.all([
          fetch(`/api/response/actions${actionsQs}`, { headers, cache: 'no-store' }).catch(() => null),
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
        // Always trust the incident_id that came from the URL — the action was just created against it.
        if (incidentIdFilter) validIncidentIds.add(incidentIdFilter);

        const allActions = Array.isArray(actionsPayload?.actions) ? actionsPayload.actions : [];
        // AI recommendation reviews are returned in the same list but split by decision:
        // pending reviews belong in Recommended Actions; accepted/rejected reviews are
        // immutable history records and belong in Action History. Legacy policy-engine
        // response_actions keep their existing behavior (all in Recommended Actions).
        const aiReviews = allActions.filter(
          (item: any) => String(item?.record_type || '') === 'ai_recommendation_review',
        );
        const legacyActions = allActions.filter(
          (item: any) => String(item?.record_type || '') !== 'ai_recommendation_review',
        );
        const pendingAiReviews = aiReviews.filter(
          (item: any) => String(item?.review_state || 'pending_review') === 'pending_review',
        );
        const decidedAiReviews = aiReviews.filter(
          (item: any) =>
            String(item?.review_state || '') === 'accepted' ||
            String(item?.review_state || '') === 'rejected',
        );

        const recommended = [...legacyActions, ...pendingAiReviews].map((item: any) =>
          normalizeActionRow(item, validIncidentIds),
        );

        const auditHistory = (Array.isArray(historyPayload?.history) ? historyPayload.history : [])
          .filter(
            (item: any) =>
              String(item?.object_type || '').includes('response_action') ||
              String(item?.action_type || '').includes('response'),
          )
          .map(normalizeHistoryRow);
        // Decided AI reviews first (most relevant), then legacy audit-derived history.
        const history = [...decidedAiReviews.map(normalizeAiReviewHistoryRow), ...auditHistory];

        if (!cancelled) {
          setRecommendedRows(recommended);
          setHistoryRows(history);

          const targetId = actionIdParam || selectedId;
          const targetExists = targetId && recommended.some((r: ActionRow) => r.id === targetId);
          if (!targetExists && recommended.length > 0) {
            setSelectedId(recommended[0].id);
          } else if (targetId && !selectedId) {
            setSelectedId(targetId);
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
  // selectedId intentionally omitted: it is set inside load() and must not re-trigger it.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl, authHeaders, runtimeLoading, incidentIdFilter, actionIdParam]);

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
    // If actions already exist, never block the table — pipeline checks are only relevant
    // when there are truly zero actions. Decided AI recommendation reviews live only in
    // Action History, so their presence must also keep the tabs visible.
    if (recommendedRows.length > 0 || historyRows.length > 0) return null;

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

    return {
      title: incidentIdFilter ? 'No response actions for this incident yet' : 'No response action recommended yet',
      body: incidentIdFilter
        ? 'No response action has been recommended for this incident yet.'
        : 'Incidents exist, but no response action has been recommended yet.',
      ctaHref: '/incidents',
      ctaLabel: 'Go to Incidents',
    };
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
                    const isAiReview = row.recordType === 'ai_recommendation_review';

                    return (
                      <tr key={row.id}>
                        <td style={{ fontFamily: 'monospace', fontSize: '0.75rem', whiteSpace: 'nowrap', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis' }} title={row.id}>
                          {row.id}
                        </td>
                        <td style={{ maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {row.action}
                          {isAiReview ? (
                            <div style={{ marginTop: '0.2rem' }}>
                              <StatusPill label="AI recommendation" variant="info" />
                            </div>
                          ) : null}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>
                          {isAiReview ? 'AI Investigation' : row.type}
                        </td>
                        <td style={{ fontSize: '0.8rem', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {row.result}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>{row.actorSystem}</td>
                        <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>{fmt(row.time)}</td>
                        <td><StatusPill label={evSrc.label} variant={evSrc.variant} /></td>
                        <td>
                          {row.decision === 'accepted' ? (
                            <StatusPill label="Accepted" variant="success" />
                          ) : row.decision === 'rejected' ? (
                            <StatusPill label="Rejected" variant="neutral" />
                          ) : (
                            <span className="muted" style={{ fontSize: '0.78rem' }}>—</span>
                          )}
                        </td>
                        <td>
                          {isAiReview ? (
                            <StatusPill label="No" variant="neutral" />
                          ) : (
                            <span className="muted" style={{ fontSize: '0.78rem' }}>—</span>
                          )}
                        </td>
                        <td style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                          {isAiReview && row.linkedIncident ? (
                            <span style={{ display: 'inline-flex', gap: '0.5rem' }}>
                              <Link href={`/incidents/${row.linkedIncident}`} prefetch={false} style={{ fontSize: '0.75rem' }}>
                                View Incident
                              </Link>
                              <Link
                                href={`/evidence?incident_id=${row.linkedIncident}`}
                                prefetch={false}
                                style={{ fontSize: '0.75rem' }}
                              >
                                View Evidence
                              </Link>
                            </span>
                          ) : (
                            <span className="muted" style={{ fontSize: '0.78rem' }}>—</span>
                          )}
                        </td>
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
                apiUrl={apiUrl}
                authHeaders={authHeaders}
                refreshCsrfToken={refreshCsrfToken}
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
  apiUrl,
  authHeaders,
  refreshCsrfToken,
}: {
  action: ActionRow;
  workspaceEvidenceSource: string;
  liveExecutionAllowed: boolean;
  onMessage: (msg: string) => void;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  refreshCsrfToken: () => Promise<string | null>;
}) {
  const router = useRouter();
  const st = actionStatusPill(action.status, action.simulated);
  const imp = impactPill(action.impact);
  const evSrc = evidenceSourcePill(action.evidenceSource, workspaceEvidenceSource);
  // AI recommendation reviews are human-review records, not simulator/executable actions.
  const isAiReview = action.recordType === 'ai_recommendation_review';
  const isSimulatorAction = !isAiReview && (action.simulated || evSrc.label === 'simulator');

  // Do not show Execute Action when backend only supports simulator mode.
  // AI review records are never executable — reviewing records a decision only.
  const canExecute = liveExecutionAllowed && !isSimulatorAction && !isAiReview;

  const approvalBlocked =
    action.requiresApproval &&
    !['approved', 'executed'].some((s) => action.status.toLowerCase().includes(s));

  function _extractErrorMessage(detail: unknown, fallback: string): string {
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') {
      const d = detail as { message?: string; error?: string };
      return d.message ?? d.error ?? fallback;
    }
    return fallback;
  }

  async function simulateAction() {
    onMessage('Simulating action…');
    try {
      const res = await fetch(`/api/response/actions/${action.id}/simulate`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const data = (await res.json()) as { id?: string; status?: string; simulation_status?: string; simulated?: boolean; detail?: unknown };
      if (res.ok) {
        onMessage('Action marked as SIMULATED.');
        // Reload the page to reflect the persisted simulated status.
        router.refresh();
      } else {
        onMessage(_extractErrorMessage(data.detail, 'Simulate failed.'));
      }
    } catch {
      onMessage('Simulate request failed. Check network connection.');
    }
  }

  async function handleEvidenceExport() {
    onMessage('Creating evidence package…');

    type EvidencePackageData = {
      package_id?: string;
      incident_id?: string;
      response_action_id?: string;
      detail?: unknown;
      code?: string;
    };

    async function postEvidencePackage(
      headers: Record<string, string>,
    ): Promise<{ res: Response; data: EvidencePackageData; parseError: boolean }> {
      // Use the same-origin proxy so the request goes through the Next.js server
      // (which has the correct API_URL) rather than relying on NEXT_PUBLIC_API_URL.
      const res = await fetch(`/api/response/actions/${action.id}/evidence-package`, {
        method: 'POST',
        headers,
      });
      let data: EvidencePackageData = {};
      let parseError = false;
      try {
        data = await res.json();
      } catch {
        parseError = true;
      }
      return { res, data, parseError };
    }

    try {
      let { res, data, parseError } = await postEvidencePackage(authHeaders());

      // On CSRF error, fetch a fresh token and retry once with the new token included.
      if (
        res.status === 403 &&
        (data.code === 'csrf_missing_or_invalid' ||
          (typeof data.detail === 'string' && data.detail.toLowerCase().includes('csrf')))
      ) {
        const freshToken = await refreshCsrfToken();
        if (freshToken) {
          const retryHeaders = { ...authHeaders(), 'X-CSRF-Token': freshToken };
          const retryResult = await postEvidencePackage(retryHeaders);
          res = retryResult.res;
          data = retryResult.data;
          parseError = retryResult.parseError;
        }
      }

      if (parseError) {
        onMessage('Evidence export failed: server returned an unexpected response.');
        return;
      }

      if (res.ok && data.package_id) {
        const params = new URLSearchParams({ package_id: data.package_id, action_id: action.id });
        const resolvedIncidentId = data.incident_id ?? action.linkedIncident ?? '';
        if (resolvedIncidentId) params.set('incident_id', resolvedIncidentId);
        router.push(`/evidence?${params.toString()}`);
      } else {
        onMessage(_extractErrorMessage(data.detail, 'Evidence export failed.'));
      }
    } catch {
      onMessage('Evidence export failed. Check network connection.');
    }
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

      {isAiReview ? (
        <div style={{ marginBottom: '0.6rem', display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
          <StatusPill label="AI recommendation" variant="info" />
          {action.decision === 'accepted' ? (
            <StatusPill label="Accepted" variant="success" />
          ) : action.decision === 'rejected' ? (
            <StatusPill label="Rejected" variant="neutral" />
          ) : (
            <StatusPill label="Pending review" variant="warning" />
          )}
          <StatusPill label="Not executed" variant="neutral" />
        </div>
      ) : null}

      {approvalBlocked && !isAiReview ? (
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
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Linked Alert</p>
        {action.linkedAlert ? (
          <Link href="/alerts" prefetch={false} style={{ fontSize: '0.78rem' }}>
            {action.linkedAlert}
          </Link>
        ) : (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
            No linked alert
          </p>
        )}
      </div>

      <div style={{ marginBottom: '0.5rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Linked Incident</p>
        {action.linkedIncident ? (
          <Link href={`/incidents/${action.linkedIncident}`} prefetch={false} style={{ fontSize: '0.78rem' }}>
            {action.linkedIncident}
          </Link>
        ) : (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
            Linked incident unavailable
          </p>
        )}
      </div>

      {isAiReview ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Decision</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {action.decision === 'accepted'
              ? 'Accepted · Not executed'
              : action.decision === 'rejected'
                ? 'Rejected · Not executed'
                : 'Pending review · Not executed'}
          </p>
          {action.reviewer ? (
            <>
              <p className="tableMeta" style={{ marginTop: '0.4rem', marginBottom: '0.1rem' }}>Reviewer</p>
              <p style={{ fontSize: '0.8rem', margin: 0 }}>{action.reviewer}</p>
            </>
          ) : null}
          {action.provider || action.model ? (
            <>
              <p className="tableMeta" style={{ marginTop: '0.4rem', marginBottom: '0.1rem' }}>Provider / Model</p>
              <p style={{ fontSize: '0.8rem', margin: 0 }}>
                {[action.provider, action.model].filter(Boolean).join(' / ')}
              </p>
            </>
          ) : null}
          <p className="tableMeta" style={{ marginTop: '0.4rem', marginBottom: '0.1rem' }}>Evidence Citations</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {action.evidenceRefsCount ?? 0} citation{(action.evidenceRefsCount ?? 0) === 1 ? '' : 's'}
            {action.evidenceSnapshotId ? ' · snapshot linked' : ''}
          </p>
        </div>
      ) : (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Approval State</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {action.approvalState ?? (action.requiresApproval ? 'Pending approval' : 'Not required')}
          </p>
        </div>
      )}

      {action.eta ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>ETA</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>{action.eta}</p>
        </div>
      ) : null}

      <div style={{ marginBottom: '0.75rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Audit Trail</p>
        <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
          {isAiReview
            ? `Human recommendation review recorded ${action.createdAt ? fmt(action.createdAt) : ''}. No action was executed.`
            : action.createdAt
              ? `Action recorded ${fmt(action.createdAt)}.`
              : 'Audit trail recorded in evidence.'}
          {isSimulatorAction ? ' Simulator record only.' : ''}
        </p>
      </div>

      {isAiReview ? (
        // AI recommendation reviews are immutable human-review records. Never offer
        // Simulate/Execute here — only neutral read links to the underlying evidence.
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <Link
            href={action.linkedIncident ? `/incidents/${action.linkedIncident}` : '/incidents'}
            prefetch={false}
            className="btn btn-primary"
            style={{ fontSize: '0.8rem' }}
          >
            View investigation
          </Link>
          <Link
            href={action.linkedIncident ? `/incidents/${action.linkedIncident}` : '/incidents'}
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.8rem' }}
          >
            View recommendation
          </Link>
          <Link
            href={action.linkedIncident ? `/evidence?incident_id=${action.linkedIncident}` : '/evidence'}
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.8rem' }}
          >
            View evidence
          </Link>
        </div>
      ) : (
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

          <Link
            href={action.linkedIncident ? `/incidents/${action.linkedIncident}` : '/incidents'}
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.8rem' }}
          >
            {action.linkedIncident ? 'View Incident' : 'View Incidents'}
          </Link>

          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: '0.8rem' }}
            onClick={() => void handleEvidenceExport()}
          >
            Evidence Export
          </button>
        </div>
      )}
    </aside>
  );
}
