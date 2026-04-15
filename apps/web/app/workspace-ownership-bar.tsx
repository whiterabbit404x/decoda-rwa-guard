'use client';

import { useMemo } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import { hasLiveTelemetry } from './workspace-monitoring-truth';

export default function WorkspaceOwnershipBar() {
  const { user } = usePilotAuth();
  const feed = useLiveWorkspaceFeed();
  const { truth, presentation } = feed;
  const role = useMemo(
    () => user?.memberships.find((membership) => membership.workspace_id === user?.current_workspace?.id)?.role ?? 'viewer',
    [user],
  );
  const telemetryFlowing = hasLiveTelemetry(truth);

  return (
    <section className="dataCard" style={{ marginBottom: 12 }}>
      <p className="sectionEyebrow">This workspace</p>
      <h2>{user?.current_workspace?.name ?? 'No active workspace selected'}</h2>
      <div className="chipRow">
        <span className="ruleChip">Role: {role}</span>
        <span className="ruleChip">Protected assets: {feed.counts.protectedAssets}</span>
        <span className="ruleChip">Monitored systems: {feed.counts.monitoredSystems}</span>
        <span className="ruleChip">Alerts for this workspace: {feed.counts.openAlerts}</span>
        <span className="ruleChip">Incidents affecting this workspace: {feed.counts.openIncidents}</span>
      </div>
      <p className="tableMeta">
        Last telemetry: {presentation.telemetryTimestampLabel} · Last heartbeat: {presentation.heartbeatTimestampLabel} · Last poll: {presentation.pollTimestampLabel} · Monitoring health: {presentation.status.toLowerCase()} · Monitoring freshness: {presentation.freshness} ·
        fetch completed: {feed.timings.lastFetchCompletedAt ? new Date(feed.timings.lastFetchCompletedAt).toLocaleString() : 'pending'}.
        {telemetryFlowing ? ' Current telemetry is live for this workspace.' : ' Current telemetry is not yet live for this workspace.'}
      </p>
    </section>
  );
}
