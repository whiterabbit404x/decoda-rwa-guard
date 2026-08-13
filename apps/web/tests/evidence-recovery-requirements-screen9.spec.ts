/**
 * Screen 9 — EV-2026-004 recovery UX: source-evidence vs generated artifacts,
 * canonical status axes, disabled-action reasons, and the Crypto-Auditing Clerk
 * evidence-chain finding.
 *
 * The live EV-2026-004 response is a completed, degraded-diagnostic proof bundle whose
 * required SOURCE evidence is still missing (generation_status=generated_partial,
 * manifest_status=missing, verification_status=not_ready, completeness=20% critical,
 * recovery_required=true, recovery_state=evidence_required). The single recovery outcome
 * is the structured blocker — never a Generate Manifest / Regenerate Package button.
 *
 * These regression tests lock the truthful recovery surface:
 *   - The Recovery-required panel lists the ACTUAL missing requirements, split into the
 *     six SOURCE evidence categories the operator collects (via the incident) and the two
 *     DERIVED integrity artifacts (file hashes / manifest hash) that are GENERATED AFTER
 *     RECOVERY — never presented as something to collect by hand.
 *   - The primary recovery action is View Incident, routed to the canonical
 *     /incidents/{id} route, deep-linked to the Evidence tab.
 *   - Verify Integrity / Download Manifest stay disabled with truthful reasons.
 *   - Generate Manifest / Regenerate Package are absent and never flicker in.
 *   - The Clerk counts ONLY the six missing source-evidence categories.
 *   - Recovery eligibility is NOT hardcoded: a future authoritative detail that permits
 *     regenerate_package surfaces the Regenerate Package button.
 *
 * Behavioural tests drive the SAME pure gates the panel renders through
 * (resolveEvidenceActionState / resolveDetailActionState / resolveRecoveryRequirements /
 * the detail reducer); source-contract guards then assert the panel wires those gates.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  DERIVED_INTEGRITY_CODES,
  resolveEvidenceActionState,
  resolveRecoveryRequirements,
} from '../app/evidence-recovery-actions';
import {
  evidenceDetailReducer,
  initEvidenceDetailState,
  resolveDetailActionState,
  selectAuthoritativeDetail,
  type EvidenceDetailEvent,
  type EvidenceDetailState,
  type EvidencePackageDetail,
} from '../app/evidence-detail-state';

const PANEL = 'app/evidence-audit-panel.tsx';
function readPanel(): string {
  return fs.readFileSync(path.join(__dirname, '..', PANEL), 'utf-8');
}

const ID = 'ev-2026-004';
const INCIDENT_ID = '3c42654c-8504-4743-b548-253e0503326e';
const READY = true;

/**
 * The canonical EV-2026-004 completeness snapshot: 20% critical, two present required
 * categories, eight missing — six source-evidence gaps plus the two derived hash
 * artifacts. `missing_codes` is authoritative for the split.
 */
const EV004_COMPLETENESS = {
  score: 20,
  status: 'critical',
  status_label: 'Critical',
  required_count: 10,
  present_count: 2,
  missing_count: 8,
  unverifiable_count: 0,
  categories: [
    { code: 'incident_identity', label: 'Incident identity and timestamps', required: true, status: 'present' },
    { code: 'original_alert', label: 'Original alert', required: true, status: 'missing' },
    { code: 'detection_provenance', label: 'Detection provenance', required: true, status: 'missing' },
    { code: 'telemetry_reference', label: 'Raw telemetry references', required: true, status: 'missing' },
    { code: 'asset_identity', label: 'Asset identity', required: true, status: 'missing' },
    { code: 'chain_metadata', label: 'Chain and transaction metadata', required: true, status: 'present' },
    { code: 'investigation_timeline', label: 'Investigation timeline', required: true, status: 'missing' },
    { code: 'audit_events', label: 'Audit events', required: true, status: 'missing' },
    { code: 'file_hashes', label: 'File hashes (SHA-256)', required: true, status: 'missing' },
    { code: 'manifest_hash', label: 'Manifest hash', required: true, status: 'missing' },
  ],
  missing_codes: [
    'original_alert',
    'detection_provenance',
    'telemetry_reference',
    'asset_identity',
    'investigation_timeline',
    'audit_events',
    'file_hashes',
    'manifest_hash',
  ],
};

