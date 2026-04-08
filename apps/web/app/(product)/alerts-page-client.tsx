'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';

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

  const selectedAlert = useMemo(() => alerts.find((item) => item.id === selectedAlertId) ?? null, [alerts, selectedAlertId]);

  useEffect(() => {
    if (!selectedAlertId) return;
    void fetch(`${apiUrl}/alerts/${selectedAlertId}/evidence`, { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => setEvidence(payload?.evidence ?? null));
  }, [apiUrl, authHeaders, selectedAlertId]);

  async function patchAlert(nextStatus: 'acknowledged' | 'resolved' | 'suppressed') {
    if (!selectedAlert) return;
    const response = await fetch(`${apiUrl}/alerts/${selectedAlert.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ status: nextStatus }),
    });
    setMessage(response.ok ? `Alert ${nextStatus}.` : `Unable to ${nextStatus} alert.`);
    if (response.ok) void load();
  }

  async function escalateIncident() {
    if (!selectedAlert) return;
    const response = await fetch(`${apiUrl}/pilot/resilience/incidents/record`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        event_type: 'alert_escalation',
        title: `Escalated alert: ${selectedAlert.title}`,
        summary: selectedAlert.summary || selectedAlert.title,
        severity: selectedAlert.severity || 'high',
        target_id: selectedAlert.target_id,
      }),
    });
    setMessage(response.ok ? 'Escalated to incident.' : 'Unable to escalate alert to incident.');
  }

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
              <p className="muted">Severity: {selectedAlert.severity} · Status: {selectedAlert.status}</p>
              <p className="muted">Asset: {selectedAlert.payload?.asset_label || 'n/a'} · Target: {selectedAlert.target_id || 'n/a'}</p>
              <p className="muted">First seen: {selectedAlert.created_at ? new Date(selectedAlert.created_at).toLocaleString() : 'n/a'} · Last seen: {selectedAlert.last_seen_at ? new Date(selectedAlert.last_seen_at).toLocaleString() : 'n/a'}</p>
              <p className="muted">Event count: {selectedAlert.occurrence_count || 1} · Dedup/group key: {selectedAlert.findings?.dedupe_key || selectedAlert.target_id || 'none'}</p>
              <div className="buttonRow">
                <button type="button" onClick={() => void patchAlert('acknowledged')}>Acknowledge</button>
                <button type="button" onClick={() => void patchAlert('resolved')}>Resolve</button>
                <button type="button" onClick={() => void escalateIncident()}>Escalate to incident</button>
                <button type="button" onClick={() => void patchAlert('suppressed')}>Mute rule</button>
              </div>
              <p className="sectionEyebrow">Evidence timeline</p>
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
