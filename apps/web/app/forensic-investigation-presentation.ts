/**
 * Digital Forensics Investigator (Screen 7) — pure presentation helpers.
 *
 * The single source of truth for how the deterministic forensic layer is labelled and
 * coloured in the UI. Kept free of React/DOM so it is unit-testable and so the panel,
 * the drawer's Workflow tab, and the report view all agree.
 *
 * Truthfulness: a finding's verification state is styled distinctly — a `verified_fact`
 * is never presented like an `unverified_hypothesis`, and an `ai_interpretation` is never
 * shown as a hard fact. Confidence/coverage come straight from the backend; nothing is
 * invented or randomised here.
 */

export type PillVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'default';

export type VerificationState =
  | 'verified_fact'
  | 'rule_match'
  | 'ai_interpretation'
  | 'unverified_hypothesis'
  | string;

export type WorkflowState =
  | 'pending'
  | 'queued'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'skipped'
  | 'degraded'
  | string;

export type Finding = {
  finding_id?: string;
  finding_type?: string;
  title?: string;
  description?: string;
  verification_state?: VerificationState;
  evidence_refs?: string[];
  value?: unknown;
};

export type RuleMatch = {
  framework?: string;
  framework_version?: string;
  rule_id?: string;
  rule_name?: string;
  match_rationale?: string;
  evidence_refs?: string[];
  match_state?: string;
  verification_state?: VerificationState;
};

export type WorkflowStage = { stage: string; label: string; state: WorkflowState };

export type ConfidenceFactor = { factor: string; effect: 'increase' | 'decrease' | 'neutral' | string; weight: number; detail?: string };

export type ForensicAnalysis = {
  calc_version?: string;
  status?: 'completed' | 'degraded' | 'failed' | string;
  degraded_reason?: string | null;
  findings?: Finding[];
  rule_matches?: RuleMatch[];
  confidence?: number;
  confidence_band?: 'high' | 'medium' | 'low' | string;
  confidence_factors?: ConfidenceFactor[];
  confidence_explanation?: string;
  evidence_coverage?: number;
  coverage_detail?: { coverage: number; present_count: number; expected_count: number; missing?: string[]; dimensions?: Array<{ key: string; label: string; present: boolean }> };
  workflow_stages?: WorkflowStage[];
  ruleset?: { name?: string; version?: string };
  counts?: { findings?: number; verified_facts?: number; rule_matches?: number; evidence_gaps?: number };
};

export type ForensicEvidenceRow = {
  kind?: string;
  type?: string;
  title?: string;
  source?: string | null;
  evidence_source?: string | null;
  tx_hash?: string | null;
  block_number?: number | string | null;
  chain_id?: number | string | null;
  observed_at?: string | null;
  reference?: string | null;
  corroboration?: 'corroborated' | 'partial' | string;
};

export type ForensicEvidence = { rows?: ForensicEvidenceRow[]; total?: number; truncated?: boolean };

export type ForensicInvestigation = {
  status?: 'completed' | 'degraded' | 'unavailable' | string;
  schema_ready?: boolean;
  message?: string;
  evidence?: ForensicEvidence;
  incident?: {
    incident_id?: string;
    reference?: string;
    title?: string;
    severity?: string | null;
    status?: string | null;
    summary?: string | null;
    target_id?: string | null;
    source_alert_id?: string | null;
    risk_score?: number | null;
    detected_at?: string | null;
    updated_at?: string | null;
    // Canonical Screen 7 case facts. Each is null when the backend could not
    // resolve it — the header then states that, rather than substituting an
    // identifier or a placeholder for a real asset name / detection category.
    asset_id?: string | null;
    asset_label?: string | null;
    detection_category?: string | null;
    detection_type?: string | null;
    detection_id?: string | null;
    event_id?: string | null;
  };
  snapshot_hash?: string;
  analysis?: ForensicAnalysis;
  ai_triage?: { status?: string; recommendation_count?: number; report_available?: boolean };
  linked?: { alerts?: number; evidence_records?: number; recommendations?: number };
  label?: string;
};

