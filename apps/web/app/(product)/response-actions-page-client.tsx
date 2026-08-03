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
  // Deterministic Playbook Execution Agent profile (from the backend `playbook`
  // field for policy actions, or derived from runbook_id/risk_level for AI reviews).
  priority?: string;
  runbookId?: string | null;
  runbookName?: string | null;
  blastRadius?: string | null;
  reversibility?: string | null;
  category?: string | null;
  riskLevel?: string | null;
  // Canonical operator-facing fields from the backend (single source of truth).
  // The raw action_type key is preserved in `actionKey` for persistence/commands,
  // but is never rendered as the primary title.
  actionKey?: string;
  displayDescription?: string;
  // ONE derived primary lifecycle state — never a concatenation of raw enums.
  lifecycleState?: string;
  lifecycleLabel?: string;
  approvalStatus?: string;
  simulationStatus?: string; // canonical: 'passed' | 'not_started'
  executionStatus?: string;
  rollbackStatus?: string;
  // Truthful provenance (never 'none'/'null').
  provenanceLabel?: string;
  hasEvidencePackage?: boolean;
  // Backend command eligibility — button visibility is NOT authorization.
  allowedCommands?: string[];
  blockedReasons?: Array<{ command: string; reason: string }>;
  nextRequiredStep?: string;
  requiresConfirmation?: boolean;
  liveExecutionConfigured?: boolean;
  // Distinct target so same-title actions across incidents are disambiguated.
  targetLabel?: string | null;
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
  'Priority',
  'Impact',
  'Status',
  'Recommended By',
  'Runbook',
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

// Truthful evidence/provenance pill. Prefers the backend's canonical provenance
// label (e.g. "Incident context only", "Evidence Package") so we NEVER render a
// bare 'none'/'null'. Falls back to the legacy evidence_source classification for
// rows that predate structured provenance.
function evidenceSourcePill(
  rowSource?: string | null,
  workspaceSource?: string,
  provenanceLabel?: string | null,
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

  // Canonical backend provenance label — always truthful, never 'none'.
  const label = (provenanceLabel ?? '').trim();
  if (label) {
    if (label === 'Evidence Package') return { label, variant: 'success' };
    if (label === 'Threat Detection' || label === 'Alert Evidence') return { label, variant: 'info' };
    if (label === 'Source unavailable') return { label, variant: 'neutral' };
    return { label, variant: 'neutral' }; // "Incident context only" etc.
  }

  // No provenance and no evidence source: state it honestly.
  return { label: 'No evidence attached', variant: 'neutral' };
}

// Canonical lifecycle variant. The LABEL is supplied by the backend
// (lifecycleLabel) — this only maps the state to a color, and never concatenates
// approval/simulation/execution into one string.
function lifecyclePill(lifecycleState?: string, lifecycleLabel?: string): { label: string; variant: PillVariant } {
  const state = (lifecycleState ?? '').toLowerCase();
  const label = lifecycleLabel || (state ? state.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : 'Unknown');

  const variant: PillVariant =
    state === 'executed' || state === 'approved' || state === 'ready_to_execute'
      ? 'success'
      : state === 'awaiting_approval'
        ? 'warning'
        : state === 'execution_failed' || state === 'blocked'
          ? 'danger'
          : state === 'recommended' || state === 'simulation_passed' || state === 'executing'
            ? 'info'
            : 'neutral';
  return { label, variant };
}

// Deterministic priority label from a risk level (used for AI-review records that
// carry risk_level rather than a backend playbook priority). Never random.
function priorityFromRisk(risk?: string | null): string | null {
  const r = (risk ?? '').toLowerCase();
  if (r === 'critical' || r === 'high' || r === 'medium' || r === 'low') return r;
  return null;
}

