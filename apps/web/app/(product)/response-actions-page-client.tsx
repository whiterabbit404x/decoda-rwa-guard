'use client';

// fallback examples remain clearly marked as SIMULATED
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react';

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
import {
  approvalErrorRequiresStepUp,
  approvalResultMessage,
  canonicalLifecycleState,
  compareHistoryRecency,
  completeMfaHref,
  deriveRowApproval,
  formatExactTimestamp,
  formatRelativeTime,
  historyOutcome,
  isApprovalDecided,
  isApprovalHistoryEvent,
  isSessionVerificationRequired,
  lifecycleLabelFor,
  normalizeApprovalGate,
  normalizeHistoryEventDto,
  reconcileCount,
  responseActionApprovalEndpoint,
  type ApprovalGate,
} from './response-actions-presentation';
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
  // Canonical approval quorum + caller permission (backend is the authority).
  requiredApprovalCount?: number;
  currentApprovalCount?: number;
  canCurrentUserApprove?: boolean | null;
  approvalPermissionReason?: string | null;
  // Canonical approval GATE: role + separation of duties + the session-MFA step-up +
  // quorum, composed by the backend. Drives the contextual approval-blocked UI so
  // the client never re-derives the MFA policy or the action's risk wording.
  approvalGate?: ApprovalGate | null;
  // Distinct target so same-title actions across incidents are disambiguated.
  targetLabel?: string | null;
};

// Canonical simulation-eligibility breakdown so the agent panel can explain WHY
// nothing is eligible (already-simulated vs blocked + reason) rather than a bare
// "No Eligible Actions to Simulate".
type SimulationBreakdown = {
  eligible: number;
  alreadySimulated: number;
  blockedTotal: number;
  blockedReasons: Array<{ reasonCode: string; label: string; count: number }>;
  total: number;
};

// Canonical response-action summary returned by the backend list endpoint. ONE
// definition, shared by the cards, the Approval Required banner, and the agent
// panel so those surfaces can never disagree with the rows.
type ActionsSummary = {
  recommended: number;
  pendingApproval: number;
  simulated: number;
  executed: number;
  readyForExecution: number;
  blocked: number;
  total: number;
  simulation: SimulationBreakdown | null;
};

function normalizeSummary(input: any): ActionsSummary | null {
  if (!input || typeof input !== 'object') return null;
  const num = (v: any): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  const simInput = input.simulation && typeof input.simulation === 'object' ? input.simulation : null;
  const simulation: SimulationBreakdown | null = simInput
    ? {
        eligible: num(simInput.eligible),
        alreadySimulated: num(simInput.already_simulated),
        blockedTotal: num(simInput.blocked_total),
        blockedReasons: Array.isArray(simInput.blocked_reasons)
          ? simInput.blocked_reasons.map((r: any) => ({
              reasonCode: String(r?.reason_code ?? ''),
              label: String(r?.label ?? ''),
              count: num(r?.count),
            }))
          : [],
        total: num(simInput.total),
      }
    : null;
  return {
    recommended: num(input.recommended),
    pendingApproval: num(input.pending_approval ?? input.awaiting_approval),
    simulated: num(input.simulated),
    executed: num(input.executed),
    readyForExecution: num(input.ready_for_execution),
    blocked: num(input.blocked),
    total: num(input.total),
    simulation,
  };
}

// Unified Action History row — a superset of the canonical backend DTO plus a few
// UI-only flags. EVERY field is supplied by the backend DTO (or reconstructed once by
// normalizeHistoryEventDto for a pre-DTO payload); the table + details view render
// these directly and never re-derive a label, actor identity, or route from a raw
// event payload.
type HistoryRow = {
  id: string;
  eventType: string;
  // Operator-facing EVENT label ("Response Action Approved") — distinct from the
  // action TITLE ("Notify Security Team"). Both are shown; neither is a raw enum.
  eventLabel: string;
  actionTitle: string | null;
  actionKey: string | null;
  typeLabel: string;
  result: string;
  outcome: string;
  // Actor account identity for display (email / name). The raw UUID lives ONLY in
  // actorId (a technical/copyable field), never as the primary label.
  actorDisplay: string;
  actorEmail: string | null;
  actorId: string | null;
  time: string | null;
  evidenceSource: string;
  evidenceSourceLabel: string | null;
  simulated: boolean;
  decision?: string | null;
  decisionLabel?: string | null;
  previousStateLabel?: string | null;
  newStateLabel?: string | null;
  approvalProgressLabel?: string | null;
  approvalPolicy?: string | null;
  executionReference?: string | null;
  note?: string | null;
  linkedIncident?: string | null;
  incidentShortId?: string | null;
  actionRoute?: string | null;
  incidentRoute?: string | null;
  evidenceRoute?: string | null;
  auditRoute?: string | null;
  eventMetadata?: Record<string, unknown>;
  // AI recommendation-review extensions (record_type === 'ai_recommendation_review').
  recordType?: string;
  sourceType?: string;
  evidenceSnapshotId?: string | null;
  evidenceRefsCount?: number;
  provider?: string | null;
  model?: string | null;
  approvalEvent?: boolean;
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
  'Event',
  'Action',
  'Type',
  'Result',
  'Actor',
  'Time',
  'Evidence Source',
  'Decision',
  'Outcome',
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

  // Distinct target so same-title actions (e.g. several "Notify security team"
  // recommendations, now deduplicated by the backend) are disambiguated by a
  // READABLE label — never by their raw UUID. Prefer the backend's canonical
  // target_label (the recommendation's reason / recipient context), then an on-chain
  // target, then the linked incident short id. After backend deduplication, any
  // remaining same-title rows are legitimately different (different incident, target,
  // or runbook) and this label is what tells them apart at a glance.
  const shortId = (value: string) => (value.length > 8 ? `${value.slice(0, 8)}…` : value);
  const backendTargetLabel =
    typeof input?.target_label === 'string' && input.target_label.trim()
      ? String(input.target_label).trim()
      : null;
  const incidentForLabel = directIncidentId || linkedIncident;
  const targetLabel = backendTargetLabel
    ? backendTargetLabel
    : input?.target_wallet
      ? String(input.target_wallet)
      : input?.token_contract
        ? String(input.token_contract)
        : incidentForLabel
          ? `Incident ${shortId(incidentForLabel)}`
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

  // Canonical approval status + requires-approval derived TOGETHER from one source
  // so the "Requires Approval" pill and the Pending Approval count can never
  // contradict. Fails CLOSED on a legacy/partial payload (see deriveRowApproval).
  const { approvalStatus: canonicalApproval, requiresApproval } = deriveRowApproval({
    approvalStatus: lifecycle.approval_status ?? input?.approval_status,
    rawStatus,
    requiresApproval: input?.requires_approval,
  });
  // Canonical lifecycle state + label — legacy snake_case enums (pending_approval …)
  // are mapped, never rendered raw.
  const canonicalState = canonicalLifecycleState(
    lifecycle.lifecycle_state || input?.lifecycle_state || rawStatus,
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
    requiresApproval,
    simulated,
    eta: input?.eta ?? input?.estimated_duration ?? input?.estimated_impact ?? null,
    approvalState: input?.approval_state ?? canonicalApproval,
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
    lifecycleState: canonicalState,
    lifecycleLabel: lifecycleLabelFor(
      lifecycle.lifecycle_state || input?.lifecycle_state || rawStatus,
      lifecycle.lifecycle_label || input?.lifecycle_label,
    ),
    approvalStatus: canonicalApproval,
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
    // Canonical approval quorum + caller permission (from the backend, single source).
    requiredApprovalCount:
      typeof commands.required_approval_count === 'number'
        ? commands.required_approval_count
        : typeof input?.required_approval_count === 'number'
          ? input.required_approval_count
          : requiresApproval
            ? 1
            : 0,
    currentApprovalCount:
      typeof commands.current_approval_count === 'number'
        ? commands.current_approval_count
        : typeof input?.current_approval_count === 'number'
          ? input.current_approval_count
          : 0,
    canCurrentUserApprove:
      typeof commands.can_current_user_approve === 'boolean' ? commands.can_current_user_approve : null,
    approvalPermissionReason: commands.approval_permission_reason
      ? String(commands.approval_permission_reason)
      : null,
    // Canonical approval gate (backend authority). Read from the top-level payload;
    // absent on legacy payloads, in which case the older permission-only path renders.
    approvalGate: normalizeApprovalGate(input?.approval_gate),
    targetLabel,
  };
}

