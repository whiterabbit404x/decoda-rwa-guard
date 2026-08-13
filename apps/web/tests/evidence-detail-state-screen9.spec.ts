/**
 * Screen 9 — EV-2026-004 "Generate Manifest" flicker regression.
 *
 * Reproduces the exact state sequence behind the deployed flicker and locks in
 * the fix. The bug: the detail drawer's manifest/recovery/verify gate was
 * resolved from `detail ?? pkg` (a field-by-field fallback to the LIST row).
 * The list and detail models legitimately disagree for a Manifest-Missing
 * package —
 *
 *   LIST row (metadata):  integrity_status: 'hash_generated', is_manifest_missing:false,
 *                         allowed_actions WITHOUT generate_manifest
 *   DETAIL   (storage):   integrity_status: 'manifest_missing', is_manifest_missing:true,
 *                         allowed_actions.generate_manifest: true
 *
 * — so whenever the authoritative detail was momentarily absent (before the
 * first response, or when the detail-fetch effect re-ran as `authHeaders`
 * changed once the async CSRF/session tokens resolved ~1s after mount, or on a
 * failed refresh), the gate fell back to the list row and hid Generate Manifest.
 *
 * These tests drive the pure state machine + gate (evidence-detail-state) that
 * now owns the fix, plus a source-contract guard that the panel actually routes
 * its footer through the detail-only gate and stale-while-refresh reducer.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveEvidenceActionState } from '../app/evidence-recovery-actions';
import {
  evidenceDetailReducer,
  initEvidenceDetailState,
  isEvidenceProgressPollingState,
  resolveDetailActionState,
  selectAuthoritativeDetail,
  type EvidenceDetailEvent,
  type EvidenceDetailState,
  type EvidencePackageDetail,
} from '../app/evidence-detail-state';

/* ── EV-2026-004 canonical models (list vs detail intentionally disagree) ──── */

const ID = 'ev-2026-004';

/** LIST/SUMMARY row: metadata-derived → NOT missing, no recovery action. */
const SUMMARY = {
  id: ID,
  status: 'completed',
  integrity_status: 'hash_generated',
  is_manifest_missing: false,
  // NB: no `generate_manifest` key at all — the summary cannot grant recovery.
  allowed_actions: { verify: true, download_manifest: true },
};

/** DETAIL: storage-authoritative → manifest_missing, recovery enabled. */
const DETAIL: EvidencePackageDetail = {
  id: ID,
  status: 'completed',
  integrity_status: 'manifest_missing',
  is_manifest_missing: true,
  allowed_actions: { generate_manifest: true, verify: false, download_manifest: false },
};

/** DETAIL after a real recovery: manifest now present → recovery legitimately off. */
const DETAIL_RECOVERED: EvidencePackageDetail = {
  id: ID,
  status: 'completed',
  integrity_status: 'hash_generated',
  is_manifest_missing: false,
  allowed_actions: { generate_manifest: false, verify: true, download_manifest: true },
};

const READY = true;

/** Fold a sequence of events over the initial state. */
function run(
  events: EvidenceDetailEvent<EvidencePackageDetail>[],
  start?: EvidenceDetailState<EvidencePackageDetail>,
): EvidenceDetailState<EvidencePackageDetail> {
  return events.reduce(
    (s, e) => evidenceDetailReducer(s, e),
    start ?? initEvidenceDetailState<EvidencePackageDetail>(),
  );
}

/** The button surface the drawer renders, resolved exactly as the panel does. */
function generateVisible(state: EvidenceDetailState<EvidencePackageDetail>): boolean {
  return resolveDetailActionState(selectAuthoritativeDetail(state), READY).showGenerate;
}

/* ── Test A: a LIST refresh must not hide a detail-gated Generate Manifest ─── */

