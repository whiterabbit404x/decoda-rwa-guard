'use client';

import DashboardExecutiveSummary from './dashboard-executive-summary';
import { DashboardPageData } from './dashboard-data';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

type Props = {
  initialData: DashboardPageData;
};

export default function DashboardLiveHydrator({ initialData }: Props) {
  const liveFeed = useLiveWorkspaceFeed();

  return (
    <DashboardExecutiveSummary
      data={initialData}
      liveFeed={liveFeed}
    />
  );
}
