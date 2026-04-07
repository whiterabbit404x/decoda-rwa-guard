'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { monitoringModeLabel, normalizeMonitoringMode, type MonitoringRuntimeStatus } from './monitoring-status-contract';

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

  const modeLabel = monitoringModeLabel(status.mode);
  const noEvidence = (status.recent_real_event_count ?? 0) <= 0 || status.recent_truthfulness_state === 'unknown_risk';
  const degraded = status.mode === 'DEGRADED' || status.mode === 'LIMITED_COVERAGE' || noEvidence;
  const tone = degraded ? 'statusBannerDegraded' : status.mode === 'OFFLINE' ? 'statusBannerDegraded' : 'statusBannerLive';
  const evidenceCopy = status.recent_evidence_state === 'real'
    ? 'Monitoring is operating with recent confirmed evidence.'
    : status.recent_evidence_state === 'degraded' || status.recent_evidence_state === 'failed'
      ? 'Coverage currently degraded.'
      : 'Live evidence unavailable.';

  return (
    <div className={`statusBanner ${tone}`}>
      <strong>{modeLabel}</strong>
      <span>
        configured={status.configured_mode ?? 'n/a'} · source={status.source_type ?? 'unknown'} · lag={status.checkpoint_lag_blocks ?? 'n/a'} · claims={status.sales_claims_allowed ? 'allowed' : 'blocked'}
      </span>
      <span>
        evidence={status.recent_evidence_state ?? 'missing'} · confidence={status.recent_confidence_basis ?? 'none'} · coverage={status.synthetic_leak_detected ? 'limited' : 'verified'}
      </span>
      <span>{evidenceCopy}</span>
      {status.degraded_reason ? <span>reason={status.degraded_reason}</span> : null}
    </div>
  );
}
