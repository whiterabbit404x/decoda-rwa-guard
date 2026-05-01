type Props = {
  heartbeatLabel: string;
  pollLabel: string;
  telemetryLabel: string;
  reportingSystems: number;
  configuredSystems: number;
  freshnessStatus: string;
  confidenceStatus: string;
  providerHealthLabel?: string;
};

export default function MonitoringHealthCard(props: Props) {
  return (
    <article className="dataCard" aria-label="Monitoring Health">
      <p className="sectionEyebrow">Monitoring health</p>
      <h3>Runtime signal quality</h3>
      <ul className="tableMeta">
        <li>Worker heartbeat: {props.heartbeatLabel}</li>
        <li>Poll loop: {props.pollLabel}</li>
        <li>Last telemetry: {props.telemetryLabel}</li>
        <li>Reporting systems: {props.reportingSystems}/{props.configuredSystems}</li>
        <li>Freshness: {props.freshnessStatus}</li>
        <li>Confidence: {props.confidenceStatus}</li>
      </ul>
      <p className="tableMeta">Provider health: {props.providerHealthLabel ?? "Unavailable"}</p>
    </article>
  );
}
