import { type LiveChainMonitoring, type RealtimeIngestionPath } from './types';
import { diagnosisVariant, formatDateTime, statusBadgeClass, statusLabel } from './helpers';

type Props = {
  chainMonitoring: LiveChainMonitoring | null;
};

function realtimeVariant(status: RealtimeIngestionPath['status']): string {
  if (status === 'active') return 'healthy';
  if (status === 'degraded') return 'degraded';
  if (status === 'rate_limited') return 'degraded';
  return 'unavailable';
}

function RealtimePathRow({ realtime }: { realtime: RealtimeIngestionPath }) {
  const variant = realtimeVariant(realtime.status);
  const isRateLimited = realtime.status === 'rate_limited';
  const isDegradedWithFallback = realtime.status === 'degraded' && realtime.fallback_active === true;
  // Two lanes, read side by side: the realtime stream's own state (with its
  // reason) and — only when the backend reports it — the fallback that is
  // carrying detection meanwhile. A degraded stream stays visibly degraded; the
  // fallback lane explains coverage, it never repaints the stream as healthy.
  return (
    <div className="shPathLanes">
      <div className="shPathLane" data-variant={variant}>
        <span className="shPathLaneName">Realtime path</span>
        <span className="shPathLaneBody">
          <span className={`${statusBadgeClass(variant)} shBadgeSm`}>
            {realtime.label}
          </span>
          {realtime.status !== 'disabled' && realtime.events_processed != null && (
            <span className="shPathLaneMeta">
              {realtime.events_processed.toLocaleString()} events &middot; {realtime.reconnect_count ?? 0} reconnects
            </span>
          )}
          {isRateLimited ? (
            <>
              <span className="shPathLaneReason">
                Provider: {realtime.provider ?? 'QuickNode'} &middot; rate limited
              </span>
              {realtime.next_retry_at && (
                <span className="shPathLaneMeta">
                  Next retry: {formatDateTime(realtime.next_retry_at)}
                </span>
              )}
            </>
          ) : (
            realtime.degraded_reason && (
              <span className="shPathLaneReason">
                {realtime.degraded_reason}
              </span>
            )
          )}
        </span>
      </div>
      {isRateLimited ? (
        <div className="shPathLane shPathLane-fallback">
          <span className="shPathLaneName">Fallback</span>
          <span className="shPathLaneBody">
            <span className="shPathLaneFallback">Stable polling: Active</span>
          </span>
        </div>
      ) : isDegradedWithFallback ? (
        <div className="shPathLane shPathLane-fallback">
          <span className="shPathLaneName">Fallback</span>
          <span className="shPathLaneBody">
            <span className="shPathLaneFallback">
              Fallback: {realtime.worker_provider_mode === 'quicknode_http_fast_tail'
                ? 'HTTP fast-tail'
                : 'Stable RPC polling'} active
            </span>
          </span>
        </div>
      ) : null}
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="shMetricRow">
      <span className="shMetricLabel">{label}</span>
      <span className="shMetricValue">{value}</span>
    </div>
  );
}

