'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

type Props = { apiUrl: string };

type TargetRow = {
  id: string;
  name: string;
  target_type?: string;
  contract_identifier?: string | null;
  wallet_address?: string | null;
  chain_network?: string | null;
  monitoring_enabled?: boolean;
  last_checked_at?: string | null;
  last_run_status?: string | null;
  asset_type?: string | null;
};

type AlertRow = {
  id: string;
  title: string;
  severity?: string;
  status?: string;
  created_at?: string;
};

type IncidentRow = {
  id: string;
  title?: string;
  event_type?: string;
  severity?: string;
  status?: string;
  created_at?: string;
};

type HistoryRun = {
  id: string;
  title: string;
  created_at: string;
};

type ActivityItem = {
  id: string;
  label: string;
  type: 'alert' | 'incident' | 'history';
  createdAt: string;
  href: string;
};

export default function ThreatOperationsPanel({ apiUrl }: Props) {
  const { authHeaders, isAuthenticated, user } = usePilotAuth();
  const feed = useLiveWorkspaceFeed();
  const [loadingSnapshot, setLoadingSnapshot] = useState(true);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [targets, setTargets] = useState<TargetRow[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [incidents, setIncidents] = useState<IncidentRow[]>([]);
  const [historyRuns, setHistoryRuns] = useState<HistoryRun[]>([]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function refreshSnapshot() {
      if (!active || !isAuthenticated || !user?.current_workspace?.id) {
        return;
      }
      try {
        const [targetsResponse, alertsResponse, incidentsResponse, historyResponse] = await Promise.all([
          fetch(`${apiUrl}/monitoring/targets`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/alerts?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/incidents?status_value=open`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/pilot/history?limit=10`, { headers: authHeaders(), cache: 'no-store' }),
        ]);
        if (!active) return;
        if (!targetsResponse.ok || !alertsResponse.ok || !incidentsResponse.ok || !historyResponse.ok) {
          throw new Error('Unable to refresh workspace monitoring snapshot.');
        }
        const targetsPayload = await targetsResponse.json();
        const alertsPayload = await alertsResponse.json();
        const incidentsPayload = await incidentsResponse.json();
        const historyPayload = await historyResponse.json();

        setTargets((targetsPayload.targets ?? []) as TargetRow[]);
        setAlerts((alertsPayload.alerts ?? []) as AlertRow[]);
        setIncidents((incidentsPayload.incidents ?? []) as IncidentRow[]);
        setHistoryRuns((historyPayload.analysis_runs ?? []) as HistoryRun[]);
        setSnapshotError(null);
      } catch (error) {
        if (active) {
          setSnapshotError(error instanceof Error ? error.message : 'Unable to refresh monitoring data.');
        }
      } finally {
        if (active) {
          setLoadingSnapshot(false);
        }
      }
    }

    function nextDelay() {
      return document.visibilityState === 'hidden' ? 60000 : 20000;
    }

    function schedule() {
      if (!active) return;
      timer = setTimeout(async () => {
        await refreshSnapshot();
        schedule();
      }, nextDelay());
    }

    void refreshSnapshot();
    schedule();

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void refreshSnapshot();
      }
    };
    window.addEventListener('pilot-history-refresh', onVisible as EventListener);
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      active = false;
      window.removeEventListener('pilot-history-refresh', onVisible as EventListener);
      document.removeEventListener('visibilitychange', onVisible);
      if (timer) clearTimeout(timer);
    };
  }, [apiUrl, authHeaders, isAuthenticated, user?.current_workspace?.id]);

  const protectedAssetCount = useMemo(() => new Set(targets.map((target) => target.asset_type || target.name).filter(Boolean)).size, [targets]);

  const activity = useMemo<ActivityItem[]>(() => {
    const alertItems = alerts.slice(0, 4).map((item) => ({
      id: `alert-${item.id}`,
      label: `Alert opened: ${item.title}`,
      type: 'alert' as const,
      createdAt: item.created_at || new Date(0).toISOString(),
      href: '/alerts',
    }));
    const incidentItems = incidents.slice(0, 4).map((item) => ({
      id: `incident-${item.id}`,
      label: `Incident active: ${item.title || item.event_type || item.id}`,
      type: 'incident' as const,
      createdAt: item.created_at || new Date(0).toISOString(),
      href: '/incidents',
    }));
    const historyItems = historyRuns.slice(0, 4).map((item) => ({
      id: `history-${item.id}`,
      label: `Checkpoint recorded: ${item.title}`,
      type: 'history' as const,
      createdAt: item.created_at || new Date(0).toISOString(),
      href: '/history',
    }));

    return [...alertItems, ...incidentItems, ...historyItems]
      .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
      .slice(0, 8);
  }, [alerts, historyRuns, incidents]);

  const statusBadge = feed.offline ? 'Offline' : feed.degraded ? 'Degraded' : feed.stale ? 'Stale' : 'Live';

  const monitoringPresentation = normalizeMonitoringPresentation(feed.runtimeStatus, {
    degraded: feed.degraded || Boolean(snapshotError),
    offline: feed.offline,
    stale: feed.stale,
  });

  return (
    <section className="stack compactStack">
      <article className="dataCard">
        <p className="sectionEyebrow">Threat monitoring state</p>
        <h2>This workspace is under continuous monitoring</h2>
        <p className="muted">Active workspace: {user?.current_workspace?.name ?? 'No active workspace selected'}.</p>
        <div className="chipRow">
          <span className="ruleChip">Monitoring mode: {feed.runtimeStatus ? monitoringPresentation.statusLabel : 'PENDING'}</span>
          <span className="ruleChip">Status: {statusBadge}</span>
          <span className="ruleChip">Protected assets: {loadingSnapshot ? '—' : protectedAssetCount}</span>
          <span className="ruleChip">Monitored targets: {feed.loading ? '—' : feed.counts.monitoredTargets}</span>
          <span className="ruleChip">Alerts for this workspace: {feed.loading ? '—' : feed.counts.openAlerts}</span>
          <span className="ruleChip">Incidents affecting this workspace: {feed.loading ? '—' : feed.counts.openIncidents}</span>
        </div>
        <p className="tableMeta">Last checkpoint: {feed.checkpointAgeSeconds != null ? `${feed.checkpointAgeSeconds}s ago` : 'pending'} · Last update: {feed.lastUpdatedAt ? new Date(feed.lastUpdatedAt).toLocaleString() : 'pending'}.</p>
        {feed.loading ? <p className="statusLine">Loading monitoring state…</p> : null}
        {feed.refreshing ? <p className="statusLine">Refreshing monitoring state…</p> : null}
        {monitoringPresentation.status === 'offline' ? <p className="statusLine">Workspace monitoring offline. Do not assume current protection coverage until connectivity returns.</p> : null}
        {monitoringPresentation.status === 'limited coverage' ? <p className="statusLine">Coverage currently limited. Validate open alerts and incidents before closure actions.</p> : null}
        {monitoringPresentation.status === 'degraded' ? <p className="statusLine">Monitoring state degraded. Validate evidence before taking closure actions.</p> : null}
        {monitoringPresentation.status === 'stale' ? <p className="statusLine">Monitoring data delayed. Await a fresh checkpoint before relying on this state.</p> : null}
      </article>

      <article className="dataCard">
        <div className="listHeader">
          <div>
            <p className="sectionEyebrow">Monitored systems</p>
            <h3>Protected assets and monitored targets</h3>
          </div>
          <Link href="/settings" prefetch={false}>Manage monitored systems</Link>
        </div>
        {loadingSnapshot ? <p className="muted">Loading monitored systems…</p> : null}
        {!loadingSnapshot && targets.length === 0 ? <p className="muted">No monitored systems are configured for this workspace yet.</p> : null}
        <div className="stack compactStack">
          {targets.slice(0, 8).map((target) => (
            <div key={target.id} className="overviewListItem">
              <div>
                <p>{target.name}</p>
                <p className="muted">{target.target_type ?? 'target'} · {target.chain_network ?? 'network n/a'} · {target.contract_identifier || target.wallet_address || 'identifier n/a'}</p>
              </div>
              <span className={`statusBadge ${target.monitoring_enabled ? 'statusBadge-live' : 'statusBadge-limited_coverage'}`}>
                {target.monitoring_enabled ? 'Active coverage' : 'Coverage paused'}
              </span>
            </div>
          ))}
        </div>
      </article>

      <article className="dataCard">
        <div className="listHeader">
          <div>
            <p className="sectionEyebrow">Recent activity</p>
            <h3>Changes in alerts, incidents, and checkpoints</h3>
          </div>
          <Link href="/history" prefetch={false}>Open workspace history</Link>
        </div>
        {loadingSnapshot ? <p className="muted">Loading recent changes…</p> : null}
        {!loadingSnapshot && activity.length === 0 ? <p className="muted">No recent activity has been recorded for this workspace yet.</p> : null}
        <div className="stack compactStack">
          {activity.map((item) => (
            <p key={item.id}>
              <Link href={item.href} prefetch={false}>{item.label}</Link>
              <br />
              <span className="muted">{new Date(item.createdAt).toLocaleString()}</span>
            </p>
          ))}
        </div>
      </article>

      <article className="dataCard">
        <p className="sectionEyebrow">Operator actions</p>
        <h3>Investigate and act from live workspace monitoring</h3>
        <p className="muted">Start from alerts, incidents, and history without running manual analysis.</p>
        <div className="buttonRow">
          <Link href="/alerts" prefetch={false}>Open alerts</Link>
          <Link href="/incidents" prefetch={false}>Open incidents</Link>
          <Link href="/history" prefetch={false}>Open history</Link>
          <Link href="/compliance" prefetch={false}>Open governance actions</Link>
        </div>
      </article>
    </section>
  );
}
