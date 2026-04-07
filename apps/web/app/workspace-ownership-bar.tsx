'use client';

import { useMemo } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

function feedLabel(offline: boolean, degraded: boolean, stale: boolean) {
  if (offline) return 'offline';
  if (degraded) return 'degraded';
  if (stale) return 'stale';
  return 'fresh';
}

export default function WorkspaceOwnershipBar() {
  const { user } = usePilotAuth();
  const feed = useLiveWorkspaceFeed();
  const role = useMemo(
    () => user?.memberships.find((membership) => membership.workspace_id === user?.current_workspace?.id)?.role ?? 'viewer',
    [user],
  );

  return (
    <section className="dataCard" style={{ marginBottom: 12 }}>
      <p className="sectionEyebrow">This workspace</p>
      <h2>{user?.current_workspace?.name ?? 'No active workspace selected'}</h2>
      <div className="chipRow">
        <span className="ruleChip">Role: {role}</span>
        <span className="ruleChip">Protected assets: {feed.counts.monitoredTargets}</span>
        <span className="ruleChip">Monitored systems: {feed.counts.monitoredTargets}</span>
        <span className="ruleChip">Alerts for this workspace: {feed.counts.openAlerts}</span>
        <span className="ruleChip">Incidents affecting this workspace: {feed.counts.openIncidents}</span>
      </div>
      <p className="tableMeta">
        Last checkpoint: {feed.checkpointAgeSeconds ?? 'n/a'}s ago · coverage freshness: {feedLabel(feed.offline, feed.degraded, feed.stale)} ·
        last update: {feed.lastUpdatedAt ? new Date(feed.lastUpdatedAt).toLocaleString() : 'pending'}.
        {feed.stale ? ' Evidence is stale for this workspace until fresh telemetry arrives.' : ' Live evidence is updating for this workspace.'}
      </p>
    </section>
  );
}
