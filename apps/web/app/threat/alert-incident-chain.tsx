import ThreatEmptyState from './threat-empty-state';

type ChainNode = { id: string; label: string; status: string; detail?: string };
type Props = { alert?: ChainNode | null; incident?: ChainNode | null; responseAction?: ChainNode | null };

function Node({ title, node }: { title: string; node: ChainNode }) {
  return (
    <div className="emptyStatePanel" style={{ marginTop: 0 }}>
      <p className="tableMeta"><strong>{title}</strong></p>
      <p>{node.label}</p>
      <p className="tableMeta">ID {node.id} · Status {node.status}</p>
      {node.detail ? <p className="tableMeta">{node.detail}</p> : null}
    </div>
  );
}

export default function AlertIncidentChain({ alert, incident, responseAction }: Props) {
  if (!alert && !incident && !responseAction) {
    return <ThreatEmptyState title="No chain linked yet" message="An alert-to-incident-to-response chain will appear here when a workflow is started." />;
  }

  return (
    <section aria-label="Alert Incident Response Chain" className="sidebarMetaCard">
      <h3>Alert → Incident → Response Action</h3>
      <div className="stack" style={{ display: 'grid', gap: '0.75rem' }}>
        {alert ? <Node title="Alert" node={alert} /> : null}
        {incident ? <Node title="Incident" node={incident} /> : null}
        {responseAction ? <Node title="Response Action" node={responseAction} /> : null}
      </div>
    </section>
  );
}
