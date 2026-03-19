'use client';

import { useEffect, useState } from 'react';

import DashboardPageContent from './dashboard-page-content';
import { DashboardPageData, DashboardResponse } from './dashboard-data';

type Props = {
  initialData: DashboardPageData;
};

type HydrationDebugState = {
  apiHydrationError?: string;
  retriesAttempted: number;
};

type LiveSectionUpdates = Partial<DashboardPageData>;

type DashboardPageDataHydrationMeta = {
  gatewayReachable: boolean;
  dashboardFetchSucceeded: boolean;
  riskLive: boolean;
  threatLive: boolean;
  complianceLive: boolean;
  resilienceLive: boolean;
  errors?: string[];
};

type DashboardPageDataHydrationResponse = {
  data: DashboardPageData;
  meta: DashboardPageDataHydrationMeta;
};

const HYDRATION_RETRY_DELAY_MS = 1200;
const OFFLINE_GATEWAY_STATUSES = new Set(['waiting', 'down', 'offline', 'unavailable', 'fallback']);

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

function normalizeRuntimeStatus(status?: string) {
  return status?.trim().toLowerCase() ?? '';
}

function isGatewayServiceOk(dashboard: DashboardResponse | null | undefined) {
  if (!dashboard) {
    return false;
  }

  return dashboard.services.some(
    (service) => service.service_name === 'api' && normalizeRuntimeStatus(service.status) === 'ok'
  );
}

function isGatewayClearlyReachable(dashboard: DashboardResponse | null | undefined) {
  if (!dashboard) {
    return false;
  }

  if (isGatewayServiceOk(dashboard)) {
    return true;
  }

  const apiCard = dashboard.cards.find((card) => card.service === 'api' || card.title === 'API Gateway');
  if (apiCard) {
    return !OFFLINE_GATEWAY_STATUSES.has(normalizeRuntimeStatus(apiCard.status));
  }

  return dashboard.services.length > 0 || dashboard.cards.length > 0;
}

function isLiveSection<T extends { source: 'live' | 'fallback' }>(section: T) {
  return section.source === 'live';
}

function collectLiveSectionUpdates(data: DashboardPageData): LiveSectionUpdates {
  const updates: LiveSectionUpdates = {};

  if (data.dashboard && isGatewayClearlyReachable(data.dashboard)) {
    updates.dashboard = data.dashboard;
  }

  if (isLiveSection(data.riskDashboard)) {
    updates.riskDashboard = data.riskDashboard;
  }

  if (isLiveSection(data.threatDashboard)) {
    updates.threatDashboard = data.threatDashboard;
  }

  if (isLiveSection(data.complianceDashboard)) {
    updates.complianceDashboard = data.complianceDashboard;
  }

  if (isLiveSection(data.resilienceDashboard)) {
    updates.resilienceDashboard = data.resilienceDashboard;
  }

  return updates;
}

function hasLiveSectionUpdates(updates: LiveSectionUpdates) {
  return Object.keys(updates).length > 0;
}

function mergeDashboardPageData(current: DashboardPageData, updates: LiveSectionUpdates): DashboardPageData {
  return {
    ...current,
    ...updates,
  };
}

function hasAnyLiveSection(meta: DashboardPageDataHydrationMeta) {
  return meta.riskLive || meta.threatLive || meta.complianceLive || meta.resilienceLive;
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
        const response = await fetch(
          `/api/dashboard-page-data?apiUrl=${encodeURIComponent(initialData.apiUrl)}`,
          {
            cache: 'no-store',
          }
        );

        if (!response.ok) {
          throw new Error(`Hydration request failed with status ${response.status}.`);
        }

        const hydrationResult = (await response.json()) as DashboardPageDataHydrationResponse;

        if (!active) {
          return;
        }

        const routeUpdates = collectLiveSectionUpdates(hydrationResult.data);

        if (hasLiveSectionUpdates(routeUpdates)) {
          setData((current) => mergeDashboardPageData(current, routeUpdates));
        }

        if (hydrationResult.meta.gatewayReachable) {
          setGatewayReachableOverride(true);
        }

        const shouldRetry =
          attempt === 0 &&
          !hydrationResult.meta.gatewayReachable &&
          !hasAnyLiveSection(hydrationResult.meta) &&
          !hasLiveSectionUpdates(routeUpdates);

        if (shouldRetry) {
          retryTimer = window.setTimeout(() => {
            void attemptHydration(1);
          }, HYDRATION_RETRY_DELAY_MS);
        }

        setDebugState({
          apiHydrationError:
            hydrationResult.meta.errors && hydrationResult.meta.errors.length > 0
              ? hydrationResult.meta.errors.join(' | ')
              : undefined,
          retriesAttempted: shouldRetry ? attempt + 1 : attempt,
        });
      } catch (error) {
        if (!active) {
          return;
        }

        debugHydrationFailure('/api/dashboard-page-data', error);

        if (attempt === 0) {
          retryTimer = window.setTimeout(() => {
            void attemptHydration(1);
          }, HYDRATION_RETRY_DELAY_MS);
        }

        setDebugState({
          apiHydrationError: toErrorMessage(error),
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
