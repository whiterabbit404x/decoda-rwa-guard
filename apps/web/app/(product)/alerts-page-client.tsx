'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import { usePilotAuth } from '../pilot-auth-context';
import { useLiveWorkspaceFeed } from '../use-live-workspace-feed';

type WorkspaceRole = 'owner' | 'admin' | 'analyst' | 'viewer';

const FILTER_KEY = 'alerts_filters_v2';

export default function AlertsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders, user } = usePilotAuth();
  const feed = useLiveWorkspaceFeed();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const role = (user?.memberships.find((item) => item.workspace_id === user.current_workspace?.id)?.role ?? 'viewer') as WorkspaceRole;
  const [alerts, setAlerts] = useState<any[]>([]);
  const [actions, setActions] = useState<any[]>([]);
  const [selectedAlertId, setSelectedAlertId] = useState('');
  const [severity, setSeverity] = useState(searchParams?.get('severity') ?? '');
  const [status, setStatus] = useState(searchParams?.get('status') ?? '');
  const [targetId, setTargetId] = useState(searchParams?.get('target') ?? '');
  const [noteText, setNoteText] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);

  const selectedAlert = useMemo(() => alerts.find((item) => item.id === selectedAlertId) ?? null, [alerts, selectedAlertId]);

  useEffect(() => {
    const persisted = window.localStorage.getItem(FILTER_KEY);
    if (!persisted) return;
    try {
      const parsed = JSON.parse(persisted) as { severity?: string; status?: string; targetId?: string };
      if (!searchParams?.get('severity') && parsed.severity) setSeverity(parsed.severity);
      if (!searchParams?.get('status') && parsed.status) setStatus(parsed.status);
      if (!searchParams?.get('target') && parsed.targetId) setTargetId(parsed.targetId);
    } catch {
      // noop
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const next = new URLSearchParams(searchParams?.toString() ?? '');
    severity ? next.set('severity', severity) : next.delete('severity');
    status ? next.set('status', status) : next.delete('status');
    targetId ? next.set('target', targetId) : next.delete('target');
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    window.localStorage.setItem(FILTER_KEY, JSON.stringify({ severity, status, targetId }));
  }, [pathname, router, searchParams, severity, status, targetId]);

  async function load() {
    setLoading(true);
    const params = new URLSearchParams();
    if (severity) params.set('severity', severity);
    if (status) params.set('status_value', status);
    if (targetId) params.set('target_id', targetId);
    const [alertsResponse, actionsResponse] = await Promise.all([
      fetch(`${apiUrl}/alerts?${params.toString()}`, { headers: authHeaders(), cache: 'no-store' }),
      fetch(`${apiUrl}/actions`, { headers: authHeaders(), cache: 'no-store' }),
    ]);
    if (alertsResponse.ok) {
      const payload = await alertsResponse.json();
      const rows = payload.alerts ?? [];
      setAlerts(rows);
      if (!selectedAlertId && rows.length > 0) setSelectedAlertId(rows[0].id);
    }
    if (actionsResponse.ok) setActions((await actionsResponse.json()).actions ?? []);
    setLoading(false);
  }

  useEffect(() => { void load(); }, [severity, status, targetId]);

  async function applyGovernanceAction(actionType: string) {
    if (!selectedAlert) return;
    const response = await fetch(`${apiUrl}/pilot/compliance/governance/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        action_type: actionType,
        target_id: selectedAlert.target_id ?? selectedAlert.id,
        target_type: 'alert',
        reason: noteText || `Operator action from workspace alert ${selectedAlert.id}`,
      }),
    });
    setMessage(response.ok ? `${actionType} submitted.` : `Unable to submit ${actionType}.`);
    if (response.ok) {
      window.dispatchEvent(new Event('pilot-history-refresh'));
      await load();
    }
  }

  async function createWorkflowAction(actionType: string) {
    if (!selectedAlert) return;
    const response = await fetch(`${apiUrl}/findings/${selectedAlert.id}/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ action_type: actionType, title: `${actionType} for workspace alert`, notes: noteText || 'Created by operator', status: 'open' }),
    });
    setMessage(response.ok ? `${actionType} created.` : `Unable to create ${actionType}.`);
    if (response.ok) {
      window.dispatchEvent(new Event('pilot-history-refresh'));
      await load();
    }
  }

  const relatedActions = actions.filter((item) => item.finding_id === selectedAlertId);
  const canGovern = role === 'owner' || role === 'admin';
  const canTriage = canGovern || role === 'analyst';

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Workspace findings</p>
            <h1>Alerts for this workspace</h1>
            <p className="lede">Review protected assets, take operator actions, and record governance decisions in a persistent audit trail.</p>
          </div>
        </div>
        <div className="buttonRow">
          <select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select>
          <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option><option value="open">open</option><option value="acknowledged">acknowledged</option><option value="resolved">resolved</option></select>
          <input placeholder="Target ID" value={targetId} onChange={(event) => setTargetId(event.target.value)} />
          <span className="ruleChip">{loading || feed.refreshing ? 'Refreshing…' : `Last update ${feed.lastUpdatedAt ? new Date(feed.lastUpdatedAt).toLocaleTimeString() : 'pending'}`}</span>
        </div>
        {feed.stale ? <p className="statusLine">Evidence for this workspace is stale. Validate telemetry before closing critical alerts.</p> : null}
        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Workspace findings</p>
            {loading ? <p className="muted">Loading workspace alerts…</p> : null}
            {alerts.map((alert) => (
              <p key={alert.id}>
                <button type="button" onClick={() => setSelectedAlertId(alert.id)}>{alert.title}</button> · <span className={`statusBadge statusBadge--${alert.severity}`}>{alert.severity}</span>
                <br />
                <span className="muted">protected asset: {alert.payload?.asset_label || 'n/a'} · monitored target: {alert.target_id || 'n/a'}</span>
              </p>
            ))}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Operator decision actions</p>
            {!selectedAlert ? <p className="muted">Select a workspace finding.</p> : (
              <>
                <p><strong>{selectedAlert.title}</strong></p>
                <p className="muted">Protected asset {selectedAlert.payload?.asset_label || 'n/a'} · workspace {user?.current_workspace?.name}</p>
                <textarea value={noteText} onChange={(event) => setNoteText(event.target.value)} placeholder="Operator note for audit trail" />
                <div className="buttonRow">
                  <button type="button" disabled={!canGovern} title={canGovern ? 'Apply governance action' : 'Only admin/owner can apply governance actions'} onClick={() => void applyGovernanceAction('block_transaction')}>Block transaction</button>
                  <button type="button" disabled={!canGovern} title={canGovern ? 'Apply governance action' : 'Only admin/owner can apply governance actions'} onClick={() => void applyGovernanceAction('freeze_wallet')}>Freeze wallet</button>
                  <button type="button" disabled={!canGovern} title={canGovern ? 'Apply governance action' : 'Only admin/owner can apply governance actions'} onClick={() => void applyGovernanceAction('pause_asset')}>Pause asset</button>
                  <button type="button" disabled={!canGovern} title={canGovern ? 'Apply governance action' : 'Only admin/owner can apply governance actions'} onClick={() => void applyGovernanceAction('apply_compliance_rule')}>Apply compliance rule</button>
                </div>
                <div className="buttonRow">
                  <button type="button" disabled={!canTriage} title={canTriage ? 'Create workflow action' : 'Viewer role is read-only'} onClick={() => void createWorkflowAction('escalate_incident')}>Escalate incident</button>
                  <button type="button" disabled={!canTriage} title={canTriage ? 'Create workflow action' : 'Viewer role is read-only'} onClick={() => void createWorkflowAction('assign_owner')}>Assign owner</button>
                  <button type="button" disabled={!canTriage} title={canTriage ? 'Create workflow action' : 'Viewer role is read-only'} onClick={() => void createWorkflowAction('remediation_task')}>Create remediation task</button>
                </div>
              </>
            )}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Action timeline</p>
            {relatedActions.length === 0 ? <p className="muted">No actions for this alert.</p> : relatedActions.map((item) => (
              <p key={item.id}>
                {item.action_type} · <span className={`statusBadge statusBadge--${item.status}`}>{item.status}</span>
                <br />
                <span className="muted">{new Date(item.created_at).toLocaleString()}</span>
              </p>
            ))}
            {message ? <p className="statusLine">{message}</p> : null}
          </article>
        </div>
      </section>
    </main>
  );
}

