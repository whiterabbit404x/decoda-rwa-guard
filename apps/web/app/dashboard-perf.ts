'use client';

/**
 * Critical-path marks for the dashboard's initial load.
 *
 * The question this answers is not "how long did each request take" — the
 * Network panel already shows that — but "which leg was the page actually
 * waiting on".
 *
 * Offsets are measured from the **start of the navigation**, not from
 * `dashboard.mount`. That distinction is the whole point: `DashboardLiveHydrator`
 * only mounts once the auth gate in `authenticated-route.tsx` opens, so anchoring
 * to mount made the two strictly serial legs in front of it — `/api/runtime-config`,
 * then `/api/auth/me` ‖ `/api/auth/csrf` — invisible, and made `session.ready` and
 * `workspace.ready` report ~0ms because React flushes child effects before parent
 * effects in the same commit.
 *
 * Marks are always recorded; the env flag only controls whether they are logged.
 * Recording unconditionally costs a `performance.now()` read and a Map write, and
 * it removes a race: the first marks fire before runtime config has resolved, so a
 * flag consulted at mark time would drop them.
 *
 * Measurement only: nothing here changes what is rendered or what a status
 * label claims.
 */

export const DASHBOARD_PERF_MARKS = [
  'runtime-config.response',
  'auth.csrf.response',
  'auth.me.response',
  'session.ready',
  'workspace.ready',
  'dashboard.mount',
  'summary.request.start',
  'summary.response',
  'runtime-status.response',
  'dashboard.skeleton.dismissed',
] as const;

export type DashboardPerfMark = (typeof DASHBOARD_PERF_MARKS)[number];

type MarkRecord = {
  atMs: number;
  detail?: Record<string, unknown>;
};

const marks = new Map<DashboardPerfMark, MarkRecord>();

/**
 * Offsets are relative to this. 0 means "the document navigation", because
 * `performance.now()` is already measured from `timeOrigin`. A client-side
 * navigation back to /dashboard re-bases it onto the nav click so that load is
 * measured on its own rather than against the original document.
 */
let epochStartMs = 0;

/** Explicit override set by tests; null means "use the window flag or build-time env". */
let enabledOverride: boolean | null = null;

export function dashboardPerfEnabled(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  if (enabledOverride !== null) {
    return enabledOverride;
  }
  // `NEXT_PUBLIC_DASHBOARD_PERF` is inlined at build time, so on its own it
  // cannot be switched on in a deployed environment without a rebuild — which is
  // exactly when the numbers are wanted. This window flag makes a deployed build
  // measurable: the capture harness sets it before any app code runs. It only
  // ever turns logging on; it cannot change what the page renders.
  const windowOverride = (window as Window & { __DECODA_DASHBOARD_PERF?: unknown })
    .__DECODA_DASHBOARD_PERF;
  if (windowOverride === true) {
    return true;
  }
  if (process.env.NODE_ENV !== 'production') {
    return true;
  }
  return process.env.NEXT_PUBLIC_DASHBOARD_PERF === 'true';
}

/** Test-only override. `null` restores the env/window-driven default. */
export function setDashboardPerfEnabled(enabled: boolean | null): void {
  enabledOverride = enabled;
}

function now(): number {
  try {
    return typeof performance !== 'undefined' ? performance.now() : Date.now();
  } catch {
    return Date.now();
  }
}

/** Milliseconds from the start of the navigation to `mark`, or null when unrecorded. */
export function sinceStart(mark: DashboardPerfMark): number | null {
  const target = marks.get(mark);
  if (!target) {
    return null;
  }
  return Number((target.atMs - epochStartMs).toFixed(1));
}

/** The recorded marks as offsets from the navigation start, in chronological order. */
export function dashboardPerfTimeline(): Record<string, number | null> {
  const recorded = DASHBOARD_PERF_MARKS.filter((mark) => marks.has(mark)).sort(
    (a, b) => (marks.get(a)?.atMs ?? 0) - (marks.get(b)?.atMs ?? 0),
  );

  const timeline: Record<string, number | null> = {};
  for (const mark of recorded) {
    timeline[mark] = sinceStart(mark);
  }
  return timeline;
}

export function markDashboardPerf(
  mark: DashboardPerfMark,
  detail?: Record<string, unknown>,
): void {
  if (typeof window === 'undefined') {
    return;
  }

  // A client-side navigation back to /dashboard starts a new epoch, so it is
  // measured on its own. Detected at mount, because that is the first mark a
  // client-side navigation produces — the auth legs do not re-run.
  if (mark === 'dashboard.mount') {
    const navClickAtMs = (window as Window & { __dashboardNavClickAtMs?: number })
      .__dashboardNavClickAtMs;
    if (typeof navClickAtMs === 'number' && navClickAtMs > epochStartMs) {
      marks.clear();
      epochStartMs = navClickAtMs;
    }
  }

  const atMs = now();
  marks.set(mark, { atMs, detail });

  try {
    performance.mark(`decoda.${mark}`);
  } catch {
    // performance.mark is unavailable or the buffer is full; the timeline
    // below is derived from our own map, so this is not load-bearing.
  }

  if (!dashboardPerfEnabled()) {
    return;
  }

  const offsetMs = sinceStart(mark);
  console.info('[dashboard-perf]', mark, {
    sinceStartMs: offsetMs,
    atIso: new Date().toISOString(),
    ...(detail ?? {}),
  });

  if (mark === 'dashboard.skeleton.dismissed') {
    console.info('[dashboard-perf] critical path', {
      totalMs: offsetMs,
      timeline: dashboardPerfTimeline(),
    });
  }
}

/** Test-only: drop recorded marks so specs do not leak state into each other. */
export function resetDashboardPerfMarksForTests(): void {
  marks.clear();
  epochStartMs = 0;
  enabledOverride = null;
}
