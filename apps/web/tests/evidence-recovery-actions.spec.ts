/**
 * Screen 9 — EV-2026-004 Manifest-Missing recovery action gate (regression).
 *
 * Behavioral tests over the canonical evidence-action gate that the detail
 * drawer consumes (app/evidence-recovery-actions.ts). These reproduce the
 * EV-2026-004 backend response and assert the recovery button surface directly,
 * plus a source-contract guard that the panel actually routes its footer through
 * this same gate (so the behavior tested here is the behavior rendered).
 *
 * Field mapping: the ticket names the fields `hash_verification` and
 * `verify_integrity`; the canonical wire contract (backend
 * get_package_allowed_actions + the EvidencePackage type) uses `integrity_status`
 * (the `manifest_missing` state) and `allowed_actions.verify`. The tests assert
 * the canonical shape the backend actually emits.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveEvidenceActionState } from '../app/evidence-recovery-actions';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const PANEL = 'app/evidence-audit-panel.tsx';

/* ── 1. EV-2026-004: authorized, unverified, Manifest-Missing package ──────── */

test('manifest_missing + generate_manifest:true → Generate Manifest visible & enabled; Verify & Download Manifest disabled', () => {
  // Equivalent to the live GET /exports/{EV-2026-004} response for an authorized
  // user: hash_verification = manifest_missing, recovery capability granted.
  const state = resolveEvidenceActionState(
    {
      integrity_status: 'manifest_missing',
      is_manifest_missing: true,
      allowed_actions: {
        generate_manifest: true,
        verify: false,
        download_manifest: false,
      },
    },
    /* ready (artifact retrievable) */ true,
  );

  // Generate Manifest is visible AND enabled.
  expect(state.showGenerate).toBe(true);
  expect(state.canGenerate).toBe(true);
  // Verify Integrity stays disabled while the manifest is missing (unchanged).
  expect(state.canVerify).toBe(false);
  // Download Manifest stays disabled while the manifest is missing (unchanged).
  expect(state.canDownloadManifest).toBe(false);
});

/* ── 2. Recovery is not offered for a package that is not Manifest-Missing ──── */

test('generate_manifest:false on a healthy package → recovery button is not shown', () => {
  const state = resolveEvidenceActionState(
    {
      integrity_status: 'hash_generated',
      is_manifest_missing: false,
      allowed_actions: {
        generate_manifest: false,
        verify: true,
        download_manifest: true,
      },
    },
    true,
  );

  expect(state.showGenerate).toBe(false);
  expect(state.canGenerate).toBe(false);
});

/* ── 3. Visibility is allowed_actions-authoritative, NEVER inferred from missing ─ */

test('manifest_missing + generate_manifest:false → Generate Manifest is NOT shown (visibility is not inferred from manifest_missing)', () => {
  // Requirement: do NOT infer Generate Manifest from is_manifest_missing. A
  // Manifest-Missing package whose backend withholds generate_manifest must NOT
  // show the button — the recovery surface shows the "Recovery required" blocker
  // instead (see the recovery_required cases below). Showing a manifest-missing
  // package's button off the missing flag is exactly the EV-2026-004 flicker.
  const state = resolveEvidenceActionState(
    {
      integrity_status: 'manifest_missing',
      is_manifest_missing: true,
      allowed_actions: {
        generate_manifest: false,
        verify: false,
        download_manifest: false,
      },
    },
    true,
  );

  expect(state.showGenerate).toBe(false); // NOT shown — no explicit permission
  expect(state.canGenerate).toBe(false);
});

/* ── 4. Recovery visibility is independent of completeness / partial state ──── */

test('recovery is gated only by allowed_actions.generate_manifest/regenerate_package, never by completeness/partial/incident evidence', () => {
  // The gate structurally cannot be influenced by completeness score, partial
  // package status, missing incident evidence, or the disabled Verify/Download
  // Manifest actions — none are inputs. A 40% Critical, partial, Manifest-Missing
  // package whose backend permits recovery still shows Generate Manifest enabled.
  const state = resolveEvidenceActionState(
    {
      integrity_status: 'manifest_missing',
      is_manifest_missing: true,
      allowed_actions: {
        generate_manifest: true,
        regenerate_package: true,
        verify: false,
        download_manifest: false,
      },
    },
    true,
  );

  expect(state.showGenerate).toBe(true);
  expect(state.canGenerate).toBe(true);
  expect(state.showRegenerate).toBe(true);
  expect(state.canRegenerate).toBe(true);
});

/* ── 5. Backend value wins over the legacy readiness fallback ───────────────── */

test('allowed_actions is authoritative: backend false overrides the ready+manifest_missing fallback', () => {
  const denied = resolveEvidenceActionState(
    { integrity_status: 'manifest_missing', is_manifest_missing: true, allowed_actions: { generate_manifest: false } },
    true,
  );
  expect(denied.canGenerate).toBe(false);

  // Only when allowed_actions is absent entirely (older API) does the fallback apply.
  const legacyFallback = resolveEvidenceActionState(
    { integrity_status: 'manifest_missing', is_manifest_missing: true },
    true,
  );
  expect(legacyFallback.canGenerate).toBe(true);
});

/* ── 5b. EV-2026-004 degraded: recovery BLOCKED (evidence_required) ─────────── */

