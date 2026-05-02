import { THREAT_COPY } from './threat-copy';

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
                <td>{alert?.label ?? THREAT_COPY.noAlertLinkedYet}</td>
                <td>{incident?.label ?? THREAT_COPY.noIncidentLinkedYet}</td>
                <td>{responseAction?.label ?? THREAT_COPY.noResponseActionLinkedYet}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <p>{THREAT_COPY.noActiveIncidentChain}</p>
      )}
    </article>
  );
}
