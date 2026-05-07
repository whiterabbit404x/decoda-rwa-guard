'use client';

import { useEffect } from 'react';

import DashboardExecutiveSummary from './dashboard-executive-summary';
import { DashboardPageData } from './dashboard-data';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

type Props = {
  initialData: DashboardPageData;
};

export default function DashboardLiveHydrator({ initialData }: Props) {
  const liveFeed = useLiveWorkspaceFeed();
  const isDev = process.env.NODE_ENV !== 'production';

  useEffect(() => {
    if (!isDev || typeof window === 'undefined') {
      return;
    }

    const renderAtMs = performance.now();
    const navClickAtMs = (window as Window & { __dashboardNavClickAtMs?: number }).__dashboardNavClickAtMs;
    const navToRenderMs = typeof navClickAtMs === 'number'
      ? Number((renderAtMs - navClickAtMs).toFixed(1))
      : null;

    console.info('[dashboard-perf] first render', {
      route: '/dashboard',
      renderAtIso: new Date().toISOString(),
      renderAtPerfNowMs: Number(renderAtMs.toFixed(1)),
      navToRenderMs,
    });
  }, [isDev]);

  return (
    <DashboardExecutiveSummary
      data={initialData}
      liveFeed={liveFeed}
    />
  );
}