// Priority pill reuses the shared severity palette (critical/high → danger,
// medium → warning, low → success) so Screen 8 stays visually consistent.
function priorityPill(priority?: string | null): { label: string; variant: PillVariant } {
  return impactPill(String(priority ?? 'medium'));
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
  // Canonical lifecycle from the backend (single source of truth). Never infer
  // "simulated" from mode='recommended' — a recommended action has NOT been
  // simulated. simulation_status='passed' means a real dry-run actually ran.
  const lifecycle = input?.lifecycle && typeof input.lifecycle === 'object' ? input.lifecycle : {};
  const simulationStatus = String(
    lifecycle.simulation_status || (input?.simulated === true ? 'passed' : 'not_started'),
  );
  const simulated = simulationStatus === 'passed';

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
  // Prefer the backend canonical display_title. The raw snake_case action_type
  // key is NEVER used as the primary operator title — fall back to a title-cased
  // key only if the backend sent no display_title.
  const titleCasedKey = String(input?.action_type || 'response action')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c: string) => c.toUpperCase());
  const displayAction = isAiReview
    ? String(input?.title || input?.display_title || 'AI recommendation')
    : String(input?.display_title || input?.action || titleCasedKey);

  // Distinct target so same-title actions (e.g. Notify Security Team across
  // different incidents) are disambiguated in the table.
  const shortId = (value: string) => (value.length > 8 ? `${value.slice(0, 8)}…` : value);
  const targetLabel = input?.target_wallet
    ? String(input.target_wallet)
    : input?.token_contract
      ? String(input.token_contract)
      : directIncidentId
        ? `Incident ${shortId(directIncidentId)}`
        : null;

  const provenance = input?.provenance && typeof input.provenance === 'object' ? input.provenance : {};
  const commands = input?.commands && typeof input.commands === 'object' ? input.commands : {};

  // Deterministic Playbook Execution Agent profile. Policy actions carry a backend
  // `playbook` object; AI reviews carry runbook_id + risk_level. Never fabricated.
  const playbook = input?.playbook && typeof input.playbook === 'object' ? input.playbook : {};
  const riskLevel = input?.risk_level ? String(input.risk_level) : null;
  const priority = String(
    playbook.priority || priorityFromRisk(riskLevel) || input?.impact || input?.severity || 'medium',
  );

  return {
    id: String(input?.id || `${input?.action_type || 'action'}-${rawIncidentId || 'none'}`),
    action: displayAction,
    type: String(input?.category || input?.type || 'Other'),
    impact: String(input?.impact || input?.severity || 'medium'),
    // Keep the RAW canonical status for filtering only; the operator-facing
    // label comes from lifecycleLabel — approval/simulation/execution are never
    // concatenated into one string.
    status: rawStatus,
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
    priority,
    runbookId: playbook.runbook_id ?? input?.runbook_id ?? null,
    runbookName: playbook.runbook_name ?? null,
    blastRadius: playbook.blast_radius ?? null,
    reversibility: playbook.reversibility ?? null,
    category: playbook.category ?? null,
    riskLevel,
    // Canonical operator-facing fields (single source of truth from the backend).
    actionKey: input?.action_key ?? input?.action_type ?? undefined,
    displayDescription: input?.display_description ?? undefined,
    lifecycleState: String(lifecycle.lifecycle_state || input?.lifecycle_state || rawStatus),
    lifecycleLabel: String(
      lifecycle.lifecycle_label ||
        input?.lifecycle_label ||
        (rawStatus ? rawStatus.charAt(0).toUpperCase() + rawStatus.slice(1) : 'Recommended'),
    ),
    approvalStatus: String(lifecycle.approval_status || input?.approval_status || ''),
    simulationStatus,
    executionStatus: String(lifecycle.execution_status || input?.execution_status || 'not_started'),
    rollbackStatus: String(lifecycle.rollback_status || input?.rollback_status || 'not_available'),
    provenanceLabel: provenance.primary_source_label ? String(provenance.primary_source_label) : undefined,
    hasEvidencePackage: provenance.has_evidence_package === true,
    allowedCommands: Array.isArray(commands.allowed_commands) ? commands.allowed_commands.map(String) : [],
    blockedReasons: Array.isArray(commands.blocked_reasons)
      ? commands.blocked_reasons.map((b: any) => ({ command: String(b?.command ?? ''), reason: String(b?.reason ?? '') }))
      : [],
    nextRequiredStep: commands.next_required_step ? String(commands.next_required_step) : undefined,
    requiresConfirmation: commands.requires_confirmation === true,
    liveExecutionConfigured: commands.live_execution_configured === true,
    targetLabel,
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

  // The selected tab survives refresh via the ?tab= query param (a canonical URL
  // pattern) rather than living only in client memory.
  const tabParam = searchParams.get('tab') === 'history' ? 'history' : 'recommended';
  const [tab, setTabState] = useState<'recommended' | 'history'>(tabParam);
  const router = useRouter();
  // Keep tab state in sync when the URL query changes (e.g. back/forward).
  useEffect(() => {
    setTabState(tabParam);
  }, [tabParam]);
  const setTab = (next: 'recommended' | 'history') => {
    setTabState(next);
    const params = new URLSearchParams(Array.from(searchParams.entries()));
    if (next === 'history') params.set('tab', 'history');
    else params.delete('tab');
    const qs = params.toString();
    router.replace(qs ? `/response-actions?${qs}` : '/response-actions', { scroll: false });
  };
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
      // Filter against the CANONICAL lifecycle state/label, not the raw status.
      const matchesStatus =
        !statusFilter ||
        (row.lifecycleState ?? '').toLowerCase().includes(statusFilter.toLowerCase()) ||
        (row.lifecycleLabel ?? '').toLowerCase().includes(statusFilter.toLowerCase());
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

  // Summary-card counts are derived from CANONICAL persisted state (the backend's
  // approval_status / simulation_status / execution_status), never by searching
  // display strings. An action that has not reached a terminal state and is not
  // superseded is "recommended".
  const isActiveRecommendation = (r: ActionRow): boolean => {
    const s = (r.lifecycleState ?? '').toLowerCase();
    return s !== 'cancelled' && s !== 'rejected' && s !== 'rolled_back';
  };
  const recommendedCount = recommendedRows.filter(isActiveRecommendation).length;
  const pendingApprovalCount = recommendedRows.filter((r) => r.approvalStatus === 'pending').length;
  const simulatedCount = recommendedRows.filter((r) => r.simulationStatus === 'passed').length;
  const executedCount = recommendedRows.filter((r) => r.executionStatus === 'executed').length;

  // Approval-required count for the orange banner + agent panel. Derived ONLY
  // from persisted canonical approval_status, never hardcoded and never from a
  // display string.
  function actionNeedsApproval(r: ActionRow): boolean {
    return r.approvalStatus === 'pending';
  }
  const approvalRequiredCount = recommendedRows.filter(actionNeedsApproval).length;

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
            <option value="awaiting_approval">Awaiting Approval</option>
            <option value="ready_to_execute">Ready to Execute</option>
            <option value="simulation_passed">Simulation Passed</option>
            <option value="executed">Executed</option>
            <option value="execution_failed">Execution Failed</option>
            <option value="cancelled">Cancelled</option>
            <option value="rolled_back">Rolled Back</option>
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

        {/* Orange approval banner — shown only when persisted state has actions
            awaiting approval on the Recommended Actions tab. The count comes from
            actionNeedsApproval() over persisted rows, never a hardcoded value. */}
        {!blocker && tab === 'recommended' && approvalRequiredCount > 0 ? (
          <div
            role="alert"
            aria-label="Approval required banner"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '1rem',
              padding: '0.85rem 1rem',
              marginBottom: '1rem',
              borderRadius: '12px',
              border: '1px solid rgba(245, 158, 11, 0.5)',
              background: 'rgba(245, 158, 11, 0.12)',
              flexWrap: 'wrap',
            }}
          >
            <div>
              <p style={{ margin: 0, fontWeight: 700, color: 'var(--warning-fg)' }}>Approval Required</p>
              <p style={{ margin: '0.15rem 0 0', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                {approvalRequiredCount} action{approvalRequiredCount === 1 ? '' : 's'} require your approval before execution.
              </p>
            </div>
            <button
              type="button"
              className="btn btn-primary"
              style={{ fontSize: '0.8rem' }}
              onClick={() => {
                setApprovalFilter('yes');
                setTab('recommended');
              }}
            >
              Review All
            </button>
          </div>
        ) : null}

        {blocker ? (
          <EmptyStateBlocker
            title={blocker.title}
            body={blocker.body}
            ctaHref={blocker.ctaHref}
            ctaLabel={blocker.ctaLabel}
          />
        ) : (
          <div className="twoColumnSection" style={{ marginTop: 0, alignItems: 'start' }}>
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
                    const st = lifecyclePill(row.lifecycleState, row.lifecycleLabel);
                    const imp = impactPill(row.impact);
                    const pri = priorityPill(row.priority);
                    const evSrc = evidenceSourcePill(row.evidenceSource, workspaceEvidenceSource, row.provenanceLabel);
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
                        <td style={{ maxWidth: '190px' }}>
                          <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500 }} title={row.action}>
                            {row.action}
                          </div>
                          {/* Distinct target disambiguates same-title actions across incidents. */}
                          {row.targetLabel ? (
                            <div className="muted" style={{ fontSize: '0.72rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.targetLabel}>
                              {row.targetLabel}
                            </div>
                          ) : null}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>{row.type}</td>
                        <td><StatusPill label={pri.label} variant={pri.variant} /></td>
                        <td><StatusPill label={imp.label} variant={imp.variant} /></td>
                        <td>
                          <StatusPill label={st.label} variant={st.variant} />
                          {/* Simulation is shown as a SEPARATE secondary chip — never
                              concatenated into the primary lifecycle label. */}
                          {row.simulationStatus === 'passed' && row.lifecycleState !== 'simulation_passed' ? (
                            <div style={{ marginTop: '0.2rem' }}>
                              <StatusPill label="Simulated" variant="info" />
                            </div>
                          ) : null}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>{row.recommendedBy}</td>
                        <td style={{ fontSize: '0.78rem', fontFamily: 'monospace', maxWidth: '110px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.runbookName ?? row.runbookId ?? ''}>
                          {row.runbookId ? row.runbookId : <span className="muted">—</span>}
                        </td>
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

            <div style={{ display: 'grid', gap: '1rem' }}>
              <PlaybookAgentPanel
                rows={recommendedRows}
                incidentIdFilter={incidentIdFilter}
                selectedAction={selectedAction}
                liveExecutionAllowed={liveExecutionAllowed}
                apiUrl={apiUrl}
                authHeaders={authHeaders}
                onMessage={setMessage}
              />
              {selectedAction ? (
                <ActionDetailPanel
                  action={selectedAction}
                  workspaceEvidenceSource={workspaceEvidenceSource}
                  onMessage={setMessage}
                  apiUrl={apiUrl}
                  authHeaders={authHeaders}
                  refreshCsrfToken={refreshCsrfToken}
                />
              ) : null}
            </div>
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
  onMessage,
  apiUrl,
  authHeaders,
  refreshCsrfToken,
}: {
  action: ActionRow;
  workspaceEvidenceSource: string;
  onMessage: (msg: string) => void;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  refreshCsrfToken: () => Promise<string | null>;
}) {
  const router = useRouter();
  const st = lifecyclePill(action.lifecycleState, action.lifecycleLabel);
  const imp = impactPill(action.impact);
  const evSrc = evidenceSourcePill(action.evidenceSource, workspaceEvidenceSource, action.provenanceLabel);
  // AI recommendation reviews are human-review records, not simulator/executable actions.
  const isAiReview = action.recordType === 'ai_recommendation_review';
  const isSimulatorAction = !isAiReview && evSrc.label === 'simulator';

  // The BACKEND is the authority on which commands are valid — button visibility
  // is not authorization. Execute is only offered when the backend's
  // allowed_commands includes it; otherwise the specific blocked reason is shown.
  const allowedCommands = action.allowedCommands ?? [];
  const executeBlockedReason =
    (action.blockedReasons ?? []).find((b) => b.command === 'execute')?.reason ?? null;
  const canExecute = !isAiReview && allowedCommands.includes('execute');
  const canSimulate = !isAiReview && (allowedCommands.includes('simulate') || allowedCommands.includes('retry_simulation'));

  const approvalBlocked = action.approvalStatus === 'pending';

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

  // Real execution: the backend re-validates every prerequisite (approval,
  // simulation, live-execution config, workspace isolation) and returns the
  // CANONICAL persisted state. We never fabricate a success — the message and
  // refresh reflect only what the backend actually persisted.
  async function executeAction() {
    if (action.requiresConfirmation && typeof window !== 'undefined') {
      const ok = window.confirm(
        `This is a high-impact action (${action.action}). Execute it now? This is guarded by the backend and requires all safety checks and approvals to pass.`,
      );
      if (!ok) return;
    }
    onMessage('Requesting execution…');
    try {
      const res = await fetch(`/api/response/actions/${action.id}/execute`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const data = (await res.json().catch(() => ({}))) as {
        status?: string;
        execution_state?: string;
        lifecycle_label?: string;
        detail?: unknown;
      };
      if (res.ok) {
        onMessage(`Execution recorded: ${data.lifecycle_label ?? data.status ?? 'updated'}.`);
      } else {
        onMessage(_extractErrorMessage(data.detail, 'Execution was blocked by the backend.'));
      }
      // Always refresh to reflect the canonical persisted state (success OR block).
      router.refresh();
    } catch {
      onMessage('Execution request failed. Check network connection.');
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

      <h4 style={{ marginBottom: action.displayDescription ? '0.25rem' : '0.75rem', fontSize: '0.95rem', lineHeight: 1.35 }}>
        {action.action}
      </h4>
      {action.displayDescription && !isAiReview ? (
        <p className="muted" style={{ fontSize: '0.78rem', margin: '0 0 0.7rem', lineHeight: 1.4 }}>
          {action.displayDescription}
        </p>
      ) : null}

      {isSimulatorAction ? (
        <div style={{ marginBottom: '0.6rem' }}>
          <StatusPill label="SIMULATED" variant="info" />
          <span className="muted" style={{ fontSize: '0.75rem', marginLeft: '0.4rem' }}>
            Simulator action only
          </span>
        </div>
      ) : null}

      {/* Truthful execution-blocked reason, if any (backend-derived). */}
      {!isAiReview && (action.blockedReasons ?? []).some((b) => b.command === 'execute') ? (
        <div style={{ marginBottom: '0.6rem' }}>
          <StatusPill label="Execution blocked" variant="warning" />
          <span className="muted" style={{ fontSize: '0.75rem', marginLeft: '0.4rem' }}>
            {(action.blockedReasons ?? []).find((b) => b.command === 'execute')?.reason}
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
          {/* Approval, Simulation, and Execution are SEPARATE canonical states —
              shown as three distinct rows, never concatenated into one pill. */}
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Lifecycle</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.35rem 0.6rem' }}>
            <div>
              <p className="tableMeta" style={{ marginBottom: '0.05rem', fontSize: '0.65rem' }}>Approval</p>
              <p style={{ fontSize: '0.76rem', margin: 0 }}>
                {action.approvalStatus === 'approved'
                  ? 'Approved'
                  : action.approvalStatus === 'pending'
                    ? 'Pending'
                    : action.approvalStatus === 'rejected'
                      ? 'Rejected'
                      : 'Not required'}
              </p>
            </div>
            <div>
              <p className="tableMeta" style={{ marginBottom: '0.05rem', fontSize: '0.65rem' }}>Simulation</p>
              <p style={{ fontSize: '0.76rem', margin: 0 }}>
                {action.simulationStatus === 'passed' ? 'Passed' : 'Not started'}
              </p>
            </div>
            <div>
              <p className="tableMeta" style={{ marginBottom: '0.05rem', fontSize: '0.65rem' }}>Execution</p>
              <p style={{ fontSize: '0.76rem', margin: 0 }}>
                {action.executionStatus === 'executed'
                  ? 'Executed'
                  : action.executionStatus === 'executing'
                    ? 'Executing'
                    : action.executionStatus === 'failed'
                      ? 'Failed'
                      : 'Not started'}
              </p>
            </div>
          </div>
          {action.provenanceLabel ? (
            <>
              <p className="tableMeta" style={{ marginTop: '0.4rem', marginBottom: '0.05rem' }}>Provenance</p>
              <p style={{ fontSize: '0.76rem', margin: 0 }}>{action.provenanceLabel}</p>
            </>
          ) : null}
        </div>
      )}

      {action.eta ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>ETA</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>{action.eta}</p>
        </div>
      ) : null}

      {!isAiReview && (action.runbookId || action.blastRadius) ? (
        <div style={{ marginBottom: '0.5rem' }}>
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Playbook</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.35rem 1rem' }}>
            <div>
              <p className="tableMeta" style={{ marginBottom: '0.05rem', fontSize: '0.65rem' }}>Runbook</p>
              <p style={{ fontSize: '0.78rem', margin: 0, fontFamily: 'monospace' }}>
                {action.runbookId ?? '—'}
              </p>
            </div>
            <div>
              <p className="tableMeta" style={{ marginBottom: '0.05rem', fontSize: '0.65rem' }}>Priority</p>
              <StatusPill label={priorityPill(action.priority).label} variant={priorityPill(action.priority).variant} />
            </div>
            <div>
              <p className="tableMeta" style={{ marginBottom: '0.05rem', fontSize: '0.65rem' }}>Blast Radius</p>
              <p style={{ fontSize: '0.78rem', margin: 0 }}>{(action.blastRadius ?? 'unknown').replace(/_/g, ' ')}</p>
            </div>
            <div>
              <p className="tableMeta" style={{ marginBottom: '0.05rem', fontSize: '0.65rem' }}>Rollback</p>
              <p style={{ fontSize: '0.78rem', margin: 0 }}>
                {action.reversibility === 'reversible'
                  ? 'Available'
                  : action.reversibility === 'irreversible'
                    ? 'Not available (irreversible)'
                    : 'Unknown'}
              </p>
            </div>
          </div>
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
        <div>
          {/* Blocked-execution reason is shown truthfully — the operator always
              sees exactly why an action cannot execute yet. */}
          {!canExecute && executeBlockedReason ? (
            <p className="muted" style={{ fontSize: '0.75rem', margin: '0 0 0.5rem' }}>
              <strong style={{ color: 'var(--warning-fg)' }}>Execution blocked:</strong> {executeBlockedReason}
            </p>
          ) : null}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {canExecute ? (
              <button
                type="button"
                className="btn btn-primary"
                style={{ fontSize: '0.8rem' }}
                onClick={() => void executeAction()}
              >
                Execute Action
              </button>
            ) : canSimulate ? (
              <button type="button" className="btn btn-primary" style={{ fontSize: '0.8rem' }} onClick={() => void simulateAction()}>
                {action.simulationStatus === 'passed' ? 'Retry Simulation' : 'Simulate Action'}
              </button>
            ) : (
              <button type="button" className="btn btn-primary" style={{ fontSize: '0.8rem' }} disabled title={executeBlockedReason ?? 'No command available'}>
                {approvalBlocked ? 'Awaiting Approval' : 'No Action Available'}
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
        </div>
      )}
    </aside>
  );
}

/* ── Playbook Execution Agent panel ──────────────────────────────────────────
   The right-side agent surface from the Screen 8 reference. Every number here is
   derived from PERSISTED action state (the rows the backend returned) or from the
   read-only safety-checks endpoint — nothing is fabricated. "Simulate All" hits
   the backend batch endpoint, which enforces eligibility server-side. */

type SafetyCheck = {
  key: string;
  label: string;
  status: 'pass' | 'warning' | 'fail' | 'unknown' | string;
  detail: string;
  checked_at?: string;
};

type SafetyChecksPayload = {
  checks?: SafetyCheck[];
  summary?: { overall?: string; total?: number; counts?: Record<string, number> };
  live_execution_configured?: boolean;
  playbook?: { runbook_id?: string | null; runbook_name?: string | null };
};

function safetyStatusPill(status: string): { label: string; variant: PillVariant } {
  const s = status.toLowerCase();
  if (s === 'pass') return { label: 'Pass', variant: 'success' };
  if (s === 'warning') return { label: 'Warning', variant: 'warning' };
  if (s === 'fail') return { label: 'Fail', variant: 'danger' };
  return { label: 'Unknown', variant: 'neutral' };
}

// Eligible for Simulate All. Mirrors the backend eligibility (which is the real
// authority) using the canonical allowed_commands when present, so the displayed
// count matches exactly what the batch command will act on. AI recommendation-
// review records are never executable/simulatable and are excluded.
function isSimulateEligible(r: ActionRow): boolean {
  if (r.recordType === 'ai_recommendation_review') return false;
  if (Array.isArray(r.allowedCommands) && r.allowedCommands.length > 0) {
    return r.allowedCommands.includes('simulate');
  }
  // Fallback for rows without canonical commands: only truly un-simulated,
  // non-terminal actions are eligible.
  if (r.simulationStatus === 'passed') return false;
  const s = (r.lifecycleState ?? '').toLowerCase();
  return s !== 'executed' && s !== 'execution_failed' && s !== 'cancelled' && s !== 'rejected' && s !== 'rolled_back';
}

function PlaybookAgentPanel({
  rows,
  incidentIdFilter,
  selectedAction,
  liveExecutionAllowed,
  apiUrl: _apiUrl,
  authHeaders,
  onMessage,
}: {
  rows: ActionRow[];
  incidentIdFilter: string;
  selectedAction: ActionRow | null;
  liveExecutionAllowed: boolean;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  onMessage: (msg: string) => void;
}) {
  const router = useRouter();
  const [simulating, setSimulating] = useState(false);
  const [checks, setChecks] = useState<SafetyChecksPayload | null>(null);
  const [checksState, setChecksState] = useState<'idle' | 'loading' | 'ready' | 'error' | 'not_applicable'>('idle');

  // Agent summary — all from CANONICAL persisted row state (lifecycle_state /
  // approval_status / execution_status), never from display-string matching.
  const recommended = rows.length;
  const awaitingApproval = rows.filter((r) => r.approvalStatus === 'pending').length;
  const readyForDryRun = rows.filter(isSimulateEligible).length;
  const simulated = rows.filter((r) => r.simulationStatus === 'passed').length;
  const readyForExecution = rows.filter((r) => r.lifecycleState === 'ready_to_execute').length;
  const blocked = rows.filter(
    (r) => r.lifecycleState === 'execution_failed' || r.lifecycleState === 'blocked',
  ).length;
  const lastEval = rows
    .map((r) => r.createdAt)
    .filter(Boolean)
    .sort()
    .slice(-1)[0] as string | undefined;

  // Load deterministic safety checks for the selected executable action.
  useEffect(() => {
    const a = selectedAction;
    if (!a) {
      setChecks(null);
      setChecksState('idle');
      return;
    }
    if (a.recordType === 'ai_recommendation_review') {
      setChecks(null);
      setChecksState('not_applicable');
      return;
    }
    let cancelled = false;
    setChecksState('loading');
    void fetch(`/api/response/actions/${encodeURIComponent(a.id)}/safety-checks`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: SafetyChecksPayload) => {
        if (cancelled) return;
        setChecks(data);
        setChecksState('ready');
      })
      .catch(() => {
        if (cancelled) return;
        setChecks(null);
        setChecksState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [selectedAction?.id, selectedAction?.recordType, authHeaders]);

  async function simulateAll() {
    if (simulating) return;
    setSimulating(true);
    onMessage('Simulating all eligible actions…');
    try {
      const qs = incidentIdFilter ? `?incident_id=${encodeURIComponent(incidentIdFilter)}` : '';
      const res = await fetch(`/api/response/actions/simulate-all${qs}`, {
        method: 'POST',
        headers: authHeaders(),
      });
      const data = (await res.json().catch(() => ({}))) as {
        counts?: { simulated?: number; skipped?: number };
        detail?: unknown;
      };
      if (res.ok) {
        const n = data.counts?.simulated ?? 0;
        const skipped = data.counts?.skipped ?? 0;
        onMessage(
          n > 0
            ? `Simulated ${n} eligible action${n === 1 ? '' : 's'}${skipped ? ` (${skipped} skipped)` : ''}.`
            : 'No eligible actions to simulate.',
        );
        router.refresh();
      } else {
        const detail = data.detail;
        onMessage(typeof detail === 'string' ? detail : 'Simulate All failed.');
      }
    } catch {
      onMessage('Simulate All request failed. Check network connection.');
    } finally {
      setSimulating(false);
    }
  }

  const summaryRows: Array<[string, number]> = [
    ['Actions recommended', recommended],
    ['Awaiting approval', awaitingApproval],
    ['Ready for dry run', readyForDryRun],
    ['Simulated', simulated],
    ['Ready for execution', readyForExecution],
    ['Blocked', blocked],
  ];

  return (
    <aside
      className="dataCard sharedSurfaceCard"
      style={{ padding: '1rem' }}
      aria-label="Playbook Execution Agent"
    >
      <p className="eyebrow" style={{ marginBottom: '0.15rem', fontSize: '0.7rem' }}>
        Autonomous operations
      </p>
      <h4 style={{ marginBottom: '0.25rem', fontSize: '0.95rem' }}>Playbook Execution Agent</h4>
      <p className="muted" style={{ fontSize: '0.76rem', margin: '0 0 0.75rem' }}>
        Deterministic execution. Runbooks are version-controlled and peer-reviewed; nothing executes without passing safety checks and required approval.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', rowGap: '0.3rem', columnGap: '0.75rem', marginBottom: '0.85rem' }}>
        {summaryRows.map(([label, value]) => (
          <div key={label} style={{ display: 'contents' }}>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{label}</span>
            <span style={{ fontSize: '0.8rem', fontWeight: 700, textAlign: 'right' }}>{value}</span>
          </div>
        ))}
      </div>

      <div style={{ marginBottom: '0.85rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Dry Run Status</p>
        <p style={{ fontSize: '0.8rem', margin: 0 }}>
          {readyForDryRun} ready · {simulated} simulated
        </p>
        <p className="muted" style={{ fontSize: '0.72rem', margin: '0.15rem 0 0' }}>
          {liveExecutionAllowed
            ? 'Live execution path detected for eligible actions.'
            : 'Execution is dry-run only until a live provider is configured.'}
        </p>
      </div>

      <button
        type="button"
        className="btn btn-primary"
        style={{ fontSize: '0.82rem', width: '100%', marginBottom: '0.85rem' }}
        disabled={simulating || readyForDryRun === 0}
        title={readyForDryRun === 0 ? 'No eligible actions to simulate' : 'Simulate all eligible actions'}
        onClick={() => void simulateAll()}
      >
        {simulating ? 'Simulating…' : 'Simulate All'}
      </button>

      <div>
        <p className="tableMeta" style={{ marginBottom: '0.35rem' }}>Safety Checks</p>
        {!selectedAction ? (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>Select an action to run its safety checks.</p>
        ) : checksState === 'not_applicable' ? (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
            Safety checks apply to executable response actions. This is an AI recommendation review (never executed).
          </p>
        ) : checksState === 'loading' ? (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>Running safety checks…</p>
        ) : checksState === 'error' ? (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
            Safety checks are unavailable for this action right now.
          </p>
        ) : checks && Array.isArray(checks.checks) && checks.checks.length > 0 ? (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
            {checks.checks.map((c) => {
              const pill = safetyStatusPill(c.status);
              return (
                <li key={c.key} style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem', justifyContent: 'space-between' }}>
                  <span style={{ fontSize: '0.76rem', color: 'var(--text-secondary)', flex: 1 }} title={c.detail}>
                    {c.label}
                  </span>
                  <StatusPill label={pill.label} variant={pill.variant} />
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>No safety checks were produced.</p>
        )}
        {checksState === 'ready' && checks?.summary?.overall ? (
          <p className="muted" style={{ fontSize: '0.72rem', margin: '0.5rem 0 0' }}>
            Overall: {checks.summary.overall}. Checks that lack data are shown as Unknown, never Pass.
          </p>
        ) : null}
      </div>
    </aside>
  );
}
