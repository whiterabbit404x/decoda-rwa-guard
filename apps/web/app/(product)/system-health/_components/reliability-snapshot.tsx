type Props = {
  reliability: Record<string, string | number | null>;
  truth: any;
};

type MetricItem = {
  label: string;
  value: string | null;
};

export function ReliabilitySnapshot({ reliability, truth }: Props) {
  const metrics: MetricItem[] = [
    {
      label: 'Active Monitoring Targets',
      value: reliability.active_targets != null ? String(reliability.active_targets) : null,
    },
    {
      label: 'Monitored Chains',
      value: reliability.monitored_chains != null ? String(reliability.monitored_chains) : null,
    },
    {
      label: 'RPC Success Rate',
      value: reliability.rpc_success_rate != null ? String(reliability.rpc_success_rate) : null,
    },
    {
      label: 'Active Alerts',
      value:
        Number(truth.active_alerts_count ?? 0) > 0
          ? `${truth.active_alerts_count} active`
          : '0 active',
    },
    {
      label: 'Active Incidents',
      value:
        Number(truth.active_incidents_count ?? 0) > 0
          ? `${truth.active_incidents_count} active`
          : '0 active',
    },
    {
      label: 'Evidence Source',
      value: String(truth.evidence_source_summary ?? '—'),
    },
  ];

  return (
    <section className="dataCard">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">Reliability snapshot</p>
          <h2>Reliability &amp; Coverage</h2>
        </div>
      </div>
      <div className="shReliabilityGrid">
        {metrics.map(({ label, value }) => (
          <div key={label} className="shReliabilityCard">
            <p className="shReliabilityLabel">{label}</p>
            {value != null ? (
              <p className="shReliabilityValue">{value}</p>
            ) : (
              <p className="shReliabilityUnavailable">Metric not implemented</p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
