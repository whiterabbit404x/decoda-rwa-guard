'use client';

import { useEffect, useState } from 'react';

import DashboardPageContent from './dashboard-page-content';
import { DashboardPageData } from './dashboard-data';

type Props = {
  initialData: DashboardPageData;
};

function mergeDashboardPageData(currentData: DashboardPageData, liveData: DashboardPageData): DashboardPageData {
  return {
    apiUrl: liveData.apiUrl,
    dashboard: liveData.dashboard ?? currentData.dashboard,
    riskDashboard: liveData.riskDashboard.source === 'live' ? liveData.riskDashboard : currentData.riskDashboard,
    threatDashboard: liveData.threatDashboard.source === 'live' ? liveData.threatDashboard : currentData.threatDashboard,
    complianceDashboard:
      liveData.complianceDashboard.source === 'live' ? liveData.complianceDashboard : currentData.complianceDashboard,
    resilienceDashboard:
      liveData.resilienceDashboard.source === 'live' ? liveData.resilienceDashboard : currentData.resilienceDashboard,
  };
}

export default function DashboardLiveHydrator({ initialData }: Props) {
  const [data, setData] = useState(initialData);

  useEffect(() => {
    let active = true;

    async function hydrateDashboard() {
      try {
        const response = await fetch('/api/dashboard-page-data', { cache: 'no-store' });

        if (!response.ok) {
          throw new Error(`Hydration request failed with status ${response.status}`);
        }

        const liveData = (await response.json()) as DashboardPageData;

        if (!active) {
          return;
        }

        setData((currentData) => mergeDashboardPageData(currentData, liveData));
      } catch (error) {
        if (process.env.NODE_ENV === 'development') {
          console.debug('[dashboard] Live hydration route failed; retaining SSR fallback state.', error);
        }
      }
    }

    void hydrateDashboard();

    return () => {
      active = false;
    };
  }, [initialData.apiUrl]);

  return <DashboardPageContent data={data} />;
}
