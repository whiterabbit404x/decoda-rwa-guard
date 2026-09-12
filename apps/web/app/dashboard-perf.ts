'use client';

/**
 * Critical-path marks for the dashboard's initial load.
 *
 * The question this answers is not "how long did each request take" — the
 * Network panel already shows that — but "which leg was the page actually
 * waiting on". Each mark is recorded relative to `dashboard.mount`, and the
 * timeline is emitted once when the skeleton is dismissed, so the critical
 * path is one log line rather than something to reconstruct by hand.
 *
 * Measurement only: nothing here changes what is rendered or what a status
 * label claims.
 *
 * Enabled in development automatically, and in production when
 * NEXT_PUBLIC_DASHBOARD_PERF is 'true' (the real numbers have to come from a
 * deployed environment, so this cannot be dev-only).
 */

export const DASHBOARD_PERF_MARKS = [
  'dashboard.mount',
  'session.ready',
  'workspace.ready',
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

export function dashboardPerfEnabled(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  if (process.env.NODE_ENV !== 'production') {
    return true;
  }
  return process.env.NEXT_PUBLIC_DASHBOARD_PERF === 'true';
}

function now(): number {
  try {
    return typeof performance !== 'undefined' ? performance.now() : Date.now();
  } catch {
    return Date.now();
  }
}

/** Milliseconds from `dashboard.mount` to `mark`, or null when either is missing. */
export function sinceMount(mark: DashboardPerfMark): number | null {
  const mount = marks.get('dashboard.mount');
  const target = marks.get(mark);
  if (!mount || !target) {
    return null;
  }
  return Number((target.atMs - mount.atMs).toFixed(1));
}

/** The recorded marks as offsets from `dashboard.mount`, in mark order. */
export function dashboardPerfTimeline(): Record<string, number | null> {
  const timeline: Record<string, number | null> = {};
  for (const mark of DASHBOARD_PERF_MARKS) {
    if (marks.has(mark)) {
      timeline[mark] = sinceMount(mark);
    }
  }
  return timeline;
}

export function markDashboardPerf(
  mark: DashboardPerfMark,
  detail?: Record<string, unknown>,
): void {
  if (!dashboardPerfEnabled()) {
    return;
  }

  // `dashboard.mount` resets the timeline so a client-side navigation back to
  // /dashboard is measured on its own, not against the first mount.
  if (mark === 'dashboard.mount') {
    marks.clear();
  }

  const atMs = now();
  marks.set(mark, { atMs, detail });

  try {
    performance.mark(`decoda.${mark}`);
  } catch {
    // performance.mark is unavailable or the buffer is full; the timeline
    // below is derived from our own map, so this is not load-bearing.
  }

  const offsetMs = sinceMount(mark);
  console.info('[dashboard-perf]', mark, {
    sinceMountMs: offsetMs,
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
}