/* ── Verification state (verified fact / rule match / AI interpretation / hypothesis) ─ */
export function verificationStateLabel(state?: VerificationState): string {
  switch ((state ?? '').toLowerCase()) {
    case 'verified_fact': return 'Verified fact';
    case 'rule_match': return 'Rule match';
    case 'ai_interpretation': return 'AI interpretation';
    case 'unverified_hypothesis': return 'Hypothesis';
    default: return 'Unknown';
  }
}

// A hypothesis must never look like a verified fact: distinct variants per state.
export function verificationStateVariant(state?: VerificationState): PillVariant {
  switch ((state ?? '').toLowerCase()) {
    case 'verified_fact': return 'success';
    case 'rule_match': return 'info';
    case 'ai_interpretation': return 'warning';
    case 'unverified_hypothesis': return 'neutral';
    default: return 'neutral';
  }
}

/* ── Confidence band ──────────────────────────────────────────── */
export function confidenceBandLabel(band?: string): string {
  switch ((band ?? '').toLowerCase()) {
    case 'high': return 'High';
    case 'medium': return 'Medium';
    case 'low': return 'Low';
    default: return 'Unknown';
  }
}

export function confidenceBandVariant(band?: string): PillVariant {
  switch ((band ?? '').toLowerCase()) {
    case 'high': return 'success';
    case 'medium': return 'warning';
    case 'low': return 'danger';
    default: return 'neutral';
  }
}

export function confidencePercent(confidence?: number | null): number {
  const v = typeof confidence === 'number' && Number.isFinite(confidence) ? confidence : 0;
  return Math.round(Math.max(0, Math.min(1, v)) * 100);
}

export function coveragePercent(coverage?: number | null): number {
  const v = typeof coverage === 'number' && Number.isFinite(coverage) ? coverage : 0;
  return Math.round(Math.max(0, Math.min(1, v)) * 100);
}

/* ── Workflow stage state ─────────────────────────────────────── */
export function workflowStateLabel(state?: WorkflowState): string {
  switch ((state ?? '').toLowerCase()) {
    case 'pending': return 'Pending';
    case 'queued': return 'Queued';
    case 'in_progress': return 'In progress';
    case 'completed': return 'Completed';
    case 'failed': return 'Failed';
    case 'skipped': return 'Skipped';
    case 'degraded': return 'Degraded';
    default: return 'Pending';
  }
}

export function workflowStateVariant(state?: WorkflowState): PillVariant {
  switch ((state ?? '').toLowerCase()) {
    case 'completed': return 'success';
    case 'in_progress': return 'info';
    case 'queued': return 'info';
    case 'failed': return 'danger';
    case 'degraded': return 'warning';
    case 'skipped': return 'neutral';
    case 'pending': return 'neutral';
    default: return 'neutral';
  }
}

/* ── AI Investigation Summary state (Queued/Investigating/Completed/Degraded/Failed) ─ */
export type SummaryState = 'Queued' | 'Investigating' | 'Completed' | 'Degraded' | 'Failed';

export function investigationSummaryState(
  analysisStatus?: string,
  aiStatus?: string,
): SummaryState {
  const ai = (aiStatus ?? '').toLowerCase();
  if (ai === 'queued') return 'Queued';
  if (ai === 'running') return 'Investigating';
  if (ai === 'failed' || ai === 'validation_failed') return 'Failed';
  if ((analysisStatus ?? '').toLowerCase() === 'degraded') return 'Degraded';
  if (ai === 'completed' || ai === 'completed_with_warnings') return 'Completed';
  if ((analysisStatus ?? '').toLowerCase() === 'completed') return 'Completed';
  return 'Queued';
}

export function summaryStateVariant(state: SummaryState): PillVariant {
  switch (state) {
    case 'Completed': return 'success';
    case 'Investigating': return 'info';
    case 'Queued': return 'info';
    case 'Degraded': return 'warning';
    case 'Failed': return 'danger';
    default: return 'neutral';
  }
}

// The AI narrative is active (poll while these hold). Deterministic analysis is always
// available; only the AI hand-off has an in-flight state worth polling for.
export function isAiActive(aiStatus?: string): boolean {
  const s = (aiStatus ?? '').toLowerCase();
  return s === 'queued' || s === 'running';
}

/* ── Evidence reference formatting (never overflow the layout) ── */
export type RefKind = 'alert' | 'telemetry' | 'rule' | 'target' | 'asset' | 'policy' | 'runbook' | 'evidence' | 'unknown';