test('A — detail says generate_manifest:true → visible; a list refresh (summary-only) keeps it visible', () => {
  const loaded = run([
    { type: 'select', id: ID },
    { type: 'refreshStart', id: ID },
    { type: 'refreshSuccess', id: ID, detail: DETAIL },
  ]);
  // 1-3: rendered with authoritative detail → Generate Manifest visible.
  expect(generateVisible(loaded)).toBe(true);

  // 4: the exports LIST refreshes. It updates the table/summary only and does
  // NOT flow through the detail reducer — there is no event that overwrites
  // `detail` from a summary row. The authoritative detail is unchanged...
  const afterListRefresh = loaded; // (a list refresh dispatches no detail event)
  expect(selectAuthoritativeDetail(afterListRefresh)).toEqual(DETAIL);

  // 5: ...so Generate Manifest is STILL visible — even though gating on the
  // summary row (which lacks generate_manifest / is not missing) would hide it.
  expect(resolveDetailActionState(SUMMARY, READY).showGenerate).toBe(false); // the trap
  expect(generateVisible(afterListRefresh)).toBe(true); // the fix
});

/* ── Test B: the button stays put across a same-package refresh (no flicker) ─ */

test('B — button remains visible while a same-package detail refresh is in flight and after it resolves true', () => {
  const loaded = run([
    { type: 'select', id: ID },
    { type: 'refreshStart', id: ID },
    { type: 'refreshSuccess', id: ID, detail: DETAIL },
  ]);
  expect(generateVisible(loaded)).toBe(true);

  // 2-3: a refresh starts (authHeaders/CSRF churn). Stale-while-refresh keeps the
  // authoritative detail on screen — the button does not disappear mid-refresh.
  const refreshing = run([{ type: 'refreshStart', id: ID }], loaded);
  expect(refreshing.refreshing).toBe(true);
  expect(selectAuthoritativeDetail(refreshing)).toEqual(DETAIL);
  expect(generateVisible(refreshing)).toBe(true);

  // 4-5: the refresh returns generate_manifest:true again → still visible.
  const resolved = run([{ type: 'refreshSuccess', id: ID, detail: DETAIL }], refreshing);
  expect(resolved.refreshing).toBe(false);
  expect(generateVisible(resolved)).toBe(true);
});

/* ── Test C: the button is removed ONLY when a new authoritative detail says so ─ */

test('C — button is removed only when a NEW authoritative detail returns generate_manifest:false', () => {
  const loaded = run([
    { type: 'select', id: ID },
    { type: 'refreshSuccess', id: ID, detail: DETAIL },
  ]);
  expect(generateVisible(loaded)).toBe(true);

  // A real recovery completed and the fresh detail is authoritative (manifest now
  // present). Only THEN does Generate Manifest go away.
  const recovered = run([{ type: 'refreshSuccess', id: ID, detail: DETAIL_RECOVERED }], loaded);
  expect(selectAuthoritativeDetail(recovered)).toEqual(DETAIL_RECOVERED);
  expect(generateVisible(recovered)).toBe(false);
});

/* ── Test D: manifest_missing is terminal for progress polling ──────────────── */

test('D — manifest_missing is NOT an in-progress polling state (poller never churns its detail)', () => {
  expect(isEvidenceProgressPollingState({ status: 'completed', integrity_status: 'manifest_missing' })).toBe(false);
  // The list-level projection of the same package (hash_generated) is terminal too.
  expect(isEvidenceProgressPollingState({ status: 'completed', integrity_status: 'hash_generated' })).toBe(false);
  // Only genuine generation lifecycle states are in-progress.
  for (const s of ['queued', 'pending', 'building', 'running']) {
    expect(isEvidenceProgressPollingState({ status: s, integrity_status: '' })).toBe(true);
  }
  for (const i of ['building', 'hashing', 'verifying']) {
    expect(isEvidenceProgressPollingState({ status: 'completed', integrity_status: i })).toBe(true);
  }
  // Other resting states never poll.
  for (const i of ['verified', 'integrity_failed', 'failed', 'legacy_export', 'superseded', 'needs_evidence']) {
    expect(isEvidenceProgressPollingState({ status: 'completed', integrity_status: i })).toBe(false);
  }
});

/* ── Test E: a list-row replacement must not overwrite selected detail state ── */

