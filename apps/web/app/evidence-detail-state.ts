/**
 * Screen 9 — selected-package DETAIL state machine (stale-while-refresh).
 *
 * A selected evidence package has two distinct data levels that MUST NOT be
 * mixed when gating recovery / verify / download-manifest actions:
 *
 *   LIST / SUMMARY model  (GET /exports row → `selectedPkg`)
 *     Manifest state here is *metadata-derived*: a recorded manifest SHA-256
 *     counts as "manifest present" because the list projection cannot afford a
 *     per-row storage read. So a package whose manifest bytes are NOT actually
 *     retrievable still shows on the list as `integrity_status: hash_generated`,
 *     `is_manifest_missing: false`, `allowed_actions.generate_manifest: false`.
 *
 *   DETAIL model  (GET /exports/{id} → `selectedPkgDetail`)
 *     Manifest state here is *storage-authoritative*: the artifact bytes were
 *     read, so `manifest_missing` / `generate_manifest: true` reflect reality.
 *
 * These two models legitimately DISAGREE for a Manifest-Missing package
 * (EV-2026-004): list says "hash_generated / not missing", detail says
 * "manifest_missing / recoverable". The detail-drawer action bar previously
 * resolved its gate from `detail ?? pkg` (field-by-field fallback to the list
 * row). Whenever `selectedPkgDetail` was momentarily absent — before the first
 * detail response arrived, and again every time the detail-fetch effect re-ran
 * because its `authHeaders` dependency changed as the async CSRF/session tokens
 * resolved ~1s after mount — the gate fell back to the list row and read
 * `is_manifest_missing: false`, hiding "Generate Manifest". That is the observed
 * appears-then-disappears flicker.
 *
 * This module owns the fix in ONE testable place:
 *  - The authoritative detail is RETAINED across refreshes (stale-while-refresh)
 *    and is only dropped when the SELECTED package actually changes — a list
 *    refresh never overwrites it (there is no reducer path from a summary row to
 *    `detail`).
 *  - A failed refresh keeps the last-known detail and records a warning instead
 *    of nulling valid actions.
 *  - `resolveDetailActionState(null)` is a PENDING state (not-loaded / refreshing),
 *    explicitly distinct from an authoritative `generate_manifest: false`. Callers
 *    render "Refreshing…" for pending rather than collapsing it into a list-row
 *    action set.
 */
import {
  resolveEvidenceActionState,
  type EvidenceActionSource,
  type EvidenceActionState,
} from './evidence-recovery-actions';

/**
 * LIST/SUMMARY row (GET /exports). Its manifest fields are metadata-derived and
 * are NOT authoritative for the manifest-recovery gate. Kept as a distinct type
 * so callers cannot accidentally feed a summary row into the detail gate.
 */
export type EvidencePackageSummary = EvidenceActionSource & { id?: string; status?: string | null };

/**
 * DETAIL response (GET /exports/{id}). Its manifest fields are storage-authoritative.
 * The recovery / verify / download-manifest gate is derived ONLY from this model.
 */
export type EvidencePackageDetail = EvidenceActionSource & { id?: string; status?: string | null };

/** Stale-while-refresh state for the currently selected package's detail. */
export type EvidenceDetailState<D> = {
  /** Identity of the selected package (empty when nothing is selected). */
  selectedId: string;
  /** Last authoritative detail response, retained across refreshes. */
  detail: D | null;
  /** Which `selectedId` the retained `detail` was fetched for ('' when none). */
  detailId: string;
  /** A detail fetch for `selectedId` is currently in flight. */
  refreshing: boolean;
  /** Last refresh for `selectedId` failed; any retained `detail` is stale. */
  error: string | null;
};

export function initEvidenceDetailState<D>(selectedId = ''): EvidenceDetailState<D> {
  return { selectedId, detail: null, detailId: '', refreshing: false, error: null };
}

export type EvidenceDetailEvent<D> =
  /** The selected package changed (or was re-affirmed) to `id`. */
  | { type: 'select'; id: string }
  /** A detail fetch for `id` started. */
  | { type: 'refreshStart'; id: string }
  /** A detail fetch for `id` returned `detail` (null = ok-but-empty body). */
  | { type: 'refreshSuccess'; id: string; detail: D | null }
  /** A detail fetch for `id` failed. */
  | { type: 'refreshError'; id: string; message: string };

/**
 * The detail state transition. Pure and framework-free so the EV-2026-004
 * sequence can be driven directly in tests (see evidence-detail-state-screen9).
 */
