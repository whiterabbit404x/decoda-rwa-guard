type ChainItem = { id: string; label: string; status: string; detail?: string };
type Props = {
  rows?: Array<{ alert: string; incident: string; action: string; status: string }>;
  alert?: ChainItem | null;
  incident?: ChainItem | null;
  responseAction?: ChainItem | null;
};

export default function AlertIncidentChain({ rows, alert, incident, responseAction }: Props) {
  const derivedRows = rows ?? (alert || incident || responseAction ? [{
    alert: alert?.label ?? 'No alert linked yet',
    incident: incident?.label ?? 'No incident linked yet',
    action: responseAction?.label ?? 'No response action linked yet',
    status: responseAction?.status ?? incident?.status ?? alert?.status ?? 'Pending',
  }] : []);
  return (
    <article className="dataCard" aria-label="Alert Incident Response Chain">
      <p className="sectionEyebrow">Incident chain</p><h3>Alert → Incident → Response Action</h3>
      {derivedRows.length === 0 ? <p className="muted">No active incident chain yet. Alerts that require investigation will appear here.</p> : (
        <div className="tableWrap"><table><thead><tr><th>Alert</th><th>Incident</th><th>Response action</th><th>Status</th></tr></thead><tbody>{derivedRows.map((r, i) => <tr key={`${r.alert}-${i}`}><td>{r.alert}</td><td>{r.incident}</td><td>{r.action}</td><td>{r.status}</td></tr>)}</tbody></table></div>
      )}
    </article>
  );
}
