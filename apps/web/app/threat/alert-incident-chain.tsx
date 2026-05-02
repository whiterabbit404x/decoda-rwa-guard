type ChainItem = { id: string; label: string; status: string; detail?: string };

type Props = {
  alert?: ChainItem | null;
  incident?: ChainItem | null;
  responseAction?: ChainItem | null;
};

export default function AlertIncidentChain({ alert, incident, responseAction }: Props) {
  const hasActiveChain = Boolean(alert || incident || responseAction);

  return (
    <article className="dataCard" aria-label="Alert Incident Response Chain">
      <p className="sectionEyebrow">Incident chain</p>
      <h3>Alert → Incident → Response Action</h3>
      {hasActiveChain ? (
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>Alert</th>
                <th>Incident</th>
                <th>Response action</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{alert?.label ?? 'No alert linked yet'}</td>
                <td>{incident?.label ?? 'No incident linked yet'}</td>
                <td>{responseAction?.label ?? 'No response action linked yet'}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <p>No active incident chain yet. Alerts that require investigation will appear here.</p>
      )}
    </article>
  );
}
