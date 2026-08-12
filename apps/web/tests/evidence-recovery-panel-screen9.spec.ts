/**
 * Screen 9 — EV-2026-004 recovery panel: truthful, flicker-free recovery surface.
 *
 * The remaining EV-2026-004 bug was frontend presentation: "Generate Manifest"
 * briefly appeared before the authoritative detail response loaded, then vanished
 * once the backend recovery model (recovery_required / recovery_state /
 * allowed_actions) arrived. These regression tests lock in the truthful fix:
 *
 *   - Recovery-button VISIBILITY comes ONLY from the authoritative detail's
 *     allowed_actions (generate_manifest / regenerate_package) — NEVER inferred
 *     from is_manifest_missing, and NEVER from the list-row summary.
 *   - While the detail is loading, the surface shows "Loading recovery options…" —
 *     never an optimistic Generate Manifest.
 *   - For EV-2026-004 (recovery_required, both recovery actions false) the surface
 *     shows a STABLE "Recovery required" blocker with the backend reason + View
 *     Incident, while Verify Integrity / Download Manifest stay disabled.
 *
 * The behavioural tests drive the SAME PENDING-aware gate + stale-while-refresh
 * reducer the panel renders through (resolveDetailActionState / the reducer), and
 * a `showRecoveryBlocker` / `generateVisible` / `regenerateVisible` model mirrors
 * the panel's exact JSX gates. Source-contract guards then assert the panel wires
 * those same gates.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveEvidenceActionState } from '../app/evidence-recovery-actions';
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
const READY = true;

/**
 * The LIVE EV-2026-004 detail response: a completed proof bundle with no
 * retrievable manifest whose required source evidence is still missing, so the
 * single recovery outcome is the structured evidence_required blocker.
 */
