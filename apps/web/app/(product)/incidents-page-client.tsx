'use client';

import { useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import { usePilotAuth } from '../pilot-auth-context';
import { useLiveWorkspaceFeed } from '../use-live-workspace-feed';

type WorkspaceRole = 'owner' | 'admin' | 'analyst' | 'viewer';
const FILTER_KEY = 'incidents_filters_v2';

export default function IncidentsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders, user } = usePilotAuth();
  const role = (user?.memberships.find((item) => item.workspace_id === user.current_workspace?.id)?.role ?? 'viewer') as WorkspaceRole;
  const feed = useLiveWorkspaceFeed();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [incidents, setIncidents] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [status, setStatus] = useState(searchParams?.get('status') ?? '');
  const [message, setMessage] = useState('');

  useEffect(() => {
    const persisted = window.localStorage.getItem(FILTER_KEY);
    if (!persisted || searchParams?.get('status')) return;
    try {
      const parsed = JSON.parse(persisted) as { status?: string };
      if (parsed.status) setStatus(parsed.status);
    } catch {
      // noop
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const next = new URLSearchParams(searchParams?.toString() ?? '');
    status ? next.set('status', status) : next.delete('status');
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    window.localStorage.setItem(FILTER_KEY, JSON.stringify({ status }));
  }, [pathname, router, searchParams, status]);

  async function load() {
    const params = new URLSearchParams();
    if (status) params.set('status_value', status);
    const response = await fetch(`${apiUrl}/incidents?${params.toString()}`, { headers: authHeaders(), cache: 'no-store' });
    if (!response.ok) return;
    const rows = (await response.json()).incidents ?? [];
    setIncidents(rows);
    if (!selectedId && rows.length) setSelectedId(rows[0].id);
  }

  useEffect(() => { void load(); }, [status]);

  async function createAction(actionType: string) {
    if (!selectedId) return;
    const response = await fetch(`${apiUrl}/findings/${selectedId}/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ action_type: actionType, title: `${actionType} for incident`, notes: 'Created from incidents queue', status: 'open' }),
    });
    setMessage(response.ok ? `${actionType} created.` : `Unable to create ${actionType}.`);
    if (response.ok) {
      window.dispatchEvent(new Event('pilot-history-refresh'));
    }
  }

  const selected = useMemo(() => incidents.find((item) => item.id === selectedId), [incidents, selectedId]);
  const canGovern = role === 'owner' || role === 'admin';
  const canTriage = canGovern || role === 'analyst';

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Workspace incidents</p>
            <h1>Incidents for this workspace</h1>
            <p className="lede">Manage incident lifecycle, assign ownership, and capture operator actions.</p>
          </div>
        </div>
        <div className="buttonRow">
          <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option><option value="open">open</option><option value="acknowledged">acknowledged</option><option value="resolved">resolved</option></select>
          <span className="ruleChip">{feed.offline ? 'Offline' : feed.degraded ? 'Degraded' : 'Live'} workspace feed</span>
          {feed.stale ? <span className="ruleChip">Evidence stale</span> : null}
        </div>
        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Open incidents</p>
            {incidents.map((incident) => (
              <p key={incident.id}>
                <button type="button" onClick={() => setSelectedId(incident.id)}>{incident.title || incident.event_type}</button> · <span className={`statusBadge statusBadge--${incident.status}`}>{incident.status}</span>
                <br />
                <span className="muted">workspace {user?.current_workspace?.name} · monitored target {incident.target_id || 'n/a'}</span>
              </p>
            ))}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Operator actions</p>
            {!selected ? <p className="muted">Select an incident.</p> : (
              <>
                <p><strong>{selected.title || selected.event_type}</strong></p>
                <p className="muted">{selected.summary || 'No summary'}</p>
                <div className="buttonRow">
                  <button type="button" disabled={!canTriage} title={canTriage ? 'Escalate incident' : 'Viewer role is read-only'} onClick={() => void createAction('escalate_incident')}>Escalate incident</button>
                  <button type="button" disabled={!canTriage} title={canTriage ? 'Assign owner' : 'Viewer role is read-only'} onClick={() => void createAction('assign_owner')}>Assign owner</button>
                  <button type="button" disabled={!canTriage} title={canTriage ? 'Create remediation task' : 'Viewer role is read-only'} onClick={() => void createAction('remediation_task')}>Create remediation task</button>
                </div>
                <div className="buttonRow">
                  <button type="button" disabled={!canGovern} title={canGovern ? 'Apply governance action' : 'Only admin/owner can apply governance actions'} onClick={() => void createAction('block_transaction')}>Block transaction</button>
                  <button type="button" disabled={!canGovern} title={canGovern ? 'Apply governance action' : 'Only admin/owner can apply governance actions'} onClick={() => void createAction('freeze_wallet')}>Freeze wallet</button>
                  <button type="button" disabled={!canGovern} title={canGovern ? 'Apply governance action' : 'Only admin/owner can apply governance actions'} onClick={() => void createAction('pause_asset')}>Pause asset</button>
                </div>
              </>
            )}
            {message ? <p className="statusLine">{message}</p> : null}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Incident timeline</p>
            {(selected?.timeline ?? []).length === 0 ? <p className="muted">No timeline events.</p> : (selected.timeline ?? []).map((item: any, index: number) => (
              <p key={`${item.event}-${index}`}>{item.event} · {item.at ? new Date(item.at).toLocaleString() : 'n/a'}</p>
            ))}
          </article>
        </div>
      </section>
    </main>
  );
}

