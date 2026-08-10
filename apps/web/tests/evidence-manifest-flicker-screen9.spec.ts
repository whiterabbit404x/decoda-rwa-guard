/**
 * Screen 9 — EV-2026-004 "Generate Manifest" flicker: production-sequence
 * reproduction + 30-second stability (regression).
 *
 * These drive the EXACT functions the detail drawer renders through — the
 * stale-while-refresh reducer (evidence-detail-state) plus the PENDING-aware
 * action gate (resolveDetailActionState) — so the behaviour asserted here is the
 * behaviour the panel produces. The panel's button VISIBILITY is:
 *
 *     showGenerate = resolveDetailActionState(selectAuthoritativeDetail(state), READY).showGenerate
 *
 * The observed deployed flicker was: Generate Manifest appears for ~1s and then
 * disappears with no click. Root cause (historical): the detail-fetch effect
 * re-ran ~1s after mount (its `authHeaders` identity flips as the async
 * CSRF/session tokens resolve) and the refetch did `setSelectedDetail(null)`,
 * dropping the action gate onto the LIST-row summary (`detail ?? pkg`) whose
 * manifest state is metadata-derived (hash_generated / not-missing / no
 * generate_manifest). The reducer now retains the authoritative detail across
 * every refresh and the gate reads the DETAIL model only, so the button cannot be
 * removed by an ordinary list/detail refresh — only by a NEW authoritative detail
 * that itself says generate_manifest:false.
 *
 * Field mapping: the ticket names `hash_verification` / `verify_integrity`; the
 * canonical wire contract uses `integrity_status` (the `manifest_missing` state)
 * and `allowed_actions.verify`. The models below use the wire contract.
 */
import { expect, test } from '@playwright/test';

import {
  evidenceDetailReducer,
  initEvidenceDetailState,
  resolveDetailActionState,
  selectAuthoritativeDetail,
  type EvidenceDetailEvent,
  type EvidenceDetailState,
  type EvidencePackageDetail,
  type EvidencePackageSummary,
} from '../app/evidence-detail-state';

const ID = 'pkg-004';
const READY = true;

/** Initial authoritative DETAIL for EV-2026-004: manifest_missing, recovery on. */
const DETAIL_INITIAL: EvidencePackageDetail = {
  id: ID,
  status: 'completed',
  integrity_status: 'manifest_missing',
  is_manifest_missing: true,
  allowed_actions: { generate_manifest: true, verify: false, download_manifest: false },
};

/** LIST summary as the ticket frames it: manifest_missing, allowed_actions omitted. */
const SUMMARY_TICKET: EvidencePackageSummary = {
  id: ID,
  integrity_status: 'manifest_missing',
  // allowed_actions intentionally omitted — a summary cannot grant/deny recovery.
};

/** The REAL EV-2026-004 list projection (metadata-derived): NOT missing. Gating on
 *  this is the trap that used to hide the button. */
const SUMMARY_REAL: EvidencePackageSummary = {
  id: ID,
  integrity_status: 'hash_generated',
  is_manifest_missing: false,
  allowed_actions: { verify: true, download_manifest: true },
};

