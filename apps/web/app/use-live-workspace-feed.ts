'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

import { connectAlertStream, type AlertStreamStatus } from './alert-stream-client';
import { normalizeMonitoringMode, runtimeStatusModeFromMonitoringStatus, type MonitoringRuntimeStatus } from './monitoring-status-contract';
import { normalizeMonitoringPresentation, type MonitoringPresentation } from './monitoring-status-presentation';
import { usePilotAuth } from './pilot-auth-context';
import { fetchRuntimeStatusDeduped } from './runtime-status-client';
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
  streamStatus: AlertStreamStatus;
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
const FEED_REQUEST_FRESHNESS_MS = 60_000;
type WorkspaceFeedSnapshot = {
  statusPayload: MonitoringRuntimeStatus | null;
  fetchedAt: number;
};
const inflightFeedSnapshotByWorkspace = new Map<string, Promise<WorkspaceFeedSnapshot>>();
const recentFeedSnapshotByWorkspace = new Map<string, WorkspaceFeedSnapshot>();

// Maximum event IDs tracked for SSE deduplication
const MAX_SEEN_EVENT_IDS = 500;

export function shouldLogLiveWorkspaceFeedDebug(): boolean {
  return process.env.NODE_ENV === 'development';
}

export function buildWorkspaceScopedHeaders(
  authHeaders: (workspaceIdOverride?: string | null) => Record<string, string>,
  workspaceId: string | null | undefined,
): Record<string, string> {
  return { ...authHeaders(workspaceId ?? null) };
}

function workspaceSnapshotCacheKey(headers: Record<string, string>): string {
  return String(headers['x-workspace-id'] ?? headers['X-Workspace-Id'] ?? 'default');
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
      degraded: offline || previousRuntime?.monitoring_status === 'limited',
      fetchWarning: true,
      failureStreak,
    };
  }
  const runtimeMode = runtimeStatusModeFromMonitoringStatus(statusPayload.monitoring_status);
  const nextRuntime = { ...statusPayload, mode: normalizeMonitoringMode(runtimeMode) };
  // Cast to string so runtime values beyond the typed union (e.g. 'error') are handled safely.
  const statusStr = nextRuntime.monitoring_status as string;
  const explicitlyOffline = statusStr === 'offline' || statusStr === 'error';
  const offline = explicitlyOffline;
  const degraded = statusStr === 'limited';
  return { nextRuntime, offline, degraded, fetchWarning: false, failureStreak: 0 };
}

