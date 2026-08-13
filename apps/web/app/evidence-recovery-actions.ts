/**
 * Canonical Screen 9 evidence-action gate.
 *
 * ONE definition of which recovery / verify / download-manifest actions a
 * package permits, derived from the backend-authoritative package response
 * (`integrity_status` / `is_manifest_missing` / `allowed_actions`). The detail
 * drawer consumes this instead of re-deriving the booleans inline, so the
 * "Generate Manifest" recovery button, "Verify Integrity" and "Download
 * Manifest" can never disagree with the backend contract — and the gate is
 * unit-testable against a raw package response (see
 * tests/evidence-recovery-actions.spec.ts).
 *
 * Truthfulness rules encoded here:
 *  - Button VISIBILITY is backend-authoritative and comes ONLY from
 *    `allowed_actions` — it is NEVER inferred from `is_manifest_missing`.
 *    "Generate Manifest" is shown iff `allowed_actions.generate_manifest === true`
 *    and "Regenerate Package" iff `allowed_actions.regenerate_package === true`.
 *    Inferring visibility from the manifest-missing fact is exactly what made the
 *    button flash for EV-2026-004 (manifest is missing, but generate_manifest is
 *    false because the required source evidence must be collected first). When
 *    neither recovery action is permitted and recovery is required, the caller
 *    shows a stable "Recovery required" panel — recovery is never a silent dead end.
 *  - Verify Integrity and Download Manifest stay disabled while the manifest is
 *    missing; they are backend-authoritative and never independently re-enabled.
 *  - The `??` fallbacks apply only to older API responses that predate
 *    `allowed_actions`; when the field is present the backend value wins. They feed
 *    the ENABLED state only — never button visibility, which is strictly
 *    `allowed_actions`-driven.
 */

/** The `allowed_actions` contract emitted by the backend for a package. */
export type EvidenceAllowedActions = {
  view?: boolean;
  download?: boolean;
  download_manifest?: boolean;
  verify?: boolean;
  copy_hash?: boolean;
  /** Primary recovery action for a Manifest-Missing package. */
  generate_manifest?: boolean;
  /** Fallback recovery: regenerate a superseding package (preserves the original). */
  regenerate_package?: boolean;
};

/**
 * Canonical recovery state emitted by the backend recovery selector. Manifest
 * PRESENCE (`is_manifest_missing`) is a fact; recovery POLICY (which action, if
 * any) is this separate axis. `evidence_required` / `permission_required` mean
 * neither recovery action is available and a structured reason says what to do
 * next — the UI shows a stable "Recovery required" panel, never a disabled button.
 */
export type EvidenceRecoveryState =
  | 'none'
  | 'generate_manifest'
  | 'regenerate_package'
  | 'evidence_required'
  | 'permission_required';

/**
 * Minimal package shape the gate needs. This mirrors the canonical fields the
 * exports list/detail endpoints return; the ticket refers to `hash_verification`
 * and `verify_integrity`, which map onto the wire contract fields
 * `integrity_status` (the manifest_missing state) and `allowed_actions.verify`.
 */
export type EvidenceActionSource = {
  integrity_status?: string | null;
  is_manifest_missing?: boolean;
  allowed_actions?: EvidenceAllowedActions;
  /** Canonical recovery state (backend-authoritative when present). */
  recovery_state?: string | null;
  /** Why recovery is blocked, when it is — a customer-safe next-step message. */
  recovery_blocked_reason?: string | null;
  /**
   * Backend fact: this package needs recovery (it is completed, non-superseded,
   * manifest-missing). When true but neither recovery action is permitted, the UI
   * shows the "Recovery required" blocker instead of leaving the surface empty.
   */
  recovery_required?: boolean;
};

export type EvidenceActionState = {
  /** The package has no retrievable manifest (the EV-2026-004 state). */
  manifestMissing: boolean;
  /** Verify Integrity is permitted (false while the manifest is missing). */
  canVerify: boolean;
  /** Download Manifest is permitted (false while the manifest is missing). */
  canDownloadManifest: boolean;
  /** In-place manifest recovery is permitted for this user + package. */
  canGenerate: boolean;
  /** Superseding regeneration (fallback recovery) is permitted. */
  canRegenerate: boolean;
  /**
   * Whether the footer renders the Generate Manifest button at all. STRICTLY
   * `allowed_actions.generate_manifest === true` — never inferred from the
   * manifest-missing fact. When recovery is required but this (and showRegenerate)
   * is false, the caller shows the stable "Recovery required" panel instead.
   */
  showGenerate: boolean;
  /**
   * Whether the footer renders the Regenerate Package fallback button at all.
   * STRICTLY `allowed_actions.regenerate_package === true`.
   */
  showRegenerate: boolean;
  /** Canonical recovery state (backend value wins; else inferred from the actions). */
  recoveryState: EvidenceRecoveryState;
  /**
   * Backend fact: this package requires recovery. When true and neither recovery
   * action is shown, the caller renders the "Recovery required" blocker so the
   * surface is never silently empty.
   */
  recoveryRequired: boolean;
  /** Customer-safe reason recovery is blocked, when it is (else null). */
  recoveryBlockedReason: string | null;
  /**
   * Recovery is blocked: neither in-place manifest generation nor regeneration is
   * available (the source evidence must first be collected, or the user lacks
   * permission). The panel shows a "Recovery required" message with a next action
   * (View Incident) rather than the Generate/Regenerate buttons.
   */
  recoveryBlocked: boolean;
};

