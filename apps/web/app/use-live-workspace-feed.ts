'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

import { normalizeMonitoringMode, type MonitoringRuntimeStatus } from './monitoring-status-contract';
import { usePilotAuth } from './pilot-auth-context';

type LiveWorkspaceCounts = {
  monitoredTargets: number;
  openAlerts: number;
  openIncidents: number;
  historyRecords: number;
};

type LiveWorkspaceFeed = {
  loading: boolean;
  refreshing: boolean;
  degraded: boolean;
  offline: boolean;
  stale: boolean;
  lastUpdatedAt: string | null;
  checkpointAgeSeconds: number | null;
  runtimeStatus: MonitoringRuntimeStatus | null;
  counts: LiveWorkspaceCounts;
};

const DEFAULT_COUNTS: LiveWorkspaceCounts = {
  monitoredTargets: 0,
  openAlerts: 0,
  openIncidents: 0,
  historyRecords: 0,
};

export function useLiveWorkspaceFeed(intervalMs = 15000): LiveWorkspaceFeed {
  const { apiUrl, authHeaders, isAuthenticated, user } = usePilotAuth();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [degraded, setDegraded] = useState(false);
  const [offline, setOffline] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<MonitoringRuntimeStatus | null>(null);
  const [counts, setCounts] = useState<LiveWorkspaceCounts>(DEFAULT_COUNTS);
  const startedRef = useRef(false);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function refresh() {
      if (!active || !isAuthenticated || !user?.current_workspace?.id || document.visibilityState === 'hidden') {
        return;
      }
      if (startedRef.current) {
        setRefreshing(true);
      }
      try {
        const [statusRes, historyRes, alertsRes, incidentsRes, targetsRes] = await Promise.all([
          fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/pilot/history?limit=20`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/alerts?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/incidents?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/monitoring/targets`, { headers: authHeaders(), cache: 'no-store' }),
        ]);
        const statusPayload = statusRes.ok ? await statusRes.json() as MonitoringRuntimeStatus : null;
        const historyPayload = historyRes.ok ? await historyRes.json() : {};
        const alertsPayload = alertsRes.ok ? await alertsRes.json() : {};
        const incidentsPayload = incidentsRes.ok ? await incidentsRes.json() : {};
        const targetsPayload = targetsRes.ok ? await targetsRes.json() : {};
        const nextRuntime = statusPayload ? { ...statusPayload, mode: normalizeMonitoringMode(statusPayload.mode) } : null;
        const targets = (targetsPayload.targets ?? []) as Array<{ monitoring_enabled?: boolean }>;
        const historyCount = Number(historyPayload?.counts?.analysis_runs ?? (historyPayload.analysis_runs ?? []).length ?? 0);
        setRuntimeStatus(nextRuntime);
        setCounts({
          monitoredTargets: targets.filter((item) => item.monitoring_enabled).length,
          openAlerts: (alertsPayload.alerts ?? []).length,
          openIncidents: (incidentsPayload.incidents ?? []).length,
          historyRecords: historyCount,
        });
        setDegraded(Boolean(nextRuntime?.degraded_reason) || !statusRes.ok || !alertsRes.ok || !incidentsRes.ok || !targetsRes.ok || !historyRes.ok);
        setOffline(false);
        setLastUpdatedAt(new Date().toISOString());
      } catch {
        if (active) {
          setOffline(true);
          setDegraded(true);
        }
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

  const stale = useMemo(() => {
    if (!lastUpdatedAt) return true;
    return Date.now() - new Date(lastUpdatedAt).getTime() > intervalMs * 2;
  }, [intervalMs, lastUpdatedAt]);

  return {
    loading,
    refreshing,
    degraded,
    offline,
    stale,
    lastUpdatedAt,
    checkpointAgeSeconds: runtimeStatus?.checkpoint_age_seconds ?? null,
    runtimeStatus,
    counts,
  };
}
