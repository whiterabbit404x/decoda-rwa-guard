'use client';

import { useMemo } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';

function feedLabel(offline: boolean, degraded: boolean) {
  if (offline) return 'Offline';
  if (degraded) return 'Degraded';
  return 'Live';
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
        <span className="ruleChip">Protected assets / monitored targets: {feed.counts.monitoredTargets}</span>
        <span className="ruleChip">Open alerts: {feed.counts.openAlerts}</span>
        <span className="ruleChip">Open incidents: {feed.counts.openIncidents}</span>
        <span className="ruleChip">Workspace history records: {feed.counts.historyRecords}</span>
      </div>
      <p className="tableMeta">
        Monitoring state: {feedLabel(feed.offline, feed.degraded)} · last update: {feed.lastUpdatedAt ? new Date(feed.lastUpdatedAt).toLocaleString() : 'pending'} · checkpoint age: {feed.checkpointAgeSeconds ?? 'n/a'}s.
        {feed.stale ? ' Evidence is stale for this workspace until fresh telemetry arrives.' : ' Live evidence is updating for this workspace.'}
      </p>
    </section>
  );
}

