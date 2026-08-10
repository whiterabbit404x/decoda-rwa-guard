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
 *  - Recovery ("Generate Manifest") is offered ONLY for a Manifest-Missing
 *    package, backend-authoritative via `allowed_actions.generate_manifest`.
 *    It is never hidden merely because completeness is low, the package is
 *    partial, incident evidence is missing, or Verify/Download Manifest are
 *    disabled — a Manifest-Missing package always SHOWS the recovery action
 *    (disabled only when the backend withholds permission, so the state stays
 *    legible instead of silently absent).
 *  - Verify Integrity and Download Manifest stay disabled while the manifest is
 *    missing; they are backend-authoritative and never independently re-enabled.
 *  - The `??` fallbacks apply only to older API responses that predate
 *    `allowed_actions`; when the field is present the backend value wins.
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
 * Minimal package shape the gate needs. This mirrors the canonical fields the
 * exports list/detail endpoints return; the ticket refers to `hash_verification`
 * and `verify_integrity`, which map onto the wire contract fields
 * `integrity_status` (the manifest_missing state) and `allowed_actions.verify`.
 */
export type EvidenceActionSource = {
  integrity_status?: string | null;
  is_manifest_missing?: boolean;
  allowed_actions?: EvidenceAllowedActions;
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
   * Whether the detail-drawer footer renders the Generate Manifest button at
   * all. A Manifest-Missing package always renders it (possibly disabled) so the
   * recovery state is never silently hidden.
   */
  showGenerate: boolean;
  /** Whether the footer renders the Regenerate Package fallback button at all. */
  showRegenerate: boolean;
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

  return {
    manifestMissing,
    canVerify,
    canDownloadManifest,
    canGenerate,
    canRegenerate,
    // A Manifest-Missing package always shows the recovery buttons so the state
    // is legible; a healthy package shows them only if the backend permits.
    showGenerate: manifestMissing || canGenerate,
    showRegenerate: manifestMissing || canRegenerate,
  };
}