/**
 * Resolve the canonical action state for a package.
 *
 * @param source  the package response fields (list row or detail).
 * @param ready   whether the package artifact is retrievable/downloadable
 *                (`isPackageReady(pkg)` in the panel). Only used for the
 *                pre-`allowed_actions` fallback.
 */
export function resolveEvidenceActionState(
  source: EvidenceActionSource,
  ready: boolean,
): EvidenceActionState {
  const integrityStatus = String(source.integrity_status ?? '').toLowerCase();
  const manifestMissing =
    integrityStatus === 'manifest_missing' || (source.is_manifest_missing ?? false);
  const aa: EvidenceAllowedActions = source.allowed_actions ?? {};

  const canVerify = aa.verify ?? (ready && !manifestMissing);
  const canDownloadManifest = aa.download_manifest ?? (ready && !manifestMissing);
  const canGenerate = aa.generate_manifest ?? (ready && manifestMissing);
  const canRegenerate = aa.regenerate_package ?? (ready && manifestMissing);

  // Button VISIBILITY is STRICTLY backend-authoritative — a recovery button is shown
  // ONLY when the package's own allowed_actions explicitly permits it. It is NEVER
  // inferred from `manifestMissing`: EV-2026-004 is manifest-missing yet must NOT show
  // Generate Manifest (its required source evidence has to be collected first, so
  // generate_manifest is false and the "Recovery required" blocker is shown instead).
  // Inferring visibility from the manifest-missing fact is the exact flicker this fixes.
  const showGenerate = aa.generate_manifest === true;
  const showRegenerate = aa.regenerate_package === true;

  // Recovery state: the backend value is authoritative; older responses without it
  // are inferred from the (possibly fallback) action booleans. A blocked state
  // (evidence must be collected first, or no permission) is never inferred from
  // absent actions — it only ever comes from the backend, so an old response can
  // never masquerade as blocked.
  const rawRecoveryState = String(source.recovery_state ?? '').toLowerCase();
  const recoveryState: EvidenceRecoveryState =
    rawRecoveryState === 'evidence_required'
      ? 'evidence_required'
      : rawRecoveryState === 'permission_required'
        ? 'permission_required'
        : rawRecoveryState === 'generate_manifest'
          ? 'generate_manifest'
          : rawRecoveryState === 'regenerate_package'
            ? 'regenerate_package'
            : canGenerate
              ? 'generate_manifest'
              : canRegenerate
                ? 'regenerate_package'
                : 'none';
  const recoveryRequired = source.recovery_required === true;
  // Recovery is BLOCKED — the caller shows a stable "Recovery required" panel instead
  // of the buttons — when the backend says so (evidence_required / permission_required)
  // OR when recovery is required but neither recovery action is permitted (both
  // withheld). This guarantees a manifest-missing package with no available action is
  // never a silent dead end. Blocked is never inferred from an absent field alone, so
  // an older response (no recovery model) can never masquerade as blocked.
  const recoveryBlocked =
    recoveryState === 'evidence_required' ||
    recoveryState === 'permission_required' ||
    (recoveryRequired && !showGenerate && !showRegenerate);
  const recoveryBlockedReason = source.recovery_blocked_reason ?? null;

  return {
    manifestMissing,
    canVerify,
    canDownloadManifest,
    canGenerate,
    canRegenerate,
    // Visibility is allowed_actions-authoritative only (see above): Generate Manifest
    // shows iff generate_manifest === true, Regenerate Package iff
    // regenerate_package === true. Neither is ever derived from manifestMissing.
    showGenerate,
    showRegenerate,
    recoveryState,
    recoveryRequired,
    recoveryBlockedReason,
    recoveryBlocked,
  };
}

/* ── Recovery requirements: source evidence vs derived integrity artifacts ────── */

/**
 * DERIVED integrity artifacts — produced automatically once enough SOURCE evidence
 * exists to regenerate the package. An operator can NEVER directly "collect" these;
 * listing them beside collectable source evidence would falsely imply a manual step.
 * They are the two hash categories the completeness snapshot always reports
 * (`file_hashes` / `manifest_hash`).
 */