/** The live EV-2026-004 authoritative detail response (internally consistent). */
const DETAIL_EV004: EvidencePackageDetail = {
  id: ID,
  status: 'completed',
  is_manifest_missing: true,
  integrity_status: 'needs_evidence',
  incident_id: INCIDENT_ID,
  // Canonical status axes (four independent facts).
  generation_status: 'generated_partial',
  manifest_status: 'missing',
  verification_status: 'not_ready',
  evidence_completeness_status: 'critical',
  completeness_score: 20,
  completeness: EV004_COMPLETENESS,
  // Canonical recovery model.
  recovery_required: true,
  recovery_state: 'evidence_required',
  recovery_blocked_reason:
    'Collect the missing required evidence before regenerating this package.',
  allowed_actions: {
    view: true,
    download: true,
    download_manifest: false,
    verify: false,
    copy_hash: false,
    generate_manifest: false,
    regenerate_package: false,
  },
} as EvidencePackageDetail;

function run(
  events: EvidenceDetailEvent<EvidencePackageDetail>[],
  start?: EvidenceDetailState<EvidencePackageDetail>,
): EvidenceDetailState<EvidencePackageDetail> {
  return events.reduce(
    (s, e) => evidenceDetailReducer(s, e),
    start ?? initEvidenceDetailState<EvidencePackageDetail>(),
  );
}

/* ── 1. resolveRecoveryRequirements: source vs derived split ──────────────────── */

test('requirements: EV-2026-004 splits into six SOURCE categories and two DERIVED artifacts', () => {
  const req = resolveRecoveryRequirements(EV004_COMPLETENESS);

  // Six source-evidence categories the operator collects — file_hashes / manifest_hash
  // are NEVER counted here.
  expect(req.source.map((r) => r.code)).toEqual([
    'original_alert',
    'detection_provenance',
    'telemetry_reference',
    'asset_identity',
    'investigation_timeline',
    'audit_events',
  ]);
  // Labels come from the backend categories.
  expect(req.source.map((r) => r.label)).toEqual([
    'Original alert',
    'Detection provenance',
    'Raw telemetry references',
    'Asset identity',
    'Investigation timeline',
    'Audit events',
  ]);
  // The two DERIVED integrity artifacts, generated after recovery.
  expect(req.derived.map((r) => r.code)).toEqual(['file_hashes', 'manifest_hash']);
  // Only these two codes are ever classified as derived.
  expect([...DERIVED_INTEGRITY_CODES].sort()).toEqual(['file_hashes', 'manifest_hash']);
});

test('requirements: file_hashes / manifest_hash are never counted as source evidence, even if listed first', () => {
  const req = resolveRecoveryRequirements({
    missing_codes: ['manifest_hash', 'file_hashes', 'original_alert'],
    categories: [
      { code: 'original_alert', label: 'Original alert', required: true, status: 'missing' },
    ],
  });
  expect(req.source.map((r) => r.code)).toEqual(['original_alert']);
  expect(req.derived.map((r) => r.code)).toEqual(['manifest_hash', 'file_hashes']);
});

test('requirements: falls back to canonical labels when categories omit them, and is empty for no completeness', () => {
  const req = resolveRecoveryRequirements({ missing_codes: ['asset_identity', 'file_hashes'] });
  expect(req.source).toEqual([{ code: 'asset_identity', label: 'Asset identity' }]);
  expect(req.derived).toEqual([{ code: 'file_hashes', label: 'File hashes' }]);
  expect(resolveRecoveryRequirements(null)).toEqual({ source: [], derived: [] });
  expect(resolveRecoveryRequirements(undefined)).toEqual({ source: [], derived: [] });
});

/* ── 2. EV-2026-004 recovery panel: blocker + requirement groups + View Incident ── */

test('panel: EV-2026-004 shows the stable Recovery-required panel with the backend reason', () => {
  const gate = resolveEvidenceActionState(DETAIL_EV004, READY);
  expect(gate.recoveryRequired).toBe(true);
  expect(gate.recoveryState).toBe('evidence_required');
  expect(gate.recoveryBlocked).toBe(true);
  expect(gate.recoveryBlockedReason).toBe(
    'Collect the missing required evidence before regenerating this package.',
  );
  // Neither recovery button is permitted.
  expect(gate.showGenerate).toBe(false);
  expect(gate.showRegenerate).toBe(false);
  // Verify + Download Manifest stay disabled.
  expect(gate.canVerify).toBe(false);
  expect(gate.canDownloadManifest).toBe(false);

  const source = readPanel();
  // The blocker renders with its title and the backend reason.
  expect(source).toContain('const showRecoveryBlocker =');
  expect(source).toContain('{showRecoveryBlocker && (');
  expect(source).toContain('Recovery required');
  expect(source).toContain('recoveryBlockedReason');
});