test('E — re-affirming the selection (as a list refresh re-renders) never overwrites the authoritative detail', () => {
  const loaded = run([
    { type: 'select', id: ID },
    { type: 'refreshSuccess', id: ID, detail: DETAIL },
  ]);

  // A list refresh re-renders the panel; the effect re-affirms the same selection
  // id. `select` with an unchanged id is a no-op — detail is preserved verbatim.
  const reaffirmed = run([{ type: 'select', id: ID }], loaded);
  expect(reaffirmed).toBe(loaded); // literally the same state object (no churn)
  expect(selectAuthoritativeDetail(reaffirmed)).toEqual(DETAIL);
  expect(generateVisible(reaffirmed)).toBe(true);

  // And a detail belonging to a DIFFERENT package never leaks onto this selection:
  // selecting another id drops the retained detail (pending), rather than showing
  // EV-2026-004's manifest_missing under the wrong package.
  const switched = run([{ type: 'select', id: 'ev-other' }], loaded);
  expect(selectAuthoritativeDetail(switched)).toBeNull();
});

/* ── Test F: visibility is strictly allowed_actions-driven + refresh retention ─ */

test('F — Generate Manifest visibility comes ONLY from allowed_actions.generate_manifest; a retained detail survives a failed refresh', () => {
  // (a) At the gate: visibility is NEVER inferred from the manifest-missing fact. A
  // manifest_missing detail with NO explicit generate_manifest does NOT show the
  // button (requirement 1) — only an explicit generate_manifest:true does. The
  // ENABLED capability still legacy-falls-back for old responses, but that never
  // makes the button appear.
  const undefinedGen = resolveEvidenceActionState(
    { integrity_status: 'manifest_missing', is_manifest_missing: true, allowed_actions: {} },
    READY,
  );
  expect(undefinedGen.showGenerate).toBe(false); // not shown — no explicit permission
  const explicitGen = resolveEvidenceActionState(
    { integrity_status: 'manifest_missing', is_manifest_missing: true, allowed_actions: { generate_manifest: true } },
    READY,
  );
  expect(explicitGen.showGenerate).toBe(true); // shown — backend explicitly permits

  // (b) Across a refresh: a prior authoritative detail with generate_manifest:true is
  // retained, and a failed refresh (no new authoritative value) must NOT downgrade it
  // to a hidden state. The last-known detail — and the visible recovery action —
  // survive, with only a warning surfaced.
  const loaded = run([
    { type: 'select', id: ID },
    { type: 'refreshSuccess', id: ID, detail: DETAIL },
  ]);
  const afterFailedRefresh = run(
    [{ type: 'refreshStart', id: ID }, { type: 'refreshError', id: ID, message: 'network' }],
    loaded,
  );
  expect(selectAuthoritativeDetail(afterFailedRefresh)).toEqual(DETAIL);
  expect(afterFailedRefresh.error).toBe('network');
  expect(generateVisible(afterFailedRefresh)).toBe(true);
});

/* ── Pending semantics: detail-absent is refreshing, never a false denial ───── */

test('pending — before the first detail response, gating is PENDING (not an authoritative false)', () => {
  const firstLoad = run([{ type: 'select', id: ID }, { type: 'refreshStart', id: ID }]);
  const gate = resolveDetailActionState(selectAuthoritativeDetail(firstLoad), READY);
  expect(gate.detailAvailable).toBe(false); // "we don't know yet", not "no recovery"
  expect(gate.showGenerate).toBe(false);
  expect(firstLoad.refreshing).toBe(true);
});

/* ── Source-contract guard: the panel wires the fix, not just this module ───── */