export function refKind(ref?: string): RefKind {
  const kind = (ref ?? '').split(':')[0]?.toLowerCase();
  const known: RefKind[] = ['alert', 'telemetry', 'rule', 'target', 'asset', 'policy', 'runbook', 'evidence'];
  return (known as string[]).includes(kind) ? (kind as RefKind) : 'unknown';
}

export function refKindLabel(ref?: string): string {
  switch (refKind(ref)) {
    case 'alert': return 'Alert';
    case 'telemetry': return 'Telemetry';
    case 'rule': return 'Rule';
    case 'target': return 'Target';
    case 'asset': return 'Asset';
    case 'policy': return 'Policy';
    case 'runbook': return 'Runbook';
    case 'evidence': return 'Evidence';
    default: return 'Reference';
  }
}

// Truncate a long identifier (tx hash / address / reference) in the middle so it never
// breaks the layout, while remaining copyable in full via the raw value.
export function truncateMiddle(value?: string | null, head = 10, tail = 8): string {
  const v = (value ?? '').trim();
  if (!v) return '—';
  if (v.length <= head + tail + 1) return v;
  return `${v.slice(0, head)}…${v.slice(-tail)}`;
}

/* ── Evidence corroboration state ─────────────────────────────── */
export function corroborationLabel(state?: string): string {
  switch ((state ?? '').toLowerCase()) {
    case 'corroborated': return 'Corroborated';
    case 'partial': return 'Partial';
    case 'uncorroborated': return 'Uncorroborated';
    default: return 'Unknown';
  }
}

export function corroborationVariant(state?: string): PillVariant {
  switch ((state ?? '').toLowerCase()) {
    case 'corroborated': return 'success';
    case 'partial': return 'warning';
    case 'uncorroborated': return 'danger';
    default: return 'neutral';
  }
}

// Simulator/replay evidence must never be labelled live_provider (CLAUDE.md truthfulness).
export function evidenceSourceLabel(source?: string | null): string {
  const raw = (source ?? '').toLowerCase();
  if (raw === 'simulator' || raw === 'demo' || raw === 'replay') return 'simulator';
  if (raw === 'live' || raw === 'live_provider') return 'live_provider';
  if (!raw) return 'none';
  return raw;
}

export function evidenceSourceVariant(source?: string | null): PillVariant {
  const label = evidenceSourceLabel(source);
  if (label === 'simulator') return 'info';
  if (label === 'live_provider') return 'success';
  return 'neutral';
}

// The incident's human reference or a stable short fallback (never a fabricated number).
export function incidentReference(reference?: string | null, incidentId?: string | null): string {
  const r = (reference ?? '').trim();
  if (r) return r;
  const id = (incidentId ?? '').trim();
  return id ? `INC-${id.slice(0, 8)}` : 'Incident';
}

/* ── Drawer ↔ full-page consistency selectors ──────────────────────────────
 * The /incidents Case File drawer and the canonical Screen 7 full incident page
 * MUST derive workflow state, the next action, and the linked detection from the
 * SAME persisted investigation payload (`/incidents/{id}/investigation`). These pure
 * selectors are the single source of truth so the two views can never disagree —
 * there is no second, browser-inferred definition of investigation state. */

// Deterministic findings are "available" when the analysis produced at least one
// finding that is not merely a verified *absence* of evidence (an evidence_gap).
export function investigationHasFindings(analysis?: ForensicAnalysis | null): boolean {
  if (!analysis) return false;
  const counts = analysis.counts;
  if (counts && typeof counts.findings === 'number') {
    const gaps = typeof counts.evidence_gaps === 'number' ? counts.evidence_gaps : 0;
    return counts.findings - gaps > 0;
  }
  return (analysis.findings ?? []).some((f) => (f.finding_type ?? '') !== 'evidence_gap');
}

/* ── Linked detection / originating rule reference ────────────────────────── */
export type DetectionReference = { reference: string; label: string; title?: string };

