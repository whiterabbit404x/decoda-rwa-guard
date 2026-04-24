'use client';

import { useEffect, useState } from 'react';

import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
import { usePilotAuth } from './pilot-auth-context';
import { fetchRuntimeStatusDeduped } from './runtime-status-client';
import {
  hasLiveTelemetry,
  hasRealTelemetryBackedChain,
  resolveWorkspaceMonitoringTruth,
  type WorkspaceMonitoringTruth,
} from './workspace-monitoring-truth';

function formatTruthValue(value: unknown): string {
  const normalized = String(value ?? '').trim();
  if (!normalized) {
    return 'unavailable';
  }
  return normalized.replaceAll('_', ' ');
}

function timestampLine(label: string, value: string | null): string {
  if (!value) {
    return `${label}: unavailable`;
  }
  return `${label}: ${new Date(value).toLocaleString()}`;
}

function summarizeFromTruth(status: ReturnType<typeof normalizeMonitoringPresentation>['status'], truth: WorkspaceMonitoringTruth): string {
  const realChainVerified = hasRealTelemetryBackedChain(truth);
  if (status === 'offline') {
    return 'Workspace monitoring offline.';
  }
  if (status === 'limited coverage') {
    return 'Coverage currently limited for this workspace.';
  }
  if (status === 'degraded') {
    return 'Monitoring state degraded.';
  }
  if (status === 'stale' || truth.telemetry_freshness === 'stale') {
    return 'Monitoring data delayed.';
  }
  if (hasLiveTelemetry(truth) && realChainVerified) {
    return truth.active_incidents_count === 0
      ? 'No active incidents currently'
      : 'Monitoring state live with telemetry-backed detection chain visibility.';
  }
  return 'No linked real anomaly evidence yet; monitoring continuity is being restored.';
}

export default function WorkspaceMonitoringModeBanner({ apiUrl }: { apiUrl: string | null }) {
  const { authHeaders, isAuthenticated } = usePilotAuth();
  const [truth, setTruth] = useState<WorkspaceMonitoringTruth | null>(null);

  useEffect(() => {
    if (!isAuthenticated || !apiUrl) {
      return;
    }
    const load = async () => {
      try {
        const payload = await fetchRuntimeStatusDeduped(authHeaders());
        if (!payload) {
          setTruth(resolveWorkspaceMonitoringTruth(null));
          return;
        }
        setTruth(resolveWorkspaceMonitoringTruth(payload));
      } catch {
        setTruth(resolveWorkspaceMonitoringTruth(null));
      }
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
      <span>Freshness: {formatTruthValue(truth.telemetry_freshness)} · Confidence: {formatTruthValue(truth.confidence)}</span>
      <span>{timestampLine('Last telemetry', truth.last_telemetry_at)}</span>
      <span>
        {hasRealTelemetryBackedChain(truth)
          ? truth.active_incidents_count === 0
            ? 'No recent confirmed anomalies yet'
            : 'Detection chain verified from evidence through response action.'
          : 'Validate chain visibility for one real item: evidence → detection → alert → incident → response action.'}
      </span>
      <span>{timestampLine('Last heartbeat', truth.last_heartbeat_at)}</span>
      <span>{timestampLine('Last poll', truth.last_poll_at)}</span>
      {presentation.status === 'limited coverage' ? <span>Coverage currently limited.</span> : null}
      {presentation.status === 'offline' ? <span>Workspace coverage offline.</span> : null}
      {truth.telemetry_freshness === 'unavailable' ? <span>Fresh telemetry unavailable.</span> : null}
    </div>
  );
}
