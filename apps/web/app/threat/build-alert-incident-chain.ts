type AlertChainInput = { id: string; title: string; status?: string };
type IncidentChainInput = { id: string; title?: string; event_type?: string; status?: string };
type ActionChainInput = { id: string; action_type?: string };

export function buildAlertIncidentChain(params: {
  alerts: AlertChainInput[];
  incidents: IncidentChainInput[];
  actionHistory: ActionChainInput[];
}) {
  const alert = params.alerts[0]
    ? { id: params.alerts[0].id, label: params.alerts[0].title, status: params.alerts[0].status || 'open' }
    : null;
  const incident = params.incidents[0]
    ? { id: params.incidents[0].id, label: params.incidents[0].title || params.incidents[0].event_type || params.incidents[0].id, status: params.incidents[0].status || 'open' }
    : null;
  const responseAction = params.actionHistory[0]
    ? { id: params.actionHistory[0].id, label: String(params.actionHistory[0].action_type || 'Action logged'), status: 'tracked' }
    : null;

  return { alert, incident, responseAction };
}
