type Props = {
  summaryLine: string;
  runtimeStatus?: string | null;
  monitoringStatus?: string | null;
  telemetryFreshness?: string | null;
  confidence?: string | number | null;
  contradictionFlags?: string[];
  guardFlags?: string[];
  dbFailureClassification?: string | null;
  statusReason?: string | null;
  failedEndpoints?: string[];
  staleCollections?: string[];
  diagnostics?: string[];
};

const renderValue = (value?: string | number | null) => (value === null || value === undefined || value === '' ? 'unavailable' : String(value));

export default function TechnicalRuntimeDetails(props: Props) {
  const { summaryLine, runtimeStatus, monitoringStatus, telemetryFreshness, confidence, contradictionFlags = [], guardFlags = [], dbFailureClassification, statusReason, failedEndpoints = [], staleCollections = [], diagnostics = [] } = props;
  return (
    <details className="tableMeta">
      <summary>View technical details</summary>
      <p className="tableMeta">{summaryLine}</p>
      <ul className="tableMeta">
        <li>runtime_status: {renderValue(runtimeStatus)}</li><li>monitoring_status: {renderValue(monitoringStatus)}</li><li>telemetry_freshness: {renderValue(telemetryFreshness)}</li><li>confidence: {renderValue(confidence)}</li>
        <li>db_failure_classification: {renderValue(dbFailureClassification)}</li><li>status_reason: {renderValue(statusReason)}</li>
        <li>contradiction_flags: {contradictionFlags.length > 0 ? contradictionFlags.join(', ') : 'none'}</li><li>guard_flags: {guardFlags.length > 0 ? guardFlags.join(', ') : 'none'}</li>
        <li>failed endpoints: {failedEndpoints.length > 0 ? failedEndpoints.join(', ') : 'none'}</li><li>stale collections: {staleCollections.length > 0 ? staleCollections.join(', ') : 'none'}</li>
      </ul>
      {diagnostics.length > 0 ? <div className="stack compactStack">{diagnostics.map((d, i) => <p className="tableMeta" key={`${d}-${i}`}>{d}</p>)}</div> : null}
    </details>
  );
}
