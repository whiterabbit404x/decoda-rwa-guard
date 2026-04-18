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
  runtimeFetchWarning: boolean;
  runtimeFetchDegraded: boolean;
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
const RUNTIME_STATUS_PROXY_PATH = '/api/ops/monitoring/runtime-status';

export function shouldLogLiveWorkspaceFeedDebug(): boolean {
  return process.env.NODE_ENV === 'development';
}

type RuntimeStatusResolution = {
  nextRuntime: MonitoringRuntimeStatus | null;
  offline: boolean;
  degraded: boolean;
  fetchWarning: boolean;
  failureStreak: number;
};
const OFFLINE_PROMOTION_THRESHOLD = 2;

export function resolveRuntimeStatus(
  statusPayload: MonitoringRuntimeStatus | null,
  statusOk: boolean,
  previousRuntime: MonitoringRuntimeStatus | null = null,
  previousFailureStreak = 0,
  failurePromotionThreshold = OFFLINE_PROMOTION_THRESHOLD,
): RuntimeStatusResolution {
  if (!statusPayload || !statusOk) {
    const failureStreak = previousFailureStreak + 1;
    const offline = failureStreak >= failurePromotionThreshold;
    const nextRuntime: MonitoringRuntimeStatus | null = offline && previousRuntime
      ? { ...previousRuntime, monitoring_status: 'offline' as const, mode: 'OFFLINE' as const }
      : previousRuntime;
    return {
      nextRuntime,
      offline,
      degraded: offline || previousRuntime?.monitoring_status === 'degraded',
      fetchWarning: true,
      failureStreak,
    };
  }
  const runtimeMode = runtimeStatusModeFromMonitoringStatus(statusPayload.monitoring_status);
  const nextRuntime = { ...statusPayload, mode: normalizeMonitoringMode(runtimeMode) };
  const explicitlyOffline = nextRuntime.monitoring_status === 'offline' || nextRuntime.monitoring_status === 'error';
  const offline = explicitlyOffline;
  const degraded = nextRuntime.monitoring_status === 'degraded';
  return { nextRuntime, offline, degraded, fetchWarning: false, failureStreak: 0 };
}

export function useLiveWorkspaceFeed(intervalMs = 15000): LiveWorkspaceFeed {
  const { apiUrl, authHeaders, isAuthenticated, user } = usePilotAuth();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastFetchCompletedAt, setLastFetchCompletedAt] = useState<string | null>(null);
  const [runtimeFetchWarning, setRuntimeFetchWarning] = useState(false);
  const [runtimeFetchDegraded, setRuntimeFetchDegraded] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<MonitoringRuntimeStatus | null>(null);
  const [counts, setCounts] = useState<LiveWorkspaceCounts>(DEFAULT_COUNTS);
  const startedRef = useRef(false);
  const lastKnownRuntimeRef = useRef<MonitoringRuntimeStatus | null>(null);
  const runtimeFailureStreakRef = useRef(0);

  useEffect(() => {
    if (!shouldLogLiveWorkspaceFeedDebug()) {
      return;
    }
    console.debug('useLiveWorkspaceFeed state-updated', {
      workspaceId: user?.current_workspace?.id ?? null,
      monitoring_status: runtimeStatus?.monitoring_status ?? null,
      reporting_systems:
        runtimeStatus?.workspace_monitoring_summary?.reporting_systems ??
        runtimeStatus?.workspace_monitoring_summary?.coverage_state?.reporting_systems ??
        runtimeStatus?.active_systems ??
        null,
      evidence_source:
        runtimeStatus?.workspace_monitoring_summary?.evidence_source ??
        runtimeStatus?.evidence_source ??
        null,
      appliedCounts: counts,
      lastFetchCompletedAt,
    });
  }, [counts, lastFetchCompletedAt, runtimeStatus, user?.current_workspace?.id]);

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
          statusRes = await fetch(RUNTIME_STATUS_PROXY_PATH, { headers: authHeaders(), cache: 'no-store' });
          statusPayload = statusRes.ok ? await statusRes.json() as MonitoringRuntimeStatus : null;
        } catch {
          statusRes = null;
          statusPayload = null;
        }
        const runtimeUnavailable = !statusRes || !statusRes.ok;
        const { nextRuntime, fetchWarning, failureStreak } = resolveRuntimeStatus(
          statusPayload,
          Boolean(statusRes?.ok),
          lastKnownRuntimeRef.current,
          runtimeFailureStreakRef.current,
        );
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
        const truth = nextRuntime?.workspace_monitoring_summary;
        const nextCounts: LiveWorkspaceCounts = {
          protectedAssets: Number(
            truth?.protected_assets ??
              truth?.coverage_state?.protected_assets ??
              nextRuntime?.protected_assets ??
              0,
          ),
          monitoredSystems: Number(
            truth?.configured_systems ??
              truth?.coverage_state?.configured_systems ??
              nextRuntime?.monitored_systems ??
              0,
          ),
          activeSystems: Number(
            truth?.reporting_systems ??
              truth?.coverage_state?.reporting_systems ??
              nextRuntime?.active_systems ??
              0,
          ),
          openAlerts: (alertsPayload.alerts ?? []).length,
          openIncidents: (incidentsPayload.incidents ?? []).length,
          historyRecords: historyCount,
        };
        if (shouldLogLiveWorkspaceFeedDebug()) {
          console.debug('useLiveWorkspaceFeed refresh-result', {
            workspaceId: user?.current_workspace?.id ?? null,
            requestPath: RUNTIME_STATUS_PROXY_PATH,
            statusCode: statusRes?.status ?? 'network_error',
            payload: statusPayload,
            monitoring_status: statusPayload?.monitoring_status ?? null,
            reporting_systems:
              statusPayload?.workspace_monitoring_summary?.reporting_systems ??
              statusPayload?.workspace_monitoring_summary?.coverage_state?.reporting_systems ??
              statusPayload?.active_systems ??
              null,
            evidence_source:
              statusPayload?.workspace_monitoring_summary?.evidence_source ??
              statusPayload?.evidence_source ??
              null,
            monitoredSystems: nextRuntime?.monitored_systems ?? null,
            enabledSystems: nextRuntime?.enabled_systems ?? null,
            runtimeUnavailable,
            runtimeFetchWarning: fetchWarning,
            runtimeFetchDegraded: runtimeUnavailable,
            runtimeFailureStreak: failureStreak,
            ancillaryFailed: !historyRes?.ok || !alertsRes?.ok || !incidentsRes?.ok,
            appliedCounts: nextCounts,
          });
        }
        setRuntimeFetchWarning(fetchWarning);
        setRuntimeFetchDegraded(runtimeUnavailable);
        runtimeFailureStreakRef.current = failureStreak;
        lastKnownRuntimeRef.current = nextRuntime;
        setRuntimeStatus(nextRuntime);
        setCounts(nextCounts);
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
    runtimeFetchWarning,
    runtimeFetchDegraded,
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
