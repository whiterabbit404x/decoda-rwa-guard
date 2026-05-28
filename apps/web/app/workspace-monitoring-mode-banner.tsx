'use client';

import { hasLiveTelemetry, hasRealTelemetryBackedChain } from './workspace-monitoring-truth';
import { useRuntimeSummary } from './runtime-summary-context';
import type { WorkspaceMonitoringTruth } from './workspace-monitoring-truth';

type BannerState = 'LIVE' | 'LIMITED_COVERAGE' | 'SETUP_REQUIRED' | 'OFFLINE';

function deriveBannerState(truth: WorkspaceMonitoringTruth): BannerState {
  if (hasLiveTelemetry(truth) && hasRealTelemetryBackedChain(truth)) return 'LIVE';
  if (truth.db_failure_reason) return 'OFFLINE';
  // Only show OFFLINE when the API returned a confirmed offline status — not when the
  // runtime-status call itself failed (status_reason === 'summary_unavailable').
  const runtimeApiMissing = truth.status_reason === 'summary_unavailable';
  if (!runtimeApiMissing && truth.runtime_status === 'offline' && truth.protected_assets_count === 0 && !truth.last_heartbeat_at) return 'OFFLINE';
  if (!truth.workspace_configured || truth.protected_assets_count === 0) return 'SETUP_REQUIRED';
  if (truth.reporting_systems_count === 0 && !truth.last_poll_at && !truth.last_heartbeat_at) return 'SETUP_REQUIRED';
  return 'LIMITED_COVERAGE';
}

type BannerMessage = { headline: string; subtext: string };

function bannerMessage(state: BannerState, truth: WorkspaceMonitoringTruth): BannerMessage {
  const runtimeApiMissing = truth.status_reason === 'summary_unavailable';
  if (state === 'LIVE') {
    return {
      headline: 'Live monitoring active.',
      subtext: 'Telemetry, detection, alert, incident, and evidence flow are verified.',
    };
  }
  if (state === 'OFFLINE') {
    return {
      headline: truth.db_failure_reason
        ? 'Runtime offline: database is unavailable.'
        : 'Runtime offline: backend or worker is unreachable.',
      subtext: 'Check API, database, worker, and workspace connectivity.',
    };
  }
  if (state === 'SETUP_REQUIRED') {
    if (runtimeApiMissing) {
      return {
        headline: 'Monitoring status unavailable.',
        subtext: 'Verify API connectivity and workspace configuration.',
      };
    }
    if (truth.protected_assets_count === 0) {
      return {
        headline: 'Setup required: no protected assets registered.',
        subtext: 'Add an asset to begin monitoring.',
      };
    }
    return {
      headline: 'Setup required: protected asset found, but live telemetry is not connected yet.',
      subtext: 'Complete provider, worker, and telemetry verification to activate live monitoring.',
    };
  }
  // LIMITED_COVERAGE
  return {
    headline: 'Limited coverage: asset is configured, but live telemetry is missing or stale.',
    subtext: 'Monitoring will become live after provider polling and telemetry verification.',
  };
}

function stateColor(state: BannerState): string {
  if (state === 'LIVE') return 'var(--success-fg, #16a34a)';
  if (state === 'OFFLINE') return 'var(--danger-fg, #dc2626)';
  return 'var(--warning-fg, #d97706)';
}

function stateLabel(state: BannerState): string {
  if (state === 'LIVE') return 'LIVE';
  if (state === 'LIMITED_COVERAGE') return 'LIMITED COVERAGE';
  if (state === 'SETUP_REQUIRED') return 'SETUP REQUIRED';
  return 'OFFLINE';
}

export default function WorkspaceMonitoringModeBanner({ apiUrl: _apiUrl }: { apiUrl: string | null }) {
  const { summary: truth, loading } = useRuntimeSummary();
  if (loading) return null;

  const state = deriveBannerState(truth);
  const { headline, subtext } = bannerMessage(state, truth);
  const color = stateColor(state);

  return (
    <div className="statusBanner" style={{ borderLeftColor: color }}>
      <strong style={{ color, fontSize: '0.75rem', letterSpacing: '0.05em' }}>{stateLabel(state)}</strong>
      <span style={{ fontSize: '0.8rem' }}>{headline}</span>
      <span style={{ fontSize: '0.75rem', opacity: 0.75 }}>{subtext}</span>
    </div>
  );
}
