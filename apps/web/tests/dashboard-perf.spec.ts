import { expect, test } from '@playwright/test';

import {
  DASHBOARD_PERF_MARKS,
  dashboardPerfTimeline,
  markDashboardPerf,
  resetDashboardPerfMarksForTests,
  setDashboardPerfEnabled,
  sinceStart,
} from '../app/dashboard-perf';

/**
 * The timeline these marks produce is what the dashboard's remaining latency is
 * read off. It previously could not see the legs that actually gate the page.
 *
 * `DashboardLiveHydrator` mounts only after the auth gate in
 * `authenticated-route.tsx` opens, and React flushes child effects before parent
 * effects in the same commit. So anchoring offsets to `dashboard.mount` — and
 * clearing the mark map there — made `/api/runtime-config` and
 * `/api/auth/me` invisible and pinned `session.ready` to ~0ms. These tests pin
 * the corrected behaviour so the blind spot cannot come back.
 */

type MutableGlobal = typeof globalThis & {
  window?: unknown;
  performance?: unknown;
  __dashboardNavClickAtMs?: number;
};

const globalRef = globalThis as MutableGlobal;

let clockMs = 0;
let originalWindow: unknown;
let originalPerformance: unknown;

function advance(toMs: number): void {
  clockMs = toMs;
}

test.beforeEach(() => {
  clockMs = 0;
  originalWindow = globalRef.window;
  originalPerformance = globalRef.performance;

  // The module guards on `typeof window`, and reads `performance.now()` at each
  // mark. A controllable clock makes the offsets exact rather than approximate.
  globalRef.window = globalRef;
  globalRef.performance = {
    now: () => clockMs,
    mark: () => undefined,
  };
  delete globalRef.__dashboardNavClickAtMs;

  resetDashboardPerfMarksForTests();
  setDashboardPerfEnabled(false); // record, but keep the test output quiet
});

test.afterEach(() => {
  resetDashboardPerfMarksForTests();
  delete globalRef.__dashboardNavClickAtMs;
  globalRef.window = originalWindow;
  globalRef.performance = originalPerformance;
});

test.describe('dashboard perf marks span the whole navigation', () => {
  test('dashboard.mount no longer erases the marks recorded before it', () => {
    // This is the regression. The auth legs complete, then the gate opens and
    // the hydrator mounts; the earlier marks must survive that.
    advance(120);
    markDashboardPerf('runtime-config.response', { ok: true, status: 200 });
    advance(900);
    markDashboardPerf('auth.me.response', { ok: true, status: 200 });
    advance(950);
    markDashboardPerf('session.ready', { authenticated: true });
    advance(1000);
    markDashboardPerf('dashboard.mount', { route: '/dashboard' });

    const timeline = dashboardPerfTimeline();
    expect(timeline['runtime-config.response']).toBe(120);
    expect(timeline['auth.me.response']).toBe(900);
    expect(timeline['session.ready']).toBe(950);
    expect(timeline['dashboard.mount']).toBe(1000);
  });

  test('session.ready carries the time the auth leg actually took, not ~0', () => {
    // React flushes the hydrator's mount effect before the provider's effect, so
    // session.ready is recorded AFTER dashboard.mount even though the session
    // resolved first. Anchoring to the navigation rather than to mount is what
    // keeps its offset truthful.
    advance(1000);
    markDashboardPerf('dashboard.mount', { route: '/dashboard' });
    advance(1010);
    markDashboardPerf('session.ready', { authenticated: true });

    expect(sinceStart('session.ready')).toBe(1010);
    expect(sinceStart('session.ready')).not.toBe(0);
  });

  test('the timeline is ordered by when marks were recorded', () => {
    advance(1000);
    markDashboardPerf('dashboard.mount', { route: '/dashboard' });
    advance(1010);
    markDashboardPerf('session.ready', { authenticated: true });
    advance(1020);
    markDashboardPerf('workspace.ready');

    // Chronological, not declaration order: mount physically happened first here.
    expect(Object.keys(dashboardPerfTimeline())).toEqual([
      'dashboard.mount',
      'session.ready',
      'workspace.ready',
    ]);
  });

  test('the three pre-gate legs are marks the timeline can report', () => {
    expect(DASHBOARD_PERF_MARKS).toContain('runtime-config.response');
    expect(DASHBOARD_PERF_MARKS).toContain('auth.me.response');
    expect(DASHBOARD_PERF_MARKS).toContain('auth.csrf.response');
  });

  test('an unrecorded mark is absent rather than reported as zero', () => {
    advance(500);
    markDashboardPerf('dashboard.mount', { route: '/dashboard' });

    expect(sinceStart('summary.response')).toBeNull();
    expect(dashboardPerfTimeline()).not.toHaveProperty(['summary.response']);
  });
});