export function useLiveWorkspaceFeed(intervalMs = 30000): LiveWorkspaceFeed {
  const { apiUrl, authHeaders, isAuthenticated, user } = usePilotAuth();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastFetchCompletedAt, setLastFetchCompletedAt] = useState<string | null>(null);
  const [runtimeFetchWarning, setRuntimeFetchWarning] = useState(false);
  const [runtimeFetchDegraded, setRuntimeFetchDegraded] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<MonitoringRuntimeStatus | null>(null);
  const [counts, setCounts] = useState<LiveWorkspaceCounts>(DEFAULT_COUNTS);
  const [streamStatus, setStreamStatus] = useState<AlertStreamStatus>('disconnected');
  const startedRef = useRef(false);
  const lastKnownRuntimeRef = useRef<MonitoringRuntimeStatus | null>(null);
  const runtimeFailureStreakRef = useRef(0);
  const workspaceIdRef = useRef<string | null>(null);
  // SSE state refs
  const sseConnectedRef = useRef(false);
  const seenEventIdsRef = useRef(new Set<string>());
  // Shared refresh fn so SSE effect can trigger a refresh without circular deps
  const refreshFnRef = useRef<((forceRefresh?: boolean) => Promise<void>) | null>(null);

  useEffect(() => {
    workspaceIdRef.current = user?.current_workspace?.id ?? null;
  }, [user?.current_workspace?.id]);

  useEffect(() => {
    if (!shouldLogLiveWorkspaceFeedDebug()) {
      return;
    }
    console.debug('useLiveWorkspaceFeed state-updated', {
      workspaceId: user?.current_workspace?.id ?? null,
      monitoring_status: runtimeStatus?.monitoring_status ?? null,
      reporting_systems: runtimeStatus?.workspace_monitoring_summary?.reporting_systems_count ?? null,
      evidence_source: runtimeStatus?.workspace_monitoring_summary?.evidence_source_summary ?? null,
      appliedCounts: counts,
      lastFetchCompletedAt,
      streamStatus,
    });
  }, [counts, lastFetchCompletedAt, runtimeStatus, streamStatus, user?.current_workspace?.id]);

  // Primary data-fetch effect: initial load + fallback polling when SSE is not live.
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function fetchWorkspaceSnapshot(
      cycleHeaders: Record<string, string>,
      forceRefresh = false,
    ): Promise<WorkspaceFeedSnapshot> {
      const cacheKey = workspaceSnapshotCacheKey(cycleHeaders);
      if (!forceRefresh) {
        const cached = recentFeedSnapshotByWorkspace.get(cacheKey);
        if (cached && (Date.now() - cached.fetchedAt) <= FEED_REQUEST_FRESHNESS_MS) {
          return cached;
        }
      }
      const inflight = inflightFeedSnapshotByWorkspace.get(cacheKey);
      if (inflight) {
        return await inflight;
      }
      const request = (async (): Promise<WorkspaceFeedSnapshot> => {
        const statusPayload = await fetchRuntimeStatusDeduped(cycleHeaders, { forceRefresh });
        const snapshot: WorkspaceFeedSnapshot = {
          statusPayload,
          fetchedAt: Date.now(),
        };
        recentFeedSnapshotByWorkspace.set(cacheKey, snapshot);
        return snapshot;
      })()
        .finally(() => {
          inflightFeedSnapshotByWorkspace.delete(cacheKey);
        });
      inflightFeedSnapshotByWorkspace.set(cacheKey, request);
      return await request;
    }

    async function refresh(forceRefresh = false) {
      const cycleWorkspaceId = workspaceIdRef.current;
      if (!active || !isAuthenticated || !cycleWorkspaceId || document.visibilityState === 'hidden') {
        return;
      }
      if (startedRef.current) {
        setRefreshing(true);
      }
      try {
        const cycleHeaders = buildWorkspaceScopedHeaders(authHeaders, cycleWorkspaceId);
        const snapshot = await fetchWorkspaceSnapshot(cycleHeaders, forceRefresh);
        const statusPayload = snapshot.statusPayload;
        const runtimeUnavailable = !statusPayload;
        const { nextRuntime, fetchWarning, failureStreak } = resolveRuntimeStatus(
          statusPayload,
          Boolean(statusPayload),
          lastKnownRuntimeRef.current,
          runtimeFailureStreakRef.current,
        );
        const truth = nextRuntime?.workspace_monitoring_summary;
        const nextCounts: LiveWorkspaceCounts = {
          protectedAssets: Number(truth?.protected_assets_count ?? 0),
          monitoredSystems: Number(truth?.monitored_systems_count ?? 0),
          activeSystems: Number(truth?.reporting_systems_count ?? 0),
          openAlerts: Number(truth?.active_alerts_count ?? 0),
          openIncidents: Number(truth?.active_incidents_count ?? 0),
          historyRecords: 0,
        };
        if (shouldLogLiveWorkspaceFeedDebug()) {
          console.debug('useLiveWorkspaceFeed refresh-result', {
            workspaceId: cycleWorkspaceId,
            workspaceHeader: cycleHeaders['x-workspace-id'] ?? null,
            requestPath: '/api/ops/monitoring/runtime-status',
            statusCode: statusPayload ? 200 : 'network_error',
            forcedRefresh: forceRefresh,
            payload: statusPayload,
            monitoring_status: statusPayload?.monitoring_status ?? null,
            reporting_systems: statusPayload?.workspace_monitoring_summary?.reporting_systems_count ?? null,
            evidence_source: statusPayload?.workspace_monitoring_summary?.evidence_source_summary ?? null,
            monitoredSystems: nextRuntime?.monitored_systems ?? null,
            enabledSystems: nextRuntime?.enabled_systems ?? null,
            runtimeUnavailable,
            runtimeFetchWarning: fetchWarning,
            runtimeFetchDegraded: runtimeUnavailable,
            runtimeFailureStreak: failureStreak,
            appliedCounts: nextCounts,
          });
        }
        if (workspaceIdRef.current !== cycleWorkspaceId) {
          return;
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

    // Expose refresh so the SSE effect can trigger immediate fetches.
    refreshFnRef.current = refresh;

    function schedule() {
      if (!active) return;
      timer = setTimeout(async () => {
        // Skip HTTP poll while SSE is delivering live events.
        if (!sseConnectedRef.current) {
          await refresh();
        }
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
    const onManualRefresh = () => {
      void refresh(true);
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('pilot-history-refresh', onManualRefresh as EventListener);
    return () => {
      active = false;
      refreshFnRef.current = null;
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('pilot-history-refresh', onManualRefresh as EventListener);
      if (timer) clearTimeout(timer);
    };
  }, [apiUrl, authHeaders, intervalMs, isAuthenticated, user?.current_workspace?.id]);

  // SSE connection effect: connects to the backend alert stream and triggers
  // immediate refreshes on alert events. Falls back to polling when disconnected.
  useEffect(() => {
    const workspaceId = user?.current_workspace?.id ?? null;

    // Fail closed: never subscribe without a valid workspace.
    if (!isAuthenticated || !workspaceId) {
      sseConnectedRef.current = false;
      setStreamStatus('disconnected');
      return;
    }

    const headers = buildWorkspaceScopedHeaders(authHeaders, workspaceId);
    // Clear deduplication state on new workspace connection.
    seenEventIdsRef.current.clear();

    const disconnect = connectAlertStream(headers, {
      onConnected: () => {
        sseConnectedRef.current = true;
      },
      onEvent: (event) => {
        // Deduplicate by event ID to avoid reprocessing after reconnect.
        if (seenEventIdsRef.current.has(event.eventId)) return;
        seenEventIdsRef.current.add(event.eventId);
        // Keep the seen-ID set bounded to avoid unbounded memory growth.
        if (seenEventIdsRef.current.size > MAX_SEEN_EVENT_IDS) {
          const oldest = seenEventIdsRef.current.values().next().value;
          if (oldest !== undefined) seenEventIdsRef.current.delete(oldest);
        }
        // Trigger an immediate force-refresh so the dashboard reflects the
        // new alert without waiting for the next polling interval.
        const fn = refreshFnRef.current;
        if (fn) void fn(true);
      },
      onHeartbeat: () => {
        // Heartbeat confirms the SSE transport is alive; no UI update needed.
      },
      onStatusChange: (status) => {
        setStreamStatus(status);
        if (status !== 'live') {
          sseConnectedRef.current = false;
        }
      },
    });

    return () => {
      sseConnectedRef.current = false;
      setStreamStatus('disconnected');
      disconnect();
    };
  }, [isAuthenticated, authHeaders, user?.current_workspace?.id]);

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
    streamStatus,
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
