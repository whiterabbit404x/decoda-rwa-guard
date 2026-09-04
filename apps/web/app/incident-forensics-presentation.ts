/**
 * Incident forensic case record (Screen 7) — pure presentation helpers + types.
 *
 * The single source of truth for how the four evidence domains, the artifact
 * integrity states, the forensic snapshot lifecycle, and the deterministic policy
 * verdict are labelled and coloured. Kept free of React/DOM so it is unit-testable
 * and so the drawer and the standalone detail route can never disagree.
 *
 * Truthfulness rules honoured here:
 *   * Nothing is derived that the backend did not send. Counts, hashes, integrity
 *     states and decisions are read straight off the API payload; this module only
 *     LABELS them.
 *   * `content_hashed` and `snapshot_sealed` are styled and worded distinctly — an
 *     ordinary database row is never presented as tamper-evident evidence.
 *   * A missing value is rendered as an explicit "not available" / "not collected"
 *     string, never as a zero, a dash that reads as "fine", or a fabricated default.
 */

import {
  evidenceSourceLabel,
  evidenceSourceVariant,
  type PillVariant,
} from './forensic-investigation-presentation';

/* ── Evidence domains ─────────────────────────────────────────────── */

export type IncidentEvidenceDomain = 'ON_CHAIN' | 'OPERATIONAL' | 'POLICY' | 'HUMAN_ACTION';

/** Canonical order — mirrors the backend `EVIDENCE_DOMAINS` tuple and the lifecycle
 *  itself: what the chain said, what the business systems said, what policy decided,
 *  what people did. */
export const EVIDENCE_DOMAINS: readonly IncidentEvidenceDomain[] = [
  'ON_CHAIN',
  'OPERATIONAL',
  'POLICY',
  'HUMAN_ACTION',
] as const;

export type IncidentEvidenceCounts = {
  on_chain?: number;
  operational?: number;
  policy?: number;
  human_actions?: number;
  total?: number;
};

/** Backend count key for a domain. The four cards read the SAME keys the backend
 *  emits, so a card can never invent a bucket the API did not produce. */
const DOMAIN_COUNT_KEYS: Record<IncidentEvidenceDomain, keyof IncidentEvidenceCounts> = {
  ON_CHAIN: 'on_chain',
  OPERATIONAL: 'operational',
  POLICY: 'policy',
  HUMAN_ACTION: 'human_actions',
};

export function domainLabel(domain: IncidentEvidenceDomain | string | null | undefined): string {
  switch (domain) {
    case 'ON_CHAIN': return 'On-Chain';
    case 'OPERATIONAL': return 'Operational';
    case 'POLICY': return 'Policy';
    case 'HUMAN_ACTION': return 'Human Actions';
    default: return 'Unclassified';
  }
}

/**
 * Semantic token for a domain accent, using the product's existing theme variables
 * (never a new hard-coded colour): on-chain = accent blue, operational = success
 * green, policy = warning amber, human action = the shared violet accent.
 */
export function domainAccentVar(domain: IncidentEvidenceDomain | string | null | undefined): string {
  switch (domain) {
    case 'ON_CHAIN': return 'var(--info-fg)';
    case 'OPERATIONAL': return 'var(--success-fg)';
    case 'POLICY': return 'var(--warning-fg)';
    case 'HUMAN_ACTION': return 'var(--violet-fg)';
    default: return 'var(--text-muted)';
  }
}

export function domainSurfaceVar(domain: IncidentEvidenceDomain | string | null | undefined): string {
  switch (domain) {
    case 'ON_CHAIN': return 'var(--info-bg)';
    case 'OPERATIONAL': return 'var(--success-bg)';
    case 'POLICY': return 'var(--warning-bg)';
    case 'HUMAN_ACTION': return 'var(--violet-bg)';
    default: return 'rgba(148,163,184,0.08)';
  }
}

