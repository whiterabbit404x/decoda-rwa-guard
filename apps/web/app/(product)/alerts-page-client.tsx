'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';
import { actionDisabledReason, actionModeLabel, capabilityMapFromPayload, isActionDisabledInMode, responseActionExecutionMessage, type ResponseActionCapability } from '../response-action-capabilities';
import ThreatChainPanel from '../threat-chain-panel';

export default function AlertsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [alerts, setAlerts] = useState<any[]>([]);
  const [selectedAlertId, setSelectedAlertId] = useState('');
  const [status, setStatus] = useState('');
  const [severity, setSeverity] = useState('');
  const [assetFilter, setAssetFilter] = useState('');
  const [targetFilter, setTargetFilter] = useState('');
  const [timeRange, setTimeRange] = useState('168');
  const [message, setMessage] = useState('');
  const [evidence, setEvidence] = useState<any>(null);
  const [actionMode, setActionMode] = useState<'simulated' | 'recommended' | 'live'>('simulated');
  const [operatorNotes, setOperatorNotes] = useState('');
  const [actionCapabilities, setActionCapabilities] = useState<Record<string, ResponseActionCapability>>({});
  const [evidenceSourceSummary, setEvidenceSourceSummary] = useState('none');
  const evidenceSectionRef = useRef<HTMLParagraphElement | null>(null);

  async function load() {
    const params = new URLSearchParams();
    if (status) params.set('status_value', status);
    if (severity) params.set('severity', severity);
    if (targetFilter) params.set('target_id', targetFilter);
    const response = await fetch(`${apiUrl}/alerts?${params.toString()}`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) return;
    const rows = (await response.json()).alerts ?? [];
    const now = Date.now();
    const filtered = rows.filter((item: any) => {
      const created = new Date(item.created_at || 0).getTime();
      const withinRange = (now - created) <= (Number(timeRange) * 3600 * 1000);
      const assetMatch = !assetFilter || String(item.payload?.asset_label || '').toLowerCase().includes(assetFilter.toLowerCase());
      return withinRange && assetMatch;
    });
    setAlerts(filtered);
    if (!selectedAlertId && filtered.length) setSelectedAlertId(filtered[0].id);
  }

  useEffect(() => { void load(); }, [status, severity, targetFilter, timeRange, assetFilter]);
  useEffect(() => {
    void fetch(`${apiUrl}/response/action-capabilities`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setActionCapabilities(capabilityMapFromPayload(payload)))
      .catch(() => setActionCapabilities({}));
  }, [apiUrl, authHeaders]);

  const selectedAlert = useMemo(() => alerts.find((item) => item.id === selectedAlertId) ?? null, [alerts, selectedAlertId]);
  const responseModeLabel = selectedAlert?.response_action_mode && selectedAlert.response_action_mode !== 'live'
    ? 'SIMULATED'
    : null;
  const actionExecutionLabel = actionModeLabel(actionMode);
  const linkedEvidenceCount = Number(selectedAlert?.linked_evidence_count || 0);

  useEffect(() => {
    if (!selectedAlertId) return;
    void fetch(`${apiUrl}/alerts/${selectedAlertId}/evidence`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setEvidence(payload?.evidence ?? null));
  }, [apiUrl, authHeaders, selectedAlertId]);
  useEffect(() => {
    void fetch(`${apiUrl}/ops/monitoring/runtime-status`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setEvidenceSourceSummary(String(payload?.workspace_monitoring_summary?.evidence_source_summary || 'none').toLowerCase()))
      .catch(() => setEvidenceSourceSummary('none'));
  }, [apiUrl, authHeaders]);

  async function refreshSelectedAlertState(alertId: string) {
    await load();
    const evidenceResponse = await fetch(`${apiUrl}/alerts/${alertId}/evidence`, { headers: authHeaders(), cache: 'no-store' });
    if (evidenceResponse.ok) setEvidence((await evidenceResponse.json()).evidence ?? null);
  }

  async function refetchLinkedIncidentTimeline(incidentId?: string | null) {
    if (!incidentId) return;
    await fetch(`${apiUrl}/incidents/${incidentId}/timeline`, { headers: authHeaders(), cache: 'no-store' });
  }

  async function patchAlert(nextStatus: 'acknowledged' | 'resolved' | 'suppressed') {
    if (!selectedAlert) return;
    const response = await fetch(`${apiUrl}/alerts/${selectedAlert.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ status: nextStatus }),
    });
    setMessage(response.ok ? `Alert ${nextStatus}.` : `Unable to ${nextStatus} alert.`);
    if (response.ok) await refreshSelectedAlertState(selectedAlert.id);
  }

  async function escalateIncident() {
    if (!selectedAlert) return;
    const response = await fetch(`${apiUrl}/alerts/${selectedAlert.id}/escalate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        title: `Escalated alert: ${selectedAlert.title}`,
        summary: selectedAlert.summary || selectedAlert.title,
      }),
    });
    setMessage(response.ok ? 'Open incident (SIMULATED workflow prep) completed.' : 'Unable to open incident.');
    if (response.ok) await refreshSelectedAlertState(selectedAlert.id);
  }

  async function runSimulatedAction(actionType: string, label: string) {
    if (!selectedAlert) return;
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
        alert_id: selectedAlert.id,
        incident_id: selectedAlert.incident_id,
        result_summary: `${modeLabel} ${label} created from alerts client`,
        operator_notes: operatorNotes.trim() || undefined,
      }),
    });
    if (!create.ok) {
      setMessage(`${modeLabel} ${label} failed to create.`);
      return;
    }
    const action = await create.json();
    await refreshSelectedAlertState(selectedAlert.id);
    await refetchLinkedIncidentTimeline(selectedAlert.incident_id);
    const execute = await fetch(`${apiUrl}/response/actions/${action.id}/execute`, { method: 'POST', headers: authHeaders() });
    const executePayload = await execute.json().catch(() => ({}));
    const executionResult = responseActionExecutionMessage(executePayload);
    if (execute.ok && executionResult.isSuccess) {
      setMessage(executionResult.text);
      await refreshSelectedAlertState(selectedAlert.id);
      await refetchLinkedIncidentTimeline(selectedAlert.incident_id);
      return;
    }
    await refreshSelectedAlertState(selectedAlert.id);
    await refetchLinkedIncidentTimeline(selectedAlert.incident_id);
    setMessage(executionResult.text || `${modeLabel} ${label} could not be executed.`);
  }
  const liveLikeMode = evidenceSourceSummary === 'live' || evidenceSourceSummary === 'hybrid';

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Operator queue</p><h1>Alerts</h1><p className="lede">Deduplicated alert queue with evidence-first triage and escalation actions.</p></div></div>
        <div className="buttonRow">
          <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option><option value="open">open</option><option value="acknowledged">acknowledged</option><option value="resolved">resolved</option><option value="suppressed">suppressed</option></select>
          <select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select>
          <input placeholder="Filter by asset" value={assetFilter} onChange={(event) => setAssetFilter(event.target.value)} />
          <input placeholder="Target id" value={targetFilter} onChange={(event) => setTargetFilter(event.target.value)} />
          <select value={timeRange} onChange={(event) => setTimeRange(event.target.value)}><option value="24">Last 24h</option><option value="168">Last 7d</option><option value="720">Last 30d</option></select>
        </div>
        <div className="twoColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Alert list</p>
            {!alerts.length ? <p className="muted">No alerts available for the current filters.</p> : null}
            {alerts.map((alert) => (
              <button key={alert.id} type="button" className="overviewListItem" onClick={() => setSelectedAlertId(alert.id)}>
                <strong>{alert.title}</strong> · {alert.severity} · {alert.status}
                <span className="tableMeta">events {alert.occurrence_count || 1} · group {alert.findings?.dedupe_key || alert.target_id || 'none'}</span>
              </button>
            ))}
          </article>
          <article className="dataCard">
            {!selectedAlert ? <p className="muted">Select an alert.</p> : <>
              <h3>{selectedAlert.title}</h3>
              <p className="muted">{selectedAlert.summary || 'No summary available.'}</p>
              {responseModeLabel ? <p className="statusLine">Response mode: <strong>{responseModeLabel}</strong></p> : null}
              <p className="muted">Severity: {selectedAlert.severity} · Status: {selectedAlert.status}</p>
              <p className="muted">Asset: {selectedAlert.payload?.asset_label || 'n/a'} · Target: {selectedAlert.target_id || 'n/a'}</p>
              <p className="muted">First seen: {selectedAlert.created_at ? new Date(selectedAlert.created_at).toLocaleString() : 'n/a'} · Last seen: {selectedAlert.last_seen_at ? new Date(selectedAlert.last_seen_at).toLocaleString() : 'n/a'}</p>
              <p className="muted">Event count: {selectedAlert.occurrence_count || 1} · Dedup/group key: {selectedAlert.findings?.dedupe_key || selectedAlert.target_id || 'none'}</p>
              <ThreatChainPanel
                chainLinkedIds={selectedAlert.chain_linked_ids}
                detectionId={selectedAlert.detection_id}
                alertId={selectedAlert.id}
                incidentId={selectedAlert.incident_id}
                actionId={selectedAlert.linked_action_id}
                linkedEvidenceCount={selectedAlert.linked_evidence_count}
                lastEvidenceAt={selectedAlert.last_evidence_at}
                evidenceOrigin={selectedAlert.evidence_origin || selectedAlert.evidence_source || selectedAlert.source}
                txHash={selectedAlert.tx_hash || evidence?.tx_hash}
                blockNumber={selectedAlert.block_number || evidence?.block_number}
                detectorKind={selectedAlert.detector_kind}
                liveLikeMode={liveLikeMode}
                evidenceDrawerLabel="Open evidence drawer"
                onOpenEvidence={() => evidenceSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
              />
              {liveLikeMode && linkedEvidenceCount <= 0 ? <p className="statusLine">LIVE/HYBRID degraded state: no persisted evidence is linked yet. Open the evidence drawer below to inspect fallback payload context.</p> : null}
              {!liveLikeMode && linkedEvidenceCount <= 0 ? <p className="muted">No linked evidence is persisted for this alert yet. Open the evidence drawer below to inspect available payload context.</p> : null}
              <div className="buttonRow">
                <select value={actionMode} onChange={(event) => setActionMode(event.target.value as 'simulated' | 'recommended' | 'live')}>
                  <option value="simulated">SIMULATED mode</option>
                  <option value="recommended">Recommended mode (SIMULATED)</option>
                  <option value="live">Live mode</option>
                </select>
                <input value={operatorNotes} onChange={(event) => setOperatorNotes(event.target.value)} placeholder="Operator notes (optional)" />
              </div>
              <div className="buttonRow">
                <button type="button" onClick={() => void patchAlert('acknowledged')}>Acknowledge</button>
                <button type="button" onClick={() => void patchAlert('resolved')}>Resolve</button>
                <button type="button" onClick={() => void escalateIncident()}>Open incident</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.freeze_wallet, actionMode)} title={actionDisabledReason(actionCapabilities.freeze_wallet, actionMode) || ''} onClick={() => void runSimulatedAction('freeze_wallet', 'Freeze wallet')}>Freeze wallet ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.block_transaction, actionMode)} title={actionDisabledReason(actionCapabilities.block_transaction, actionMode) || ''} onClick={() => void runSimulatedAction('block_transaction', 'Block transaction')}>Block transaction ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.revoke_approval, actionMode)} title={actionDisabledReason(actionCapabilities.revoke_approval, actionMode) || ''} onClick={() => void runSimulatedAction('revoke_approval', 'Revoke approval')}>Revoke approval ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.disable_monitored_system, actionMode)} title={actionDisabledReason(actionCapabilities.disable_monitored_system, actionMode) || ''} onClick={() => void runSimulatedAction('disable_monitored_system', 'Disable monitored system')}>Disable monitored system ({actionExecutionLabel})</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.suppress_rule, actionMode)} title={actionDisabledReason(actionCapabilities.suppress_rule, actionMode) || ''} onClick={() => void runSimulatedAction('suppress_rule', 'Suppress rule')}>Suppress/mute rule ({actionExecutionLabel})</button>
                <button type="button" onClick={() => void patchAlert('suppressed')}>Mute rule</button>
                <button type="button" disabled={isActionDisabledInMode(actionCapabilities.notify_team, actionMode)} title={actionDisabledReason(actionCapabilities.notify_team, actionMode) || ''} onClick={() => void runSimulatedAction('notify_team', 'Notify team')}>Notify team ({actionExecutionLabel})</button>
              </div>
              {actionMode === 'live' ? <p className="tableMeta">Live constraints: unsupported actions show “Unsupported live action”; manual paths show “Manual-only in live mode”.</p> : null}
              <p className="tableMeta">Response actions: Freeze wallet · Block transaction · Revoke approval · Disable monitored system · Suppress rule · Notify team</p>
              <p ref={evidenceSectionRef} className="sectionEyebrow">Evidence timeline</p>
              <p className="tableMeta">tx {evidence?.tx_hash || 'n/a'} · block {evidence?.block_number || 'n/a'} · target {evidence?.target_name || 'n/a'}</p>
              <pre>{JSON.stringify(evidence?.raw_payload_excerpt || {}, null, 2)}</pre>
              <p className="muted">Recommended actions: acknowledge if understood, escalate if active risk, suppress only with documented reason.</p>
            </>}
            {message ? <p className="statusLine">{message}</p> : null}
          </article>
        </div>
      </section>
    </main>
  );
}