test('panel gates the detail drawer on the DETAIL model only and uses the stale-while-refresh reducer', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'app/evidence-audit-panel.tsx'),
    'utf-8',
  );
  // Uses the canonical detail-state module.
  expect(source).toContain("from './evidence-detail-state'");
  expect(source).toContain('evidenceDetailReducer');
  expect(source).toContain('selectAuthoritativeDetail(detailState)');
  // Stale-while-refresh: detail is refreshed via dispatched events, never cleared
  // eagerly. The old eager null-on-refetch setter is gone.
  expect(source).toContain("dispatchDetail({ type: 'refreshSuccess'");
  expect(source).toContain("dispatchDetail({ type: 'refreshError'");
  expect(source).not.toContain('setSelectedDetail(null)');
  // The action gate is DETAIL-authoritative and PENDING-aware: it is resolved from
  // the detail model only (null → PENDING, every action withheld), never from a
  // list-row summary fallback (`detail ?? pkg`).
  expect(source).toContain('resolveDetailActionState(detail ?? null');
  expect(source).not.toContain('integrity_status: detail?.integrity_status ?? pkg.integrity_status');
  expect(source).not.toContain('const detailActions = (detail ?? pkg).allowed_actions');
  // Section 5: exactly one authoritative action source — the detail's allowed_actions.
  expect(source).toContain('const detailActions = detail?.allowed_actions;');
  // The gate's readiness fallback is detail-derived too — the action gate never
  // reads isPackageReady(pkg) (the list-row summary). This keeps recovery
  // visibility 100% detail-authoritative: no summary field, not even `ready`, can
  // influence whether Generate Manifest is shown.
  expect(source).toContain(
    'resolveDetailActionState(detail ?? null, detail ? isPackageReady(detail) : false)',
  );
  // Development-only assertion guards the flicker invariant (section 10).
  expect(source).toContain("'[EV-2026-004]");
  // Polling terminality routes through the single shared predicate.
  expect(source).toContain('isEvidenceProgressPollingState');
});

/* ── Section 5 — the detail-fetch effect keys on the STABLE id, not the object ─ */

test('detail-fetch effect keys on selectedPkgId (stable), never the selectedPkg OBJECT identity', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'app/evidence-audit-panel.tsx'),
    'utf-8',
  );
  // The effect resolves the id from the stable string identifier and reconciles the
  // reducer selection with it — a same-id re-run is a no-op that retains the detail.
  expect(source).toContain('const id = selectedPkgId;');
  expect(source).toContain("dispatchDetail({ type: 'select', id });");
  // Its dependency array is the stable id (+ auth/reload), NEVER the selectedPkg
  // object. `selectedPkg` is re-derived (`packages.find(...)`) on every list/poll
  // refresh, so a NEW object identity is minted every few seconds; keying the
  // detail-fetch effect on it would tear down and refetch the detail on every
  // background refresh — the churn that (paired with any detail-clearing) removed
  // "Generate Manifest" with no user action. Lock the effect onto the id only.
  expect(source).toContain('}, [selectedPkgId, authHeaders, reloadKey]);');
  expect(source).not.toContain('[selectedPkg,');
  expect(source).not.toContain(', selectedPkg,');
  expect(source).not.toContain(', selectedPkg]');
});

/* ── Section 6 — a list refresh never overwrites the authoritative detail ─────── */

test('the canonical list-refresh path (loadExportsOnce) updates rows/metrics only, never the selected detail', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'app/evidence-audit-panel.tsx'),
    'utf-8',
  );
  // Isolate the shared list-refresh callback used by the create loop and the async
  // progress poller. It must feed the LIST/SUMMARY models (packages/metrics) only —
  // there is NO reducer path from a summary row to `detail`, so a background list
  // refresh cannot replace the storage-authoritative detail with a metadata summary.
  const start = source.indexOf('const loadExportsOnce = useCallback');
  expect(start).toBeGreaterThan(-1);
  const end = source.indexOf('}, [authHeaders, urlPackageId, urlActionId, urlIncidentId]);', start);
  expect(end).toBeGreaterThan(start);
  const loadExportsOnce = source.slice(start, end);
  expect(loadExportsOnce).toContain('setPackages(');
  // The summary path must not touch the detail state machine at all.
  expect(loadExportsOnce).not.toContain('dispatchDetail');
  expect(loadExportsOnce).not.toContain('setSelectedDetail');
});
