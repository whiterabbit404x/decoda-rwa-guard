'use client';

import { useMemo } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import { hasLiveTelemetry } from './workspace-monitoring-truth';

export default function WorkspaceOwnershipBar() {
  const { user } = usePilotAuth();
  const feed = useLiveWorkspaceFeed();
  const truth = feed.monitoring.truth;
  const presentation = feed.monitoring.presentation;
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
        <span className="ruleChip">Protected assets: {truth.protected_assets_count}</span>
        <span className="ruleChip">Monitored systems: {truth.monitored_systems_count}</span>
        <span className="ruleChip">Alerts for this workspace: {truth.active_alerts_count}</span>
        <span className="ruleChip">Incidents affecting this workspace: {truth.active_incidents_count}</span>
      </div>
      <p className="tableMeta">
        Last telemetry: {presentation.telemetryTimestampLabel} · Last heartbeat: {presentation.heartbeatTimestampLabel} · Last poll: {presentation.pollTimestampLabel} · Monitoring health: {presentation.status.toLowerCase()} · Monitoring freshness: {presentation.freshness} ·
        fetch completed: {feed.monitoring.lastFetchCompletedAt ? new Date(feed.monitoring.lastFetchCompletedAt).toLocaleString() : 'pending'}.
        {telemetryFlowing ? ' Current telemetry is live for this workspace.' : ' Current telemetry is not yet live for this workspace.'}
        {feed.runtimeFetchWarning ? ' Refresh delayed; showing last confirmed monitoring snapshot.' : ''}
      </p>
    </section>
  );
}
