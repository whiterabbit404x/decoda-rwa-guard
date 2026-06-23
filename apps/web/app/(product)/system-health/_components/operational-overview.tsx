import { type ComponentDetail, COMPONENT_META } from './types';
import { formatShortTime, statusBadgeClass, statusLabel } from './helpers';

type Props = {
  components: Record<string, ComponentDetail>;
  noSystemHealthData: boolean;
};

export function OperationalOverview({ components, noSystemHealthData }: Props) {
  return (
    <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">Component health</p>
          <h2>Operational Overview</h2>
        </div>
      </div>
      <div className="tableWrap">
        <table className="shOpsTable">
          <thead>
            <tr>
              <th>Component</th>
              <th>Status</th>
              <th>Signal</th>
              <th>Last Event</th>
              <th>Age</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(COMPONENT_META).map(([key, meta]) => {
              const comp: ComponentDetail | undefined = components[key];
              const status = comp?.status ?? 'unavailable';
              return (
                <tr key={key} className="shOpsRow">
                  <td>
                    <strong>{meta.label}</strong>
                    <span className="shOpsWhat">{meta.what}</span>
                  </td>
                  <td>
                    <span
                      className={statusBadgeClass(status)}
                      style={{ fontSize: '0.72rem' }}
                    >
                      {statusLabel(status)}
                    </span>
                  </td>
                  <td>
                    <span className="shOpsSignal">
                      {comp?.message ?? (noSystemHealthData ? 'Endpoint unreachable' : 'Unavailable')}
                    </span>
                  </td>
                  <td>
                    <span className="timestamp">
                      {comp?.last_event ? formatShortTime(comp.last_event) : '—'}
                    </span>
                  </td>
                  <td>
                    <span className="tableMeta">{comp?.age ?? '—'}</span>
                  </td>
                  <td>
                    {comp?.action ? (
                      <span className="shOpsAction">{comp.action}</span>
                    ) : (
                      <span className="tableMeta muted">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {noSystemHealthData && (
        <p className="explanation small" style={{ marginTop: '0.75rem' }}>
          <strong>Health data unavailable.</strong> The backend health endpoint could not be reached.{' '}
          <a href="/system-health">Refresh</a>
        </p>
      )}
    </section>
  );
}
