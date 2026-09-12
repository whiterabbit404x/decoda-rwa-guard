'use client';

import { useEffect } from 'react';

import DashboardExecutiveSummary from './dashboard-executive-summary';
import { markDashboardPerf } from './dashboard-perf';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

export default function DashboardLiveHydrator() {
  const liveFeed = useLiveWorkspaceFeed();

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    const navClickAtMs = (window as Window & { __dashboardNavClickAtMs?: number }).__dashboardNavClickAtMs;
    markDashboardPerf('dashboard.mount', {
      route: '/dashboard',
      navToMountMs: typeof navClickAtMs === 'number'
        ? Number((performance.now() - navClickAtMs).toFixed(1))
        : null,
    });
  }, []);

  return <DashboardExecutiveSummary liveFeed={liveFeed} />;
}
