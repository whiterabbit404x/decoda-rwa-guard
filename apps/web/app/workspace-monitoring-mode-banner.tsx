'use client';

import { useEffect, useState } from 'react';

import { normalizeMonitoringMode, type MonitoringRuntimeStatus } from './monitoring-status-contract';
import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
import { usePilotAuth } from './pilot-auth-context';

export default function WorkspaceMonitoringModeBanner({ apiUrl }: { apiUrl: string | null }) {
  const { authHeaders, isAuthenticated } = usePilotAuth();
  const [status, setStatus] = useState<MonitoringRuntimeStatus | null>(null);

  useEffect(() => {
    if (!isAuthenticated || !apiUrl) {
      return;
    }
    const load = async () => {
      const response = await fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' });
      if (!response.ok) {
        return;
      }
      const payload = await response.json() as MonitoringRuntimeStatus;
      setStatus({
        ...payload,
        mode: normalizeMonitoringMode(payload.mode),
        configured_mode: normalizeMonitoringMode(payload.configured_mode),
      });
    };
    void load();
  }, [apiUrl, authHeaders, isAuthenticated]);

  if (!status) {
    return null;
  }

  const presentation = normalizeMonitoringPresentation(status, {
    degraded: status.mode === 'DEGRADED' || Boolean(status.degraded_reason),
    offline: status.mode === 'OFFLINE',
    stale: status.mode === 'STALE',
  });

  const tone = presentation.status === 'live' ? 'statusBannerLive' : 'statusBannerDegraded';

  return (
    <div className={`statusBanner ${tone}`}>
      <strong>{presentation.statusLabel}</strong>
      <span>Monitoring state: {presentation.summary}</span>
      <span>Freshness: {presentation.freshness} · Confidence: {presentation.confidence}</span>
      <span>Last confirmed checkpoint: {presentation.lastCheckpointLabel}</span>
      {presentation.status === 'limited coverage' ? <span>Coverage currently limited.</span> : null}
      {presentation.status === 'offline' ? <span>Workspace coverage offline.</span> : null}
      {presentation.freshness === 'unavailable' ? <span>Fresh telemetry unavailable.</span> : null}
    </div>
  );
}
