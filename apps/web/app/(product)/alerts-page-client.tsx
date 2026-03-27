'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function AlertsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [alerts, setAlerts] = useState<any[]>([]);
  const [actions, setActions] = useState<any[]>([]);
  const [decisions, setDecisions] = useState<any[]>([]);
  const [members, setMembers] = useState<any[]>([]);
  const [selectedAlertId, setSelectedAlertId] = useState<string>('');
  const [status, setStatus] = useState('open');
  const [ownerUserId, setOwnerUserId] = useState('');
  const [actionDueAt, setActionDueAt] = useState('');
  const [noteText, setNoteText] = useState('');
  const [message, setMessage] = useState('');
  const [timeline, setTimeline] = useState<any[]>([]);
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [targetFilter, setTargetFilter] = useState('');

  const selectedAlert = useMemo(() => alerts.find((item) => item.id === selectedAlertId) ?? null, [alerts, selectedAlertId]);

  async function load() {
    const params = new URLSearchParams();
    if (severityFilter) params.set('severity', severityFilter);
    if (statusFilter) params.set('status_value', statusFilter);
    if (sourceFilter) params.set('source', sourceFilter);
    if (targetFilter) params.set('target_id', targetFilter);
    const [alertsResponse, actionsResponse, decisionsResponse, membersResponse] = await Promise.all([
      fetch(`${apiUrl}/alerts?${params.toString()}`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/actions`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/decisions`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/workspace/members`, { headers: authHeaders(), cache: 'no-store' }),
    ]);
    if (alertsResponse.ok) {
      const payload = await alertsResponse.json();
      const nextAlerts = payload.alerts ?? [];
      setAlerts(nextAlerts);
      if (!selectedAlertId && nextAlerts.length > 0) setSelectedAlertId(nextAlerts[0].id);
    }
    if (actionsResponse.ok) setActions((await actionsResponse.json()).actions ?? []);
    if (decisionsResponse.ok) setDecisions((await decisionsResponse.json()).decisions ?? []);
    if (membersResponse.ok) setMembers((await membersResponse.json()).members ?? []);
  }

  useEffect(() => { void load(); }, [severityFilter, statusFilter, sourceFilter, targetFilter]);

  useEffect(() => {
    if (!selectedAlertId) return;
    const fetchTimeline = async () => {
      const response = await fetch(`${apiUrl}/alerts/${selectedAlertId}`, { headers: authHeaders(), cache: 'no-store' });
      if (response.ok) setTimeline((await response.json()).events ?? []);
    };
    void fetchTimeline();
  }, [selectedAlertId]);

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
      body: JSON.stringify({ decision_type: decisionType, reason: 'Set from operator console', notes: noteText || `Status context: ${status}` }),
    });
    setMessage(response.ok ? `Decision recorded: ${decisionType}.` : 'Unable to create decision.');
    if (response.ok) await load();
  }

  async function createAction(actionType: string) {
    if (!selectedAlertId) return;
    const response = await fetch(`${apiUrl}/findings/${selectedAlertId}/actions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ action_type: actionType, owner_user_id: ownerUserId || null, due_at: actionDueAt || null, title: `${actionType} for selected finding`, notes: noteText || 'Created from alerts workflow', status: 'open' }),
    });
    setMessage(response.ok ? `Action created: ${actionType}.` : 'Unable to create action.');
    if (response.ok) await load();
  }

  async function updateAction(nextAction: any, nextStatus: string) {
    const response = await fetch(`${apiUrl}/actions/${nextAction.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ status: nextStatus, owner_user_id: nextAction.owner_user_id, notes: nextAction.notes, due_at: nextAction.due_at }),
    });
    setMessage(response.ok ? `Action marked ${nextStatus}.` : 'Unable to update action.');
    if (response.ok) await load();
  }

  const relatedActions = actions.filter((item) => item.finding_id === selectedAlertId);
  const relatedDecisions = decisions.filter((item) => item.finding_id === selectedAlertId);

  return <main className="productPage"><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Alerts and findings</p><h1>Operator action console</h1><p className="lede">Open alert, assign owner, escalate/suppress/accept risk, and export evidence from one workflow.</p></div></div><div className="buttonRow"><select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}><option value="">All severities</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">All statuses</option><option value="open">open</option><option value="acknowledged">acknowledged</option><option value="resolved">resolved</option></select><select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">All sources</option><option value="live">live</option><option value="fallback">fallback</option><option value="demo">demo</option></select><input placeholder="Target ID" value={targetFilter} onChange={(event) => setTargetFilter(event.target.value)} /></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Findings</p>{alerts.length === 0 ? <p className="muted">No findings yet.</p> : alerts.map((a) => <p key={a.id}><button type="button" onClick={() => setSelectedAlertId(a.id)}>{a.title}</button> · <span className={`statusBadge statusBadge--${a.severity}`}>{a.severity}</span> · <span className={`statusBadge statusBadge--${a.status}`}>{a.status}</span></p>)}</article><article className="dataCard"><p className="sectionEyebrow">Finding detail</p>{selectedAlert ? <><p><strong>{selectedAlert.title}</strong></p><p className="muted">{selectedAlert.summary || 'No summary.'}</p><p className="muted">Module: {selectedAlert.module_key || 'n/a'} · Target: {selectedAlert.target_id || 'n/a'} · Source: {selectedAlert.source || selectedAlert.source_service || 'n/a'}</p><div className="buttonRow"><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="open">open</option><option value="acknowledged">acknowledged</option><option value="resolved">resolved</option></select><button type="button" onClick={() => void updateAlertStatus(status)}>Update status</button></div><div className="buttonRow"><select value={ownerUserId} onChange={(event) => setOwnerUserId(event.target.value)}><option value="">Unassigned owner</option>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.full_name || member.email}</option>)}</select><input type="datetime-local" value={actionDueAt} onChange={(event) => setActionDueAt(event.target.value)} /></div><textarea placeholder="Operator note / suppression reason / escalation context" value={noteText} onChange={(event) => setNoteText(event.target.value)} /><div className="buttonRow"><button type="button" onClick={() => void createDecision('accepted_risk')}>Accept risk</button><button type="button" onClick={() => void createDecision('escalated')}>Escalate</button><button type="button" onClick={() => void createDecision('suppress')}>Suppress</button><button type="button" onClick={() => void createDecision('exception_approved')}>Approve exception</button></div><div className="buttonRow"><button type="button" onClick={() => void createAction('assign_owner')}>Assign owner</button><button type="button" onClick={() => void createAction('remediation_task')}>Create remediation task</button><button type="button" onClick={() => void createAction('add_note')}>Add note</button></div><div className="buttonRow"><Link href={`/exports?from_alert=${selectedAlert.id}`}>Open exports</Link><button type="button" onClick={() => window.location.assign('/exports')}>Export evidence</button></div></> : <p className="muted">Select a finding to inspect details and act.</p>}</article><article className="dataCard"><p className="sectionEyebrow">Timeline, actions, decisions</p><p className="muted">Related decisions: {relatedDecisions.length}</p>{relatedDecisions.slice(0, 8).map((d) => <p key={d.id}>{d.decision_type} · {d.status}</p>)}<p className="muted">Related actions: {relatedActions.length}</p>{relatedActions.slice(0, 8).map((a) => <div key={a.id}><p>{a.action_type} · <span className={`statusBadge statusBadge--${a.status}`}>{a.status}</span> · due {a.due_at ? new Date(a.due_at).toLocaleString() : 'n/a'}</p><div className="buttonRow"><button type="button" onClick={() => void updateAction(a, 'open')}>Open</button><button type="button" onClick={() => void updateAction(a, 'in_progress')}>In progress</button><button type="button" onClick={() => void updateAction(a, 'closed')}>Closed</button></div></div>)}<p className="muted">Activity feed</p>{timeline.slice(0, 8).map((item) => <p key={item.id}>{item.event_type} · {new Date(item.created_at).toLocaleString()}</p>)}{message ? <p className="statusLine">{message}</p> : null}</article></div></section></main>;
}
