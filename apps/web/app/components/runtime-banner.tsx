'use client';

import { hasLiveTelemetry, hasRealTelemetryBackedChain } from '../workspace-monitoring-truth';
import { useRuntimeSummary } from '../runtime-summary-context';

function formatAge(iso: string | null): string {
  if (!iso) return 'never';
  const diffMs = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diffMs / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

function formatStatus(value: string): string {
  return value.replaceAll('_', ' ');
}

type BannerField = { label: string; value: string };

function Field({ label, value }: BannerField) {
  return (
    <span className="runtimeBannerField">
      <span className="runtimeBannerLabel">{label}</span>
      <span className="runtimeBannerValue">{value}</span>
    </span>
  );
}

function Sep() {
  return <span className="runtimeBannerSep" aria-hidden="true">·</span>;
}

export default function RuntimeBanner() {
  const { summary, loading, nextActionLabel, reasonMessageForCode } = useRuntimeSummary();

  if (loading) return null;

  const topReason = summary.continuity_reason_codes?.[0] ?? summary.status_reason;
  const reasonCopy = topReason ? reasonMessageForCode(topReason) : null;

  const healthProvable =
    summary.runtime_status === 'live'
    && summary.monitoring_status === 'live'
    && summary.telemetry_freshness === 'fresh'
    && summary.confidence === 'high'
    && hasLiveTelemetry(summary)
    && hasRealTelemetryBackedChain(summary)
    && !topReason;

  const monitoringValue = healthProvable ? 'Live' : formatStatus(summary.monitoring_status ?? 'unverified');
  const freshnessValue  = healthProvable ? 'Fresh' : formatStatus(summary.telemetry_freshness ?? 'unknown');
  const confidenceValue = healthProvable ? 'High'  : formatStatus(summary.confidence ?? 'low');

  const toneClass = healthProvable
    ? 'runtimeBannerLive'
    : summary.monitoring_status === 'degraded'
      ? 'runtimeBannerStale'
      : 'runtimeBannerDead';

  return (
    <section
      className={`runtimeBanner ${toneClass}`}
      aria-label="Monitoring runtime status"
      aria-live="polite"
    >
      <Field label="Monitoring" value={monitoringValue} />
      <Sep />
      <Field label="Freshness" value={freshnessValue} />
      <Sep />
      <Field label="Confidence" value={confidenceValue} />
      <Sep />
      <Field label="Telemetry" value={formatAge(summary.last_telemetry_at)} />
      <Sep />
      <Field label="Heartbeat" value={formatAge(summary.last_heartbeat_at)} />
      <Sep />
      <Field label="Poll" value={formatAge(summary.last_poll_at)} />
      <Sep />
      <Field label="Next action" value={nextActionLabel} />
      {reasonCopy ? (
        <>
          <Sep />
          <Field label="Limitation" value={reasonCopy} />
        </>
      ) : null}
      {!healthProvable ? (
        <>
          <Sep />
          <span className="runtimeBannerField">
            <span className="runtimeBannerLabel" style={{ color: 'var(--warning-fg)' }}>
              Live/healthy display disabled until telemetry verified
            </span>
          </span>
        </>
      ) : null}
    </section>
  );
}
