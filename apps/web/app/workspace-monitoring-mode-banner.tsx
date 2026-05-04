'use client';

import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
import { hasLiveTelemetry, hasRealTelemetryBackedChain } from './workspace-monitoring-truth';
import { useRuntimeSummary } from './runtime-summary-context';

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

export default function WorkspaceMonitoringModeBanner({ apiUrl: _apiUrl }: { apiUrl: string | null }) {
  const { summary: truth, loading, missingLabel } = useRuntimeSummary();
  if (loading) {
    return null;
  }

  const presentation = normalizeMonitoringPresentation(truth);
  const tone = presentation.status === 'live' ? 'statusBannerLive' : 'statusBannerDegraded';

  return (
    <div className={`statusBanner ${tone}`}>
      <strong>{presentation.statusLabel}</strong>
      <span>Monitoring state: {missingLabel}</span>
      <span>Freshness: {formatTruthValue(truth.telemetry_freshness)} · Confidence: {formatTruthValue(truth.confidence)}</span>
      <span>{timestampLine('Last telemetry', truth.last_telemetry_at)}</span>
      <span>
        {hasLiveTelemetry(truth) && hasRealTelemetryBackedChain(truth)
          ? 'Detection chain verified from evidence through response action.'
          : 'Validate chain visibility for one real item: evidence → detection → alert → incident → response action.'}
      </span>
      <span>{timestampLine('Last heartbeat', truth.last_heartbeat_at)}</span>
      <span>{timestampLine('Last poll', truth.last_poll_at)}</span>
    </div>
  );
}