test('panel: the Recovery-required panel lists source evidence and generated artifacts in SEPARATE groups', () => {
  const source = readPanel();
  // The two group headings.
  expect(source).toContain('Source evidence required');
  expect(source).toContain('Generated after recovery');
  // Both groups are rendered from the ONE canonical requirement split.
  expect(source).toContain('resolveRecoveryRequirements(completeness)');
  expect(source).toContain('recoveryRequirements.source.map');
  expect(source).toContain('recoveryRequirements.derived.map');
  // The derived group explains these are produced automatically — never collected.
  expect(source).toContain(
    'Produced automatically once enough source evidence exists to regenerate the package.',
  );
});

test('panel: primary recovery action is View Incident routed to the canonical incident evidence tab', () => {
  const source = readPanel();
  // Canonical /incidents/{id} route, deep-linked to the Evidence tab.
  expect(source).toContain('`/incidents/${encodeURIComponent(pkg.incident_id)}?tab=evidence`');
  expect(source).toContain('href={incidentHref}');
  expect(source).toContain('View Incident');
  // No fabricated recovery actions: the blocker offers no onClick collect/generate/
  // regenerate handler — its only action is the View Incident <Link>.
  const blockerStart = source.indexOf('aria-label="Recovery required"');
  const blockerEnd = source.indexOf('{detailAvailable && (showGenerateManifest', blockerStart);
  const blockerJsx = source.slice(blockerStart, blockerEnd);
  expect(blockerJsx).toContain('href={incidentHref}');
  expect(blockerJsx).not.toContain('onClick');
  expect(blockerJsx).not.toContain('onGenerateManifest');
  expect(blockerJsx).not.toContain('onRegeneratePackage');
});

/* ── 3. Canonical status axes: distinct Generation / Hash / Manifest / Completeness ─ */

test('axes: package detail shows a distinct Manifest axis, never folded into Hash Verification', () => {
  const source = readPanel();
  // A dedicated Manifest row driven by the canonical manifest_status axis.
  expect(source).toContain('<span className="tableMeta">Manifest</span>');
  expect(source).toContain('manifestState');
  expect(source).toContain('const manifestState = manifestStatus(source);');
  expect(source).toContain('const MANIFEST_STATUS_LABELS');
  // manifest_status: 'missing' → "Missing".
  expect(source).toContain("missing: { label: 'Missing', variant: 'danger' }");
});

test('axes: Generation Status and Hash Verification prefer the canonical axes (Generated / Partial, Not Ready)', () => {
  const source = readPanel();
  // Generation Status honours the canonical generation_status axis verbatim.
  expect(source).toContain('const GENERATION_STATUS_LABELS');
  expect(source).toContain("generated_partial: { label: 'Generated / Partial Package', variant: 'warning' }");
  expect(source).toContain('pkg.generation_status');
  // Hash Verification honours the canonical verification_status axis; not_ready → "Not Ready".
  expect(source).toContain('const VERIFICATION_STATUS_LABELS');
  expect(source).toContain("not_ready: { label: 'Not Ready', variant: 'warning' }");
  expect(source).toContain('pkg.verification_status');
  // "Needs Evidence" is never an integrity/hash state.
  expect(source).toContain("never \"Needs Evidence\"");
});

/* ── 4. Disabled-action reasons (tooltips) ────────────────────────────────────── */

test('tooltips: Verify Integrity and Download Manifest explain WHY they are disabled', () => {
  const source = readPanel();
  expect(source).toContain(
    'This package is missing required source evidence and does not yet have a verifiable manifest.',
  );
  expect(source).toContain('No manifest is available for this diagnostic package.');
  // The reasons feed the disabled buttons' title attributes.
  expect(source).toContain('title={detailCanVerify ? undefined : verifyDisabledReason}');
  expect(source).toContain('title={detailCanManifest ? undefined : downloadManifestDisabledReason}');
  // No raw backend JSON is exposed in the reasons.
  expect(source).not.toContain('recovery_state":"evidence_required');
});

/* ── 5. Crypto-Auditing Clerk evidence-chain finding ──────────────────────────── */