const DETAIL_EV004: EvidencePackageDetail = {
  id: ID,
  status: 'completed',
  is_manifest_missing: true,
  integrity_status: 'needs_evidence',
  incident_id: 'inc-ev-004',
  // Canonical status axes (manifest availability and completeness are separate).
  generation_status: 'generated_partial',
  manifest_status: 'missing',
  verification_status: 'not_ready',
  evidence_completeness_status: 'critical',
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

/** Detail that DOES permit in-place manifest generation. */
const DETAIL_CAN_GENERATE: EvidencePackageDetail = {
  id: ID,
  status: 'completed',
  is_manifest_missing: true,
  integrity_status: 'manifest_missing',
  recovery_required: true,
  recovery_state: 'generate_manifest',
  allowed_actions: { generate_manifest: true, regenerate_package: false, verify: false, download_manifest: false },
};

/** Detail that permits the fallback superseding regeneration only. */
const DETAIL_CAN_REGENERATE: EvidencePackageDetail = {
  id: ID,
  status: 'completed',
  is_manifest_missing: true,
  integrity_status: 'manifest_missing',
  recovery_required: true,
  recovery_state: 'regenerate_package',
  allowed_actions: { generate_manifest: false, regenerate_package: true, verify: false, download_manifest: false },
};

function run(
  events: EvidenceDetailEvent<EvidencePackageDetail>[],
  start?: EvidenceDetailState<EvidencePackageDetail>,
): EvidenceDetailState<EvidencePackageDetail> {
  return events.reduce(
    (s, e) => evidenceDetailReducer(s, e),
    start ?? initEvidenceDetailState<EvidencePackageDetail>(),
  );
}

/* ── Panel-mirroring gates: these compute EXACTLY what the panel renders. ────── */

/** Generate Manifest button on screen (detail-authoritative, PENDING-aware). */
function generateVisible(state: EvidenceDetailState<EvidencePackageDetail>): boolean {
  return resolveDetailActionState(selectAuthoritativeDetail(state), READY).showGenerate;
}
/** Regenerate Package button on screen. */
function regenerateVisible(state: EvidenceDetailState<EvidencePackageDetail>): boolean {
  return resolveDetailActionState(selectAuthoritativeDetail(state), READY).showRegenerate;
}
/** The "Recovery required" blocker on screen — the panel's `showRecoveryBlocker`. */
function recoveryBlockerVisible(state: EvidenceDetailState<EvidencePackageDetail>): boolean {
  const g = resolveDetailActionState(selectAuthoritativeDetail(state), READY);
  return g.detailAvailable && (g.recoveryRequired || g.recoveryBlocked) && !g.showGenerate && !g.showRegenerate;
}
/** The neutral "Loading recovery options…" placeholder — the panel's `actionsPending`. */
function loadingVisible(state: EvidenceDetailState<EvidencePackageDetail>, detailError: string | null): boolean {
  const detailAvailable = resolveDetailActionState(selectAuthoritativeDetail(state), READY).detailAvailable;
  return !detailAvailable && !detailError;
}

/* ── A. Detail loading: no Generate Manifest, shows "Loading recovery options…" ─ */

test('A — while the detail is loading: no Generate Manifest button, and the surface shows "Loading recovery options…"', () => {
  // Exactly how the panel starts: reducer initialised for the selected id (refreshing
  // is briefly false), then the fetch effect dispatches select + refreshStart.
  const initial = initEvidenceDetailState<EvidencePackageDetail>(ID);
  const loading = run([{ type: 'select', id: ID }, { type: 'refreshStart', id: ID }], initial);

  // No recovery button of any kind is on screen before the authoritative detail lands.
  expect(generateVisible(initial)).toBe(false);
  expect(generateVisible(loading)).toBe(false);
  expect(regenerateVisible(loading)).toBe(false);
  expect(recoveryBlockerVisible(loading)).toBe(false);
  // The neutral loading placeholder is what shows instead — never an optimistic button.
  expect(loadingVisible(initial, null)).toBe(true);
  expect(loadingVisible(loading, null)).toBe(true);

  // Source-contract: the loading placeholder text is gated on the PENDING flag, and
  // the footer's Generate Manifest lives in the NON-pending branch only.
  const source = readPanel();
  expect(source).toContain('{actionsPending && (');
  expect(source).toContain('Loading recovery options…');
  expect(source).toContain('const actionsPending = !detailAvailable');
});

/* ── B. EV-2026-004 detail: blocker shown, both recovery actions withheld ─────── */

test('B — EV-2026-004 authoritative detail: Generate/Regenerate absent, Recovery-required blocker + reason + View Incident, Verify & Download Manifest disabled', () => {
  const gate = resolveDetailActionState(DETAIL_EV004, READY);

  // Recovery model is honoured verbatim.
  expect(gate.detailAvailable).toBe(true);
  expect(gate.recoveryRequired).toBe(true);
  expect(gate.recoveryState).toBe('evidence_required');
  expect(gate.recoveryBlocked).toBe(true);

  // Neither recovery button is shown (both allowed_actions false).
  expect(gate.showGenerate).toBe(false); // Generate Manifest ABSENT
  expect(gate.showRegenerate).toBe(false); // Regenerate Package ABSENT

  // The Recovery-required blocker is what renders instead, with the backend reason.
  const loaded = run([{ type: 'select', id: ID }, { type: 'refreshSuccess', id: ID, detail: DETAIL_EV004 }]);
  expect(recoveryBlockerVisible(loaded)).toBe(true);
  expect(gate.recoveryBlockedReason).toBe(
    'Collect the missing required evidence before regenerating this package.',
  );
  // View Incident is available (evidence_required, not a permission block, incident linked).
  expect(gate.recoveryState).not.toBe('permission_required');
  expect(DETAIL_EV004.incident_id).toBeTruthy();

  // Verify Integrity and Download Manifest remain disabled while verification is
  // not_ready and the manifest is missing.
  expect(gate.canVerify).toBe(false);
  expect(gate.canDownloadManifest).toBe(false);

  // Source-contract: the panel renders the stable blocker (title + View Incident),
  // gated on showRecoveryBlocker, and surfaces the backend reason.
  const source = readPanel();
  expect(source).toContain('const showRecoveryBlocker =');
  expect(source).toContain('{showRecoveryBlocker && (');
  expect(source).toContain('Recovery required');
  expect(source).toContain('View Incident');
  expect(source).not.toContain('View Incident / Collect Evidence');
  expect(source).toContain('recoveryBlockedReason');
});

/* ── C. Detail with generate_manifest:true → Generate Manifest visible ────────── */

test('C — detail with allowed_actions.generate_manifest:true → Generate Manifest is shown & enabled', () => {
  const gate = resolveDetailActionState(DETAIL_CAN_GENERATE, READY);
  expect(gate.showGenerate).toBe(true);
  expect(gate.canGenerate).toBe(true);
  // The blocker is suppressed while a real recovery action is available.
  const loaded = run([{ type: 'select', id: ID }, { type: 'refreshSuccess', id: ID, detail: DETAIL_CAN_GENERATE }]);
  expect(generateVisible(loaded)).toBe(true);
  expect(recoveryBlockerVisible(loaded)).toBe(false);
});

/* ── D. Detail with regenerate_package:true → Regenerate Package visible ──────── */

test('D — detail with allowed_actions.regenerate_package:true → Regenerate Package is shown & enabled', () => {
  const gate = resolveDetailActionState(DETAIL_CAN_REGENERATE, READY);
  expect(gate.showRegenerate).toBe(true);
  expect(gate.canRegenerate).toBe(true);
  expect(gate.showGenerate).toBe(false);
  const loaded = run([{ type: 'select', id: ID }, { type: 'refreshSuccess', id: ID, detail: DETAIL_CAN_REGENERATE }]);
  expect(regenerateVisible(loaded)).toBe(true);
  expect(recoveryBlockerVisible(loaded)).toBe(false);
});

/* ── E. No recovery-button flicker before the detail loads ────────────────────── */

test('E — Generate Manifest never flickers: it is false at EVERY step of the EV-2026-004 load sequence', () => {
  // Reproduce the exact deployed sequence and assert Generate Manifest is NEVER
  // visible: not on first paint (reducer initialised, refreshing briefly false), not
  // while the detail fetch is in flight, and not after the authoritative EV-2026-004
  // detail (generate_manifest:false) lands. It is false throughout — no 1s flash.
  const steps: EvidenceDetailState<EvidencePackageDetail>[] = [];
  let state = initEvidenceDetailState<EvidencePackageDetail>(ID);
  steps.push(state); // first paint
  state = run([{ type: 'select', id: ID }], state);
  steps.push(state); // selection reconciled
  state = run([{ type: 'refreshStart', id: ID }], state);
  steps.push(state); // detail fetch in flight
  state = run([{ type: 'refreshSuccess', id: ID, detail: DETAIL_EV004 }], state);
  steps.push(state); // authoritative detail landed

  for (const s of steps) {
    expect(generateVisible(s)).toBe(false);
    expect(regenerateVisible(s)).toBe(false);
  }
  // Only the FINAL, authoritative step reveals the recovery surface — as the blocker,
  // never as a button. Every earlier step is PENDING (loading), not a resolved state.
  expect(recoveryBlockerVisible(steps[0])).toBe(false);
  expect(recoveryBlockerVisible(steps[3])).toBe(true);

  // A background detail refresh (authHeaders/CSRF churn) must not resurrect a button:
  // stale-while-refresh keeps the EV-2026-004 blocker; Generate Manifest stays false.
  const refreshing = run([{ type: 'refreshStart', id: ID }], state);
  expect(generateVisible(refreshing)).toBe(false);
  expect(recoveryBlockerVisible(refreshing)).toBe(true);
  const refreshed = run([{ type: 'refreshSuccess', id: ID, detail: DETAIL_EV004 }], refreshing);
  expect(generateVisible(refreshed)).toBe(false);
  expect(recoveryBlockerVisible(refreshed)).toBe(true);
});

/* ── Requirement 1 guard: visibility is not inferred from the manifest-missing fact ─ */

test('visibility is allowed_actions-authoritative: a manifest_missing detail with no explicit permission shows NO recovery button', () => {
  // is_manifest_missing:true but no generate_manifest / regenerate_package permission
  // → NEITHER button is shown. (The blocker covers recovery guidance instead.)
  const gate = resolveEvidenceActionState(
    { is_manifest_missing: true, integrity_status: 'manifest_missing', recovery_required: true, allowed_actions: {} },
    READY,
  );
  expect(gate.showGenerate).toBe(false);
  expect(gate.showRegenerate).toBe(false);
  // recovery_required with no permitted action → the surface is BLOCKED (not empty).
  expect(gate.recoveryBlocked).toBe(true);
});
