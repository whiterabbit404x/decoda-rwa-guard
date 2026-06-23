import { formatDateTime } from './helpers';

type Props = {
  truth: any;
  presentation: any;
  isOperational: boolean;
  isOffline: boolean;
  summaryMissing: boolean;
  reportingSystems: number;
  monitoredSystems: number;
  hasHeartbeat: boolean;
  hasTelemetry: boolean;
};

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="shMetricRow">
      <span className="shMetricLabel">{label}</span>
      <span className="shMetricValue">{value}</span>
    </div>
  );
}

export function StatusOverviewPanel({
  truth,
  presentation,
  isOperational,
  isOffline,
  summaryMissing,
  reportingSystems,
  monitoredSystems,
  hasHeartbeat,
  hasTelemetry,
}: Props) {
  return (
    <section className="dataCard">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">Canonical runtime truth</p>
          <h2>Status Overview</h2>
        </div>
      </div>

      <div className="shMetricsGrid">
        <MetricRow
          label="Overall status"
          value={isOperational ? 'Operational' : isOffline ? 'Offline' : 'Degraded'}
        />
        <MetricRow
          label="Monitoring status"
          value={presentation.statusLabel ?? String(truth.monitoring_status ?? '—')}
        />
        <MetricRow
          label="Freshness status"
          value={presentation.freshness ?? String(truth.telemetry_freshness ?? '—')}
        />
        <MetricRow
          label="Confidence status"
          value={presentation.confidence ?? String(truth.confidence_status ?? '—')}
        />
        <MetricRow
          label="Reporting systems"
          value={`${reportingSystems} / ${monitoredSystems}`}
        />
        <MetricRow label="Last heartbeat" value={formatDateTime(truth.last_heartbeat_at)} />
        <MetricRow label="Last poll" value={formatDateTime(truth.last_poll_at)} />
        <MetricRow
          label="Last telemetry"
          value={formatDateTime(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at)}
        />
        <MetricRow label="Last detection" value={formatDateTime(truth.last_detection_at)} />
      </div>

      {!summaryMissing && !isOperational && (
        <div className="shTruthGuards">
          {!hasHeartbeat && <p>• Worker heartbeat not received.</p>}
          {!hasTelemetry && hasHeartbeat && <p>• No telemetry received from chain.</p>}
          {reportingSystems === 0 && monitoredSystems > 0 && (
            <p>• No monitored systems reporting.</p>
          )}
        </div>
      )}
    </section>
  );
}
