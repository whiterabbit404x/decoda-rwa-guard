import { type LiveChainMonitoring } from './types';
import { diagnosisVariant, formatDateTime, statusBadgeClass, statusLabel } from './helpers';

type Props = {
  chainMonitoring: LiveChainMonitoring | null;
};

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
      <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Base chain</p>
            <h2>Live Chain Monitoring</h2>
          </div>
        </div>
        <div className="shEmptyState">
          <div className="shEmptyIcon">!</div>
          <p className="shEmptyText">Live chain monitoring data unavailable.</p>
          <p className="shEmptySubtext">Component check missing from backend response.</p>
        </div>
      </section>
    );
  }

  const variant = diagnosisVariant(chainMonitoring.diagnosis);

  return (
    <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">
            Base chain &middot; Chain ID {chainMonitoring.expected_chain_id}
          </p>
          <h2>Live Chain Monitoring</h2>
        </div>
      </div>

      <div className="twoColumnSection shChainPanel" style={{ marginTop: '0.75rem' }}>
        <div className={`shDiagnosisCard shDiagnosisCard-${variant}`}>
          <div className="shDiagnosisHeader">
            <p className="shDiagnosisEyebrow">Monitoring Diagnosis</p>
            <span className={statusBadgeClass(variant)} style={{ fontSize: '0.7rem' }}>
              {statusLabel(variant)}
            </span>
          </div>
          <p className="shDiagnosisText">{chainMonitoring.diagnosis}</p>

          <div className="shDiagnosisDetails">
            <MetricRow
              label="Worker enabled"
              value={chainMonitoring.worker_enabled ? 'Yes' : 'No'}
            />
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

        <div>
          <p className="sectionEyebrow" style={{ marginBottom: '0.5rem' }}>
            Monitoring Metrics
          </p>
          <div className="shMetricsGrid">
            <MetricRow
              label="Heartbeat age"
              value={chainMonitoring.heartbeat_age_human ?? '—'}
            />
            <MetricRow
              label="Poll interval"
              value={`${chainMonitoring.polling_interval_seconds}s`}
            />
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
    </section>
  );
}
