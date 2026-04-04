'use client';
import { useEffect, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';
import { normalizeMonitoringMode, type MonitoringRuntimeStatus } from '../monitoring-status-contract';

export default function IncidentsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [incidents, setIncidents] = useState<any[]>([]);
  const [status, setStatus] = useState('open');
  const [selectedId, setSelectedId] = useState('');
  const [message, setMessage] = useState('');
  const [runtimeStatus, setRuntimeStatus] = useState<MonitoringRuntimeStatus | null>(null);

  async function load() {
    const response = await fetch(`${apiUrl}/incidents`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json();
    const rows = payload.incidents ?? [];
    setIncidents(rows);
    if (!selectedId && rows.length) setSelectedId(rows[0].id);
    const runtimeResponse = await fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' });
    if (runtimeResponse.ok) {
      const runtimePayload = await runtimeResponse.json() as MonitoringRuntimeStatus;
      setRuntimeStatus({ ...runtimePayload, mode: normalizeMonitoringMode(runtimePayload.mode) });
    }
  }

  useEffect(() => { void load(); }, []);

  async function saveStatus() {
    if (!selectedId) return;
    const response = await fetch(`${apiUrl}/incidents/${selectedId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ status }),
    });
    setMessage(response.ok ? `Incident moved to ${status}.` : 'Unable to update incident.');
    if (response.ok) await load();
  }

  const selected = incidents.find((item) => item.id === selectedId);
  const liveLike = runtimeStatus?.mode === 'LIVE' || runtimeStatus?.mode === 'HYBRID' || runtimeStatus?.configured_mode === 'LIVE' || runtimeStatus?.configured_mode === 'HYBRID';
  const incidentsEmptyCopy = liveLike
    ? ((runtimeStatus?.recent_evidence_state === 'degraded' || runtimeStatus?.recent_evidence_state === 'failed')
      ? 'Monitoring degraded. Incident absence does not prove safety.'
      : 'No real evidence observed yet. Zero incidents is not proof of safety.')
    : 'No incidents yet.';
  return <main className="productPage"><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Incidents</p><h1>Monitoring incident queue</h1></div></div><div className="threeColumnSection"><article className="dataCard"><p className="sectionEyebrow">Open incidents</p>{incidents.length === 0 ? <p className="muted">{incidentsEmptyCopy}</p> : incidents.map((incident) => <p key={incident.id}><button type="button" onClick={() => { setSelectedId(incident.id); setStatus(incident.status); }}>{incident.title || incident.event_type}</button> · <span className={`statusBadge statusBadge--${incident.severity}`}>{incident.severity}</span> · <span className={`statusBadge statusBadge--${incident.status}`}>{incident.status}</span><br /><span className="muted">target: {incident.target_id || 'n/a'}</span></p>)}</article><article className="dataCard"><p className="sectionEyebrow">Incident detail</p>{selected ? <><p><strong>{selected.title || selected.event_type}</strong></p><p className="muted">{selected.summary}</p><p className="muted">Linked alerts: {(selected.linked_alert_ids || []).join(', ') || 'none'}</p><div className="buttonRow"><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="open">open</option><option value="acknowledged">acknowledged</option><option value="resolved">resolved</option></select><button type="button" onClick={() => void saveStatus()}>Update incident</button></div></> : <p className="muted">Select an incident.</p>}</article><article className="dataCard"><p className="sectionEyebrow">Timeline</p>{(selected?.timeline ?? []).length === 0 ? <p className="muted">No timeline events.</p> : (selected.timeline ?? []).map((item: any, index: number) => <p key={`${item.event}-${index}`}>{item.event} · {item.at ? new Date(item.at).toLocaleString() : 'n/a'}</p>)}{message ? <p className="statusLine">{message}</p> : null}</article></div></section></main>;
}