export function LiveChainMonitoringPanel({ chainMonitoring }: Props) {
  if (!chainMonitoring) {
    return (
      <section className="dataCard featureSection shSection">
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Base chain</p>
            <h2>Live Chain Monitoring</h2>
          </div>
        </div>
        <div className="shEmptyState">
          <div className="shEmptyIcon" aria-hidden="true">!</div>
          <p className="shEmptyText">Live chain monitoring data unavailable.</p>
          <p className="shEmptySubtext">Component check missing from backend response.</p>
        </div>
      </section>
    );
  }

  const variant = diagnosisVariant(chainMonitoring.diagnosis);

  return (
    <section className="dataCard featureSection shSection shChainSection">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">
            Base chain &middot; Chain ID {chainMonitoring.expected_chain_id}
          </p>
          <h2>Live Chain Monitoring</h2>
        </div>
      </div>

      <div className="twoColumnSection shChainPanel">
        <div className={`shDiagnosisCard shDiagnosisCard-${variant}`}>
          <div className="shDiagnosisHeader">
            <p className="shDiagnosisEyebrow">Monitoring Diagnosis</p>
            <span className={`${statusBadgeClass(variant)} shBadgeSm`}>
              {statusLabel(variant)}
            </span>
          </div>
          <p className="shDiagnosisText">{chainMonitoring.diagnosis}</p>

          <div className="shDiagnosisDetails">
            <MetricRow
              label="Worker enabled"
              value={chainMonitoring.worker_enabled ? 'Yes' : 'No'}
            />
            {chainMonitoring.worker_enabled && chainMonitoring.worker_enabled_source &&
              chainMonitoring.worker_enabled_source !== 'none' && (
                <MetricRow label="Enabled via" value={chainMonitoring.worker_enabled_source} />
              )}
            <MetricRow
              label="RPC configured"
              value={chainMonitoring.rpc_configured ? 'Yes' : 'No'}
            />
            <MetricRow label="Expected chain ID" value={String(chainMonitoring.expected_chain_id)} />
            {chainMonitoring.latest_rpc_block && (
              <MetricRow label="Latest RPC block" value={chainMonitoring.latest_rpc_block} />
            )}
          </div>
        </div>

        <div className="shChainMetrics">
          <p className="sectionEyebrow shChainMetricsTitle">Monitoring Metrics</p>
          {/* Three separate proofs, never merged: a heartbeat proves the worker is
              alive, a poll proves the monitoring loop ran, telemetry proves chain
              data actually arrived. */}
          <div className="shChainGroups">
            <div className="shChainGroup">
              <p className="shChainGroupTitle">Worker heartbeat</p>
              <div className="shMetricsGrid">
                <MetricRow
                  label="Heartbeat age"
                  value={chainMonitoring.heartbeat_age_human ?? '—'}
                />
                <MetricRow
                  label="Poll interval (backup)"
                  value={`${chainMonitoring.polling_interval_seconds}s`}
                />
              </div>
            </div>
            <div className="shChainGroup">
              <p className="shChainGroupTitle">Polling loop</p>
              <div className="shMetricsGrid">
                <MetricRow
                  label="Last poll"
                  value={
                    chainMonitoring.last_poll_at ? formatDateTime(chainMonitoring.last_poll_at) : '—'
                  }
                />
                <MetricRow
                  label="Last successful poll"
                  value={
                    chainMonitoring.last_successful_poll_at
                      ? formatDateTime(chainMonitoring.last_successful_poll_at)
                      : '—'
                  }
                />
                {chainMonitoring.latest_polled_block != null && (
                  <MetricRow
                    label="Latest polled block"
                    value={`#${chainMonitoring.latest_polled_block}`}
                  />
                )}
              </div>
            </div>
            <div className="shChainGroup">
              <p className="shChainGroupTitle">Telemetry &amp; detection</p>
              <div className="shMetricsGrid">
                <MetricRow
                  label="Last telemetry"
                  value={
                    chainMonitoring.last_telemetry_at
                      ? formatDateTime(chainMonitoring.last_telemetry_at)
                      : '—'
                  }
                />
                <MetricRow
                  label="Telemetry 1h / 24h"
                  value={`${chainMonitoring.recent_telemetry_1h} / ${chainMonitoring.recent_telemetry_24h}`}
                />
                <MetricRow
                  label="Last detection"
                  value={
                    chainMonitoring.last_detection_at
                      ? formatDateTime(chainMonitoring.last_detection_at)
                      : '—'
                  }
                />
                <MetricRow
                  label="Detections 1h / 24h"
                  value={`${chainMonitoring.recent_detections_1h} / ${chainMonitoring.recent_detections_24h}`}
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {chainMonitoring.realtime_ingestion_path && (
        <div className="shPathBlock">
          <p className="sectionEyebrow shPathBlockTitle">Ingestion Paths</p>
          <div className="shMetricsGrid shPathStatic">
            <MetricRow label="Polling backup" value="Active" />
            <MetricRow label="Backfill" value="Active" />
          </div>
          <RealtimePathRow realtime={chainMonitoring.realtime_ingestion_path} />
        </div>
      )}
    </section>
  );
}