test.describe('client-side navigation is measured on its own', () => {
  test('a nav click re-bases the epoch and drops the previous page view', () => {
    advance(200);
    markDashboardPerf('runtime-config.response', { ok: true, status: 200 });
    advance(1000);
    markDashboardPerf('dashboard.skeleton.dismissed', { outcome: 'ready' });

    // The user navigates away and clicks back into /dashboard at t=5000.
    globalRef.__dashboardNavClickAtMs = 5000;
    advance(5050);
    markDashboardPerf('dashboard.mount', { route: '/dashboard' });
    advance(5400);
    markDashboardPerf('dashboard.skeleton.dismissed', { outcome: 'ready' });

    const timeline = dashboardPerfTimeline();
    expect(timeline['dashboard.mount']).toBe(50);
    expect(timeline['dashboard.skeleton.dismissed']).toBe(400);
    // Marks from the first page view must not bleed into the second.
    expect(timeline).not.toHaveProperty(['runtime-config.response']);
  });

  test('a stale nav click from before the current epoch does not re-base it', () => {
    globalRef.__dashboardNavClickAtMs = 5000;
    advance(5050);
    markDashboardPerf('dashboard.mount', { route: '/dashboard' });

    // Same timestamp still sitting on window: a re-mount must not restart the
    // epoch again and report the elapsed time as ~0.
    advance(5600);
    markDashboardPerf('dashboard.mount', { route: '/dashboard' });

    expect(sinceStart('dashboard.mount')).toBe(600);
  });

  test('a full document load measures from navigation start, with no nav click', () => {
    advance(1500);
    markDashboardPerf('dashboard.skeleton.dismissed', { outcome: 'ready' });

    // performance.now() is already relative to timeOrigin, so with no nav click
    // the offset IS the time since the navigation began.
    expect(sinceStart('dashboard.skeleton.dismissed')).toBe(1500);
  });
});

test.describe('logging is separable from recording', () => {
  test('marks are recorded even while logging is disabled', () => {
    setDashboardPerfEnabled(false);
    advance(300);
    markDashboardPerf('auth.me.response', { ok: true, status: 200 });

    // Recording unconditionally is what removes the race: the first marks fire
    // before anything could have switched logging on.
    expect(sinceStart('auth.me.response')).toBe(300);
  });

  test('a production build stays silent until the window flag is set', () => {
    // The flag only earns its keep in production, where NEXT_PUBLIC_DASHBOARD_PERF
    // is inlined at build time and cannot otherwise be switched on.
    const originalNodeEnv = process.env.NODE_ENV;
    const originalPublicFlag = process.env.NEXT_PUBLIC_DASHBOARD_PERF;
    const originalInfo = console.info;
    const logged: unknown[][] = [];

    setDashboardPerfEnabled(null);
    process.env.NODE_ENV = 'production';
    delete process.env.NEXT_PUBLIC_DASHBOARD_PERF;
    console.info = (...args: unknown[]) => {
      logged.push(args);
    };

    try {
      advance(500);
      markDashboardPerf('summary.response', { ok: true, status: 200 });
      expect(logged).toHaveLength(0);

      (globalRef as { __DECODA_DASHBOARD_PERF?: boolean }).__DECODA_DASHBOARD_PERF = true;
      advance(750);
      markDashboardPerf('dashboard.skeleton.dismissed', { outcome: 'ready' });
    } finally {
      console.info = originalInfo;
      delete (globalRef as { __DECODA_DASHBOARD_PERF?: boolean }).__DECODA_DASHBOARD_PERF;
      process.env.NODE_ENV = originalNodeEnv;
      if (originalPublicFlag === undefined) {
        delete process.env.NEXT_PUBLIC_DASHBOARD_PERF;
      } else {
        process.env.NEXT_PUBLIC_DASHBOARD_PERF = originalPublicFlag;
      }
    }

    // console.info('[dashboard-perf] critical path', { totalMs, timeline })
    const criticalPath = logged.find((args) => args[0] === '[dashboard-perf] critical path');
    expect(criticalPath).toBeTruthy();
    expect((criticalPath?.[1] as { totalMs?: number })?.totalMs).toBe(750);

    // The mark recorded while logging was off is still in the timeline.
    expect((criticalPath?.[1] as { timeline?: Record<string, number> })?.timeline)
      .toHaveProperty(['summary.response'], 500);
  });
});