// Map a backend Action History row (the canonical DTO, or a pre-DTO legacy payload)
// onto the unified HistoryRow. The heavy lifting — human EVENT label, canonical action
// TITLE, resolved actor identity, previous -> new lifecycle, decision, quorum progress,
// evidence provenance, and navigation routes — is done ONCE by normalizeHistoryEventDto
// (backend-authoritative); this function never re-derives a label or actor from raw
// event fields.
function normalizeHistoryRow(input: any): HistoryRow {
  const dto = normalizeHistoryEventDto(input);
  const evidence = (dto.evidenceSource ?? '').toLowerCase();
  const simulated = evidence === 'fallback' || evidence === 'simulator' || evidence === 'demo';
  const approvalEvent = isApprovalHistoryEvent(input) || dto.eventType.startsWith('response_action_');
  return {
    id: dto.eventId || '-',
    eventType: dto.eventType,
    eventLabel: dto.eventLabel,
    actionTitle: dto.actionTitle,
    actionKey: dto.actionKey,
    typeLabel: dto.typeLabel || 'Action Approval',
    result: dto.resultLabel || dto.result || 'Recorded',
    outcome: historyOutcome({ eventType: dto.eventType, decision: dto.decision, resultLabel: dto.resultLabel }),
    actorDisplay: dto.actorEmail || dto.actorDisplayName || 'System',
    actorEmail: dto.actorEmail,
    actorId: dto.actorId,
    time: dto.occurredAt,
    evidenceSource: dto.evidenceSource || 'runtime',
    evidenceSourceLabel: dto.evidenceSourceLabel,
    simulated,
    decision: dto.decision,
    decisionLabel: dto.decisionLabel,
    previousStateLabel: dto.previousStateLabel,
    newStateLabel: dto.newStateLabel,
    approvalProgressLabel: dto.approvalProgressLabel,
    approvalPolicy: dto.approvalPolicy,
    executionReference: dto.executionReference,
    note: dto.note,
    approvalEvent,
    linkedIncident: dto.incidentId,
    incidentShortId: dto.incidentShortId,
    actionRoute: dto.actionRoute,
    incidentRoute: dto.incidentRoute,
    evidenceRoute: dto.evidenceRoute,
    auditRoute: dto.auditRoute,
    eventMetadata: dto.eventMetadata,
  };
}

