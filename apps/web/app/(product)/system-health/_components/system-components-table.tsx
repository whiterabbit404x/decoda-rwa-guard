import { type ReliabilityComponent, reliabilityPillClass, reliabilityStatusLabel } from './reliability-types';

type Props = {
  components: ReliabilityComponent[];
};

export function SystemComponentsTable({ components }: Props) {
  return (
    <section className="dataCard featureSection">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">Normalized component health</p>
          <h2>System Components</h2>
        </div>
      </div>
      <div className="tableWrap">
        <table className="shOpsTable">
          <thead>
            <tr>
              <th>Component</th>
              <th>Status</th>
              <th>Detail</th>
              <th>Last Healthy</th>
            </tr>
          </thead>
          <tbody>
            {components.map((c) => {
              // A disabled component shows its reason (not "healthy"); an unknown
              // component reads grey, never green. Truthfulness rules 2 & 3.
              const detail =
                c.status === 'disabled'
                  ? c.disabled_reason ?? c.reason ?? 'Disabled by configuration.'
                  : c.reason ?? '—';
              return (
                <tr key={c.component} className="shOpsRow">
                  <td>
                    <strong>{c.label}</strong>
                    {c.critical ? <span className="shRelTag">critical path</span> : null}
                  </td>
                  <td>
                    <span className={reliabilityPillClass(c.status)} style={{ fontSize: '0.72rem' }}>
                      {reliabilityStatusLabel(c.status)}
                    </span>
                  </td>
                  <td>
                    <span className="shOpsSignal">{detail}</span>
                  </td>
                  <td>
                    <span className="tableMeta" title={c.last_healthy_at ?? undefined}>
                      {c.last_healthy_ago ?? c.age_human ?? '—'}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