export function evidenceDetailReducer<D>(
  state: EvidenceDetailState<D>,
  event: EvidenceDetailEvent<D>,
): EvidenceDetailState<D> {
  switch (event.type) {
    case 'select': {
      // Same selection re-affirmed (e.g. an `authHeaders`/`reloadKey`-driven
      // effect re-run): keep everything, including the authoritative detail.
      if (event.id === state.selectedId) return state;
      // New selection: retain detail only if we already hold it for this exact
      // id (re-selecting a package we still have); otherwise drop it, because a
      // retained detail belongs to the previously-selected package and must not
      // leak onto a different one.
      const keep = state.detailId === event.id && state.detail != null;
      return {
        selectedId: event.id,
        detail: keep ? state.detail : null,
        detailId: keep ? state.detailId : '',
        refreshing: event.id !== '',
        error: null,
      };
    }
    case 'refreshStart': {
      if (event.id !== state.selectedId) return state;
      // Stale-while-refresh: keep the current detail visible; mark refreshing and
      // clear any prior refresh error (a fresh attempt supersedes it). Never clear
      // `detail` here — clearing it is what dropped the gate onto the list row and
      // hid Generate Manifest mid-refresh.
      if (state.refreshing && state.error == null) return state;
      return { ...state, refreshing: true, error: null };
    }
    case 'refreshSuccess': {
      // Ignore a response for a selection that has since changed.
      if (event.id !== state.selectedId) return state;
      if (event.detail == null) {
        // Ok response but no authoritative detail body: retain any prior detail
        // (never downgrade a good detail to null on an empty response) and stop
        // refreshing. Only surface an error when there was nothing to show.
        return {
          ...state,
          refreshing: false,
          error: state.detail == null ? 'Package detail was not returned.' : null,
        };
      }
      // Atomic replace only on a real, authoritative detail response.
      return {
        selectedId: state.selectedId,
        detail: event.detail,
        detailId: event.id,
        refreshing: false,
        error: null,
      };
    }
    case 'refreshError': {
      if (event.id !== state.selectedId) return state;
      // Retain the last-known detail (stale) and surface a refresh warning. Never
      // clear a valid detail on a failed refresh — doing so silently removes valid
      // recovery actions, which is exactly the flicker this module prevents.
      return { ...state, refreshing: false, error: event.message };
    }
    default:
      return state;
  }
}

/**
 * The authoritative detail for the CURRENT selection, or null when the selected
 * package's own detail has not loaded yet. Never returns a detail belonging to a
 * previously-selected package.
 */
export function selectAuthoritativeDetail<D>(state: EvidenceDetailState<D>): D | null {
  return state.detailId === state.selectedId ? state.detail : null;
}

/**
 * Progress-polling terminality. `manifest_missing` is a resting/terminal state,
 * NOT an in-progress generation state — the async poller must not churn (refetch
 * and replace) a terminal package's rows/detail every few seconds. Only true
 * generation lifecycle states (queued/pending/building/running job status, or
 * building/hashing/verifying integrity) are in-progress. One shared, testable
 * definition consumed by the list poller in the panel.
 */
const IN_PROGRESS_JOB_STATUSES = new Set(['queued', 'pending', 'building', 'running']);
const IN_PROGRESS_INTEGRITY_STATES = new Set(['building', 'hashing', 'verifying']);
export function isEvidenceProgressPollingState(pkg: {
  status?: string | null;
  integrity_status?: string | null;
}): boolean {
  const jobStatus = String(pkg.status ?? '').toLowerCase();
  const integrity = String(pkg.integrity_status ?? '').toLowerCase();
  return IN_PROGRESS_JOB_STATUSES.has(jobStatus) || IN_PROGRESS_INTEGRITY_STATES.has(integrity);
}

/** Gate result plus whether it was resolved from an authoritative detail. */
export type EvidenceDetailActionState = EvidenceActionState & { detailAvailable: boolean };

/**
 * Detail-authoritative action gate for the drawer footer / recovery panel.
 *
 * `detail == null` → PENDING (`detailAvailable: false`): the selected package's
 * detail is not loaded / is refreshing. Every action is withheld, but this is a
 * "we don't know yet" state, NOT an authoritative `generate_manifest: false`.
 * Callers show a "Refreshing…" affordance for pending instead of deriving a
 * (false) action set from the list-row summary.
 *
 * `detail` present → delegate to the canonical `resolveEvidenceActionState`.
 */
export function resolveDetailActionState(
  detail: EvidenceActionSource | null | undefined,
  ready: boolean,
): EvidenceDetailActionState {
  if (detail == null) {
    return {
      manifestMissing: false,
      canVerify: false,
      canDownloadManifest: false,
      canGenerate: false,
      canRegenerate: false,
      showGenerate: false,
      showRegenerate: false,
      // PENDING: recovery options are not known until the authoritative detail
      // loads — the caller shows "Loading recovery options…", never a resolved
      // recovery panel (buttons OR blocker) derived from the list-row summary.
      recoveryState: 'none',
      recoveryRequired: false,
      recoveryBlockedReason: null,
      recoveryBlocked: false,
      detailAvailable: false,
    };
  }
  return { ...resolveEvidenceActionState(detail, ready), detailAvailable: true };
}
