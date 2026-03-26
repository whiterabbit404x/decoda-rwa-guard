'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function AlertsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [alerts, setAlerts] = useState<any[]>([]);
  const [actions, setActions] = useState<any[]>([]);
  const [decisions, setDecisions] = useState<any[]>([]);
  const [selectedAlertId, setSelectedAlertId] = useState<string>('');
  const [status, setStatus] = useState('open');
  const [message, setMessage] = useState('');

  const selectedAlert = useMemo(() => alerts.find((item) => item.id === selectedAlertId) ?? null, [alerts, selectedAlertId]);

  async function load() {
    const [alertsResponse, actionsResponse, decisionsResponse] = await Promise.all([
      fetch(`${apiUrl}/alerts`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/actions`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/decisions`, { headers: authHeaders(), cache: 'no-store' }),
    ]);
    if (alertsResponse.ok) {
      const payload = await alertsResponse.json();
      setAlerts(payload.alerts ?? []);
      if (!selectedAlertId && (payload.alerts ?? []).length > 0) setSelectedAlertId(payload.alerts[0].id);
    }
    if (actionsResponse.ok) setActions((await actionsResponse.json()).actions ?? []);
    if (decisionsResponse.ok) setDecisions((await decisionsResponse.json()).decisions ?? []);
  }

  useEffect(() => { void load(); }, []);

  async function updateAlertStatus(nextStatus: string) {
    if (!selectedAlertId) return;
    const response = await fetch(`${apiUrl}/alerts/${selectedAlertId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ status: nextStatus })
    });
    setMessage(response.ok ? `Alert moved to ${nextStatus}.` : 'Unable to update alert status.');
    if (response.ok) await load();
  }

  async function createDecision(decisionType: string) {
    if (!selectedAlertId) return;
    const response = await fetch(`${apiUrl}/findings/${selectedAlertId}/decision`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ decision_type: decisionType, reason: 'Set from operator console', notes: `Status context: ${status}` }),
    });
    setMessage(response.ok ? `Decision recorded: ${decisionType}.` : 'Unable to create decision.');
    if (response.ok) await load();
  }

  async function createAction(actionType: string) {
    if (!selectedAlertId) return;
    const response = await fetch(`${apiUrl}/findings/${selectedAlertId}/actions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ action_type: actionType, title: `${actionType} for selected finding`, notes: 'Created from alerts workflow', status: 'open' }),
    });
    setMessage(response.ok ? `Action created: ${actionType}.` : 'Unable to create action.');
    if (response.ok) await load();
  }

  async function updateAction(nextAction: any, nextStatus: string) {
    const response = await fetch(`${apiUrl}/actions/${nextAction.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ status: nextStatus, owner_user_id: nextAction.owner_user_id, notes: nextAction.notes }),
    });
    setMessage(response.ok ? `Action marked ${nextStatus}.` : 'Unable to update action.');
    if (response.ok) await load();
  }

  return <main className="productPage"><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Alerts and findings</p><h1>Operator action console</h1><p className="lede">Inspect findings, take decisions, and manage remediation actions.</p></div></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Findings</p>{alerts.length === 0 ? <p className="muted">No findings yet.</p> : alerts.map((a) => <p key={a.id}><button type="button" onClick={() => setSelectedAlertId(a.id)}>{a.title}</button> · {a.severity} · {a.status}</p>)}</article><article className="dataCard"><p className="sectionEyebrow">Finding detail</p>{selectedAlert ? <><p><strong>{selectedAlert.title}</strong></p><p className="muted">{selectedAlert.summary || 'No summary.'}</p><div className="buttonRow"><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="open">open</option><option value="acknowledged">acknowledged</option><option value="resolved">resolved</option></select><button type="button" onClick={() => void updateAlertStatus(status)}>Update status</button></div><div className="buttonRow"><button type="button" onClick={() => void createDecision('accepted_risk')}>Mark accepted risk</button><button type="button" onClick={() => void createDecision('escalated')}>Escalate review</button><button type="button" onClick={() => void createDecision('suppress')}>Suppress</button><button type="button" onClick={() => void createDecision('exception_approved')}>Approve exception</button></div><div className="buttonRow"><button type="button" onClick={() => void createAction('assign_owner')}>Assign owner</button><button type="button" onClick={() => void createAction('remediation_task')}>Create remediation task</button><button type="button" onClick={() => void createAction('add_note')}>Add note</button></div></> : <p className="muted">Select a finding to inspect details and act.</p>}</article><article className="dataCard"><p className="sectionEyebrow">Decisions and actions</p><p className="muted">Decisions: {decisions.length}</p>{decisions.slice(0, 6).map((d) => <p key={d.id}>{d.decision_type} · {d.finding_id}</p>)}<p className="muted">Actions: {actions.length}</p>{actions.slice(0, 8).map((a) => <div key={a.id}><p>{a.action_type} · {a.status}</p><div className="buttonRow"><button type="button" onClick={() => void updateAction(a, 'open')}>Open</button><button type="button" onClick={() => void updateAction(a, 'in_progress')}>In progress</button><button type="button" onClick={() => void updateAction(a, 'closed')}>Closed</button></div></div>)}{message ? <p className="statusLine">{message}</p> : null}</article></div></section></main>;
}
