'use client';

import DashboardPageContent from './dashboard-page-content';
import { DashboardPageData } from './dashboard-data';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

type Props = {
  initialData: DashboardPageData;
};

export default function DashboardLiveHydrator({ initialData }: Props) {
  const liveFeed = useLiveWorkspaceFeed();
  const gatewayReachableOverride = liveFeed.monitoring.presentation.status !== 'offline';

  return (
    <DashboardPageContent
      data={initialData}
      gatewayReachableOverride={gatewayReachableOverride}
      liveFeed={liveFeed}
    />
  );
}
