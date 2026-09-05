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
  type WorkflowStage,
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

/**
 * Whether a section's record was RETRIEVED, kept strictly apart from what the
 * record then said. `not_collected` means nothing was compared — it is not a
 * mismatch, and no label in this module may render it as one.
 */
export type CaseCollectionState = 'not_collected' | 'collected' | 'error';

/** How a persisted decision was reached, as the backend resolved it. */
export type PolicyDecisionSource =
  | 'matched_policy'
  | 'fail_closed_default'
  | 'no_evaluation'
  /** A decision is recorded but nothing in it establishes where it came from. */
  | 'unattributed';

/** How the incident came to exist, from persisted linkage only. */
export type IncidentOrigin = 'detection' | 'alert' | 'manual' | 'system' | 'unknown';

/** Which persisted record the on-chain identifying facts were read from. */
export type OnChainFactSource = 'detection' | 'evidence_snapshot';

export type IncidentCaseSummary = {
  event_id?: string | null;
  correlation?: {
    event_id?: string | null;
    incident_id?: string | null;
    alert_id?: string | null;
    detection_id?: string | null;
    asset_id?: string | null;
  };
  origin?: {
    origin?: IncidentOrigin | string;
    detection_linked?: boolean;
    alert_linked?: boolean;
    source_event_type?: string | null;
  };
  detection?: {
    detection_id?: string | null;
    category?: string | null;
    detection_type?: string | null;
    title?: string | null;
    severity?: string | null;
    reason_code?: string | null;
    detected_at?: string | null;
    state?: CaseSectionState | string;
    collection_state?: CaseCollectionState | string;
  };
  on_chain?: {
    state?: CaseSectionState | string;
    collection_state?: CaseCollectionState | string;
    operation?: string | null;
    observed_amount?: CaseAmountFact | null;
    tx_hash?: string | null;
    block_number?: string | null;
    observed_at?: string | null;
    preconfirmation_at?: string | null;
    source?: string | null;
    fact_source?: OnChainFactSource | string | null;
    artifact_count?: number;
  };
  operational?: {
    state?: CaseSectionState | string;
    collection_state?: CaseCollectionState | string;
    reconciliation_scope?: 'EVENT' | 'ASSET' | string | null;
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
    collection_state?: CaseCollectionState | string;
    decision?: string | null;
    decision_source?: PolicyDecisionSource | string;
    policy_id?: string | null;
    policy_key?: string | null;
    policy_version?: number | string | null;
    engine_version?: string | null;
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
 * Whether any record was RETRIEVED for a section — the prior question to "what did
 * it say". Prefers the backend's explicit `collection_state`; falls back to the
 * verdict state for a payload from an older backend, which is the same rule the
 * backend applies. Never guesses "collected" from a neighbouring section.
 */
export function sectionCollected(section: {
  state?: CaseSectionState | string;
  collection_state?: CaseCollectionState | string;
} | null | undefined): boolean {
  const collection = section?.collection_state;
  if (collection === 'collected') return true;
  if (collection === 'not_collected' || collection === 'error') return false;
  return caseSectionRecorded(section?.state);
}

/**
 * The operational half in the five words that must never be confused.
 *
 * NOT COLLECTED — no operational data was retrieved.
 * NOT MATCHED   — operational data WAS retrieved and failed to match.
 * MATCHED       — operational data was retrieved and matched.
 * ERROR         — collection or reconciliation failed.
 * UNKNOWN       — the system cannot determine the state.
 *
 * This distinction is the whole point of the operational domain: "we never looked"
 * and "we looked and it did not add up" are opposite claims about a customer's
 * books, and one must never be rendered in the other's words.
 */
export type OperationalOutcome = 'not_collected' | 'not_matched' | 'matched' | 'indeterminate' | 'error' | 'unknown';

export function operationalOutcome(operational: IncidentCaseSummary['operational'] | null | undefined): OperationalOutcome {
  if (!sectionCollected(operational)) {
    return operational?.collection_state === 'error' ? 'error' : 'not_collected';
  }
  switch (operational?.state) {
    case 'anomaly': return 'not_matched';
    case 'reconciled': return 'matched';
    case 'indeterminate': return 'indeterminate';
    case 'observed': return 'matched';
    default: return 'unknown';
  }
}

const OPERATIONAL_OUTCOME_LABELS: Record<OperationalOutcome, string> = {
  not_collected: 'Not collected',
  not_matched: 'Not matched',
  matched: 'Matched',
  indeterminate: 'Could not be established',
  error: 'Collection error',
  unknown: 'Unknown',
};

export function operationalOutcomeLabel(outcome: OperationalOutcome): string {
  return OPERATIONAL_OUTCOME_LABELS[outcome];
}

/** The sentence that says exactly what happened, so no label has to carry it alone. */
const OPERATIONAL_OUTCOME_DETAIL: Record<OperationalOutcome, string> = {
  not_collected: 'No operational data was retrieved for this event. This is not a mismatch — nothing was compared.',
  not_matched: 'Operational data was retrieved and did not match the chain reading.',
  matched: 'Operational data was retrieved and matched the chain reading.',
  indeterminate: 'Operational data was retrieved but the reconciliation could not establish truth.',
  error: 'The operational collection or reconciliation failed. No verdict is claimed.',
  unknown: 'The operational state could not be determined from the recorded data.',
};

export function operationalOutcomeDetail(outcome: OperationalOutcome): string {
  return OPERATIONAL_OUTCOME_DETAIL[outcome];
}

/** `not_collected` and `error` are NEUTRAL, never danger: absence is not an accusation. */
export function operationalOutcomeVariant(outcome: OperationalOutcome): PillVariant {
  if (outcome === 'not_matched') return 'danger';
  if (outcome === 'matched') return 'success';
  return 'neutral';
}

/* ── Policy decision provenance ───────────────────────────────────── */

/**
 * WHERE a persisted decision came from. A DENY reached because no policy governed
 * the operation is the deterministic fail-closed rule doing its job — not a failed
 * lookup of a policy record, which is how a bare "Policy Not Found" beside a DENY
 * reads. An authoritative decision whose source is unexplained is exactly what this
 * screen must not show.
 */
export function policyDecisionSourceLabel(source: PolicyDecisionSource | string | null | undefined): string {
  switch (source) {
    case 'matched_policy': return 'Matched policy';
    case 'fail_closed_default': return 'Default fail-closed rule';
    case 'unattributed': return 'Decision source not recorded';
    default: return 'No evaluation recorded';
  }
}

/**
 * The decision source for a policy section, preferring the backend's own field.
 *
 * When a payload predates that field, the SAME rule the backend applies is used
 * as the fallback — a recorded decision that carries a policy identity was made
 * by that policy, and one that carries none was the fail-closed refusal. This is
 * not a second, frontend-only definition of the state: it is the identical rule,
 * kept here only so an older payload reports a truthful source instead of
 * reporting "no evaluation" beside a real DENY.
 */
export function resolvePolicyDecisionSource(
  policy: IncidentCaseSummary['policy'] | null | undefined,
): PolicyDecisionSource {
  const declared = policy?.decision_source;
  if (declared === 'matched_policy' || declared === 'fail_closed_default'
      || declared === 'no_evaluation' || declared === 'unattributed') {
    return declared;
  }
  if (!policy?.decision) return 'no_evaluation';
  if (policy.policy_key || policy.policy_id) return 'matched_policy';
  // The fail-closed branch only ever produces DENY, so no other decision may be
  // labelled with it — an unattributed ALLOW is reported as unattributed.
  return policy.decision.toUpperCase() === 'DENY' ? 'fail_closed_default' : 'unattributed';
}

export function policyDecisionSourceDetail(source: PolicyDecisionSource | string | null | undefined): string | null {
  switch (source) {
    case 'matched_policy':
      return 'Decided by the governing policy recorded on this evaluation.';
    case 'fail_closed_default':
      return 'No applicable policy governed this operation, so the deterministic engine refused it. Nothing authorized it.';
    case 'unattributed':
      return 'This evaluation records no policy identity and no fail-closed reason code, so its source cannot be established from the record.';
    default:
      return null;
  }
}

/**
 * The one-line reason a fail-closed DENY happened, from the persisted reason codes.
 * Returns null for a decision a policy actually made — that one is explained by its
 * policy identity, not by this sentence.
 */
export function failClosedReason(policy: IncidentCaseSummary['policy'] | null | undefined): string | null {
  if (resolvePolicyDecisionSource(policy) !== 'fail_closed_default') return null;
  const codes = (policy?.reason_codes ?? []).map((code) => code.toUpperCase());
  if (codes.includes('OPERATION_NOT_ESTABLISHED')) {
    return 'The governed operation could not be established, so no policy could be matched to it.';
  }
  if (codes.includes('POLICY_NOT_FOUND')) {
    return 'No policy governs this operation.';
  }
  return 'No applicable policy was available at evaluation time.';
}

/**
 * The policy identity AS RECORDED at evaluation time — the policy key and the
 * version it carried, joined for display.
 * Read only from the evaluation record, so it survives the policy being edited,
 * archived or deleted afterwards. Returns null when the decision carried no policy
 * identity at all — a fail-closed refusal, which `policyDecisionSourceLabel` names.
 */
export function evaluatedPolicyReference(policy: {
  policy_key?: string | null;
  policy_version?: number | string | null;
} | null | undefined): string | null {
  const key = (policy?.policy_key ?? '').toString().trim();
  if (!key) return null;
  const version = policy?.policy_version;
  return version === null || version === undefined || version === '' ? key : `${key} v${version}`;
}

/* ── Incident origin ──────────────────────────────────────────────── */

const ORIGIN_LABELS: Record<IncidentOrigin, string> = {
  detection: 'Detection',
  alert: 'Alert',
  manual: 'Manual',
  system: 'System',
  unknown: 'Unknown',
};

export function incidentOriginLabel(origin: IncidentOrigin | string | null | undefined): string {
  return ORIGIN_LABELS[(origin ?? 'unknown') as IncidentOrigin] ?? 'Unknown';
}

/**
 * Why this incident has no linked detection, when it has none. An incident
 * escalated from an alert or opened by hand never HAD a Screen 5 detection, and
 * saying so is different from reporting a broken relationship. Returns null when a
 * detection IS linked — there is nothing to explain.
 */
export function missingDetectionExplanation(
  origin: IncidentCaseSummary['origin'] | null | undefined,
): string {
  if (origin?.detection_linked) return '';
  switch (origin?.origin) {
    case 'alert':
      return 'No linked detection is recorded for this incident. It was raised from an alert, which does not itself create a detection record.';
    case 'manual':
      return 'No linked detection is recorded for this incident. It was opened directly rather than by the detection engine.';
    case 'system':
      return 'No linked detection is recorded for this incident. It was opened by an automated workflow rather than by the detection engine.';
    default:
      return 'No linked detection is recorded for this incident, and no origin is recorded to explain why.';
  }
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
  /** Screen 8's canonical approval gate. Present only for an approvable action. */
  approval_gate?: {
    required_approval_count?: number;
    current_approval_count?: number;
    approval_progress_label?: string | null;
  } | null;
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

/** Pluralise "action" against a count, so no label reads "1 actions". */
function actionNoun(count: number): string {
  return count === 1 ? 'action' : 'actions';
}

/**
 * Fold this incident's response actions into one truthful sentence for the Case
 * File and Overview. Ordered by what an operator must act on first: a failure
 * outranks a pending approval, which outranks an execution that already
 * happened. Screen 7 states this; Screen 8 remains the only place it can change.
 *
 * Every count here counts ACTIONS, not approvals. Each number is the number of
 * response-action rows in that state — an action whose own approval quorum is
 * "1 of 2 collected" still counts once. A bare "Awaiting approval (2)" cannot be
 * read that way (2 approvals pending? 2 required? 2 actions?), so each label
 * names its unit outright.
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
  if (failed > 0) {
    return { ...base, state: 'failed', label: `${failed} ${actionNoun(failed)} failed to execute` };
  }
  if (awaitingApproval > 0) {
    return {
      ...base,
      state: 'awaiting_approval',
      label: `${awaitingApproval} ${actionNoun(awaitingApproval)} awaiting approval`,
    };
  }
  if (executed > 0) {
    return { ...base, state: 'executed', label: `${executed} ${actionNoun(executed)} executed` };
  }
  if (approved > 0) {
    return {
      ...base,
      state: 'approved',
      label: `${approved} ${actionNoun(approved)} approved, not executed`,
    };
  }
  return {
    ...base,
    state: 'recommended',
    label: `${rows.length} ${actionNoun(rows.length)} recommended`,
  };
}

/**
 * The approval quorum for ONE action, as Screen 8's gate recorded it — "1 of 2
 * approvals received". Returns null when the backend reported no quorum, so the
 * UI says nothing rather than implying a single approval suffices.
 */
export function approvalQuorumLabel(action: CaseResponseAction | null | undefined): string | null {
  const gate = action?.approval_gate;
  const required = gate?.required_approval_count;
  const current = gate?.current_approval_count;
  if (typeof required !== 'number' || !Number.isFinite(required) || required <= 0) return null;
  const received = typeof current === 'number' && Number.isFinite(current) ? current : 0;
  return `${received} of ${required} ${required === 1 ? 'approval' : 'approvals'} received`;
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

/* ── Compact integrity summary (Case File drawer) ─────────────────── */

/**
 * The six operational-integrity facts the narrow Case File answers at a glance:
 * what was detected, what the chain recorded, what the systems of record said,
 * what policy decided, where the response stands, and what evidence proves it.
 *
 * This is the SAME canonical case record the full-investigation Overview renders
 * — folded into one line each, never a second reading of it. The full record
 * (reason-code lists, reconciliation detail, artifact metadata, approval routing)
 * belongs to Open Full Investigation; nothing is duplicated here.
 */
export type IntegritySummaryKey =
  | 'detection' | 'on_chain' | 'operational' | 'policy' | 'response' | 'evidence';

export type IntegritySummaryRow = {
  key: IntegritySummaryKey;
  /** Section heading, e.g. "On-Chain". */
  label: string;
  /** The one-line primary value. Absence is stated here, never left blank. */
  value: string;
  /** A short secondary line, or null. Never a paragraph — the drawer is a summary. */
  detail: string | null;
  /** A machine identifier (policy key, reason code) rendered monospace, or null. */
  code: string | null;
  /**
   * The state badge. `null` whenever there is no state to report — a row that is
   * loading, unreadable or genuinely absent gets NO coloured pill, so absence can
   * never read as a verdict (CLAUDE.md: no data must not be shown as safe).
   */
  badge: { label: string; variant: PillVariant } | null;
  /** True only when a real backend record stands behind the row. */
  recorded: boolean;
};

/** Wording for a record that could not be read, is still being read, or is absent. */
function absentValue(load: ForensicLoadState, absent: string): string {
  switch (load) {
    case 'idle':
    case 'loading': return 'Loading…';
    case 'unauthorized': return 'Not permitted in this workspace';
    case 'not_found': return 'Incident not found';
    case 'error': return 'Unavailable';
    default: return absent;
  }
}

/** Severity badge for the detection row. Absent severity earns no badge. */
function detectionSeverityBadge(severity: string | null | undefined): { label: string; variant: PillVariant } | null {
  const raw = (severity ?? '').trim().toLowerCase();
  if (!raw) return null;
  const label = humanizeToken(raw) ?? raw;
  if (raw === 'critical' || raw === 'high') return { label, variant: 'danger' };
  if (raw === 'medium') return { label, variant: 'warning' };
  if (raw === 'low') return { label, variant: 'success' };
  if (raw === 'info') return { label, variant: 'info' };
  return { label, variant: 'neutral' };
}

/**
 * Short state word for the response badge. The exact counts stay on the value line —
 * the badge names the state an operator must act on, in the shared response vocabulary,
 * short enough to sit beside the value in a 360px column rather than wrapping under it.
 */
const RESPONSE_STATE_BADGE: Record<CaseResponseState['state'], string> = {
  none: 'None',
  awaiting_approval: 'Awaiting',
  approved: 'Approved',
  executed: 'Executed',
  failed: 'Failed',
  recommended: 'Recommended',
};

/**
 * Short snapshot label for the compact badge. Same four states the full label names
 * ("Evidence snapshot ready" → "Ready"); the wording is shortened, never the meaning,
 * and an unrecorded state stays "Unknown" rather than borrowing a good one.
 */
function snapshotStatusShortLabel(status: ForensicSnapshotStatus | null | undefined): string {
  switch (status) {
    case 'collecting': return 'Collecting';
    case 'ready': return 'Ready';
    case 'sealed': return 'Sealed';
    case 'failed': return 'Integrity failed';
    default: return 'Unknown';
  }
}

/**
 * The artifact total, split by the domain each artifact was classified into —
 * "3 on-chain · 2 policy · 8 human". A total is the sum of four provenance
 * domains, and reading it as evidence for any one of them is precisely the
 * mistake that makes "13 artifacts" look like it contradicts "Operational: not
 * collected". A domain the backend did not report is omitted rather than shown
 * as zero; a domain it reported as zero is omitted too, since naming it adds
 * nothing to a one-line summary. Returns null when no domain count was reported.
 */
export function evidenceDomainBreakdown(
  counts: IncidentEvidenceCounts | null | undefined,
): string | null {
  const parts = EVIDENCE_DOMAINS
    .map((domain) => ({ domain, count: domainCount(counts, domain) }))
    .filter((entry): entry is { domain: IncidentEvidenceDomain; count: number } =>
      entry.count !== null && entry.count > 0)
    .map((entry) => `${entry.count} ${domainLabel(entry.domain).toLowerCase()}`);
  return parts.length === 0 ? null : parts.join(' · ');
}

/** "AUTH_MISSING" + 2 others → one short line, never a stack of pills in a 360px column. */
function reasonCodeSummary(codes: readonly string[] | null | undefined): string | null {
  const list = (codes ?? []).filter((code) => !!code && code.trim());
  if (list.length === 0) return null;
  const first = humanizeToken(list[0]) ?? list[0];
  return list.length === 1 ? first : `${first} +${list.length - 1} more`;
}

/**
 * Fold the canonical case record into the six compact rows.
 *
 * Truthfulness rules enforced here (CLAUDE.md):
 *   * Observed / Not matched / DENY / Ready / Verified appear ONLY where the
 *     backend state proves them. A section with no record says "Not available",
 *     "Not collected", "Not evaluated", "No response action" or "No snapshot"
 *     and carries no badge.
 *   * A read that is still in flight, or that failed, is reported as such — it
 *     never collapses into the "genuinely absent" wording.
 *   * Nothing is derived that the backend did not send: counts, decisions and
 *     states are read straight off the payload and only LABELLED here.
 */
export function buildIntegritySummary(input: {
  summary: IncidentCaseSummary | null;
  /** Load state of the incident's forensic evidence record (the case summary). */
  summaryLoad: ForensicLoadState;
  /** Screen 8's folded response state. */
  response: CaseResponseState;
  /** Whether Screen 8's action records have actually been read. */
  responseLoad: ForensicLoadState;
}): IntegritySummaryRow[] {
  const { summary, summaryLoad, response, responseLoad } = input;
  const known = summaryLoad === 'ready' || summaryLoad === 'empty';
  const detection = summary?.detection ?? {};
  const onChain = summary?.on_chain ?? {};
  const operational = summary?.operational ?? {};
  const policy = summary?.policy ?? {};
  const evidence = summary?.evidence ?? {};

  /* Detection — what the detector said. */
  const detectionName =
    detection.title ?? humanizeToken(detection.detection_type) ?? humanizeToken(detection.category);
  const origin = summary?.origin ?? {};
  const detectionRow: IntegritySummaryRow = known && detectionName
    ? {
      key: 'detection', label: 'Detection', value: detectionName,
      detail: detection.category && detection.detection_type ? humanizeToken(detection.category) : null,
      // The machine reason code is an auditor's key, not a summary fact: it is listed
      // in the full record rather than stacked into the narrow column.
      code: null,
      badge: detectionSeverityBadge(detection.severity),
      recorded: true,
    }
    : {
      key: 'detection', label: 'Detection',
      value: absentValue(known ? 'empty' : summaryLoad, 'No linked detection'),
      // An incident escalated from an alert or opened by hand never HAD a Screen 5
      // detection. Naming the origin turns an empty section from something that
      // reads like a broken relationship into a stated fact about how the case began.
      detail: known && origin.origin ? `Origin: ${incidentOriginLabel(origin.origin)}` : null,
      code: null, badge: null, recorded: false,
    };

  /* On-chain — what the chain recorded. */
  const observedAmount = formatCaseAmount(onChain.observed_amount);
  const onChainRecorded = known && caseSectionRecorded(onChain.state);
  const onChainValue = [humanizeToken(onChain.operation), observedAmount].filter(Boolean).join(' ');
  const onChainRow: IntegritySummaryRow = onChainRecorded
    ? {
      key: 'on_chain', label: 'On-Chain', value: onChainValue || 'Chain event recorded',
      detail: onChain.block_number ? `Block ${onChain.block_number}` : null,
      code: null,
      badge: { label: caseStateLabel(onChain.state), variant: caseStateVariant(onChain.state) },
      recorded: true,
    }
    : {
      key: 'on_chain', label: 'On-Chain',
      value: absentValue(known ? 'empty' : summaryLoad, 'Not available'),
      detail: null, code: null, badge: null, recorded: false,
    };

  /* Operational — what the systems of record said, and whether they said anything.
     The badge carries the outcome in the five-word vocabulary that keeps
     "nothing was collected" apart from "collected and did not match". */
  const operationalRecorded = known && sectionCollected(operational);
  const outcome = operationalOutcome(operational);
  const operationalRow: IntegritySummaryRow = operationalRecorded
    ? {
      key: 'operational', label: 'Operational',
      value: humanizeToken(operational.reconciliation_status) ?? '',
      detail: formatCaseAmount(operational.variance_amount)
        ? `Variance ${formatCaseAmount(operational.variance_amount)}`
        : operational.authoritative_source ?? null,
      code: null,
      badge: { label: operationalOutcomeLabel(outcome), variant: operationalOutcomeVariant(outcome) },
      recorded: true,
    }
    : {
      key: 'operational', label: 'Operational',
      value: absentValue(known ? 'empty' : summaryLoad, 'Not collected'),
      // Stated so the absence cannot be read as a failed comparison. Operational
      // artifacts may still exist for the ASSET; none was reconciled for this event.
      detail: known ? 'Nothing was compared for this event' : null,
      code: null, badge: null, recorded: false,
    };

  /* Policy — the deterministic engine's verdict, never an AI explanation.
     The badge states the DECISION; the value states where the decision came from.
     A DENY reached because no policy governed the operation is the fail-closed rule
     doing its job, and it is named as that rather than as "Policy Not Found", which
     reads as a broken lookup of a policy record. An authoritative decision whose
     source an operator cannot see is exactly what this screen must not show. */
  const policyRecorded = known && caseSectionRecorded(policy.state);
  const policySource = resolvePolicyDecisionSource(policy);
  const failClosed = policySource === 'fail_closed_default';
  const policyRow: IntegritySummaryRow = policyRecorded
    ? {
      key: 'policy', label: 'Policy',
      value: failClosed
        ? 'No applicable policy'
        : reasonCodeSummary(policy.reason_codes) ?? policy.decision ?? 'No decision recorded',
      // Where the verdict came from. Always stated for a recorded decision, so
      // "who decided this" is never left to be inferred from a reason code.
      detail: policyDecisionSourceLabel(policySource),
      // The policy identity AS RECORDED at evaluation time — it survives the policy
      // being edited or deleted afterwards. A fail-closed refusal names no policy,
      // because none applied; inventing one here would fabricate a relationship.
      code: evaluatedPolicyReference(policy),
      badge: policy.decision
        ? { label: policy.decision, variant: policyDecisionVariant(policy.decision) }
        : null,
      recorded: true,
    }
    : {
      key: 'policy', label: 'Policy',
      // No evaluation is NOT a DENY. It carries no decision badge at all.
      value: absentValue(known ? 'empty' : summaryLoad, 'Not evaluated'),
      detail: null, code: null, badge: null, recorded: false,
    };

  /* Response — Screen 8's state, reported not restated. */
  const responseKnown = responseLoad === 'ready' || responseLoad === 'empty';
  const responseRow: IntegritySummaryRow = responseKnown && response.total > 0
    ? {
      key: 'response', label: 'Response', value: response.label,
      // Both numbers name their unit. "5 recommended" beside "Awaiting approval (2)"
      // left an operator to guess whether the 2 were approvals or actions.
      detail: `${response.total} response ${response.total === 1 ? 'action' : 'actions'} recommended in total`,
      code: null,
      badge: { label: RESPONSE_STATE_BADGE[response.state], variant: responseStateVariant(response.state) },
      recorded: true,
    }
    : {
      key: 'response', label: 'Response',
      value: absentValue(responseKnown ? 'empty' : responseLoad, 'No response action'),
      detail: null, code: null, badge: null, recorded: false,
    };

  /* Evidence — what proves it. A count the backend did not report is not zero. */
  const artifactCount = typeof evidence.artifact_count === 'number' ? evidence.artifact_count : null;
  const evidenceRow: IntegritySummaryRow = known && artifactCount !== null
    ? {
      key: 'evidence', label: 'Evidence',
      value: `${artifactCount} ${artifactCount === 1 ? 'artifact' : 'artifacts'}`,
      // The total spans four provenance domains, so it must not be read as proof
      // that any one of them was collected — 13 artifacts beside "Operational: not
      // collected" is only contradictory if the total looks like an operational
      // count. The breakdown is stated whenever the backend reported one.
      detail: evidenceDomainBreakdown(evidence.counts)
        ?? (evidence.snapshot_status ? null : snapshotStatusLabel(evidence.snapshot_status)),
      code: evidence.package_number ?? null,
      // A snapshot badge is shown only for a state the backend actually recorded —
      // "unknown" is not a state worth colouring.
      badge: evidence.snapshot_status
        ? { label: snapshotStatusShortLabel(evidence.snapshot_status), variant: snapshotStatusVariant(evidence.snapshot_status) }
        : null,
      recorded: true,
    }
    : {
      key: 'evidence', label: 'Evidence',
      value: absentValue(known ? 'empty' : summaryLoad, 'No snapshot'),
      detail: null, code: null, badge: null, recorded: false,
    };

  // A row whose value repeats its badge prints the same fact twice in a column that has
  // no room for it. Compared case- and space-insensitively, because the two strings come
  // from different vocabularies — the engine's own status token and this module's outcome
  // word — and "Not Matched" beside "Not matched" is still one fact rendered twice. The
  // badge keeps the state; the value goes blank and the renderer skips it. Nothing is
  // lost — the badge already says it.
  const sameFact = (value: string, badge: string): boolean =>
    value.trim().toLowerCase() === badge.trim().toLowerCase();
  return [detectionRow, onChainRow, operationalRow, policyRow, responseRow, evidenceRow].map((row) =>
    row.badge && sameFact(row.value, row.badge.label) ? { ...row, value: '' } : row,
  );
}

/* ── Compact investigation progress (Case File drawer) ────────────── */

/**
 * The persisted workflow stages, folded into a single progress line so the drawer
 * does not push a seven-item checklist below the fold. The full checklist stays on
 * the full-investigation workspace — this is the same canonical `workflow_stages`
 * payload counted, never a second, browser-inferred definition of "done".
 *
 * Returns null when there are no stages: "0 / 0" would be a claim the data does
 * not support.
 */
export type WorkflowProgress = {
  total: number;
  completed: number;
  failed: number;
  /** Floor-rounded completion percentage, 0–100. */
  percent: number;
  /** The stage an operator is on now (first in-progress/queued), else null. */
  current: string | null;
};

/* ── Investigation coverage (which lifecycle stages have a record) ── */

/**
 * Coverage is deliberately NOT a completion score. It answers one question per
 * lifecycle domain — "is there a persisted record for this?" — so a case whose
 * flow never ran a policy evaluation shows a gap instead of a manufactured
 * timeline event. `missing` is a truthful answer, never a failure.
 */
export type CoverageState = 'available' | 'missing' | 'not_applicable';

export type CoverageRow = { key: string; label: string; state: CoverageState };

export function coverageStateLabel(state: CoverageState): string {
  switch (state) {
    case 'available': return 'Available';
    case 'not_applicable': return 'Not applicable';
    default: return 'Missing';
  }
}

/** Available is the only affirmative state; a gap is neutral, never success. */
export function coverageStateVariant(state: CoverageState): PillVariant {
  return state === 'available' ? 'success' : 'neutral';
}

/**
 * One coverage row per lifecycle domain, from persisted records only.
 *
 * Detection is `not_applicable` — not `missing` — for an incident whose ORIGIN
 * shows it was never raised by the detection engine: an alert escalation or a
 * manually opened case never had a detection to collect, and reporting that as a
 * gap would invent an expectation the workflow never set.
 */
export function investigationCoverage(input: {
  summary: IncidentCaseSummary | null;
  summaryLoad: ForensicLoadState;
  responseTotal: number;
  responseLoad: ForensicLoadState;
}): CoverageRow[] {
  const { summary, summaryLoad, responseTotal, responseLoad } = input;
  const known = summaryLoad === 'ready' || summaryLoad === 'empty';
  const origin = summary?.origin ?? {};
  const evidence = summary?.evidence ?? {};
  const seen = (available: boolean): CoverageState =>
    known && available ? 'available' : 'missing';

  const detectionState: CoverageState = !known
    ? 'missing'
    : origin.detection_linked
      ? 'available'
      : origin.origin === 'alert' || origin.origin === 'manual' || origin.origin === 'system'
        ? 'not_applicable'
        : 'missing';

  return [
    { key: 'on_chain', label: 'On-chain event', state: seen(sectionCollected(summary?.on_chain)) },
    { key: 'operational', label: 'Operational state', state: seen(sectionCollected(summary?.operational)) },
    { key: 'detection', label: 'Detection', state: detectionState },
    { key: 'policy', label: 'Policy evaluation', state: seen(caseSectionRecorded(summary?.policy?.state)) },
    {
      key: 'response',
      label: 'Response',
      state: (responseLoad === 'ready' || responseLoad === 'empty') && responseTotal > 0
        ? 'available'
        : 'missing',
    },
    {
      key: 'evidence',
      label: 'Evidence',
      state: seen(typeof evidence.artifact_count === 'number' && evidence.artifact_count > 0),
    },
  ];
}

export function summarizeWorkflowProgress(
  stages: readonly WorkflowStage[] | null | undefined,
): WorkflowProgress | null {
  const rows = stages ?? [];
  if (rows.length === 0) return null;
  const completed = rows.filter((s) => s.state === 'completed').length;
  const failed = rows.filter((s) => s.state === 'failed').length;
  const active = rows.find((s) => s.state === 'in_progress') ?? rows.find((s) => s.state === 'queued');
  return {
    total: rows.length,
    completed,
    failed,
    percent: Math.floor((completed / rows.length) * 100),
    current: active?.label ?? null,
  };
}
