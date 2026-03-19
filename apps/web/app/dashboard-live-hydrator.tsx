'use client';

import { useEffect, useState } from 'react';

import DashboardPageContent from './dashboard-page-content';
import { DashboardPageData } from './dashboard-data';

type Props = {
  initialData: DashboardPageData;
};

function debugHydrationFailure(path: string, error: unknown) {
  if (process.env.NODE_ENV === 'development') {
    console.debug(`[dashboard] Live hydration failed for ${path}; retaining current section state.`, error);
  }
}

export default function DashboardLiveHydrator({ initialData }: Props) {
  const [data, setData] = useState(initialData);

  useEffect(() => {
    let active = true;

    async function hydrateDashboard() {
      try {
        const response = await fetch('/api/dashboard-page-data', {
          cache: 'no-store',
        });

        if (!response.ok) {
          throw new Error(`Hydration request failed with status ${response.status}.`);
        }

        const liveData = (await response.json()) as DashboardPageData;

        if (!active) {
          return;
        }

        setData(liveData);
      } catch (error) {
        if (!active) {
          return;
        }

        debugHydrationFailure('/api/dashboard-page-data', error);
      }
    }

    void hydrateDashboard();

    return () => {
      active = false;
    };
  }, []);

  return <DashboardPageContent data={data} />;
}
