'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';
import RuntimeSummaryPanel from '../runtime-summary-panel';
import { StatusPill, TableShell, TabStrip } from '../components/ui-primitives';
import { fetchRuntimeStatusDeduped } from '../runtime-status-client';
import { hasRealTelemetryBackedChain, resolveWorkspaceMonitoringTruth } from '../workspace-monitoring-truth';

type ActionRow = {
  id: string;
  action: string;
  type: string;
  impact: string;
  status: string;
  recommendedBy: string;
  linkedIncident: string;
  evidenceSource: string;
  requiresApproval: string;
  simulated: boolean;
};

function normalizeActionRow(input: any): ActionRow {
  const mode = String(input?.mode || input?.response_action_mode || '').toLowerCase();
  const source = String(input?.source || input?.evidence_source || '').toLowerCase();
  const simulated = mode === 'simulated' || mode === 'recommended' || source === 'fallback' || source === 'simulator';
  const status = String(input?.status || input?.workflow_status || 'pending');
  return {
    id: String(input?.id || `${input?.action_type || 'action'}-${input?.incident_id || 'none'}`),
    action: String(input?.action_type || input?.action || 'Response action'),
    type: String(input?.category || input?.type || 'Workflow'),
    impact: String(input?.result_summary || input?.impact || 'Recorded for workflow traceability.'),
    status: simulated ? `${status} · SIMULATED` : status,
    recommendedBy: String(input?.recommended_by || input?.actor_type || 'Policy engine'),
    linkedIncident: String(input?.incident_id || input?.linked_incident_id || 'unlinked'),
    evidenceSource: String(input?.evidence_source || input?.source || 'runtime'),
    requiresApproval: input?.requires_approval === false ? 'No' : 'Yes',
    simulated,
  };
}

export default function ResponseActionsPageClient({ apiUrl }: { apiUrl: string }) {
  const { authHeaders } = usePilotAuth();
  const [tab, setTab] = useState<'recommended' | 'history'>('recommended');
  const [recommendedRows, setRecommendedRows] = useState<ActionRow[]>([]);
  const [historyRows, setHistoryRows] = useState<ActionRow[]>([]);
  const [liveClaimsAllowed, setLiveClaimsAllowed] = useState(false);

  useEffect(() => {
    async function load() {
      const headers = authHeaders();
      const [actionsRes, historyRes, alertsRes, incidentsRes, runtimePayload] = await Promise.all([
        fetch(`${apiUrl}/response/actions?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
        fetch(`${apiUrl}/history/actions?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
        fetch(`${apiUrl}/alerts?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
        fetch(`${apiUrl}/incidents?limit=50`, { headers, cache: 'no-store' }).catch(() => null),
        fetchRuntimeStatusDeduped(headers).catch(() => null),
      ]);

      const actionsPayload = actionsRes && actionsRes.ok ? await actionsRes.json() : {};
      const historyPayload = historyRes && historyRes.ok ? await historyRes.json() : {};
      const alertsPayload = alertsRes && alertsRes.ok ? await alertsRes.json() : {};
      const incidentsPayload = incidentsRes && incidentsRes.ok ? await incidentsRes.json() : {};

      const incidentIds = new Set((Array.isArray(incidentsPayload?.incidents) ? incidentsPayload.incidents : []).map((item: any) => String(item?.id || '')));
      const alertIncidentIds = new Set((Array.isArray(alertsPayload?.alerts) ? alertsPayload.alerts : []).map((item: any) => String(item?.incident_id || '')).filter(Boolean));
      const workflowIncidentIds = new Set([...incidentIds, ...alertIncidentIds]);

      const recommended = (Array.isArray(actionsPayload?.actions) ? actionsPayload.actions : [])
        .map(normalizeActionRow)
        .filter((row: ActionRow) => workflowIncidentIds.has(row.linkedIncident) || row.linkedIncident === 'unlinked');
      const history = (Array.isArray(historyPayload?.history) ? historyPayload.history : [])
        .filter((item: any) => String(item?.object_type || '').includes('response_action') || String(item?.action_type || '').includes('response'))
        .map((item: any) => normalizeActionRow({
          ...item,
          id: item.id,
          action: item.action_type,
          type: item.object_type,
          incident_id: item.details_json?.incident_id || item.object_id,
          result_summary: item.details_json?.result_summary,
          source: item.details_json?.source,
          status: item.details_json?.status || 'recorded',
          recommended_by: item.actor_type,
          requires_approval: item.details_json?.requires_approval,
        }));

      setRecommendedRows(recommended);
      setHistoryRows(history);
      setLiveClaimsAllowed(hasRealTelemetryBackedChain(resolveWorkspaceMonitoringTruth(runtimePayload)));
    }
    void load();
  }, [apiUrl, authHeaders]);

  const rows = useMemo(() => (tab === 'recommended' ? recommendedRows : historyRows), [tab, recommendedRows, historyRows]);

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="featureSection">
        <div className="sectionHeader"><div><h1>Response Actions</h1><p className="muted">Recommended and historical response actions are sourced from alerts/incidents/workflow APIs. Fallback records are explicitly labeled as SIMULATED.</p></div></div>
        <TabStrip tabs={[{ key: 'recommended', label: 'Recommended Actions' }, { key: 'history', label: 'Action History' }]} active={tab} onChange={(value) => setTab(value as 'recommended' | 'history')} />
        <TableShell headers={['Action', 'Type', 'Impact', 'Status', 'Recommended By', 'Linked Incident', 'Evidence Source', 'Requires Approval']}>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.action}</td><td>{row.type}</td><td>{row.impact}</td><td>{row.status}</td><td>{row.recommendedBy}</td>
              <td>{row.linkedIncident === 'unlinked' ? 'unlinked' : <Link href="/incidents" prefetch={false}>{row.linkedIncident}</Link>}</td>
              <td>{row.evidenceSource}</td><td>{row.requiresApproval}</td>
            </tr>
          ))}
        </TableShell>
        {!rows.length ? <p className="muted">No workflow-linked response actions available yet. Any fallback examples remain clearly marked as SIMULATED.</p> : null}
        <article className="dataCard">
          <div className="listHeader"><h3>Execution labels</h3><StatusPill label="SIMULATED" /></div>
          <p className="muted">Rows sourced from simulator or fallback evidence include explicit SIMULATED labeling in status.</p>
          <div className="listHeader"><h3>Live execution</h3><StatusPill label={liveClaimsAllowed ? 'Live' : 'Unavailable'} /></div>
          <p className="muted">{liveClaimsAllowed ? 'Live execution claims are shown because canonical runtime summary confirms a real telemetry-backed chain.' : 'Live execution claims are hidden until canonical runtime summary confirms a real telemetry-backed chain.'}</p>
        </article>
      </section>
    </main>
  );
}
