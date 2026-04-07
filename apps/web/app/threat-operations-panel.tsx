'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { normalizeMonitoringPresentation, type MonitoringPresentationStatus } from './monitoring-status-presentation';
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
  explanation?: string;
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

type ThreatSignal = {
  id: string;
  timestamp: string;
  severity: string;
  title: string;
  asset: string;
  explanation: string;
  state: 'New' | 'Investigating' | 'Resolved';
  href: string;
};

type TimelineItem = {
  id: string;
  timestamp: string;
  category: 'Alert' | 'Incident' | 'Checkpoint';
  description: string;
  href: string;
};

function formatRelativeTime(value?: string | null): string {
  if (!value) return 'Pending';
  const diffMs = Date.now() - new Date(value).getTime();
  if (Number.isNaN(diffMs) || diffMs < 0) return 'Pending';
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatAbsoluteTime(value?: string | null): string {
  if (!value) return 'Pending';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Pending' : date.toLocaleString();
}

function statusTone(status: MonitoringPresentationStatus): string {
  if (status === 'offline') return 'offline';
  if (status === 'degraded' || status === 'stale' || status === 'limited coverage') return 'attention';
  return 'healthy';
}

function severityClass(severity?: string) {
  const normalized = String(severity ?? '').toLowerCase();
  if (normalized.includes('critical')) return 'critical';
  if (normalized.includes('high')) return 'high';
  if (normalized.includes('medium')) return 'medium';
  return 'low';
}

function severityLabel(severity?: string) {
  const normalized = String(severity ?? '').toLowerCase();
  if (normalized.includes('critical')) return 'Critical';
  if (normalized.includes('high')) return 'High';
  if (normalized.includes('medium')) return 'Medium';
  return 'Low';
}

function coverageLabel(target: TargetRow): 'Full' | 'Partial' | 'Missing' | 'Stale' {
  if (!target.monitoring_enabled) return 'Missing';
  const lastChecked = target.last_checked_at ? new Date(target.last_checked_at).getTime() : null;
  if (!lastChecked) return 'Partial';
  if (Date.now() - lastChecked > 20 * 60 * 1000) return 'Stale';
  return 'Full';
}

function coverageClass(label: ReturnType<typeof coverageLabel>) {
  if (label === 'Full') return 'healthy';
  if (label === 'Partial' || label === 'Stale') return 'attention';
  return 'offline';
}

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
          fetch(`${apiUrl}/pilot/history?limit=12`, { headers: authHeaders(), cache: 'no-store' }),
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

  const monitoringPresentation = normalizeMonitoringPresentation(feed.runtimeStatus, {
    degraded: feed.degraded || Boolean(snapshotError),
    offline: feed.offline,
    stale: feed.stale,
  });

  const openAlerts = alerts.length;
  const activeIncidents = incidents.length;
  const protectedAssetCount = useMemo(
    () => new Set(targets.map((target) => target.asset_type || target.name).filter(Boolean)).size,
    [targets],
  );

  const threatSignals = useMemo<ThreatSignal[]>(() => {
    const fromAlerts = alerts.slice(0, 10).map((item) => ({
      id: `alert-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      severity: severityLabel(item.severity),
      title: item.title,
      asset: 'Alerted workspace asset',
      explanation: item.explanation || 'Alert condition triggered and is waiting for operator review.',
      state: 'New' as const,
      href: '/alerts',
    }));
    const fromIncidents = incidents.slice(0, 10).map((item) => ({
      id: `incident-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      severity: severityLabel(item.severity),
      title: item.title || item.event_type || 'Incident escalation',
      asset: 'Incident response queue',
      explanation: 'An active incident is open for investigation and containment tracking.',
      state: 'Investigating' as const,
      href: '/incidents',
    }));

    return [...fromAlerts, ...fromIncidents]
      .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
      .slice(0, 8);
  }, [alerts, incidents]);

  const timelineItems = useMemo<TimelineItem[]>(() => {
    const alertItems = alerts.slice(0, 6).map((item) => ({
      id: `alert-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Alert' as const,
      description: item.title,
      href: '/alerts',
    }));
    const incidentItems = incidents.slice(0, 6).map((item) => ({
      id: `incident-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Incident' as const,
      description: item.title || item.event_type || 'Incident opened',
      href: '/incidents',
    }));
    const historyItems = historyRuns.slice(0, 6).map((item) => ({
      id: `history-${item.id}`,
      timestamp: item.created_at || new Date(0).toISOString(),
      category: 'Checkpoint' as const,
      description: item.title,
      href: '/history',
    }));

    return [...alertItems, ...incidentItems, ...historyItems]
      .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
      .slice(0, 12);
  }, [alerts, historyRuns, incidents]);

  const reportingSystems = targets.filter((target) => target.monitoring_enabled).length;
  const latestRiskScore = useMemo(() => {
    if (alerts.some((item) => severityClass(item.severity) === 'critical')) return { value: 92, tier: 'High' };
    if (alerts.some((item) => severityClass(item.severity) === 'high')) return { value: 78, tier: 'Elevated' };
    if (alerts.length > 0 || incidents.length > 0) return { value: 62, tier: 'Guarded' };
    return { value: 28, tier: 'Low' };
  }, [alerts, incidents]);

  const statusSummary =
    openAlerts > 0
      ? `Continuous monitoring is active across ${reportingSystems} monitored systems. ${openAlerts} open alert${openAlerts === 1 ? '' : 's'} require review.`
      : `Continuous monitoring is active across ${reportingSystems} monitored systems with no open alerts.`;

  const lastTelemetryLabel = feed.checkpointAgeSeconds != null
    ? `${feed.checkpointAgeSeconds}s ago`
    : formatRelativeTime(feed.lastUpdatedAt);

  return (
    <section className="stack monitoringConsoleStack">
      <article className="dataCard monitoringHeaderCard">
        <div className="monitoringHeaderTop">
          <div>
            <p className="sectionEyebrow">Threat monitoring command center</p>
            <h2>{user?.current_workspace?.name ?? 'Workspace monitoring console'}</h2>
          </div>
          <div className="monitoringHeaderActions">
            <Link href="/settings" prefetch={false} className="secondaryCta">Manage monitored systems</Link>
            <Link href="/alerts" prefetch={false} className="secondaryCta">View alerts</Link>
            <Link href="/incidents" prefetch={false} className="secondaryCta">Open incidents</Link>
          </div>
        </div>
        <div className="chipRow monitoringHeaderChips">
          <span className={`statusBadge statusBadge-${statusTone(monitoringPresentation.status)}`}>{monitoringPresentation.status === 'live' ? 'Healthy' : monitoringPresentation.status === 'stale' ? 'Attention needed' : monitoringPresentation.status === 'limited coverage' ? 'Attention needed' : monitoringPresentation.status === 'degraded' ? 'Degraded' : 'Offline'}</span>
          <span className="ruleChip">Last telemetry {lastTelemetryLabel}</span>
          <span className="ruleChip">Protected assets {protectedAssetCount}</span>
          <span className="ruleChip">Monitored systems {feed.counts.monitoredTargets}</span>
          <span className="ruleChip">Open alerts {feed.counts.openAlerts}</span>
          <span className="ruleChip">Active incidents {feed.counts.openIncidents}</span>
        </div>
        <p className="explanation">{statusSummary}</p>
        <p className="tableMeta">Updated {formatRelativeTime(feed.lastUpdatedAt)} · Last checkpoint {monitoringPresentation.lastCheckpointLabel}</p>
        {feed.loading ? <p className="statusLine">Loading monitoring state…</p> : null}
        {feed.refreshing ? <p className="statusLine">Refreshing monitoring state…</p> : null}
        {snapshotError ? <p className="statusLine">{snapshotError}</p> : null}
      </article>

      <section className="monitoringKpiGrid" aria-label="Monitoring KPIs">
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Monitoring Health</p>
          <p className="kpiValue">{monitoringPresentation.status === 'live' ? 'Healthy' : monitoringPresentation.status === 'limited coverage' ? 'Attention needed' : monitoringPresentation.status === 'stale' ? 'Attention needed' : monitoringPresentation.statusLabel}</p>
          <p className="tableMeta">{monitoringPresentation.summary}</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Protected Assets</p>
          <p className="kpiValue">{loadingSnapshot ? '—' : protectedAssetCount}</p>
          <p className="tableMeta">Assets with active monitoring definitions.</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Open Alerts</p>
          <p className="kpiValue">{loadingSnapshot ? '—' : openAlerts}</p>
          <p className="tableMeta"><Link href="/alerts" prefetch={false}>Review alert queue</Link></p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Active Incidents</p>
          <p className="kpiValue">{loadingSnapshot ? '—' : activeIncidents}</p>
          <p className="tableMeta"><Link href="/incidents" prefetch={false}>Open incident queue</Link></p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Latest Risk Score</p>
          <p className="kpiValue">{latestRiskScore.value}</p>
          <p className="tableMeta">{latestRiskScore.tier} operational risk posture.</p>
        </article>
        <article className="dataCard kpiCard">
          <p className="sectionEyebrow">Coverage Status</p>
          <p className="kpiValue">{reportingSystems} of {targets.length || 0}</p>
          <p className="tableMeta">Systems reporting telemetry.</p>
        </article>
      </section>

      <article className="dataCard">
        <div className="listHeader">
          <div>
            <p className="sectionEyebrow">Live Threat Feed</p>
            <h3>Active threat signals</h3>
          </div>
          <Link href="/alerts" prefetch={false}>Review alerts</Link>
        </div>
        {loadingSnapshot ? <p className="muted">Loading active threat signals…</p> : null}
        {!loadingSnapshot && threatSignals.length === 0 ? (
          <div className="emptyStatePanel">
            <h4>No active threat signals yet</h4>
            <p className="muted">Monitoring is running, but no qualifying anomalies have been recorded for this workspace.</p>
            <div className="buttonRow">
              <Link href="/settings" prefetch={false}>Add monitored systems</Link>
              <Link href="/settings" prefetch={false}>Review detection coverage</Link>
              <Link href="/alerts" prefetch={false}>Open alert history</Link>
            </div>
          </div>
        ) : null}
        <div className="stack compactStack">
          {threatSignals.map((signal) => (
            <div key={signal.id} className="overviewListItem signalRow">
              <div>
                <p className="signalTitle"><span className={`statusBadge statusBadge-${severityClass(signal.severity)}`}>{signal.severity}</span> {signal.title}</p>
                <p className="muted">{signal.asset} · {signal.explanation}</p>
                <p className="tableMeta">{formatAbsoluteTime(signal.timestamp)} · {formatRelativeTime(signal.timestamp)}</p>
              </div>
              <div className="signalActions">
                <span className="ruleChip">{signal.state}</span>
                <Link href={signal.href} prefetch={false}>Review</Link>
              </div>
            </div>
          ))}
        </div>
      </article>

      <section className="twoColumnSection monitoringLowerGrid">
        <article className="dataCard">
          <div className="listHeader">
            <div>
              <p className="sectionEyebrow">Protected Assets & Coverage</p>
              <h3>System coverage and telemetry health</h3>
            </div>
            <Link href="/settings" prefetch={false}>Manage monitored systems</Link>
          </div>
          {loadingSnapshot ? <p className="muted">Loading monitored systems…</p> : null}
          {!loadingSnapshot && targets.length === 0 ? (
            <div className="emptyStatePanel">
              <h4>No monitored systems configured</h4>
              <p className="muted">Active monitoring requires at least one protected system before telemetry and detections can be evaluated.</p>
              <div className="buttonRow">
                <Link href="/settings" prefetch={false}>Add first monitored system</Link>
                <Link href="/help" prefetch={false}>View setup guide</Link>
              </div>
            </div>
          ) : null}
          {targets.length > 0 ? (
            <div className="tableWrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset/System</th>
                    <th>Type</th>
                    <th>Health</th>
                    <th>Coverage</th>
                    <th>Latest Signal</th>
                    <th>Last Seen</th>
                    <th>Risk</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {targets.slice(0, 10).map((target) => {
                    const coverage = coverageLabel(target);
                    const risk = openAlerts > 0 ? 'High' : 'Low';
                    return (
                      <tr key={target.id}>
                        <td>{target.name}<span className="tableMeta">{target.contract_identifier || target.wallet_address || 'Identifier unavailable'}</span></td>
                        <td>{target.target_type || target.asset_type || 'system'}</td>
                        <td><span className={`statusBadge statusBadge-${target.monitoring_enabled ? 'healthy' : 'offline'}`}>{target.monitoring_enabled ? 'Healthy' : 'Offline'}</span></td>
                        <td><span className={`statusBadge statusBadge-${coverageClass(coverage)}`}>{coverage}</span></td>
                        <td>{alerts[0]?.title || 'No active signals'}</td>
                        <td>{formatRelativeTime(target.last_checked_at)}</td>
                        <td><span className={`statusBadge statusBadge-${risk === 'High' ? 'high' : 'low'}`}>{risk}</span></td>
                        <td><Link href="/settings" prefetch={false}>Review</Link></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </article>

        <div className="stack compactStack">
          <article className="dataCard">
            <div className="listHeader">
              <div>
                <p className="sectionEyebrow">Detection Evidence</p>
                <h3>Recent detection outcomes</h3>
              </div>
              <Link href="/history" prefetch={false}>View workspace history</Link>
            </div>
            {alerts.length === 0 && incidents.length === 0 ? (
              <div className="emptyStatePanel">
                <h4>No detection evidence recorded yet</h4>
                <p className="muted">Monitoring is active. Evidence items appear here automatically when alerts or incidents are created.</p>
              </div>
            ) : (
              <div className="stack compactStack">
                {alerts.slice(0, 3).map((alert) => (
                  <div key={alert.id} className="overviewListItem">
                    <div>
                      <p>{alert.title}</p>
                      <p className="muted">Impacted asset: Alerted workspace asset · Recommended action: Validate signal context and assign reviewer.</p>
                      <p className="tableMeta">Incident opened: {activeIncidents > 0 ? 'Yes' : 'No'} · Governance follow-up: {historyRuns.length > 0 ? 'Recorded' : 'Pending'}</p>
                    </div>
                    <span className={`statusBadge statusBadge-${severityClass(alert.severity)}`}>{severityLabel(alert.severity)}</span>
                  </div>
                ))}
              </div>
            )}
          </article>

          <article className="dataCard">
            <div className="listHeader">
              <div>
                <p className="sectionEyebrow">Investigation Timeline</p>
                <h3>Recent monitoring activity</h3>
              </div>
              <Link href="/history" prefetch={false}>Open full history</Link>
            </div>
            {timelineItems.length === 0 ? (
              <div className="emptyStatePanel">
                <h4>No alerts, incidents, or checkpoints yet</h4>
                <p className="muted">Monitoring is active. As events arrive, this timeline will populate with linked investigation history.</p>
              </div>
            ) : (
              <div className="stack compactStack">
                {timelineItems.map((item) => (
                  <div key={item.id} className="overviewListItem">
                    <div>
                      <p>{item.description}</p>
                      <p className="tableMeta">{item.category} · {formatAbsoluteTime(item.timestamp)}</p>
                    </div>
                    <Link href={item.href} prefetch={false}>Open</Link>
                  </div>
                ))}
              </div>
            )}
          </article>

          <article className="dataCard">
            <p className="sectionEyebrow">Action Center</p>
            <h3>Investigation and response actions</h3>
            <p className="muted">Use these queues to triage risk, coordinate response, and verify governance follow-through.</p>
            <div className="buttonRow">
              <Link href="/alerts" prefetch={false}>Review alerts</Link>
              <Link href="/incidents" prefetch={false}>Open incident queue</Link>
              <Link href="/history" prefetch={false}>View workspace history</Link>
              <Link href="/settings" prefetch={false}>Manage monitored systems</Link>
              <Link href="/compliance" prefetch={false}>Review governance actions</Link>
            </div>
          </article>
        </div>
      </section>
    </section>
  );
}
