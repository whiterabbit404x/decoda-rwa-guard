'use client';

import { useEffect } from 'react';

import DashboardPageContent from './dashboard-page-content';
import { DashboardPageData } from './dashboard-data';

type Props = {
  initialData: DashboardPageData;
};

function debugHydrationDisabled() {
  if (process.env.NODE_ENV === 'development') {
    console.debug('[dashboard-page-data trace] source=hydrator status=disabled');
  }
}

export default function DashboardLiveHydrator({ initialData }: Props) {
  useEffect(() => {
    debugHydrationDisabled();
  }, []);

  return (
    <DashboardPageContent
      data={initialData}
      gatewayReachableOverride={false}
    />
  );
}