test('clerk: counts ONLY the six missing source-evidence categories (not the two derived artifacts)', () => {
  // The count the agent reports is source.length — derived artifacts excluded.
  expect(resolveRecoveryRequirements(EV004_COMPLETENESS).source.length).toBe(6);
  const gate = resolveEvidenceActionState(DETAIL_EV004, READY);
  expect(gate.recoveryState).toBe('evidence_required');

  const source = readPanel();
  expect(source).toContain('const missingSourceCount = recoveryRequirements.source.length;');
  expect(source).toContain('showEvidenceChainSummary');
  // The finding wording (dynamic count + fixed explanation).
  expect(source).toContain('required source-evidence');
  expect(source).toContain(
    'verification cannot begin until the evidence chain is complete enough to',
  );
  expect(source).toContain('regenerate the package.');
  // Counting is gated on the backend recovery state, never hardcoded.
  expect(source).toContain("recoveryGate?.recoveryState === 'evidence_required'");
});

/* ── 6. No recovery-button flicker across the whole EV-2026-004 load sequence ──── */

test('flicker: Generate Manifest / Regenerate Package are false at EVERY step; only the blocker resolves', () => {
  const steps: EvidenceDetailState<EvidencePackageDetail>[] = [];
  let state = initEvidenceDetailState<EvidencePackageDetail>(ID);
  steps.push(state); // first paint
  state = run([{ type: 'select', id: ID }], state);
  steps.push(state);
  state = run([{ type: 'refreshStart', id: ID }], state);
  steps.push(state); // detail fetch in flight
  state = run([{ type: 'refreshSuccess', id: ID, detail: DETAIL_EV004 }], state);
  steps.push(state); // authoritative detail landed

  for (const s of steps) {
    const gate = resolveDetailActionState(selectAuthoritativeDetail(s), READY);
    expect(gate.showGenerate).toBe(false);
    expect(gate.showRegenerate).toBe(false);
  }
  // Only the final, authoritative step reveals the blocker — earlier steps are PENDING.
  expect(resolveDetailActionState(selectAuthoritativeDetail(steps[0]), READY).detailAvailable).toBe(false);
  const finalGate = resolveDetailActionState(selectAuthoritativeDetail(steps[3]), READY);
  expect(finalGate.detailAvailable).toBe(true);
  expect(finalGate.recoveryBlocked).toBe(true);

  // A background refresh (authHeaders/CSRF churn) must not resurrect a button.
  const refreshing = run([{ type: 'refreshStart', id: ID }], state);
  const refreshingGate = resolveDetailActionState(selectAuthoritativeDetail(refreshing), READY);
  expect(refreshingGate.showGenerate).toBe(false);
  expect(refreshingGate.recoveryBlocked).toBe(true);
});

/* ── 7. Recovery eligibility is NOT hardcoded: a future detail can permit regenerate ─ */

test('eligibility: a future authoritative detail with regenerate_package:true surfaces Regenerate Package', () => {
  // Start in the blocked EV-2026-004 state.
  let state = run([
    { type: 'select', id: ID },
    { type: 'refreshSuccess', id: ID, detail: DETAIL_EV004 },
  ]);
  const blocked = resolveDetailActionState(selectAuthoritativeDetail(state), READY);
  expect(blocked.showRegenerate).toBe(false);
  expect(blocked.recoveryBlocked).toBe(true);

  // Later, once enough evidence is restored, the backend permits regeneration. The panel
  // must reflect the NEW authoritative detail — eligibility comes from allowed_actions,
  // never a hardcoded frontend rule.
  const RESTORED: EvidencePackageDetail = {
    ...DETAIL_EV004,
    recovery_state: 'regenerate_package',
    recovery_blocked_reason: null,
    allowed_actions: {
      ...DETAIL_EV004.allowed_actions,
      regenerate_package: true,
    },
  } as EvidencePackageDetail;
  state = run([{ type: 'refreshSuccess', id: ID, detail: RESTORED }], state);

  const restored = resolveDetailActionState(selectAuthoritativeDetail(state), READY);
  expect(restored.showRegenerate).toBe(true); // Regenerate Package appears
  expect(restored.canRegenerate).toBe(true);
  expect(restored.recoveryBlocked).toBe(false); // the blocker gives way to the action
  // Generate Manifest is still NOT shown (its allowed_action stays false).
  expect(restored.showGenerate).toBe(false);

  // Source-contract: the panel renders Regenerate Package strictly from the gate's
  // showRegenerate, so this transition is driven entirely by backend allowed_actions.
  const source = readPanel();
  expect(source).toContain('showRegeneratePackage');
  expect(source).toContain('actionGate.showRegenerate');
});