test('recovery_state:evidence_required → blocked, Generate/Regenerate withheld, reason surfaced', () => {
  // The exact current EV-2026-004 detail response: manifest is factually missing,
  // integrity_status is needs_evidence (a SEPARATE axis), and the single recovery
  // outcome is the structured evidence_required blocker.
  const state = resolveEvidenceActionState(
    {
      integrity_status: 'needs_evidence',
      is_manifest_missing: true,
      recovery_state: 'evidence_required',
      recovery_blocked_reason:
        'Collect the missing required evidence before regenerating this package.',
      allowed_actions: {
        generate_manifest: false,
        regenerate_package: false,
        verify: false,
        download_manifest: false,
      },
    },
    true,
  );

  expect(state.manifestMissing).toBe(true);
  expect(state.recoveryState).toBe('evidence_required');
  expect(state.recoveryBlocked).toBe(true);
  // The Generate/Regenerate buttons are WITHHELD — replaced by the stable panel —
  // never shown disabled as a dead end.
  expect(state.showGenerate).toBe(false);
  expect(state.showRegenerate).toBe(false);
  expect(state.canGenerate).toBe(false);
  expect(state.canRegenerate).toBe(false);
  expect(state.recoveryBlockedReason).toContain('evidence');
});

test('recovery_state:permission_required → blocked with reason, recovery buttons withheld', () => {
  const state = resolveEvidenceActionState(
    {
      integrity_status: 'needs_evidence',
      is_manifest_missing: true,
      recovery_state: 'permission_required',
      recovery_blocked_reason:
        'You do not have permission to recover this package. Ask a workspace administrator for evidence export access.',
      allowed_actions: { generate_manifest: false, regenerate_package: false },
    },
    true,
  );
  expect(state.recoveryBlocked).toBe(true);
  expect(state.showGenerate).toBe(false);
  expect(state.showRegenerate).toBe(false);
  expect(state.recoveryBlockedReason).toContain('permission');
});

test('recovery_state:generate_manifest → NOT blocked, Generate Manifest shown & enabled', () => {
  const state = resolveEvidenceActionState(
    {
      integrity_status: 'manifest_missing',
      is_manifest_missing: true,
      recovery_state: 'generate_manifest',
      allowed_actions: {
        generate_manifest: true,
        regenerate_package: true,
        verify: false,
        download_manifest: false,
      },
    },
    true,
  );
  expect(state.recoveryBlocked).toBe(false);
  expect(state.showGenerate).toBe(true);
  expect(state.canGenerate).toBe(true);
  expect(state.recoveryState).toBe('generate_manifest');
});

/* ── 6. Source-contract guard: the panel footer is governed by this gate ────── */

test('panel routes the detail drawer through the canonical PENDING-aware gate and gates the footer on it', () => {
  const source = read(PANEL);
  // The panel resolves actions through the ONE canonical, PENDING-aware gate
  // (evidence-detail-state → resolveDetailActionState, which delegates to
  // resolveEvidenceActionState). A not-yet-loaded detail is PENDING, never a
  // summary-derived decision.
  expect(source).toContain('resolveDetailActionState(detail ?? null');
  // The footer Generate/Regenerate buttons are shown AND enabled from the gate.
  expect(source).toContain('const detailCanGenerate = actionGate.canGenerate');
  expect(source).toContain('const detailCanRegenerate = actionGate.canRegenerate');
  expect(source).toContain('const manifestMissing = actionGate.manifestMissing');
  // Button VISIBILITY flows from the SAME gate, so presence and enabled state can
  // never diverge — the second action bar gates on showGenerate/showRegenerate.
  expect(source).toContain('const showGenerateManifest = actionGate.showGenerate');
  expect(source).toContain('const showRegeneratePackage = actionGate.showRegenerate');
  expect(source).toContain('onGenerateManifest && showGenerateManifest');
  expect(source).toContain('onRegeneratePackage && showRegeneratePackage');
  // The two models are never mixed for the action gate (no summary fallback).
  expect(source).not.toContain('(manifestMissing || detailCanGenerate)');
  // Verify Integrity / Download Manifest disabled state also flows from the gate.
  expect(source).toContain('const detailCanVerify = actionGate.canVerify');
  expect(source).toContain('const detailCanManifest = actionGate.canDownloadManifest');
});

/* ── 7. Source-contract guard: blocked-recovery panel + pending copy ─────────── */

test('panel renders a stable Recovery-required panel (View Incident) gated on the canonical recovery flags', () => {
  const source = read(PANEL);
  // The blocker flags come from the SAME canonical gate as the buttons.
  expect(source).toContain('const recoveryRequired = actionGate.recoveryRequired');
  expect(source).toContain('const recoveryBlocked = actionGate.recoveryBlocked');
  // The blocker is gated on recovery_required/blocked AND neither recovery button
  // being shown (requirement: both actions false + recovery_required) — never on the
  // manifest-missing fact. The buttons win if both were signalled, so the blocker and
  // the action card are mutually exclusive.
  expect(source).toContain('const showRecoveryBlocker =');
  expect(source).toContain('(recoveryRequired || recoveryBlocked)');
  expect(source).toContain('!showGenerateManifest');
  expect(source).toContain('!showRegeneratePackage');
  expect(source).toContain('{showRecoveryBlocker && (');
  expect(source).toContain('Recovery required');
  // Primary action is exactly "View Incident" (requirement 4) — not the old
  // "View Incident / Collect Evidence" label.
  expect(source).toContain('View Incident');
  expect(source).not.toContain('View Incident / Collect Evidence');
  // The Generate/Regenerate action card renders ONLY from the authoritative detail's
  // allowed_actions visibility flags — never from the manifest-missing fact.
  expect(source).toContain('detailAvailable && (showGenerateManifest || showRegeneratePackage)');
  // While the authoritative detail is loading, the recovery surface is PENDING.
  expect(source).toContain('Loading recovery options…');
  // The manifest-missing fact must NOT gate the recovery surface any more.
  expect(source).not.toContain('manifestMissing && recoveryBlocked');
  expect(source).not.toContain('manifestMissing && !recoveryBlocked');
});
