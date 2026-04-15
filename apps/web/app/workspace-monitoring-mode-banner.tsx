'use client';

import { useEffect, useState } from 'react';

import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
import { usePilotAuth } from './pilot-auth-context';
import { hasLiveTelemetry, resolveWorkspaceMonitoringTruth, type WorkspaceMonitoringTruth } from './workspace-monitoring-truth';

function formatTruthValue(value: string): string {
  return value.replaceAll('_', ' ');
}

function timestampLine(label: string, value: string | null): string {
  if (!value) {
    return `${label}: unavailable`;
  }
  return `${label}: ${new Date(value).toLocaleString()}`;
}

function summarizeFromTruth(status: ReturnType<typeof normalizeMonitoringPresentation>['status'], truth: WorkspaceMonitoringTruth): string {
  if (status === 'offline') {
    return 'Workspace monitoring offline.';
  }
  if (status === 'limited coverage') {
    return 'Coverage currently limited for this workspace.';
  }
  if (status === 'degraded') {
    return 'Monitoring state degraded.';
  }
  if (status === 'stale' || truth.freshness_status === 'stale') {
    return 'Monitoring data delayed.';
  }
  if (hasLiveTelemetry(truth)) {
    return 'Monitoring state live with verified telemetry.';
  }
  return 'Monitoring state active; telemetry verification pending.';
}

export default function WorkspaceMonitoringModeBanner({ apiUrl }: { apiUrl: string | null }) {
  const { authHeaders, isAuthenticated } = usePilotAuth();
  const [truth, setTruth] = useState<WorkspaceMonitoringTruth | null>(null);

  useEffect(() => {
    if (!isAuthenticated || !apiUrl) {
      return;
    }
    const load = async () => {
      const response = await fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      setTruth(resolveWorkspaceMonitoringTruth(payload));
    };
    void load();
  }, [apiUrl, authHeaders, isAuthenticated]);

  if (!truth) {
    return null;
  }

  const presentation = normalizeMonitoringPresentation(truth);
  const tone = presentation.status === 'live' ? 'statusBannerLive' : 'statusBannerDegraded';
  const summary = summarizeFromTruth(presentation.status, truth);

  return (
    <div className={`statusBanner ${tone}`}>
      <strong>{presentation.statusLabel}</strong>
      <span>Monitoring state: {summary}</span>
      <span>Freshness: {formatTruthValue(truth.freshness_status)} · Confidence: {formatTruthValue(truth.confidence_status)}</span>
      <span>{timestampLine('Last telemetry', truth.last_telemetry_at)}</span>
      <span>{timestampLine('Last heartbeat', truth.last_heartbeat_at)}</span>
      <span>{timestampLine('Last poll', truth.last_poll_at)}</span>
      {presentation.status === 'limited coverage' ? <span>Coverage currently limited.</span> : null}
      {presentation.status === 'offline' ? <span>Workspace coverage offline.</span> : null}
      {truth.freshness_status === 'unavailable' ? <span>Fresh telemetry unavailable.</span> : null}
    </div>
  );
}