/** DETAIL after a real recovery: manifest now present → recovery legitimately off. */
const DETAIL_RECOVERED: EvidencePackageDetail = {
  id: ID,
  status: 'completed',
  integrity_status: 'hash_generated',
  is_manifest_missing: false,
  allowed_actions: { generate_manifest: false, verify: true, download_manifest: true },
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

/** Exactly how the panel resolves whether Generate Manifest is on screen. */
function generateVisible(state: EvidenceDetailState<EvidencePackageDetail>): boolean {
  return resolveDetailActionState(selectAuthoritativeDetail(state), READY).showGenerate;
}

/* ── Section 11 — reproduce the production sequence exactly ──────────────────── */

test('11 — production sequence: visible → list refresh → detail refresh (loading) → detail true → stays visible; removed only on authoritative false', () => {
  // Initial detail: manifest_missing + generate_manifest:true → visible.
  let state = run([
    { type: 'select', id: ID },
    { type: 'refreshStart', id: ID },
    { type: 'refreshSuccess', id: ID, detail: DETAIL_INITIAL },
  ]);
  expect(generateVisible(state)).toBe(true);

  // Simulate a list refresh with a summary (allowed_actions omitted). A list refresh
  // updates the table/summary only — it dispatches NO detail event — so the
  // authoritative detail is untouched and Generate Manifest is STILL visible.
  // (Gating on the summary's undefined generate_manifest must not hide it: section 9.)
  const afterListRefresh = state; // a list refresh dispatches no detail event
  expect(selectAuthoritativeDetail(afterListRefresh)).toEqual(DETAIL_INITIAL);
  expect(generateVisible(afterListRefresh)).toBe(true);
  // The trap the fix removes: gating on the REAL metadata summary (not-missing)
  // would hide it — the detail gate is what keeps it visible.
  expect(resolveDetailActionState(SUMMARY_REAL, READY).showGenerate).toBe(false);
  expect(resolveDetailActionState(SUMMARY_TICKET, READY).detailAvailable).toBe(true);

  // Detail refresh begins (authHeaders/CSRF churn). Stale-while-refresh keeps the
  // authoritative detail on screen — the button does NOT disappear mid-refresh.
  state = run([{ type: 'refreshStart', id: ID }], afterListRefresh);
  expect(state.refreshing).toBe(true);
  expect(selectAuthoritativeDetail(state)).toEqual(DETAIL_INITIAL);
  expect(generateVisible(state)).toBe(true);

  // Detail refresh returns generate_manifest:true again → remains visible.
  state = run([{ type: 'refreshSuccess', id: ID, detail: DETAIL_INITIAL }], state);
  expect(state.refreshing).toBe(false);
  expect(generateVisible(state)).toBe(true);

  // ONLY when a NEW authoritative detail explicitly returns generate_manifest:false
  // (a real recovery landed) may the button disappear.
  const recovered = run([{ type: 'refreshSuccess', id: ID, detail: DETAIL_RECOVERED }], state);
  expect(selectAuthoritativeDetail(recovered)).toEqual(DETAIL_RECOVERED);
  expect(generateVisible(recovered)).toBe(false);
});

test('11b — first-load PENDING withholds every action (Verify/Download never blink enabled) but is not an authoritative denial', () => {
  // Before the first detail response, detail is null → PENDING. Every action is
  // withheld, but this is "we don't know yet", NOT generate_manifest:false. This is
  // why Verify Integrity / Download Manifest do not flash enabled on first load.
  const firstLoad = run([{ type: 'select', id: ID }, { type: 'refreshStart', id: ID }]);
  const gate = resolveDetailActionState(selectAuthoritativeDetail(firstLoad), READY);
  expect(gate.detailAvailable).toBe(false);
  expect(gate.showGenerate).toBe(false);
  expect(gate.canVerify).toBe(false);
  expect(gate.canDownloadManifest).toBe(false);
  expect(firstLoad.refreshing).toBe(true);
});

/* ── Section 12 — 30-second stability across repeated background refreshes ───── */

test('12 — Generate Manifest stays visible through many list / metrics / audit refreshes and same-package detail refreshes', () => {
  let state = run([
    { type: 'select', id: ID },
    { type: 'refreshStart', id: ID },
    { type: 'refreshSuccess', id: ID, detail: DETAIL_INITIAL },
  ]);
  expect(generateVisible(state)).toBe(true);

  // ~30s of activity: periodic list refresh, metrics refresh and audit-count refresh
  // all update sibling React state ONLY — none dispatch a detail event, so the
  // authoritative detail (and the button) is unaffected. Interleave same-package
  // detail refresh cycles (the authHeaders/reloadKey churn) to prove stale-while-
  // refresh never drops the button between refreshStart and refreshSuccess.
  for (let tick = 0; tick < 12; tick += 1) {
    // list / metrics / audit refresh — no detail event; the reducer state is the
    // same object, so the button cannot move.
    const beforeBackground = state;
    expect(generateVisible(beforeBackground)).toBe(true);
    expect(selectAuthoritativeDetail(beforeBackground)).toEqual(DETAIL_INITIAL);

    // Re-affirming the selection (a list re-render dispatches select with the same
    // id) is a no-op that preserves the authoritative detail verbatim.
    const reaffirmed = run([{ type: 'select', id: ID }], state);
    expect(reaffirmed).toBe(state);
    expect(generateVisible(reaffirmed)).toBe(true);

    // Every third tick, a full same-package detail refresh runs.
    if (tick % 3 === 0) {
      const refreshing = run([{ type: 'refreshStart', id: ID }], state);
      expect(refreshing.refreshing).toBe(true);
      expect(generateVisible(refreshing)).toBe(true); // visible mid-refresh
      state = run([{ type: 'refreshSuccess', id: ID, detail: DETAIL_INITIAL }], refreshing);
      expect(generateVisible(state)).toBe(true); // visible after refresh resolves
    } else {
      state = reaffirmed;
    }
  }

  // A failed refresh at the end keeps the last-known detail (+ warning), never hides
  // the recovery action.
  const afterFailure = run(
    [{ type: 'refreshStart', id: ID }, { type: 'refreshError', id: ID, message: 'network' }],
    state,
  );
  expect(afterFailure.error).toBe('network');
  expect(selectAuthoritativeDetail(afterFailure)).toEqual(DETAIL_INITIAL);
  expect(generateVisible(afterFailure)).toBe(true);
});
