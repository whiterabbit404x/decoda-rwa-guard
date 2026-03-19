'use client';

import { useEffect, useState } from 'react';

import DashboardPageContent from './dashboard-page-content';
import { DashboardPageData } from './dashboard-data';

type Props = {
  initialData: DashboardPageData;
};

type HydrationDebugState = {
  apiHydrationError?: string;
  backupReachabilityError?: string;
  retriesAttempted: number;
};

const HYDRATION_RETRY_DELAY_MS = 1200;
const BACKUP_FETCH_TIMEOUT_MS = 2000;

function toErrorMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message;
  }

  return String(error);
}

function debugHydrationFailure(path: string, error: unknown) {
  if (process.env.NODE_ENV === 'development') {
    console.debug(`[dashboard] Live hydration failed for ${path}; retaining current section state.`, error);
  }
}

async function fetchGatewayReachability(apiUrl: string): Promise<boolean> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), BACKUP_FETCH_TIMEOUT_MS);

  try {
    const response = await fetch(`${apiUrl}/dashboard`, {
      cache: 'no-store',
      signal: controller.signal,
    });

    return response.ok;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export default function DashboardLiveHydrator({ initialData }: Props) {
  const [data, setData] = useState(initialData);
  const [gatewayReachableOverride, setGatewayReachableOverride] = useState(false);
  const [debugState, setDebugState] = useState<HydrationDebugState>({ retriesAttempted: 0 });

  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;

    async function attemptHydration(attempt: number): Promise<void> {
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
        setGatewayReachableOverride((current) => current || Boolean(liveData.dashboard));
        setDebugState({ retriesAttempted: attempt });
      } catch (error) {
        if (!active) {
          return;
        }

        debugHydrationFailure('/api/dashboard-page-data', error);

        const gatewayReachable = await fetchGatewayReachability(initialData.apiUrl);

        if (!active) {
          return;
        }

        setGatewayReachableOverride(gatewayReachable);

        if (attempt === 0) {
          retryTimer = window.setTimeout(() => {
            void attemptHydration(1);
          }, HYDRATION_RETRY_DELAY_MS);
          setDebugState({
            apiHydrationError: toErrorMessage(error),
            backupReachabilityError: gatewayReachable ? undefined : `Gateway reachability probe failed for ${initialData.apiUrl}/dashboard.`,
            retriesAttempted: attempt + 1,
          });
          return;
        }

        setDebugState({
          apiHydrationError: toErrorMessage(error),
          backupReachabilityError: gatewayReachable ? undefined : `Gateway reachability probe failed for ${initialData.apiUrl}/dashboard.`,
          retriesAttempted: attempt + 1,
        });
      }
    }

    void attemptHydration(0);

    return () => {
      active = false;
      if (retryTimer !== undefined) {
        window.clearTimeout(retryTimer);
      }
    };
  }, [initialData.apiUrl]);

  return (
    <DashboardPageContent
      data={data}
      gatewayReachableOverride={gatewayReachableOverride}
    />
  );
}