export const DERIVED_INTEGRITY_CODES = ['file_hashes', 'manifest_hash'] as const;
const DERIVED_INTEGRITY_SET = new Set<string>(DERIVED_INTEGRITY_CODES);

/**
 * Fallback human labels for evidence category codes, used only when a completeness
 * response omits the per-category `label` (older payloads). The backend's category
 * `label` is authoritative and wins when present.
 */
const EVIDENCE_CODE_LABELS: Record<string, string> = {
  incident_identity: 'Incident identity',
  original_alert: 'Original alert',
  detection_provenance: 'Detection provenance',
  telemetry_reference: 'Raw telemetry references',
  asset_identity: 'Asset identity',
  chain_metadata: 'Chain and transaction metadata',
  investigation_timeline: 'Investigation timeline',
  response_recommendation: 'Recommended response',
  approval_decision: 'Approval or rejection decision',
  execution_result: 'Executed response and execution result',
  rejection_evidence: 'Rejection evidence',
  closure_state: 'Closure or containment state',
  audit_events: 'Audit events',
  file_hashes: 'File hashes',
  manifest_hash: 'Manifest hash',
};

export type MissingRequirement = { code: string; label: string };

/**
 * The missing recovery requirements, split by whether the operator can act on them.
 *  - `source`  : source-evidence gaps that must be collected (via the incident)
 *                before the package can be regenerated.
 *  - `derived` : integrity artifacts (file hashes / manifest hash) that are
 *                GENERATED AFTER RECOVERY — never collected by hand.
 */
export type EvidenceRecoveryRequirements = {
  source: MissingRequirement[];
  derived: MissingRequirement[];
};

/** The completeness fields the requirement split reads. */
export type CompletenessRequirementSource = {
  missing_codes?: string[] | null;
  categories?: Array<{
    code?: string | null;
    label?: string | null;
    required?: boolean;
    status?: string | null;
  }> | null;
};

/**
 * Split a package's MISSING required evidence (canonical `completeness.missing_codes`)
 * into source-evidence gaps and derived integrity artifacts.
 *
 * The order of `missing_codes` is preserved. `file_hashes` / `manifest_hash` are the
 * only DERIVED artifacts — everything else is source evidence the operator collects on
 * the incident. This is the ONE place that classification lives, so the recovery panel
 * (which lists both groups) and the Crypto-Auditing Clerk summary (which counts only the
 * source gaps) can never disagree about what "6 source-evidence categories are missing"
 * means. Never counts file_hashes / manifest_hash as operator-collected source evidence.
 */
export function resolveRecoveryRequirements(
  completeness: CompletenessRequirementSource | null | undefined,
): EvidenceRecoveryRequirements {
  const source: MissingRequirement[] = [];
  const derived: MissingRequirement[] = [];
  if (!completeness) return { source, derived };

  const labelByCode = new Map<string, string>();
  for (const c of completeness.categories ?? []) {
    if (c?.code && typeof c.label === 'string' && c.label) labelByCode.set(c.code, c.label);
  }

  for (const code of completeness.missing_codes ?? []) {
    if (!code) continue;
    const label = labelByCode.get(code) ?? EVIDENCE_CODE_LABELS[code] ?? code;
    (DERIVED_INTEGRITY_SET.has(code) ? derived : source).push({ code, label });
  }
  return { source, derived };
}

/* ── Included Artifacts: truthful available / missing / derived inventory ──────── */

/**
 * The canonical incident-trace fields the Included-Artifacts inventory reads. These
 * come from the storage-authoritative detail response (`incident_trace`), which is
 * built from the package's OWN stored summary — never from hardcoded proof-bundle
 * labels, the legacy `includes` list, or package-template expectations.
 */
export type IncidentTraceSource = {
  available_sections?: string[] | null;
  missing_sections?: string[] | null;
};

/** One artifact row in the Included-Artifacts inventory. */
export type IncludedArtifact = { code: string; label: string };

/**
 * The Included-Artifacts inventory, split by canonical truth:
 *  - `available` : evidence sections the package actually contains (green check).
 *  - `missing`   : source-evidence sections/categories the package does NOT contain
 *                  (red cross) — never shown as a green check.
 *  - `derived`   : integrity artifacts (file hashes / manifest hash) that are
 *                  GENERATED AFTER RECOVERY and are not yet available.
 */
export type IncludedArtifacts = {
  available: IncludedArtifact[];
  missing: IncludedArtifact[];
  derived: IncludedArtifact[];
};

