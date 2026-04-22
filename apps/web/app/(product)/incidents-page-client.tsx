'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';
import { actionDisabledReason, actionModeLabel, capabilityMapFromPayload, isActionDisabledInMode, responseActionExecutionMessage, type ResponseActionCapability } from '../response-action-capabilities';
import ThreatChainPanel from '../threat-chain-panel';

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
  const [actionMode, setActionMode] = useState<'simulated' | 'recommended' | 'live'>('simulated');
  const [operatorNotes, setOperatorNotes] = useState('');
  const [actionCapabilities, setActionCapabilities] = useState<Record<string, ResponseActionCapability>>({});
  const [evidenceSourceSummary, setEvidenceSourceSummary] = useState('none');
  const [evidence, setEvidence] = useState<any>(null);
  const evidenceSectionRef = useRef<HTMLParagraphElement | null>(null);

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
  const selected = useMemo(() => incidents.find((item) => item.id === selectedId), [incidents, selectedId]);
  const responseModeLabel = selected?.response_action_mode && selected.response_action_mode !== 'live'
    ? 'SIMULATED'
    : null;
  const actionExecutionLabel = actionModeLabel(actionMode);
  const linkedEvidenceCount = Number(selected?.linked_evidence_count || 0);

  useEffect(() => { void load(); }, [status, owner]);
  useEffect(() => {
    void fetch(`${apiUrl}/response/action-capabilities`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setActionCapabilities(capabilityMapFromPayload(payload)))
      .catch(() => setActionCapabilities({}));
  }, [apiUrl, authHeaders]);

  useEffect(() => {
    if (!selectedId) return;
    void fetchIncidentTimeline(selectedId);
  }, [apiUrl, authHeaders, selectedId]);
  useEffect(() => {
    if (!selected?.source_alert_id) {
      setEvidence(null);
      return;
    }
    void fetch(`${apiUrl}/alerts/${selected.source_alert_id}/evidence`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setEvidence(payload?.evidence ?? null))
      .catch(() => setEvidence(null));
  }, [apiUrl, authHeaders, selected?.source_alert_id]);
  useEffect(() => {
    void fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setEvidenceSourceSummary(String(payload?.workspace_monitoring_summary?.evidence_source_summary || 'none').toLowerCase()))
      .catch(() => setEvidenceSourceSummary('none'));
  }, [apiUrl, authHeaders]);

  async function fetchIncidentTimeline(incidentId: string) {
    const timelineResponse = await fetch(`${apiUrl}/incidents/${incidentId}/timeline`, { headers: authHeaders(), cache: 'no-store' });
    if (timelineResponse.ok) setTimeline((await timelineResponse.json()).timeline ?? []);
  }

  async function refreshSelectedIncidentState(incidentId: string, sourceAlertId?: string | null) {
    await load();
    await fetchIncidentTimeline(incidentId);
    if (sourceAlertId) {
      const evidenceResponse = await fetch(`${apiUrl}/alerts/${sourceAlertId}/evidence`, { headers: authHeaders(), cache: 'no-store' });
      if (evidenceResponse.ok) setEvidence((await evidenceResponse.json()).evidence ?? null);
    }
  }

  async function updateWorkflow(nextStatus: typeof WORKFLOW_STATUSES[number]) {
    if (!selected) return;
    const response = await fetch(`${apiUrl}/incidents/${selected.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ workflow_status: nextStatus }),
    });
    setMessage(response.ok ? `Incident moved to ${nextStatus}.` : 'Unable to update incident workflow.');
    if (response.ok) {
      await refreshSelectedIncidentState(selected.id, selected.source_alert_id);
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
      await refreshSelectedIncidentState(selectedId, selected?.source_alert_id);
    }
  }

  async function runSimulatedAction(actionType: string, label: string) {
    if (!selected) return;
    const capability = actionCapabilities[actionType];
    const disabledReason = actionDisabledReason(capability, actionMode);
    if (disabledReason) {
      setMessage(disabledReason);
      return;
    }
    const modeLabel = actionModeLabel(actionMode);
    const create = await fetch(`${apiUrl}/response/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        action_type: actionType,
        mode: actionMode,
        status: 'pending',
        incident_id: selected.id,
        alert_id: selected.source_alert_id,
        result_summary: `${modeLabel} ${label} created from incidents client`,
        operator_notes: operatorNotes.trim() || undefined,
      }),
    });
    if (!create.ok) {
      setMessage(`${modeLabel} ${label} failed to create.`);
      return;
    }
    const action = await create.json();
    await refreshSelectedIncidentState(selected.id, selected.source_alert_id);
    const execute = await fetch(`${apiUrl}/response/actions/${action.id}/execute`, { method: 'POST', headers: authHeaders() });
    const executePayload = await execute.json().catch(() => ({}));
    const executionResult = responseActionExecutionMessage(executePayload);
    if (execute.ok && executionResult.isSuccess) {
      setMessage(executionResult.text);
      await refreshSelectedIncidentState(selected.id, selected.source_alert_id);
      return;
    }
    await refreshSelectedIncidentState(selected.id, selected.source_alert_id);
    setMessage(executionResult.text || `${modeLabel} ${label} could not be executed.`);
  }
  const liveLikeMode = evidenceSourceSummary === 'live' || evidenceSourceSummary === 'hybrid';

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
            {!incidents.length ? <p className="muted">No incidents available for the current filters.</p> : null}
            {incidents.map((incident) => <button key={incident.id} type="button" className="overviewListItem" onClick={() => setSelectedId(incident.id)}><strong>{incident.title || incident.event_type}</strong> · {incident.workflow_status || incident.status}</button>)}
          </article>
          <article className="dataCard">
            {!selected ? <p className="muted">Select an incident.</p> : <>
              <h3>{selected.title || selected.event_type}</h3>
              {responseModeLabel ? <p className="statusLine">Response mode: <strong>{responseModeLabel}</strong></p> : null}
              <p className="muted">Severity: {selected.severity || 'n/a'} · Owner: {selected.owner_user_id || selected.assignee_user_id || 'unassigned'}</p>
              <p className="muted">Linked alerts: {(selected.linked_alert_ids || []).join(', ') || 'none'}</p>
              <p className="muted">Created: {selected.created_at ? new Date(selected.created_at).toLocaleString() : 'n/a'} · Resolved: {selected.resolved_at ? new Date(selected.resolved_at).toLocaleString() : 'not resolved'}</p>
              <ThreatChainPanel
                chainLinkedIds={selected.chain_linked_ids}
                detectionId={selected.linked_detection_id}
                alertId={selected.source_alert_id}
                incidentId={selected.id}
                actionId={selected.linked_action_id}
                linkedEvidenceCount={selected.linked_evidence_count}
                lastEvidenceAt={selected.last_evidence_at}
                evidenceOrigin={selected.evidence_origin || selected.evidence_source}
                txHash={selected.tx_hash || evidence?.tx_hash}
                blockNumber={selected.block_number || evidence?.block_number}
                detectorKind={selected.detector_kind}
                liveLikeMode={liveLikeMode}
                evidenceDrawerLabel="Open evidence drawer"
                onOpenEvidence={() => evidenceSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
              />
              {liveLikeMode && linkedEvidenceCount <= 0 ? <p className="statusLine">LIVE/HYBRID degraded state: no persisted evidence is linked yet. Open the evidence drawer section below for fallback payload context.</p> : null}
              {!liveLikeMode && linkedEvidenceCount <= 0 ? <p className="muted">No linked evidence is persisted for this incident yet. Open the evidence drawer section below for available payload context.</p> : null}
              <div className="buttonRow">
                <select value={actionMode} onChange={(event) => setActionMode(event.target.value as 'simulated' | 'recommended' | 'live')}>
                  <option value="simulated">SIMULATED mode</option>
                  <option value="recommended">Recommended mode (SIMULATED)</option>
                  <option value="live">Live mode</option>
                </select>
                <input value={operatorNotes} onChange={(event) => setOperatorNotes(event.target.value)} placeholder="Operator notes (optional)" />
              </div>
              <div className="buttonRow">
                <button type="button" onClick={() => void updateWorkflow('investigating')}>Mark investigating</button>
                <button type="button" onClick={() => void updateWorkflow('contained')}>Mark contained</button>
                <button type="button" onClick={() => void updateWorkflow('resolved')}>Resolve</button>
                <button type="button" onClick={() => void updateWorkflow('reopened')}>Reopen</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.notify_team, actionMode)} title={actionDisabledReason(actionCapabilities.notify_team, actionMode) || ''} onClick={() => void runSimulatedAction('notify_team', 'Execute simulated response')}>Execute simulated response ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.block_transaction, actionMode)} title={actionDisabledReason(actionCapabilities.block_transaction, actionMode) || ''} onClick={() => void runSimulatedAction('block_transaction', 'Block transaction')}>Block transaction ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.revoke_approval, actionMode)} title={actionDisabledReason(actionCapabilities.revoke_approval, actionMode) || ''} onClick={() => void runSimulatedAction('revoke_approval', 'Revoke approval')}>Revoke approval ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.freeze_wallet, actionMode)} title={actionDisabledReason(actionCapabilities.freeze_wallet, actionMode) || ''} onClick={() => void runSimulatedAction('freeze_wallet', 'Freeze wallet')}>Freeze wallet ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.disable_monitored_system, actionMode)} title={actionDisabledReason(actionCapabilities.disable_monitored_system, actionMode) || ''} onClick={() => void runSimulatedAction('disable_monitored_system', 'Disable monitored system')}>Disable monitored system ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.suppress_rule, actionMode)} title={actionDisabledReason(actionCapabilities.suppress_rule, actionMode) || ''} onClick={() => void runSimulatedAction('suppress_rule', 'Suppress rule')}>Suppress/mute rule ({actionExecutionLabel})</button>
              </div>
              {actionMode === 'live' ? <p className="tableMeta">Live constraints: unsupported actions show “Unsupported live action”; manual paths show “Manual-only in live mode”.</p> : null}
              <p className="tableMeta">Response actions: Freeze wallet · Block transaction · Revoke approval · Disable monitored system · Suppress rule · Notify team</p>
              <p ref={evidenceSectionRef} className="tableMeta">Evidence source {selected.evidence_origin || selected.evidence_source || 'n/a'} · tx {selected.tx_hash || evidence?.tx_hash || 'n/a'} · block {selected.block_number || evidence?.block_number || 'n/a'} · detector {selected.detector_kind || 'n/a'}</p>
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