// The canonical originating detection/rule reference for an incident, taken from the
// immutable investigation snapshot: the 'rule' evidence row, else the deterministic
// 'detection_rule' finding's rule ref. Returns null only when no such reference
// exists — so the drawer never renders "none" while the full page shows an
// originating detection rule for the same incident.
export function linkedDetectionRef(investigation?: ForensicInvestigation | null): DetectionReference | null {
  if (!investigation) return null;
  const ruleRow = (investigation.evidence?.rows ?? []).find(
    (r) => (r.kind ?? '').toLowerCase() === 'rule' && !!r.reference,
  );
  if (ruleRow?.reference) {
    return { reference: ruleRow.reference, label: refKindLabel(ruleRow.reference), title: ruleRow.title ?? undefined };
  }
  const finding = (investigation.analysis?.findings ?? []).find(
    (f) => (f.finding_type ?? '') === 'detection_rule',
  );
  const ref = (finding?.evidence_refs ?? []).find((r) => refKind(r) === 'rule');
  if (ref) {
    return { reference: ref, label: refKindLabel(ref), title: finding?.title };
  }
  return null;
}

/* ── Canonical next action (drawer) ───────────────────────────────────────── */
export type NextActionKind =
  | 'start' | 'progress' | 'review-findings' | 'retry' | 'rerun' | 'review-response';

export type NextAction = {
  label: string;
  kind: NextActionKind;
  secondary?: { label: string; kind: 'rerun' };
};

const NEXT_ACTION_LABELS: Record<NextActionKind, string> = {
  start: 'Start Investigation',
  progress: 'View Investigation Progress',
  'review-findings': 'Review Findings',
  retry: 'Retry Investigation',
  rerun: 'Re-run Investigation',
  'review-response': 'Review Response Recommendation',
};

// Derive the drawer's next action purely from persisted investigation state (never
// from the incident's coarse workflow_status alone). `awaitingResponse` is the
// canonical approval-required signal, supplied by the caller from the incident's
// persisted awaiting-response status, so the drawer and the "Awaiting Response" KPI
// agree and Screen 8's approval boundary is respected: the drawer only routes a
// reviewer to the recommendation — it never approves anything itself.
export function investigationNextAction(
  investigation?: ForensicInvestigation | null,
  opts?: { awaitingResponse?: boolean },
): NextAction {
  const analysis = investigation?.analysis;
  // No forensic investigation exists yet (schema not ready / nothing built).
  if (!investigation || investigation.status === 'unavailable' || !analysis) {
    return { label: NEXT_ACTION_LABELS.start, kind: 'start' };
  }
  const state = investigationSummaryState(analysis.status, investigation.ai_triage?.status);
  // Investigation still in flight.
  if (state === 'Queued' || state === 'Investigating') {
    return { label: NEXT_ACTION_LABELS.progress, kind: 'progress' };
  }
  if (state === 'Failed') {
    return { label: NEXT_ACTION_LABELS.retry, kind: 'retry' };
  }
  // Terminal deterministic analysis (Completed or Degraded).
  // An approval-required response recommendation is the reviewer's next step.
  if (opts?.awaitingResponse) {
    return { label: NEXT_ACTION_LABELS['review-response'], kind: 'review-response' };
  }
  // Findings exist → review them; a manual re-run stays available as a secondary action.
  if (investigationHasFindings(analysis)) {
    return {
      label: NEXT_ACTION_LABELS['review-findings'],
      kind: 'review-findings',
      secondary: { label: NEXT_ACTION_LABELS.rerun, kind: 'rerun' },
    };
  }
  // Terminal but nothing derived yet — offer to (re)start the investigation.
  return { label: NEXT_ACTION_LABELS.start, kind: 'start' };
}

/* ── Canonical incident KPI predicates (list-page) ────────────────────────── */
// "In Investigation": the incident's persisted workflow/status is investigating —
// the project's canonical equivalent of a running investigation. Derived from the
// persisted incident row, never from transient drawer state.
export function isInInvestigationStatus(status?: string | null): boolean {
  return (status ?? '').toLowerCase() === 'investigating';
}

// "Awaiting Response": the incident is at the approval-required response stage.
// 'awaiting_response' is canonical; 'contained' is the legacy persisted value that
// maps to Awaiting Response in the status pill.
export function isAwaitingResponseStatus(status?: string | null): boolean {
  const s = (status ?? '').toLowerCase();
  return s === 'awaiting_response' || s === 'contained';
}
