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

/* ── 3. Truthful denial: Manifest-Missing but no recovery permission ────────── */

test('manifest_missing + generate_manifest:false → button is SHOWN but DISABLED (never silently hidden)', () => {
  // A Manifest-Missing package whose user lacks recovery permission must still
  // surface the action (disabled) so the state stays legible — it is not hidden.
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

  expect(state.showGenerate).toBe(true); // visible (Manifest-Missing)
  expect(state.canGenerate).toBe(false); // but disabled (no permission)
});

/* ── 4. Recovery visibility is independent of completeness / partial state ──── */

test('recovery is gated only by manifest_missing + generate_manifest, never by completeness/partial/incident evidence', () => {
  // The gate structurally cannot be influenced by completeness score, partial
  // package status, missing incident evidence, or the disabled Verify/Download
  // Manifest actions — none are inputs. A 40% Critical, partial, Manifest-Missing
  // package with recovery permission still shows Generate Manifest enabled.
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

/* ── 6. Source-contract guard: the panel footer is governed by this gate ────── */

test('panel routes the detail drawer through resolveEvidenceActionState and gates the footer on it', () => {
  const source = read(PANEL);
  // The panel imports and uses the canonical gate.
  expect(source).toContain("from './evidence-recovery-actions'");
  expect(source).toContain('resolveEvidenceActionState(');
  // The footer Generate/Regenerate buttons are shown from the gate-derived values.
  expect(source).toContain('const detailCanGenerate = actionGate.canGenerate');
  expect(source).toContain('const detailCanRegenerate = actionGate.canRegenerate');
  expect(source).toContain('const manifestMissing = actionGate.manifestMissing');
  expect(source).toContain('(manifestMissing || detailCanGenerate)');
  // Verify Integrity / Download Manifest disabled state also flows from the gate.
  expect(source).toContain('const detailCanVerify = actionGate.canVerify');
  expect(source).toContain('const detailCanManifest = actionGate.canDownloadManifest');
});
