'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';

const WORKFLOW_STATUSES = ['open', 'investigating', 'contained', 'resolved', 'reopened'] as const;

export default function IncidentsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [incidents, setIncidents] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [status, setStatus] = useState('');
  const [owner, setOwner] = useState('');
  const [timeline, setTimeline] = useState<any[]>([]);
  const [note, setNote] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    const params = new URLSearchParams();
    if (status) params.set('status_value', status);
    if (owner) params.set('assignee_user_id', owner);
    const response = await fetch(`${apiUrl}/incidents?${params.toString()}`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) return;
    const rows = (await response.json()).incidents ?? [];
    setIncidents(rows);
    if (!selectedId && rows.length) setSelectedId(rows[0].id);
  }

  useEffect(() => { void load(); }, [status, owner]);

  useEffect(() => {
    if (!selectedId) return;
    void fetch(`${apiUrl}/incidents/${selectedId}/timeline`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setTimeline(payload?.timeline ?? []));
  }, [apiUrl, authHeaders, selectedId]);

  const selected = useMemo(() => incidents.find((item) => item.id === selectedId), [incidents, selectedId]);
  const responseModeLabel = selected?.response_action_mode && selected.response_action_mode !== 'live_enforcement'
    ? 'SIMULATED'
    : null;

  async function updateWorkflow(nextStatus: typeof WORKFLOW_STATUSES[number]) {
    if (!selected) return;
    const response = await fetch(`${apiUrl}/incidents/${selected.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ workflow_status: nextStatus }),
    });
    setMessage(response.ok ? `Incident moved to ${nextStatus}.` : 'Unable to update incident workflow.');
    if (response.ok) {
      await load();
      const timelineResponse = await fetch(`${apiUrl}/incidents/${selected.id}/timeline`, { headers: authHeaders(), cache: 'no-store' });
      if (timelineResponse.ok) setTimeline((await timelineResponse.json()).timeline ?? []);
    }
  }

  async function addNote() {
    if (!selectedId || !note.trim()) return;
    const response = await fetch(`${apiUrl}/incidents/${selectedId}/timeline`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ message: note.trim() }),
    });
    setMessage(response.ok ? 'Note added.' : 'Unable to add note.');
    if (response.ok) {
      setNote('');
      const timelineResponse = await fetch(`${apiUrl}/incidents/${selectedId}/timeline`, { headers: authHeaders(), cache: 'no-store' });
      if (timelineResponse.ok) setTimeline((await timelineResponse.json()).timeline ?? []);
    }
  }

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Incident lifecycle</p><h1>Incidents</h1><p className="lede">Track open → investigating → contained → resolved → reopened with persistent activity logs.</p></div></div>
        <div className="buttonRow">
          <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{WORKFLOW_STATUSES.map((value) => <option key={value} value={value}>{value}</option>)}</select>
          <input value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="Owner user id" />
        </div>
        <div className="twoColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Incident queue</p>
            {incidents.map((incident) => <button key={incident.id} type="button" className="overviewListItem" onClick={() => setSelectedId(incident.id)}><strong>{incident.title || incident.event_type}</strong> · {incident.workflow_status || incident.status}</button>)}
          </article>
          <article className="dataCard">
            {!selected ? <p className="muted">Select an incident.</p> : <>
              <h3>{selected.title || selected.event_type}</h3>
              {responseModeLabel ? <p className="statusLine">Response mode: <strong>{responseModeLabel}</strong></p> : null}
              <p className="muted">Severity: {selected.severity || 'n/a'} · Owner: {selected.owner_user_id || selected.assignee_user_id || 'unassigned'}</p>
              <p className="muted">Linked alerts: {(selected.linked_alert_ids || []).join(', ') || 'none'}</p>
              <p className="muted">Created: {selected.created_at ? new Date(selected.created_at).toLocaleString() : 'n/a'} · Resolved: {selected.resolved_at ? new Date(selected.resolved_at).toLocaleString() : 'not resolved'}</p>
              <div className="buttonRow">
                <button type="button" onClick={() => void updateWorkflow('investigating')}>Mark investigating</button>
                <button type="button" onClick={() => void updateWorkflow('contained')}>Mark contained</button>
                <button type="button" onClick={() => void updateWorkflow('resolved')}>Resolve</button>
                <button type="button" onClick={() => void updateWorkflow('reopened')}>Reopen</button>
              </div>
              <p className="tableMeta">Response actions: Freeze wallet · Block transaction · Revoke approval · Disable monitored system · Suppress rule · Notify team</p>
              <p className="sectionEyebrow">Merged event timeline</p>
              {timeline.map((item, index) => <p key={`${item.id || index}`}>{item.event_type}: {item.message || ''} · {item.created_at ? new Date(item.created_at).toLocaleString() : 'n/a'}</p>)}
              <div className="buttonRow">
                <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Add incident note" />
                <button type="button" onClick={() => void addNote()}>Add note</button>
              </div>
            </>}
            {message ? <p className="statusLine">{message}</p> : null}
          </article>
        </div>
      </section>
    </main>
  );
}