export function domainBorderVar(domain: IncidentEvidenceDomain | string | null | undefined): string {
  switch (domain) {
    case 'ON_CHAIN': return 'var(--info-bdr)';
    case 'OPERATIONAL': return 'var(--success-bdr)';
    case 'POLICY': return 'var(--warning-bdr)';
    case 'HUMAN_ACTION': return 'var(--violet-bdr)';
    default: return 'var(--border)';
  }
}

/**
 * The count for one domain, or `null` when the backend sent no number for it.
 * `null` is NOT zero: "the API did not report this bucket" and "there are none"
 * are different facts, and the card renders them differently.
 */
export function domainCount(
  counts: IncidentEvidenceCounts | null | undefined,
  domain: IncidentEvidenceDomain,
): number | null {
  const value = counts?.[DOMAIN_COUNT_KEYS[domain]];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/* ── Artifacts ────────────────────────────────────────────────────── */

export type ArtifactIntegrityStatus = 'snapshot_sealed' | 'content_hashed' | 'unverified' | string;

export type IncidentEvidenceArtifact = {
  id: string;
  incident_id?: string | null;
  event_id?: string | null;
  domain?: IncidentEvidenceDomain | string | null;
  artifact_type?: string | null;
  file_name?: string | null;
  source?: string | null;
  collected_at?: string | null;
  content_sha256?: string | null;
  integrity_status?: ArtifactIntegrityStatus | null;
  integrity_label?: string | null;
  immutable?: boolean | null;
  metadata?: Record<string, unknown> | null;
};

/**
 * How an artifact reached this incident, when the link is WEAKER than the event
 * itself. Returns null for a record that names this incident's canonical event or
 * the incident directly — those need no caveat.
 *
 * An asset-scoped record concerns the same asset but was never linked to this
 * event, and a policy verdict reached for a sibling on the same asset is not this
 * incident's verdict. Both would be misleading rendered like event evidence, so
 * the weaker link is stated on the row.
 */
export function linkScopeCaveat(artifact: IncidentEvidenceArtifact): string | null {
  const metadata = artifact.metadata ?? {};
  const scope = typeof metadata.link_scope === 'string' ? metadata.link_scope : null;
  const provenance = typeof metadata.match_provenance === 'string' ? metadata.match_provenance : null;
  if (scope === 'ASSET') return 'Asset scope';
  if (provenance === 'ASSET_SHARED') return 'Asset scope';
  if (provenance === 'UNATTRIBUTED') return 'Unattributed';
  return null;
}

/**
 * The artifact's evidence PROVENANCE — live provider, simulator, or none recorded.
 * Returns null only when the collector recorded no provenance at all for this kind
 * of record (a policy verdict or a human decision has no chain provenance).
 *
 * Reuses the product's existing provenance vocabulary, so simulator data is
 * labelled the same way here as everywhere else and can never be presented as
 * live customer evidence.
 */
export function artifactEvidenceSource(
  artifact: IncidentEvidenceArtifact,
): { label: string; variant: PillVariant } | null {
  const raw = artifact.metadata?.evidence_source;
  if (typeof raw !== 'string' || !raw.trim()) return null;
  return { label: evidenceSourceLabel(raw), variant: evidenceSourceVariant(raw) };
}

/** Whether this artifact is simulator/replay data rather than live provider evidence. */
export function isSimulatedArtifact(artifact: IncidentEvidenceArtifact): boolean {
  return artifactEvidenceSource(artifact)?.label === 'simulator';
}

export function integrityLabel(status: ArtifactIntegrityStatus | null | undefined): string {
  switch (status) {
    case 'snapshot_sealed': return 'Sealed in snapshot';
    case 'content_hashed': return 'Content hashed';
    case 'unverified': return 'Unverified';
    default: return 'Unverified';
  }
}

/**
 * Distinct variants per integrity state. A content-hashed live row must never look
 * like a snapshot-sealed one: the first is "this is what the row says now", the
 * second is "this copy is tamper-evident".
 */
export function integrityVariant(status: ArtifactIntegrityStatus | null | undefined): PillVariant {
  switch (status) {
    case 'snapshot_sealed': return 'success';
    case 'content_hashed': return 'info';
    default: return 'neutral';
  }
}

/** Only a snapshot-sealed artifact may carry the immutability mark, and only when
 *  the backend actually asserted it. A truthy `immutable` on any other state is
 *  refused here rather than rendered. */
export function showsImmutableMark(artifact: IncidentEvidenceArtifact): boolean {
  return artifact.immutable === true && artifact.integrity_status === 'snapshot_sealed';
}

/** Human artifact-type label — replaces a raw snake_case key. Unknown types are
 *  title-cased rather than dropped, so a new backend type is still readable. */
export function artifactTypeLabel(artifactType: string | null | undefined): string {
  const raw = (artifactType ?? '').trim();
  if (!raw) return 'Artifact';
  return raw
    .split(/[_.\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

/** Short display for a `sha256:<hex>` digest. Returns null when there is no real
 *  digest — the table then says "Not hashed" instead of showing invented hex. */
export function shortDigest(digest: string | null | undefined): string | null {
  const raw = (digest ?? '').trim();
  if (!raw) return null;
  const hex = raw.startsWith('sha256:') ? raw.slice('sha256:'.length) : raw;
  if (hex.length <= 12) return hex || null;
  return `${hex.slice(0, 6)}…${hex.slice(-4)}`;
}

export type DomainFilter = IncidentEvidenceDomain | 'ALL';

/** Filter the directory by domain. 'ALL' returns every artifact, including any the
 *  backend could not classify — a real record is never hidden by a filter default. */
export function filterArtifacts(
  artifacts: readonly IncidentEvidenceArtifact[],
  filter: DomainFilter,
): IncidentEvidenceArtifact[] {
  if (filter === 'ALL') return [...artifacts];
  return artifacts.filter((artifact) => artifact.domain === filter);
}

/* ── Forensic snapshot lifecycle ──────────────────────────────────── */

export type ForensicSnapshotStatus = 'collecting' | 'ready' | 'sealed' | 'failed' | string;

export type IncidentForensicSnapshot = {
  status?: ForensicSnapshotStatus | null;
  snapshot_id?: string | null;
  snapshot_hash?: string | null;
  hash_verified?: boolean | null;
  schema_version?: string | null;
  evidence_count?: number | null;
  is_complete?: boolean | null;
  created_at?: string | null;
};

export function snapshotStatusLabel(status: ForensicSnapshotStatus | null | undefined): string {
  switch (status) {
    case 'collecting': return 'Evidence collecting';
    case 'ready': return 'Evidence snapshot ready';
    case 'sealed': return 'Evidence package sealed';
    case 'failed': return 'Snapshot integrity failed';
    default: return 'Snapshot state unknown';
  }
}

export function snapshotStatusVariant(status: ForensicSnapshotStatus | null | undefined): PillVariant {
  switch (status) {
    case 'sealed': return 'success';
    case 'ready': return 'info';
    case 'collecting': return 'warning';
    case 'failed': return 'danger';
    default: return 'neutral';
  }
}

/* ── Screen 9 evidence package linkage ────────────────────────────── */

export type IncidentEvidencePackage = {
  available?: boolean;
  reason?: string | null;
  package_id?: string | null;
  package_number?: string | null;
  export_type?: string | null;
  status?: string | null;
  integrity_status?: string | null;
  integrity_label?: string | null;
  sealed_at?: string | null;
  created_at?: string | null;
  route?: string | null;
};

/** Whether a "View Evidence Package" link may be rendered. Requires a package the
 *  backend confirmed AND a route to open — never a link built from an id we guessed. */
export function hasEvidencePackage(pkg: IncidentEvidencePackage | null | undefined): boolean {
  return Boolean(pkg?.available && pkg?.package_id);
}

/** The truthful absent-state sentence when no package exists. */
export function evidencePackageAbsenceLabel(pkg: IncidentEvidencePackage | null | undefined): string {
  if (pkg?.reason === 'unavailable') {
    return 'Evidence package storage is unavailable for this deployment.';
  }
  return 'Evidence package not generated.';
}

/* ── Policy forensics ─────────────────────────────────────────────── */

export type IncidentPolicyEvaluation = {
  evaluation_id: string;
  policy_id?: string | null;
  policy_key?: string | null;
  policy_version?: number | string | null;
  decision?: string | null;
  reason_codes?: string[];
  required_approvals?: string[];
  operation?: string | null;
  amount_usd?: string | number | null;
  simulation?: boolean;
  engine_version?: string | null;
  evaluated_at?: string | null;
  canonical_event_id?: string | null;
  authority?: string | null;
};

export function policyDecisionVariant(decision: string | null | undefined): PillVariant {
  const value = (decision ?? '').toUpperCase();
  if (value === 'DENY') return 'danger';
  if (value === 'ALLOW') return 'success';
  return 'neutral';
}

/**
 * The evaluations that actually gated this incident's response, newest first.
 * Screen 11 simulations are excluded: a what-if predicts, it never authorizes, and
 * presenting one as the verdict would misstate why a response was gated.
 */
export function enforcementEvaluations(
  evaluations: readonly IncidentPolicyEvaluation[] | null | undefined,
): IncidentPolicyEvaluation[] {
  return (evaluations ?? []).filter((evaluation) => evaluation.simulation !== true);
}

/* ── Forensic timeline ────────────────────────────────────────────── */

export type IncidentTimelineActorType = 'system' | 'user' | 'ai' | string;

export type IncidentTimelineEvent = {
  id: string;
  incident_id?: string | null;
  event_id?: string | null;
  occurred_at?: string | null;
  event_type?: string | null;
  stage?: string | null;
  stage_label?: string | null;
  domain?: IncidentEvidenceDomain | string | null;
  source?: string | null;
  title?: string | null;
  description?: string | null;
  actor_type?: IncidentTimelineActorType | null;
  actor_id?: string | null;
  related_entity_type?: string | null;
  related_entity_id?: string | null;
  metadata?: Record<string, unknown> | null;
};

/**
 * Lifecycle order: canonical server `occurred_at` ascending, id as a stable
 * tiebreaker. Array position from the API is never trusted as the order, and an
 * event without a timestamp sorts last rather than being dropped or given one.
 */
export function sortTimelineEvents(
  events: readonly IncidentTimelineEvent[],
): IncidentTimelineEvent[] {
  return [...events].sort((left, right) => {
    const a = (left.occurred_at ?? '').trim();
    const b = (right.occurred_at ?? '').trim();
    if (!a && !b) return left.id.localeCompare(right.id);
    if (!a) return 1;
    if (!b) return -1;
    const at = Date.parse(a);
    const bt = Date.parse(b);
    if (Number.isFinite(at) && Number.isFinite(bt) && at !== bt) return at - bt;
    if (a !== b) return a < b ? -1 : 1;
    return left.id.localeCompare(right.id);
  });
}

/**
 * Wall-clock time at the precision the record carries, as `HH:MM:SS.mmm`.
 * Millisecond precision is shown only when the source timestamp actually has a
 * fractional-second component — a `.000` is never appended to a second-precision
 * record to make it look more forensic than it is.
 */
export function formatForensicTime(value: string | null | undefined): string {
  const raw = (value ?? '').trim();
  if (!raw) return 'Unknown';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return 'Unknown';
  const hh = String(parsed.getHours()).padStart(2, '0');
  const mm = String(parsed.getMinutes()).padStart(2, '0');
  const ss = String(parsed.getSeconds()).padStart(2, '0');
  const base = `${hh}:${mm}:${ss}`;
  return /\.\d+/.test(raw) ? `${base}.${String(parsed.getMilliseconds()).padStart(3, '0')}` : base;
}

/** Calendar day for a record, or null when there is no parseable timestamp. */
export function forensicDay(value: string | null | undefined): string | null {
  const raw = (value ?? '').trim();
  if (!raw) return null;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleDateString();
}

/**
 * Day headings for a chronological event list: the day is emitted for the first
 * event and again whenever the day CHANGES, so a lifecycle spanning midnight can
 * never read as one burst of times. Events with no timestamp get no heading.
 * Returns one entry per input event, aligned by index.
 */
export function timelineDayHeadings(
  events: readonly IncidentTimelineEvent[],
): (string | null)[] {
  let previous: string | null = null;
  return events.map((event) => {
    const day = forensicDay(event.occurred_at);
    if (day === null || day === previous) return null;
    previous = day;
    return day;
  });
}

export function formatForensicDate(value: string | null | undefined): string {
  const raw = (value ?? '').trim();
  if (!raw) return 'Unknown';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return 'Unknown';
  return parsed.toLocaleString();
}

/** Actor attribution. An AI layer is labelled recommend-only and is never shown as
 *  the party that took an action. */
export function actorLabel(event: IncidentTimelineEvent): string {
  switch (event.actor_type) {
    case 'user': return event.actor_id ? `Operator ${event.actor_id.slice(0, 8)}` : 'Operator';
    case 'ai': return 'AI (recommend only)';
    default: return event.source?.trim() || 'System';
  }
}

/* ── Load lifecycle (shared by the evidence + timeline views) ─────── */

export type ForensicLoadState = 'idle' | 'loading' | 'ready' | 'empty' | 'unauthorized' | 'not_found' | 'error';

/**
 * The load state for a fetched forensic view. `empty` is a LOADED state with no
 * records — it renders the truthful "nothing collected" sentence, never a spinner
 * and never sample content.
 */
export function loadStateFor(
  status: number | null,
  hasRecords: boolean,
): ForensicLoadState {
  if (status === null) return 'error';
  if (status === 401 || status === 403) return 'unauthorized';
  if (status === 404) return 'not_found';
  if (status < 200 || status >= 300) return 'error';
  return hasRecords ? 'ready' : 'empty';
}

/** Truthful empty-state copy, scoped to the selected domain filter. */
export function emptyEvidenceMessage(filter: DomainFilter): string {
  if (filter === 'ALL') return 'No evidence has been collected for this incident.';
  return `No ${domainLabel(filter).toLowerCase()} evidence has been collected for this incident.`;
}

/* ── Case summary (Overview + Case File snapshot) ─────────────────── */

/**
 * The deterministic states a case-summary section reports. `not_recorded` is a
 * real answer, not a gap to paper over: a section with no record says so.
 * Mirrors the backend `STATE_*` constants — this module labels them, never
 * re-derives them.
 */
export type CaseSectionState =
  | 'not_recorded' | 'observed' | 'anomaly' | 'indeterminate' | 'reconciled' | 'decided';

export type CaseAmountFact = {
  value?: string | null;
  decimals?: number | string | null;
  unit?: string | null;
};

export type IncidentCaseSummary = {
  event_id?: string | null;
  correlation?: {
    event_id?: string | null;
    incident_id?: string | null;
    alert_id?: string | null;
    detection_id?: string | null;
    asset_id?: string | null;
  };
  detection?: {
    detection_id?: string | null;
    category?: string | null;
    detection_type?: string | null;
    title?: string | null;
    severity?: string | null;
    reason_code?: string | null;
    detected_at?: string | null;
  };
  on_chain?: {
    state?: CaseSectionState | string;
    operation?: string | null;
    observed_amount?: CaseAmountFact | null;
    tx_hash?: string | null;
    block_number?: string | null;
    observed_at?: string | null;
    preconfirmation_at?: string | null;
    source?: string | null;
    artifact_count?: number;
  };
  operational?: {
    state?: CaseSectionState | string;
    reason_code?: string | null;
    reconciliation_status?: string | null;
    expected_amount?: CaseAmountFact | null;
    variance_amount?: CaseAmountFact | null;
    authoritative_source?: string | null;
    evaluated_at?: string | null;
    artifact_count?: number;
  };
  policy?: {
    state?: CaseSectionState | string;
    decision?: string | null;
    policy_key?: string | null;
    policy_version?: number | string | null;
    evaluation_id?: string | null;
    evaluated_at?: string | null;
    reason_codes?: string[];
    required_approvals?: string[];
    authority?: string | null;
    evaluation_count?: number;
    artifact_count?: number;
  };
  evidence?: {
    artifact_count?: number;
    counts?: IncidentEvidenceCounts;
    snapshot_status?: string | null;
    snapshot_hash_verified?: boolean | null;
    package_id?: string | null;
    package_number?: string | null;
    package_integrity?: string | null;
    package_route?: string | null;
  };
};

const CASE_STATE_LABELS: Record<CaseSectionState, string> = {
  not_recorded: 'Not recorded',
  observed: 'Observed',
  anomaly: 'Mismatch',
  indeterminate: 'Could not be established',
  reconciled: 'Reconciled',
  decided: 'Decision recorded',
};

/**
 * Operator wording for a case-section state. An unknown state from a newer
 * backend is reported as unknown rather than guessed into a reassuring label.
 */
export function caseStateLabel(state: CaseSectionState | string | null | undefined): string {
  const key = (state ?? '') as CaseSectionState;
  return CASE_STATE_LABELS[key] ?? 'Unknown';
}

/**
 * Colour for a case-section state. `not_recorded` and `indeterminate` are
 * deliberately NEUTRAL, never success: absent or unestablished truth must not
 * read as "clean" (CLAUDE.md — no data must not be shown as safe).
 */
export function caseStateVariant(state: CaseSectionState | string | null | undefined): PillVariant {
  const key = (state ?? '').toString();
  if (key === 'anomaly') return 'danger';
  if (key === 'observed') return 'info';
  if (key === 'reconciled') return 'success';
  if (key === 'decided') return 'info';
  return 'neutral';
}

/** True when a section has a real record behind it. */
export function caseSectionRecorded(state: CaseSectionState | string | null | undefined): boolean {
  return (state ?? 'not_recorded') !== 'not_recorded';
}

/**
 * An amount as the record stores it, with its unit. Exact ledger values are
 * passed through unscaled — a display-side conversion would be a second,
 * divergent reading of the same number. Returns null when nothing was recorded.
 */
export function formatCaseAmount(amount: CaseAmountFact | null | undefined): string | null {
  const raw = (amount?.value ?? '').toString().trim();
  if (!raw) return null;
  const unit = (amount?.unit ?? '').toString().trim();
  return unit ? `${raw} ${unit}` : raw;
}

/** Operator label for a snake_case / SCREAMING_SNAKE backend token. */
export function humanizeToken(token: string | null | undefined): string | null {
  const raw = (token ?? '').trim();
  if (!raw) return null;
  return raw
    .split(/[_.\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(' ');
}

/* ── Response state (Screen 8's facts, summarized for Screen 7) ───── */

/**
 * The response fields Screen 7 reads. These come from the SAME response-actions
 * endpoint Screen 8 renders, so the two screens can never describe one action's
 * approval or execution state differently. Screen 7 only reports it.
 */
export type CaseResponseAction = {
  id?: string;
  display_title?: string;
  lifecycle_state?: string;
  lifecycle_label?: string;
  approval_status?: string;
  execution_status?: string;
};

export type CaseResponseState = {
  /** 'none' = no action recommended yet — never rendered as "nothing to do". */
  state: 'none' | 'awaiting_approval' | 'approved' | 'executed' | 'failed' | 'recommended';
  label: string;
  total: number;
  awaitingApproval: number;
  approved: number;
  executed: number;
  failed: number;
};

/**
 * Fold this incident's response actions into one truthful sentence for the Case
 * File and Overview. Ordered by what an operator must act on first: a failure
 * outranks a pending approval, which outranks an execution that already
 * happened. Screen 7 states this; Screen 8 remains the only place it can change.
 */
export function summarizeResponseState(
  actions: readonly CaseResponseAction[] | null | undefined,
): CaseResponseState {
  const rows = actions ?? [];
  const awaitingApproval = rows.filter((a) => a.approval_status === 'pending').length;
  const approved = rows.filter((a) => a.approval_status === 'approved').length;
  const executed = rows.filter((a) => a.execution_status === 'executed').length;
  const failed = rows.filter(
    (a) => a.execution_status === 'failed' || a.lifecycle_state === 'execution_failed',
  ).length;
  const base = { total: rows.length, awaitingApproval, approved, executed, failed };
  if (rows.length === 0) {
    return { ...base, state: 'none', label: 'No response action recommended yet' };
  }
  if (failed > 0) return { ...base, state: 'failed', label: `${failed} execution failed` };
  if (awaitingApproval > 0) {
    return { ...base, state: 'awaiting_approval', label: `Awaiting approval (${awaitingApproval})` };
  }
  if (executed > 0) return { ...base, state: 'executed', label: `${executed} executed` };
  if (approved > 0) return { ...base, state: 'approved', label: `${approved} approved, not executed` };
  return { ...base, state: 'recommended', label: `${rows.length} recommended` };
}

export function responseStateVariant(state: CaseResponseState['state']): PillVariant {
  if (state === 'failed') return 'danger';
  if (state === 'awaiting_approval') return 'warning';
  if (state === 'executed') return 'success';
  if (state === 'approved' || state === 'recommended') return 'info';
  return 'neutral';
}

/* ── Incident queue counters (Screen 7 KPI row) ───────────────────── */

/**
 * The four canonical queue counters, as the backend computes them over the WHOLE
 * workspace. The browser does not re-derive them from the page of rows it is
 * holding: that page is capped and already narrowed by the list filters, so
 * counting it would report a subset as the total — and would disagree with the
 * Dashboard's Open Incidents card, which uses this same lifecycle definition.
 */
export type IncidentQueueCounts = {
  open_incidents: number;
  critical_incidents: number;
  in_investigation: number;
  awaiting_response: number;
  total: number;
};

function counterValue(source: Record<string, unknown>, key: string): number | null {
  const raw = source[key];
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string' && raw.trim() !== '' && Number.isFinite(Number(raw))) return Number(raw);
  return null;
}

/**
 * Read the counters off `GET /incidents/summary`. Returns null when the payload
 * does not carry all four — a KPI tile must show "unavailable", never a zero an
 * operator would read as "nothing is open" (CLAUDE.md: no data must not be shown
 * as safe).
 */
export function parseIncidentQueueCounts(payload: unknown): IncidentQueueCounts | null {
  const root = (payload ?? null) as Record<string, unknown> | null;
  if (!root || typeof root !== 'object') return null;
  const counts = (root.counts ?? root) as Record<string, unknown>;
  if (!counts || typeof counts !== 'object') return null;
  const open = counterValue(counts, 'open_incidents');
  const critical = counterValue(counts, 'critical_incidents');
  const investigating = counterValue(counts, 'in_investigation');
  const awaiting = counterValue(counts, 'awaiting_response');
  if (open === null || critical === null || investigating === null || awaiting === null) return null;
  return {
    open_incidents: open,
    critical_incidents: critical,
    in_investigation: investigating,
    awaiting_response: awaiting,
    total: counterValue(counts, 'total') ?? 0,
  };
}
