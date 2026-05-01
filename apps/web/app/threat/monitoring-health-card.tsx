import type { ReactNode } from 'react';

type Props = {
  heartbeatLabel?: string;
  pollLabel?: string;
  telemetryLabel?: string;
  reportingSystems?: number;
  configuredSystems?: number;
  freshnessStatus?: string;
  confidenceStatus?: string;
  providerHealthLabel?: string;
  children?: ReactNode;
};

function Row({ label, value }: { label: string; value: string }) {
  return <div className="statusMatrixRow"><span>{label}</span><strong>{value}</strong></div>;
}

export default function MonitoringHealthCard(props: Props) {
  if (props.children && !props.heartbeatLabel && !props.pollLabel && !props.telemetryLabel) return <section aria-label="Monitoring Health">{props.children}</section>;
  return (
    <section aria-label="Monitoring Health" className="sidebarMetaCard">
      <h3>Monitoring health</h3>
      <p className="tableMeta">Customer-safe status for signal flow, freshness, and provider reliability.</p>
      <div className="statusMatrix">
        <Row label="Heartbeat" value={props.heartbeatLabel || "Unknown"} />
        <Row label="Polling" value={props.pollLabel || "Unknown"} />
        <Row label="Telemetry" value={props.telemetryLabel || "Unknown"} />
        <Row label="Coverage" value={`${props.reportingSystems ?? 0}/${props.configuredSystems ?? 0} systems reporting`} />
        <Row label="Freshness" value={props.freshnessStatus || "Unknown"} />
        <Row label="Confidence" value={props.confidenceStatus || "Unknown"} />
        <Row label="Provider health" value={props.providerHealthLabel || "Unknown"} />
      </div>
      {props.children}
    </section>
  );
}