/**
 * Canonical, ordered catalog of customer-facing evidence artifacts. Each entry maps a
 * single displayed artifact onto the section-name aliases the backend emits in
 * `incident_trace.available_sections` / `missing_sections` (the available list uses the
 * singular section names like `alert`/`telemetry`; the missing list uses the plural
 * `alerts`/`telemetry_evidence`, so BOTH forms are listed) AND onto the canonical
 * completeness category code. Internal-only sections (`target_context`,
 * `provider_context`) are deliberately absent — they are not customer evidence artifacts.
 * `investigation_timeline` has no section and is resolved from completeness alone.
 */
const INCLUDED_ARTIFACT_CATALOG: Array<{
  code: string;
  label: string;
  sections: string[];
  completenessCode: string | null;
}> = [
  { code: 'incident', label: 'Incident', sections: ['incident'], completenessCode: 'incident_identity' },
  { code: 'original_alert', label: 'Original Alert', sections: ['alert', 'alerts'], completenessCode: 'original_alert' },
  { code: 'detection_provenance', label: 'Detection Provenance', sections: ['detection', 'detections'], completenessCode: 'detection_provenance' },
  { code: 'telemetry_evidence', label: 'Raw Telemetry Evidence', sections: ['telemetry', 'telemetry_evidence'], completenessCode: 'telemetry_reference' },
  { code: 'asset_identity', label: 'Asset Identity', sections: ['asset_context'], completenessCode: 'asset_identity' },
  { code: 'investigation_timeline', label: 'Investigation Timeline', sections: [], completenessCode: 'investigation_timeline' },
  { code: 'response_action', label: 'Response Action', sections: ['response_action', 'response_actions'], completenessCode: 'response_recommendation' },
  { code: 'audit_events', label: 'Audit Events', sections: ['audit_log'], completenessCode: 'audit_events' },
  { code: 'export_metadata', label: 'Export Metadata', sections: ['export_metadata'], completenessCode: null },
];

/**
 * Build the truthful Included-Artifacts inventory from the CANONICAL detail response —
 * `incident_trace` (available/missing sections) plus `completeness` (missing category
 * codes). This is the ONLY source of the checklist: a green check is shown iff the
 * section is in `available_sections`, and an artifact is listed as missing iff its
 * section is in `missing_sections` OR its completeness category is in `missing_codes`.
 * A missing artifact is therefore NEVER shown with a green check, and the list is never
 * derived from hardcoded proof-bundle labels, the legacy `includes` field, or the
 * package-template expectations — all of which can disagree with what the package
 * actually contains (the EV-2026-004 contradiction).
 *
 * The two derived integrity artifacts (file hashes / manifest hash) are split out via
 * the SAME classifier the recovery panel uses (`resolveRecoveryRequirements`), so the
 * "generated after recovery" group can never disagree with the recovery requirements.
 */
export function resolveIncludedArtifacts(
  incidentTrace: IncidentTraceSource | null | undefined,
  completeness: CompletenessRequirementSource | null | undefined,
): IncludedArtifacts {
  const available: IncludedArtifact[] = [];
  const missing: IncludedArtifact[] = [];

  const availableSections = new Set(
    (incidentTrace?.available_sections ?? []).filter(Boolean).map((s) => String(s).toLowerCase()),
  );
  const missingSections = new Set(
    (incidentTrace?.missing_sections ?? []).filter(Boolean).map((s) => String(s).toLowerCase()),
  );
  const missingCodes = new Set((completeness?.missing_codes ?? []).filter(Boolean).map((c) => String(c)));

  for (const entry of INCLUDED_ARTIFACT_CATALOG) {
    // The Included-Artifacts inventory is a SECTION inventory, so it uses the stable
    // section label (not the verbose completeness category label, which the separate
    // Evidence Completeness checklist already shows) — "Incident", never "Incident
    // identity and timestamps".
    const label = entry.label;
    const isAvailable = entry.sections.some((s) => availableSections.has(s));
    if (isAvailable) {
      available.push({ code: entry.code, label });
      continue;
    }
    const isMissing =
      entry.sections.some((s) => missingSections.has(s)) ||
      (entry.completenessCode != null && missingCodes.has(entry.completenessCode));
    if (isMissing) {
      // Fail closed: never show a green check for an artifact the canonical response
      // does not report as available.
      missing.push({ code: entry.code, label });
    }
    // No canonical signal (neither available nor missing) → omit rather than invent a
    // present/absent claim.
  }

  // The derived integrity artifacts come from the ONE canonical requirement split so
  // the Included-Artifacts "generated after recovery" group and the recovery panel's
  // derived group are always identical.
  const derived = resolveRecoveryRequirements(completeness).derived.map((d) => ({
    code: d.code,
    label: d.label,
  }));

  return { available, missing, derived };
}
