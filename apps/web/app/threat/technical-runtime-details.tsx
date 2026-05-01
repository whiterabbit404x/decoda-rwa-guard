import type { ReactNode } from 'react';

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
  children?: ReactNode;
};

function renderValue(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return 'unavailable';
  return String(value);
}

export default function TechnicalRuntimeDetails({
  summaryLine,
  runtimeStatus,
  monitoringStatus,
  telemetryFreshness,
  confidence,
  contradictionFlags = [],
  guardFlags = [],
  dbFailureClassification,
  statusReason,
  failedEndpoints = [],
  staleCollections = [],
  children,
}: Props) {
  return (
    <details className="tableMeta">
      <summary>View technical details</summary>
      <p className="tableMeta">{summaryLine}</p>
      <ul className="tableMeta">
        <li>runtime_status: {renderValue(runtimeStatus)}</li>
        <li>monitoring_status: {renderValue(monitoringStatus)}</li>
        <li>telemetry_freshness: {renderValue(telemetryFreshness)}</li>
        <li>confidence: {renderValue(confidence)}</li>
        <li>db_failure_classification: {renderValue(dbFailureClassification)}</li>
        <li>status_reason: {renderValue(statusReason)}</li>
        <li>contradiction_flags: {contradictionFlags.length > 0 ? contradictionFlags.join(', ') : 'none'}</li>
        <li>guard_flags: {guardFlags.length > 0 ? guardFlags.join(', ') : 'none'}</li>
        <li>failed endpoints: {failedEndpoints.length > 0 ? failedEndpoints.join(', ') : 'none'}</li>
        <li>stale collections: {staleCollections.length > 0 ? staleCollections.join(', ') : 'none'}</li>
      </ul>
      {children}
    </details>
  );
}