// Accepted / rejected AI recommendation reviews are immutable human-review records,
// not executed actions. They render in Action History with a truthful AI source, the
// decision, executed=No, the reviewer, and links to the incident and its evidence.
function normalizeAiReviewHistoryRow(input: any): HistoryRow {
  // This is the RECOMMENDATION REVIEW event, kept strictly separate from the
  // response-action APPROVAL event (an audit-history row rendered by
  // normalizeHistoryRow). Its decision + timestamp come from the REVIEW domain
  // (review_state / reviewed_at) — NEVER from approval_status — so an approval is
  // never rendered here as an "Accepted" review, and the two events stay distinct.
  const reviewState = String(input?.review_state || input?.decision || '').toLowerCase();
  const decision =
    reviewState === 'accepted' ? 'accepted' : reviewState === 'rejected' ? 'rejected' : null;
  const incidentId = input?.incident_id ? String(input.incident_id) : null;
  const actionId = String(input?.recommendation_id || input?.id || '-');
  const reviewer = String(input?.reviewer_email || input?.reviewer_id || 'Reviewer');
  return {
    id: actionId,
    eventType: 'recommendation_reviewed',
    eventLabel: 'AI Recommendation Reviewed',
    actionTitle: String(input?.title || input?.action_type || 'AI recommendation'),
    actionKey: input?.action_type ? String(input.action_type) : null,
    typeLabel: 'AI Recommendation Review',
    result: decision === 'accepted' ? 'Accepted' : decision === 'rejected' ? 'Rejected' : 'Reviewed',
    outcome: historyOutcome({ recordType: 'ai_recommendation_review', decision }),
    actorDisplay: reviewer,
    actorEmail: reviewer.includes('@') ? reviewer : null,
    actorId: input?.reviewer_id ? String(input.reviewer_id) : null,
    time: input?.reviewed_at ?? input?.created_at ?? null,
    // AI investigation evidence — never simulator, never live-chain.
    evidenceSource: String(input?.evidence_source || 'ai_investigation'),
    evidenceSourceLabel: 'AI investigation',
    simulated: false,
    recordType: 'ai_recommendation_review',
    sourceType: String(input?.source_type || 'ai_investigation'),
    decision,
    decisionLabel: decision === 'accepted' ? 'Accepted' : decision === 'rejected' ? 'Rejected' : null,
    approvalEvent: false,
    linkedIncident: incidentId,
    incidentShortId: incidentId ? `INC-${incidentId.slice(0, 8)}` : null,
    actionRoute: actionId && actionId !== '-' ? `/response-actions?action_id=${actionId}` : null,
    incidentRoute: incidentId ? `/incidents/${incidentId}` : null,
    evidenceRoute: incidentId ? `/evidence?incident_id=${incidentId}` : null,
    auditRoute: actionId && actionId !== '-' ? `/history?event_id=${actionId}` : null,
    evidenceSnapshotId: input?.evidence_snapshot_id ? String(input.evidence_snapshot_id) : null,
    evidenceRefsCount:
      typeof input?.evidence_refs_count === 'number'
        ? input.evidence_refs_count
        : Array.isArray(input?.evidence_refs)
          ? input.evidence_refs.length
          : 0,
    provider: input?.provider ?? null,
    model: input?.model ?? null,
    eventMetadata: {
      recommendation_id: actionId,
      review_state: reviewState || null,
      provider: input?.provider ?? null,
      model: input?.model ?? null,
      evidence_snapshot_id: input?.evidence_snapshot_id ?? null,
    },
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
  const [backendSummary, setBackendSummary] = useState<ActionsSummary | null>(null);
  const [historyRows, setHistoryRows] = useState<HistoryRow[]>([]);
  const [selectedId, setSelectedId] = useState(actionIdParam);
  // The history row whose full event details are open (event-details view). Independent
  // of the Recommended-Actions selection.
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  // Dedicated Action History filters — SEPARATE from the Recommended-Actions filters, so
  // an active-action predicate (Requires Approval / status) can never hide a completed
  // approval, a recommendation review, a blocked attempt, a simulation, or an execution.
  const [historyEventFilter, setHistoryEventFilter] = useState('');
  const [historyResultFilter, setHistoryResultFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  // Seed the approval (Review All) filter from the URL so returning from the security
  // (MFA) flow restores the exact filter the operator left, alongside the selected
  // action (action_id) and incident scope (incident_id).
  const [approvalFilter, setApprovalFilter] = useState(searchParams.get('approval') ?? '');
  const [dataLoading, setDataLoading] = useState(false);
  const [liveExecutionAllowed, setLiveExecutionAllowed] = useState(false);
  const [message, setMessage] = useState('');
  // Bumped after any command that mutates canonical state (approve, reject,
  // simulate, execute) so the client re-fetches /api/response/actions and its
  // summary — router.refresh() alone does not re-run this client data load, which
  // is why Pending Approval / the banner / the row state appeared "stuck".
  const [reloadKey, setReloadKey] = useState(0);
  const reloadActions = () => setReloadKey((k) => k + 1);

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
        // Every customer-facing read goes through the SAME-ORIGIN /api proxy. A direct
        // `${apiUrl}/…` browser fetch silently fails in this deployment because only
        // NEXT_PUBLIC_API_URL is exposed client-side and it is unset, so `apiUrl` is ''
        // and the request hits the Next server (no such route) instead of the backend.
        // That is why the persisted response-action approval audit event never reached
        // Action History: the /history/actions read failed and the client rendered an
        // empty audit stream, leaving only the recommendation-review row (derived from
        // the already-proxied /api/response/actions payload). The Action History,
        // Alerts and Incidents reads must use the proxy transport, like runtime-status.
        const [actionsRes, historyRes, alertsRes, incidentsRes, runtimePayload] = await Promise.all([
          fetch(`/api/response/actions${actionsQs}`, { headers, cache: 'no-store' }).catch(() => null),
          fetch(`/api/history/actions?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
          fetch(`/api/alerts?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
          fetch(`/api/incidents?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
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
        // AI-recommendation-backed actions are returned in the same list, split by the
        // canonical APPROVAL status (from the response-action approval domain), NOT by
        // review_state: an action still Awaiting Approval belongs in Recommended
        // Actions; one decided in the approval domain (approved/rejected) is an
        // immutable history record. A prior recommendation review never moves it.
        const aiDecided = (item: any): boolean =>
          isApprovalDecided(item?.approval_status ?? item?.lifecycle?.approval_status);
        const aiReviews = allActions.filter(
          (item: any) => String(item?.record_type || '') === 'ai_recommendation_review',
        );
        const legacyActions = allActions.filter(
          (item: any) => String(item?.record_type || '') !== 'ai_recommendation_review',
        );
        const pendingAiReviews = aiReviews.filter((item: any) => !aiDecided(item));
        const decidedAiReviews = aiReviews.filter((item: any) => aiDecided(item));

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
        // Recommendation REVIEW rows come from the decided AI actions, but ONLY when a
        // genuine human review actually happened (review_state accepted/rejected). An
        // approved-but-unreviewed action is represented solely by its response-action
        // approval audit event (above) — never as a fabricated "review" row.
        const reviewHistory = decidedAiReviews.map(normalizeAiReviewHistoryRow).filter((row: HistoryRow) => row.decision === 'accepted' || row.decision === 'rejected');
        // ONE newest-first stream across BOTH domains — response-action APPROVAL audit
        // events and recommendation REVIEW records — ordered by the ACTUAL event time.
        // A just-completed approval (its own timestamp) therefore sorts ABOVE an older
        // recommendation review, so the newest event is always visible first.
        const history = [...reviewHistory, ...auditHistory].sort(compareHistoryRecency);

        if (!cancelled) {
          setRecommendedRows(recommended);
          // Canonical counts from the backend summary (one definition for cards,
          // banner, and agent panel). The row-derived counts stay as a fallback +
          // dev-time consistency check.
          setBackendSummary(normalizeSummary(actionsPayload?.summary));
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
  // reloadKey is included so a completed command (approve/reject/simulate/execute)
  // re-fetches the canonical actions + summary.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl, authHeaders, runtimeLoading, incidentIdFilter, actionIdParam, reloadKey]);

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
    // History filtering depends ONLY on the free-text search and the DEDICATED history
    // filters (event type / result) — never on the Recommended-Actions active-action
    // predicates — so Action History stays a complete, independent audit stream.
    return historyRows.filter((row) => {
      const q = search.toLowerCase();
      const matchesSearch =
        !q ||
        row.eventLabel.toLowerCase().includes(q) ||
        (row.actionTitle ?? '').toLowerCase().includes(q) ||
        row.actorDisplay.toLowerCase().includes(q) ||
        (row.incidentShortId ?? '').toLowerCase().includes(q) ||
        row.id.toLowerCase().includes(q);
      const matchesEvent = !historyEventFilter || row.eventType === historyEventFilter;
      const matchesResult =
        !historyResultFilter ||
        row.result.toLowerCase().includes(historyResultFilter.toLowerCase());
      return matchesSearch && matchesEvent && matchesResult;
    });
  }, [historyRows, search, historyEventFilter, historyResultFilter]);

  const selectedHistoryRow = useMemo(
    () => historyRows.find((r) => r.id === selectedHistoryId) ?? null,
    [historyRows, selectedHistoryId],
  );

  const activeRows = tab === 'recommended' ? filteredRecommended : filteredHistory;

  const selectedAction = useMemo(
    () => filteredRecommended.find((r) => r.id === selectedId) ?? filteredRecommended[0] ?? null,
    [filteredRecommended, selectedId],
  );

  // Summary-card counts come from the ONE canonical backend summary so the cards,
  // the Approval Required banner, and the Playbook Execution Agent panel can never
  // disagree with each other or with the rows. The row-derived values are kept as
  // a fallback (older backend without a summary) AND as a dev-time consistency
  // assertion — a mismatch means the backend and the rows have diverged.
  const isActiveRecommendation = (r: ActionRow): boolean => {
    const s = (r.lifecycleState ?? '').toLowerCase();
    return s !== 'cancelled' && s !== 'rejected' && s !== 'rolled_back';
  };
  const rowRecommended = recommendedRows.filter(isActiveRecommendation).length;
  const rowPendingApproval = recommendedRows.filter((r) => r.approvalStatus === 'pending').length;
  const rowSimulated = recommendedRows.filter((r) => r.simulationStatus === 'passed').length;
  const rowExecuted = recommendedRows.filter((r) => r.executionStatus === 'executed').length;

  // Reconcile the canonical backend summary with the row-derived counts. When they
  // agree, the canonical backend value is used; when a stale/partial backend
  // disagrees with the visible rows, we fail CLOSED to the row-derived truth so a
  // card can never read 0 while the table shows actions awaiting approval.
  const recommendedCount = reconcileCount(backendSummary?.recommended, rowRecommended);
  const pendingApprovalCount = reconcileCount(backendSummary?.pendingApproval, rowPendingApproval);
  const simulatedCount = reconcileCount(backendSummary?.simulated, rowSimulated);
  const executedCount = reconcileCount(backendSummary?.executed, rowExecuted);

  // Dev-time invariant: the canonical backend summary must match the row-derived
  // counts. Surfacing a divergence here prevents the "cards say 0 while rows say
  // Requires Approval" class of bug from silently returning.
  useEffect(() => {
    if (!backendSummary || recommendedRows.length === 0) return;
    if (process.env.NODE_ENV === 'production') return;
    if (
      backendSummary.pendingApproval !== rowPendingApproval ||
      backendSummary.simulated !== rowSimulated ||
      backendSummary.executed !== rowExecuted ||
      backendSummary.recommended !== rowRecommended
    ) {
      // eslint-disable-next-line no-console
      console.warn(
        'response-actions summary/rows count mismatch',
        { backendSummary, rowRecommended, rowPendingApproval, rowSimulated, rowExecuted },
      );
    }
  }, [backendSummary, rowRecommended, rowPendingApproval, rowSimulated, rowExecuted, recommendedRows.length]);

  // Approval-required count for the orange banner + agent panel. Canonical backend
  // summary first; row-derived approval_status as the fallback.
  function actionNeedsApproval(r: ActionRow): boolean {
    return r.approvalStatus === 'pending';
  }
  const approvalRequiredCount = reconcileCount(
    backendSummary?.pendingApproval,
    recommendedRows.filter(actionNeedsApproval).length,
  );

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
            placeholder={tab === 'history' ? 'Search history...' : 'Search actions...'}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ flex: '1 1 200px', minWidth: '180px' }}
            aria-label={tab === 'history' ? 'Search history' : 'Search actions'}
          />

          {tab === 'recommended' ? (
            <>
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
            </>
          ) : (
            // DEDICATED Action History filters — event type + result. These NEVER include
            // the Requires Approval / status active-action predicates, so no completed
            // approval, review, blocked attempt, simulation, or execution is ever hidden.
            <>
              <select
                value={historyEventFilter}
                onChange={(e) => setHistoryEventFilter(e.target.value)}
                aria-label="History event type filter"
              >
                <option value="">All Events</option>
                <option value="response_action_approved">Response Action Approved</option>
                <option value="response_action_approval_recorded">Approval Recorded</option>
                <option value="response_action_rejected">Response Action Rejected</option>
                <option value="response_action_approval_blocked">Approval Attempt Blocked</option>
                <option value="response_action_executed">Response Action Executed</option>
                <option value="recommendation_reviewed">AI Recommendation Reviewed</option>
              </select>

              <select
                value={historyResultFilter}
                onChange={(e) => setHistoryResultFilter(e.target.value)}
                aria-label="History result filter"
              >
                <option value="">All Results</option>
                <option value="Success">Success</option>
                <option value="MFA Required">MFA Required</option>
                <option value="Accepted">Accepted</option>
                <option value="Rejected">Rejected</option>
              </select>
            </>
          )}
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
                    const evSrc = evidenceSourcePill(
                      row.evidenceSource,
                      workspaceEvidenceSource,
                      row.evidenceSourceLabel,
                    );
                    const isAiReview = row.recordType === 'ai_recommendation_review';
                    const exactTime = formatExactTimestamp(row.time);
                    const isSelected = row.id === selectedHistoryId;

                    return (
                      <tr
                        key={row.id}
                        onClick={() => setSelectedHistoryId(isSelected ? null : row.id)}
                        style={{ cursor: 'pointer', background: isSelected ? 'rgba(59,130,246,0.08)' : undefined }}
                      >
                        {/* EVENT — the operator-facing event label (never a raw enum), with
                            the previous -> new lifecycle transition + quorum progress. */}
                        <td style={{ maxWidth: '190px' }}>
                          <div style={{ fontWeight: 600, fontSize: '0.82rem' }}>{row.eventLabel}</div>
                          {row.previousStateLabel && row.newStateLabel ? (
                            <div className="muted" style={{ marginTop: '0.15rem', fontSize: '0.68rem' }}>
                              {row.previousStateLabel} → {row.newStateLabel}
                            </div>
                          ) : null}
                          {row.approvalProgressLabel ? (
                            <div className="muted" style={{ fontSize: '0.68rem' }}>
                              {row.approvalProgressLabel} approvals
                            </div>
                          ) : null}
                        </td>
                        {/* ACTION — the human action TITLE ("Notify Security Team"). */}
                        <td style={{ maxWidth: '170px' }}>
                          <span style={{ fontSize: '0.82rem' }}>{row.actionTitle ?? '—'}</span>
                          {isAiReview ? (
                            <div style={{ marginTop: '0.2rem' }}>
                              <StatusPill label="AI recommendation" variant="info" />
                            </div>
                          ) : null}
                        </td>
                        <td style={{ fontSize: '0.8rem' }}>{row.typeLabel}</td>
                        <td style={{ fontSize: '0.8rem', maxWidth: '130px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {row.result}
                        </td>
                        {/* ACTOR — account identity (email/name). The raw UUID is only a
                            tooltip/technical hint, never the primary label. */}
                        <td style={{ fontSize: '0.8rem' }} title={row.actorId ?? undefined}>
                          {row.actorDisplay}
                        </td>
                        {/* TIME — readable relative time in the table; the EXACT persisted
                            timestamp is on hover + as the accessible label. */}
                        <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>
                          <time dateTime={row.time ?? undefined} title={exactTime} aria-label={exactTime}>
                            {formatRelativeTime(row.time)}
                          </time>
                        </td>
                        <td><StatusPill label={evSrc.label} variant={evSrc.variant} /></td>
                        <td>
                          {row.decisionLabel ? (
                            <StatusPill
                              label={row.decisionLabel}
                              variant={
                                row.decision === 'approved' || row.decision === 'accepted'
                                  ? 'success'
                                  : row.decision === 'rejected'
                                    ? 'neutral'
                                    : 'info'
                              }
                            />
                          ) : (
                            <span className="muted" style={{ fontSize: '0.78rem' }}>Not applicable</span>
                          )}
                        </td>
                        {/* OUTCOME — event-specific, never a bare context-free dash. An
                            approval reads "Approval quorum reached", not an execution. */}
                        <td style={{ fontSize: '0.78rem', maxWidth: '150px' }}>{row.outcome}</td>
                        <td style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }} onClick={(e) => e.stopPropagation()}>
                          <span style={{ display: 'inline-flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                            {row.actionRoute ? (
                              <Link href={row.actionRoute} prefetch={false} style={{ fontSize: '0.75rem' }}>
                                View Action
                              </Link>
                            ) : null}
                            {row.incidentRoute ? (
                              <Link href={row.incidentRoute} prefetch={false} style={{ fontSize: '0.75rem' }}>
                                View Incident
                              </Link>
                            ) : null}
                            {row.auditRoute ? (
                              <Link href={row.auditRoute} prefetch={false} style={{ fontSize: '0.75rem' }}>
                                View Audit Event
                              </Link>
                            ) : null}
                            <button
                              type="button"
                              onClick={() => setSelectedHistoryId(isSelected ? null : row.id)}
                              style={{ background: 'none', border: 'none', padding: 0, color: 'var(--accent, #3b82f6)', cursor: 'pointer', fontSize: '0.75rem' }}
                            >
                              {isSelected ? 'Hide details' : 'View details'}
                            </button>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </TableShell>
              )}
              {tab === 'history' && selectedHistoryRow ? (
                <HistoryEventDetails
                  row={selectedHistoryRow}
                  workspaceEvidenceSource={workspaceEvidenceSource}
                  onClose={() => setSelectedHistoryId(null)}
                />
              ) : null}
            </div>

            <div style={{ display: 'grid', gap: '1rem' }}>
              <PlaybookAgentPanel
                rows={recommendedRows}
                summary={backendSummary}
                incidentIdFilter={incidentIdFilter}
                selectedAction={selectedAction}
                liveExecutionAllowed={liveExecutionAllowed}
                apiUrl={apiUrl}
                authHeaders={authHeaders}
                onMessage={setMessage}
                onDataChanged={reloadActions}
              />
              {selectedAction ? (
                <ActionDetailPanel
                  action={selectedAction}
                  workspaceEvidenceSource={workspaceEvidenceSource}
                  reviewFilter={approvalFilter}
                  onMessage={setMessage}
                  apiUrl={apiUrl}
                  authHeaders={authHeaders}
                  refreshCsrfToken={refreshCsrfToken}
                  onDataChanged={reloadActions}
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

// Event-details view for a selected Action History row. Renders the FULL canonical
// event from the backend DTO fields the row already carries — the human event label,
// action title + id, incident, actor account identity (with the raw UUID kept as a
// separate copyable technical field), the exact persisted timestamp, previous -> new
// lifecycle, decision, approval progress, result, approval policy, evidence provenance,
// audit event id, a safe event explanation, and the related links. Sensitive
// authentication metadata is never present (the backend strips it from event_metadata).
function HistoryEventDetails({
  row,
  workspaceEvidenceSource,
  onClose,
}: {
  row: HistoryRow;
  workspaceEvidenceSource: string;
  onClose: () => void;
}) {
  const evSrc = evidenceSourcePill(row.evidenceSource, workspaceEvidenceSource, row.evidenceSourceLabel);
  const rows: Array<{ label: string; value: ReactNode }> = [
    { label: 'Event', value: row.eventLabel },
    { label: 'Type', value: row.typeLabel },
    { label: 'Action', value: row.actionTitle ?? '—' },
    {
      label: 'Action ID',
      value: (() => {
        const raw = row.eventMetadata?.action_id;
        return typeof raw === 'string' && raw ? <code style={{ fontSize: '0.72rem' }}>{raw}</code> : '—';
      })(),
    },
    { label: 'Incident', value: row.incidentShortId ?? '—' },
    { label: 'Actor', value: row.actorEmail ?? row.actorDisplay },
    {
      label: 'Actor ID (technical)',
      value: row.actorId ? <code style={{ fontSize: '0.72rem' }}>{row.actorId}</code> : '—',
    },
    { label: 'Exact timestamp', value: formatExactTimestamp(row.time) },
    { label: 'Previous state', value: row.previousStateLabel ?? '—' },
    { label: 'New state', value: row.newStateLabel ?? '—' },
    { label: 'Decision', value: row.decisionLabel ?? 'Not applicable' },
    { label: 'Approval progress', value: row.approvalProgressLabel ? `${row.approvalProgressLabel} approvals` : '—' },
    { label: 'Result', value: row.result },
    { label: 'Outcome', value: row.outcome },
    { label: 'Approval policy', value: row.approvalPolicy ? humanizeApprovalPolicy(row.approvalPolicy) : '—' },
    { label: 'Evidence provenance', value: <StatusPill label={evSrc.label} variant={evSrc.variant} /> },
    { label: 'Execution reference', value: row.executionReference ? <code style={{ fontSize: '0.72rem' }}>{row.executionReference}</code> : '—' },
    { label: 'Audit event ID', value: <code style={{ fontSize: '0.72rem' }}>{row.id}</code> },
  ];

  return (
    <div
      style={{
        marginTop: '1rem',
        border: '1px solid var(--border, #e5e7eb)',
        borderRadius: '0.5rem',
        padding: '1rem',
        background: 'var(--panel, rgba(0,0,0,0.02))',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.95rem' }}>{row.eventLabel}</h3>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted, #6b7280)', fontSize: '0.85rem' }}
          aria-label="Close event details"
        >
          Close
        </button>
      </div>
      <p className="muted" style={{ marginTop: 0, fontSize: '0.8rem' }}>
        {historyEventExplanation(row)}
      </p>
      <dl style={{ display: 'grid', gridTemplateColumns: 'minmax(140px, max-content) 1fr', gap: '0.35rem 1rem', margin: 0, fontSize: '0.82rem' }}>
        {rows.map((item) => (
          <Fragment key={item.label}>
            <dt className="muted" style={{ fontWeight: 500 }}>{item.label}</dt>
            <dd style={{ margin: 0 }}>{item.value}</dd>
          </Fragment>
        ))}
      </dl>
      {row.note ? (
        <p style={{ marginTop: '0.6rem', fontSize: '0.8rem' }}>
          <span className="muted" style={{ fontWeight: 500 }}>Note: </span>
          {row.note}
        </p>
      ) : null}
      <div style={{ marginTop: '0.75rem', display: 'inline-flex', gap: '0.75rem', flexWrap: 'wrap' }}>
        {row.actionRoute ? (
          <Link href={row.actionRoute} prefetch={false} style={{ fontSize: '0.8rem' }}>View Action</Link>
        ) : null}
        {row.incidentRoute ? (
          <Link href={row.incidentRoute} prefetch={false} style={{ fontSize: '0.8rem' }}>View Incident</Link>
        ) : null}
        {row.evidenceRoute ? (
          <Link href={row.evidenceRoute} prefetch={false} style={{ fontSize: '0.8rem' }}>View Evidence</Link>
        ) : null}
        {row.auditRoute ? (
          <Link href={row.auditRoute} prefetch={false} style={{ fontSize: '0.8rem' }}>View Audit Event</Link>
        ) : null}
      </div>
    </div>
  );
}

// A safe, human explanation of what an event means — no secrets, no raw metadata.
function historyEventExplanation(row: HistoryRow): string {
  switch (row.eventType) {
    case 'response_action_approved':
      return 'A governed response-action approval reached quorum and the action is cleared to proceed toward execution.';
    case 'response_action_approval_recorded':
      return 'An approval decision was recorded, but the required approval quorum has not yet been met.';
    case 'response_action_rejected':
      return 'A governed response-action approval was rejected; the action will not proceed.';
    case 'response_action_approval_blocked':
      return 'An approval attempt was blocked because the session had not completed the required MFA step-up.';
    case 'response_action_executed':
      return 'The approved response action was executed.';
    case 'recommendation_reviewed':
      return 'An AI-generated recommendation was reviewed by an operator. This is a review record, separate from a response-action approval.';
    default:
      return `${row.eventLabel} — a workspace audit event.`;
  }
}

function humanizeApprovalPolicy(policy: string): string {
  return policy
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function ActionDetailPanel({
  action,
  workspaceEvidenceSource,
  reviewFilter,
  onMessage,
  apiUrl,
  authHeaders,
  refreshCsrfToken,
  onDataChanged,
}: {
  action: ActionRow;
  workspaceEvidenceSource: string;
  reviewFilter?: string;
  onMessage: (msg: string) => void;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  refreshCsrfToken: () => Promise<string | null>;
  onDataChanged: () => void;
}) {
  const router = useRouter();
  // Session step-up (Screen 8 approval reauthentication). `user.mfa_enabled` decides
  // between the inline Verify Session flow (already enrolled — the reported case) and
  // the enrollment fallback (not yet enrolled). Enrollment and session reauthentication
  // are SEPARATE workflows, so an enrolled operator is never sent back through enrollment.
  const { user, verifySessionStepUp } = usePilotAuth();
  const mfaEnrolled = user?.mfa_enabled === true;
  const [stepUpOpen, setStepUpOpen] = useState(false);
  const [stepUpCode, setStepUpCode] = useState('');
  const [stepUpError, setStepUpError] = useState<string | null>(null);
  const [stepUpBusy, setStepUpBusy] = useState(false);
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
  // Approve / Reject visibility. Screen 8 "Approve" is a RESPONSE-ACTION APPROVAL
  // for BOTH policy actions AND AI-recommendation-backed actions — it is NEVER an
  // AI recommendation REVIEW. The backend's allowed_commands is the single
  // authority (it re-checks role + separation of duties on every command; button
  // visibility is never authorization). can_current_user_approve surfaces a
  // truthful read-only reason when the caller is not authorized or has already
  // recorded a decision for this action version.
  const approvalPending = action.approvalStatus === 'pending';
  const commandAllowsApprove = allowedCommands.includes('approve');
  const userMayApprove = action.canCurrentUserApprove !== false;
  // Canonical approval GATE (backend authority): role + separation of duties + the
  // session-MFA step-up + quorum. It decides whether the caller can approve NOW; a
  // legacy payload without a gate falls back to the permission-only signal so an
  // older backend still renders Approve.
  const gate = action.approvalGate ?? null;
  const canApproveNow = gate ? gate.canApprove : userMayApprove;
  // Approval is blocked specifically by the missing session-MFA step-up (the caller
  // HAS approval permission). This renders a contextual "Approval blocked" panel
  // with a DISABLED Approve control and a Complete MFA call to action — never a
  // silent failure, and never generic "destructive" wording for a low-risk action.
  const mfaBlocked = approvalPending && commandAllowsApprove && isSessionVerificationRequired(gate);
  const showApproval = commandAllowsApprove && canApproveNow;
  // A truthful read-only reason for the OTHER blocked cases (wrong role / already
  // decided) — the operator always sees WHY, rather than a hidden control.
  const approvalReadOnlyReason =
    approvalPending && commandAllowsApprove && !canApproveNow && !mfaBlocked
      ? gate?.blockedReason ?? action.approvalPermissionReason ?? 'You do not have permission to approve this action.'
      : null;
  // The app's ESTABLISHED security flow, preserving the selected action + Review All
  // filter + incident scope so the operator returns to this exact action after MFA.
  const mfaHref = completeMfaHref({
    actionId: action.id,
    incidentId: action.linkedIncident,
    approvalFilter: reviewFilter || 'yes',
  });
  const approvalDenominator =
    gate?.requiredApprovalCount ?? action.requiredApprovalCount ?? (action.requiresApproval ? 1 : 0);
  const approvalNumerator = gate?.currentApprovalCount ?? action.currentApprovalCount ?? 0;

  async function handleVerifySession() {
    // Submit ONLY the current authenticator code to the dedicated, CSRF-protected
    // session step-up endpoint (never the enrollment endpoint). On success the session
    // is marked freshly MFA-verified, so we refresh the selected action DTO to flip the
    // gate to approvable and keep the operator on the SAME action — the selected action,
    // Review All filter, incident scope, and return URL are all preserved because
    // nothing navigates away.
    const code = stepUpCode.trim();
    if (!/^\d{6}$/.test(code)) {
      setStepUpError('Enter the 6-digit code from your authenticator app.');
      return;
    }
    setStepUpBusy(true);
    setStepUpError(null);
    try {
      await verifySessionStepUp(code);
      setStepUpOpen(false);
      setStepUpCode('');
      onMessage('Session verified. You can now approve this action.');
      // Re-fetch the canonical action DTO so the approval gate reflects the fresh
      // step-up and the Approve control becomes enabled.
      onDataChanged();
      router.refresh();
    } catch (err) {
      setStepUpError(err instanceof Error ? err.message : 'Session verification failed. Enter a fresh code and try again.');
    } finally {
      setStepUpBusy(false);
    }
  }

  // Contextual "Session verification required" panel rendered NEXT TO the approval
  // controls (not only at the bottom of the page): the backend-composed reason
  // (risk-classified, never title-string-matched), a disabled Approve control, and a
  // Verify Session control that opens an inline TOTP step-up form. Enrollment and
  // session reauthentication are SEPARATE — an already-enrolled operator verifies their
  // session here; only a not-yet-enrolled operator is routed to the enrollment flow.
  const mfaBlockedPanel = mfaBlocked ? (
    <div
      role="alert"
      style={{
        width: '100%',
        border: '1px solid rgba(245, 158, 11, 0.5)',
        background: 'rgba(245, 158, 11, 0.12)',
        borderRadius: '10px',
        padding: '0.75rem 0.85rem',
        marginBottom: '0.5rem',
      }}
    >
      <StatusPill label="Session verification required" variant="warning" />
      <p style={{ margin: '0.4rem 0 0.15rem', fontWeight: 700, color: 'var(--warning-fg)' }}>
        🔒 {mfaEnrolled ? 'Verify your session to approve' : 'Enable MFA to approve'}
      </p>
      <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
        {mfaEnrolled
          ? 'Enter your current authenticator code to approve this action.'
          : 'Enroll multi-factor authentication before approving this response action.'}
      </p>
      <p style={{ margin: '0.15rem 0 0', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
        {gate?.blockedReason ?? 'Complete a session verification before approving this response action.'}
      </p>
      {gate?.nextRequiredStep ? (
        <p style={{ margin: '0.15rem 0 0', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
          Next step: {gate.nextRequiredStep}
        </p>
      ) : null}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.6rem' }}>
        <button
          type="button"
          className="btn btn-primary"
          style={{ fontSize: '0.8rem' }}
          disabled
          aria-disabled="true"
          title="Verify your session to approve this action"
        >
          🔒 Approve
        </button>
        {mfaEnrolled ? (
          <button
            type="button"
            className="btn btn-primary"
            style={{ fontSize: '0.8rem' }}
            aria-expanded={stepUpOpen}
            onClick={() => { setStepUpError(null); setStepUpOpen(true); }}
          >
            Verify Session
          </button>
        ) : (
          <>
            <Link href={mfaHref} prefetch={false} className="btn btn-primary" style={{ fontSize: '0.8rem' }}>
              Enroll MFA
            </Link>
            <Link href={mfaHref} prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.8rem' }}>
              Open Security Settings
            </Link>
          </>
        )}
      </div>
    </div>
  ) : null;

  // The focused session step-up form (inline). Rendered whenever step-up is open AND
  // the operator is enrolled — driven either by the Verify Session button above or by
  // an approval command that came back needing a fresh step-up (assurance lapsed).
  const stepUpForm = stepUpOpen && mfaEnrolled ? (
    <form
      role="dialog"
      aria-label="Session verification"
      onSubmit={(event) => { event.preventDefault(); void handleVerifySession(); }}
      style={{
        width: '100%',
        border: '1px solid rgba(148, 163, 184, 0.35)',
        background: 'rgba(15, 23, 42, 0.04)',
        borderRadius: '10px',
        padding: '0.75rem 0.85rem',
        marginBottom: '0.5rem',
      }}
    >
      <p style={{ margin: '0 0 0.15rem', fontWeight: 700, fontSize: '0.85rem' }}>Session verification</p>
      <label htmlFor={`stepup-code-${action.id}`} className="label" style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, marginBottom: '0.25rem' }}>
        Current 6-digit authenticator code
      </label>
      <input
        id={`stepup-code-${action.id}`}
        name="stepup-code"
        value={stepUpCode}
        onChange={(event) => { setStepUpCode(event.target.value.replace(/\D/g, '').slice(0, 6)); setStepUpError(null); }}
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        placeholder="123456"
        aria-invalid={stepUpError ? true : undefined}
        aria-describedby={stepUpError ? `stepup-error-${action.id}` : undefined}
        disabled={stepUpBusy}
        style={{ width: '100%', maxWidth: '9rem', letterSpacing: '0.2em', fontSize: '1rem', padding: '0.4rem 0.5rem' }}
      />
      {stepUpError ? (
        <p id={`stepup-error-${action.id}`} role="alert" style={{ color: 'var(--danger-fg, #dc2626)', fontSize: '0.75rem', margin: '0.4rem 0 0' }}>
          {stepUpError}
        </p>
      ) : null}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.6rem' }}>
        <button type="submit" className="btn btn-primary" style={{ fontSize: '0.8rem' }} disabled={stepUpBusy || stepUpCode.length !== 6}>
          {stepUpBusy ? 'Verifying…' : 'Verify'}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          style={{ fontSize: '0.8rem' }}
          disabled={stepUpBusy}
          onClick={() => { setStepUpOpen(false); setStepUpError(null); setStepUpCode(''); }}
        >
          Cancel
        </button>
      </div>
    </form>
  ) : null;

  const approvalReadOnlyNotice = approvalReadOnlyReason ? (
    <p className="muted" style={{ fontSize: '0.75rem', margin: '0 0 0.5rem', width: '100%' }}>
      <strong style={{ color: 'var(--warning-fg)' }}>Approval required:</strong> {approvalReadOnlyReason}
    </p>
  ) : null;

  async function approveAction() {
    // ALWAYS the dedicated response-action approval command — never the AI
    // recommendation-review endpoint. A prior recommendation review therefore
    // cannot return "already reviewed" and cannot block this approval. The backend
    // returns the canonical result message (recorded / quorum reached / still
    // pending / already approved this version / not permitted / not approvable).
    onMessage('Submitting approval…');
    try {
      const res = await fetch(responseActionApprovalEndpoint(action.id, 'approve'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({}),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: unknown; message?: string; code?: unknown };
      // If the session's step-up assurance lapsed between loading this action and
      // clicking Approve, the command fails closed with a reauth/MFA requirement.
      // Surface the Verify Session form (for an enrolled operator) instead of a bare,
      // un-actionable backend error — no approval is retried automatically.
      if (!res.ok && mfaEnrolled && approvalErrorRequiresStepUp(res.status, data)) {
        setStepUpError(null);
        setStepUpOpen(true);
        onMessage('Verify your session to approve this action, then click Approve again.');
        return;
      }
      onMessage(approvalResultMessage(res.ok, data, 'Approval was blocked by the backend.'));
      // Re-fetch the canonical actions + summary so Pending Approval, the banner,
      // the row state, and the approval progress reflect the persisted quorum.
      if (res.ok) onDataChanged();
      router.refresh();
    } catch {
      onMessage('Approval request failed. Check network connection.');
    }
  }

  async function rejectAction() {
    // Reject requires a reason (enforced by the backend too — the prompt is a
    // convenience, not the authority). ALWAYS the response-action approval domain,
    // never the recommendation-review endpoint.
    const reason = typeof window !== 'undefined' ? window.prompt('Reason for rejecting this action (required):')?.trim() : '';
    if (!reason) {
      onMessage('Rejection cancelled — a reason is required.');
      return;
    }
    onMessage('Submitting rejection…');
    try {
      const res = await fetch(responseActionApprovalEndpoint(action.id, 'reject'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ reason }),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: unknown; message?: string };
      onMessage(
        res.ok
          ? (typeof data.message === 'string' && data.message ? data.message : 'Rejection recorded.')
          : _extractErrorMessage(data.detail, 'Rejection was blocked by the backend.'),
      );
      if (res.ok) onDataChanged();
      router.refresh();
    } catch {
      onMessage('Rejection request failed. Check network connection.');
    }
  }

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
        // Re-fetch canonical actions + summary to reflect the persisted status.
        onDataChanged();
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
        onDataChanged();
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
          {/* Recommendation REVIEW and Action APPROVAL are two DIFFERENT gates, shown
              as explicitly labelled rows so a green "Accepted" review is never read
              as an approved response action. The Review is the operator's judgement
              of the AI suggestion; the Approval is the governed quorum toward
              execution; Execution is a third, separate state (never executed here). */}
          <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Recommendation Review</p>
          {action.decision === 'accepted' ? (
            <StatusPill label="Accepted" variant="success" />
          ) : action.decision === 'rejected' ? (
            <StatusPill label="Rejected" variant="neutral" />
          ) : (
            <StatusPill label="Pending review" variant="warning" />
          )}
          <p className="tableMeta" style={{ marginTop: '0.4rem', marginBottom: '0.1rem' }}>Action Approval</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {action.approvalStatus === 'approved'
              ? 'Approved'
              : action.approvalStatus === 'rejected'
                ? 'Rejected'
                : action.approvalStatus === 'pending'
                  ? 'Awaiting Approval'
                  : 'Not required'}
          </p>
          {approvalDenominator > 0 ? (
            <>
              <p className="tableMeta" style={{ marginTop: '0.4rem', marginBottom: '0.1rem' }}>Approval Progress</p>
              <p style={{ fontSize: '0.8rem', margin: 0 }}>
                {approvalNumerator} of {approvalDenominator}
                <span className="muted"> approved</span>
              </p>
            </>
          ) : null}
          <p className="tableMeta" style={{ marginTop: '0.4rem', marginBottom: '0.1rem' }}>Execution</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>Not Executed</p>
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
              {/* Approval quorum progress (0 of 1, 1 of 2, …) from canonical counts. */}
              {approvalDenominator > 0 ? (
                <p className="muted" style={{ fontSize: '0.68rem', margin: '0.1rem 0 0' }}>
                  {approvalNumerator} of {approvalDenominator} approved
                </p>
              ) : null}
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
        // An AI-recommendation-backed action is APPROVED here via the response-action
        // approval domain (never simulated/executed on Screen 8). Approve/Reject
        // route to the response-action approval command; the recommendation REVIEW
        // (accepted/rejected) is shown separately in the Decision line and is decided
        // in the incident investigation. When the caller is not authorized we show a
        // truthful read-only reason instead of silently hiding the controls.
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {/* Session step-up missing: contextual panel + disabled Approve + Verify
              Session control, shown NEXT TO the controls (not only at the bottom). */}
          {mfaBlockedPanel}
          {/* The inline TOTP step-up form (opened from Verify Session or an assurance lapse). */}
          {stepUpForm}
          {/* Other blocked cases (wrong role / already decided): truthful read-only. */}
          {approvalReadOnlyNotice}
          {showApproval ? (
            <>
              <button type="button" className="btn btn-primary" style={{ fontSize: '0.8rem' }} onClick={() => void approveAction()}>
                Approve
              </button>
              <button type="button" className="btn btn-secondary" style={{ fontSize: '0.8rem' }} onClick={() => void rejectAction()}>
                Reject
              </button>
            </>
          ) : null}
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
          {/* Session step-up missing: contextual panel + disabled Approve + Verify
              Session control, shown NEXT TO the controls (not only at the bottom). */}
          {mfaBlockedPanel}
          {/* The inline TOTP step-up form (opened from Verify Session or an assurance lapse). */}
          {stepUpForm}
          {/* Read-only approval explanation for the OTHER blocked cases (wrong role /
              already decided): say WHY rather than hiding the controls with no reason. */}
          {approvalReadOnlyNotice}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {/* Approve / Reject appear when the backend says approval is the next
                step AND the full approval gate (role + MFA + quorum) is satisfied.
                Reject requires a reason. Both call real, permission-enforced commands. */}
            {showApproval ? (
              <>
                <button type="button" className="btn btn-primary" style={{ fontSize: '0.8rem' }} onClick={() => void approveAction()}>
                  Approve
                </button>
                <button type="button" className="btn btn-secondary" style={{ fontSize: '0.8rem' }} onClick={() => void rejectAction()}>
                  Reject
                </button>
              </>
            ) : null}
            {canExecute ? (
              <button
                type="button"
                className="btn btn-primary"
                style={{ fontSize: '0.8rem' }}
                onClick={() => void executeAction()}
              >
                Execute Action
              </button>
            ) : canSimulate && !showApproval ? (
              <button type="button" className="btn btn-primary" style={{ fontSize: '0.8rem' }} onClick={() => void simulateAction()}>
                {action.simulationStatus === 'passed' ? 'Retry Simulation' : 'Simulate Action'}
              </button>
            ) : showApproval || mfaBlocked ? (
              // Approve control already rendered (enabled above, or disabled in the
              // MFA-blocked panel) — don't duplicate another disabled pill.
              null
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
  summary,
  incidentIdFilter,
  selectedAction,
  liveExecutionAllowed,
  apiUrl: _apiUrl,
  authHeaders,
  onMessage,
  onDataChanged,
}: {
  rows: ActionRow[];
  summary: ActionsSummary | null;
  incidentIdFilter: string;
  selectedAction: ActionRow | null;
  liveExecutionAllowed: boolean;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  onMessage: (msg: string) => void;
  onDataChanged: () => void;
}) {
  const router = useRouter();
  const [simulating, setSimulating] = useState(false);
  const [checks, setChecks] = useState<SafetyChecksPayload | null>(null);
  const [checksState, setChecksState] = useState<'idle' | 'loading' | 'ready' | 'error' | 'not_applicable'>('idle');

  // Agent summary — the SAME canonical backend summary the top cards use, so the
  // panel and the cards can never disagree. Row-derived values are the fallback.
  const recommended = summary?.recommended ?? rows.length;
  // Reconciled against the visible rows so the panel can never disagree with the
  // cards (or under-report while the table shows actions awaiting approval).
  const awaitingApproval = reconcileCount(
    summary?.pendingApproval,
    rows.filter((r) => r.approvalStatus === 'pending').length,
  );
  // Canonical simulation-eligibility breakdown (ONE backend predicate). The eligible
  // count, already-simulated count, and blocked reasons all come from the summary so
  // the panel truthfully explains WHY nothing is eligible. Row-derived counts remain
  // the fallback for an older backend that does not yet return the breakdown.
  const simBreakdown = summary?.simulation ?? null;
  const readyForDryRun = simBreakdown?.eligible ?? rows.filter(isSimulateEligible).length;
  const alreadySimulated =
    simBreakdown?.alreadySimulated ?? rows.filter((r) => r.simulationStatus === 'passed').length;
  const simBlockedTotal =
    simBreakdown?.blockedTotal ??
    rows.filter((r) => r.recordType === 'ai_recommendation_review').length;
  const simBlockedReasons = simBreakdown?.blockedReasons ?? [];
  const simulated = summary?.simulated ?? rows.filter((r) => r.simulationStatus === 'passed').length;
  const readyForExecution = summary?.readyForExecution ?? rows.filter((r) => r.lifecycleState === 'ready_to_execute').length;
  const blocked =
    summary?.blocked ??
    rows.filter((r) => r.lifecycleState === 'execution_failed' || r.lifecycleState === 'blocked').length;
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
        onDataChanged();
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
        {/* Canonical eligibility breakdown — never a bare "no eligible actions".
            Every action is accounted for: eligible vs already-simulated vs blocked. */}
        <p style={{ fontSize: '0.8rem', margin: 0 }}>
          {readyForDryRun} eligible · {alreadySimulated} already simulated
          {simBlockedTotal > 0 ? ` · ${simBlockedTotal} blocked` : ''}
        </p>
        {readyForDryRun === 0 && simBlockedReasons.length > 0 ? (
          <ul style={{ margin: '0.3rem 0 0', paddingLeft: '1rem', listStyle: 'disc' }}>
            {simBlockedReasons.map((r) => (
              <li key={r.reasonCode} className="muted" style={{ fontSize: '0.72rem', lineHeight: 1.35 }}>
                {r.count} {r.count === 1 ? 'action' : 'actions'}: {r.label}
              </li>
            ))}
          </ul>
        ) : null}
        <p className="muted" style={{ fontSize: '0.72rem', margin: '0.15rem 0 0' }}>
          {liveExecutionAllowed
            ? 'Live execution path detected for eligible actions.'
            : 'Execution is dry-run only until a live provider is configured.'}
        </p>
      </div>

      {/* Truthful eligibility label: only actions without a currently-valid
          simulation (and not terminal/executing) are eligible, so already-simulated
          actions are never re-run. When nothing is eligible the button is disabled
          and the breakdown above states exactly why. */}
      <button
        type="button"
        className="btn btn-primary"
        style={{ fontSize: '0.82rem', width: '100%', marginBottom: '0.85rem' }}
        disabled={simulating || readyForDryRun === 0}
        title={
          readyForDryRun === 0
            ? alreadySimulated > 0 && simBlockedTotal === 0
              ? 'All eligible actions already have a valid simulation'
              : simBlockedTotal > 0
                ? `${alreadySimulated} already simulated · ${simBlockedTotal} blocked`
                : 'No eligible actions to simulate'
            : `Dry-run simulate the ${readyForDryRun} eligible action${readyForDryRun === 1 ? '' : 's'}`
        }
        onClick={() => void simulateAll()}
      >
        {simulating
          ? 'Simulating…'
          : readyForDryRun === 0
            ? 'No Eligible Actions to Simulate'
            : `Simulate ${readyForDryRun} Eligible Action${readyForDryRun === 1 ? '' : 's'}`}
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
