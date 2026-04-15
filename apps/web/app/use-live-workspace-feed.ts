'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

import { normalizeMonitoringMode, runtimeStatusModeFromMonitoringStatus, type MonitoringRuntimeStatus } from './monitoring-status-contract';
import { normalizeMonitoringPresentation, type MonitoringPresentation } from './monitoring-status-presentation';
import { usePilotAuth } from './pilot-auth-context';
import { resolveWorkspaceMonitoringTruth, type WorkspaceMonitoringTruth } from './workspace-monitoring-truth';

type LiveWorkspaceCounts = {
  protectedAssets: number;
  monitoredSystems: number;
  activeSystems: number;
  openAlerts: number;
  openIncidents: number;
  historyRecords: number;
};

type LiveWorkspaceFeed = {
  loading: boolean;
  refreshing: boolean;
  lastFetchCompletedAt: string | null;
  runtimeStatus: MonitoringRuntimeStatus | null;
  counts: LiveWorkspaceCounts;
  monitoring: {
    truth: WorkspaceMonitoringTruth;
    presentation: MonitoringPresentation;
    lastTelemetryAt: string | null;
    lastHeartbeatAt: string | null;
    lastPollAt: string | null;
    lastFetchCompletedAt: string | null;
  };
};

const DEFAULT_COUNTS: LiveWorkspaceCounts = {
  protectedAssets: 0,
  monitoredSystems: 0,
  activeSystems: 0,
  openAlerts: 0,
  openIncidents: 0,
  historyRecords: 0,
};

export function shouldLogLiveWorkspaceFeedDebug(): boolean {
  return process.env.NODE_ENV === 'development';
}

type RuntimeStatusResolution = {
  nextRuntime: MonitoringRuntimeStatus | null;
  offline: boolean;
  degraded: boolean;
};

export function resolveRuntimeStatus(
  statusPayload: MonitoringRuntimeStatus | null,
  statusOk: boolean,
): RuntimeStatusResolution {
  if (!statusPayload || !statusOk) {
    return { nextRuntime: null, offline: true, degraded: true };
  }
  const runtimeMode = runtimeStatusModeFromMonitoringStatus(statusPayload.monitoring_status);
  const nextRuntime = { ...statusPayload, mode: normalizeMonitoringMode(runtimeMode) };
  const offline = nextRuntime.monitoring_status === 'offline' || nextRuntime.monitoring_status === 'error';
  const degraded = nextRuntime.monitoring_status === 'degraded';
  return { nextRuntime, offline, degraded };
}

export function useLiveWorkspaceFeed(intervalMs = 15000): LiveWorkspaceFeed {
  const { apiUrl, authHeaders, isAuthenticated, user } = usePilotAuth();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastFetchCompletedAt, setLastFetchCompletedAt] = useState<string | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<MonitoringRuntimeStatus | null>(null);
  const [counts, setCounts] = useState<LiveWorkspaceCounts>(DEFAULT_COUNTS);
  const startedRef = useRef(false);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function safeJson(response: Response | null): Promise<any> {
      if (!response?.ok) {
        return {};
      }
      try {
        return await response.json();
      } catch {
        return {};
      }
    }

    async function refresh() {
      if (!active || !isAuthenticated || !user?.current_workspace?.id || document.visibilityState === 'hidden') {
        return;
      }
      if (startedRef.current) {
        setRefreshing(true);
      }
      try {
        let statusRes: Response | null = null;
        let statusPayload: MonitoringRuntimeStatus | null = null;
        try {
          statusRes = await fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' });
          statusPayload = statusRes.ok ? await statusRes.json() as MonitoringRuntimeStatus : null;
        } catch {
          statusRes = null;
          statusPayload = null;
        }
        const runtimeUnavailable = !statusRes || !statusRes.ok;
        const { nextRuntime } = resolveRuntimeStatus(statusPayload, Boolean(statusRes?.ok));
        const ancillaryResults = await Promise.allSettled([
          fetch(`${apiUrl}/pilot/history?limit=20`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/alerts?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/incidents?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
        ]);
        const historyRes = ancillaryResults[0].status === 'fulfilled' ? ancillaryResults[0].value : null;
        const alertsRes = ancillaryResults[1].status === 'fulfilled' ? ancillaryResults[1].value : null;
        const incidentsRes = ancillaryResults[2].status === 'fulfilled' ? ancillaryResults[2].value : null;
        const historyPayload = await safeJson(historyRes);
        const alertsPayload = await safeJson(alertsRes);
        const incidentsPayload = await safeJson(incidentsRes);
        const historyCount = Number(historyPayload?.counts?.analysis_runs ?? (historyPayload.analysis_runs ?? []).length ?? 0);
        if (shouldLogLiveWorkspaceFeedDebug()) {
          console.debug('useLiveWorkspaceFeed runtime-status', {
            statusCode: statusRes?.status ?? 'network_error',
            payload: statusPayload,
            monitoredSystems: nextRuntime?.monitored_systems ?? null,
            enabledSystems: nextRuntime?.enabled_systems ?? null,
            runtimeUnavailable,
            ancillaryFailed: !historyRes?.ok || !alertsRes?.ok || !incidentsRes?.ok,
          });
        }
        setRuntimeStatus(nextRuntime);
        const truth = nextRuntime?.workspace_monitoring_summary;
        setCounts({
          protectedAssets: Number(truth?.protected_assets ?? truth?.coverage_state?.protected_assets ?? nextRuntime?.protected_assets ?? 0),
          monitoredSystems: Number(truth?.configured_systems ?? truth?.coverage_state?.configured_systems ?? nextRuntime?.monitored_systems ?? 0),
          activeSystems: Number(truth?.reporting_systems ?? truth?.coverage_state?.reporting_systems ?? nextRuntime?.active_systems ?? 0),
          openAlerts: (alertsPayload.alerts ?? []).length,
          openIncidents: (incidentsPayload.incidents ?? []).length,
          historyRecords: historyCount,
        });
        const completedAt = new Date().toISOString();
        setLastFetchCompletedAt(completedAt);
      } catch {
        // Keep runtime-derived monitoring truth as the source of status.
      } finally {
        if (active) {
          setLoading(false);
          setRefreshing(false);
          startedRef.current = true;
        }
      }
    }

    function schedule() {
      if (!active) return;
      timer = setTimeout(async () => {
        await refresh();
        schedule();
      }, intervalMs);
    }

    void refresh();
    schedule();
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void refresh();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      active = false;
      document.removeEventListener('visibilitychange', onVisible);
      if (timer) clearTimeout(timer);
    };
  }, [apiUrl, authHeaders, intervalMs, isAuthenticated, user?.current_workspace?.id]);

  const truth = useMemo(() => resolveWorkspaceMonitoringTruth(runtimeStatus), [runtimeStatus]);
  const presentation = useMemo(() => normalizeMonitoringPresentation(truth), [truth]);

  return {
    loading,
    refreshing,
    lastFetchCompletedAt,
    runtimeStatus,
    counts,
    monitoring: {
      truth,
      presentation,
      lastTelemetryAt: truth.last_telemetry_at,
      lastHeartbeatAt: truth.last_heartbeat_at,
      lastPollAt: truth.last_poll_at,
      lastFetchCompletedAt,
    },
  };
}
