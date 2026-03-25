'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';

import { determineHistoryCategory, filterRecordsByRecentActivity, HistoryPayload } from './pilot-history';
import StatusBadge from './status-badge';

type Props = {
  history: HistoryPayload | null;
  loading?: boolean;
  error?: string | null;
  workspaceName?: string | null;
};

type DetailRecord =
  | { kind: 'analysis'; payload: HistoryPayload['analysis_runs'][number] }
  | { kind: 'governance'; payload: HistoryPayload['governance_actions'][number] }
  | { kind: 'incident'; payload: HistoryPayload['incidents'][number] };

export default function HistoryRecordsView({ history, loading = false, error, workspaceName }: Props) {
  const [recentDays, setRecentDays] = useState(30);
  const [detailRecord, setDetailRecord] = useState<DetailRecord | null>(null);

  const filtered = useMemo(() => {
    if (!history) {
      return null;
    }
    return {
      threatRuns: filterRecordsByRecentActivity(history.analysis_runs.filter((item) => determineHistoryCategory(item.analysis_type) === 'threat'), recentDays),
      complianceRuns: filterRecordsByRecentActivity(history.analysis_runs.filter((item) => determineHistoryCategory(item.analysis_type) === 'compliance'), recentDays),
      resilienceRuns: filterRecordsByRecentActivity(history.analysis_runs.filter((item) => determineHistoryCategory(item.analysis_type) === 'resilience'), recentDays),
      governanceActions: filterRecordsByRecentActivity(history.governance_actions, recentDays),
      incidents: filterRecordsByRecentActivity(history.incidents, recentDays),
    };
  }, [history, recentDays]);

  return (
    <section className="featureSection">
      <div className="sectionHeader">
        <div>
          <p className="eyebrow">Persisted customer records</p>
          <h1>Workspace history</h1>
          <p className="lede">Browse threat analyses, compliance actions, and resilience incidents saved for {workspaceName ?? history?.workspace.name ?? 'the active workspace'}.</p>
        </div>
        <div className="historyToolbar">
          <label className="label compactLabel">
            Recent activity
            <select value={recentDays} onChange={(event) => setRecentDays(Number(event.target.value))}>
              <option value={1}>24h</option>
              <option value={7}>7d</option>
              <option value={30}>30d</option>
              <option value={3650}>All</option>
            </select>
          </label>
        </div>
      </div>

      {loading ? <div className="emptyStatePanel"><h2>Loading persisted records…</h2><p>We are pulling the latest saved analyses and operational events for this workspace.</p></div> : null}
      {error ? <div className="emptyStatePanel"><h2>Unable to load history</h2><p>{error}</p></div> : null}
      {!loading && !error && !history ? <div className="emptyStatePanel"><h2>No saved records yet</h2><p>Run live analyses from the threat, compliance, or resilience routes to start building a customer-ready audit trail.</p></div> : null}
      {!loading && !error && history && history.analysis_runs.length === 0 ? (
        <div className="emptyStatePanel">
          <h2>No analyses yet</h2>
          <p>Your workspace is ready. Run your first threat analysis to create a reviewable audit trail.</p>
          <div className="heroActionRow">
            <Link href="/threat">Run your first threat analysis</Link>
          </div>
        </div>
      ) : null}

      {history && filtered ? (
        <>
          <div className="summaryGrid">
            <article className="metricCard"><p className="metricLabel">Threat runs</p><p className="metricValue">{filtered.threatRuns.length}</p><p className="metricMeta">Saved analyses in window</p></article>
            <article className="metricCard"><p className="metricLabel">Compliance runs</p><p className="metricValue">{filtered.complianceRuns.length}</p><p className="metricMeta">Includes screens and governance</p></article>
            <article className="metricCard"><p className="metricLabel">Resilience runs</p><p className="metricValue">{filtered.resilienceRuns.length}</p><p className="metricMeta">Reconciliation and backstop reviews</p></article>
            <article className="metricCard"><p className="metricLabel">Incidents</p><p className="metricValue">{filtered.incidents.length}</p><p className="metricMeta">Operational incidents in window</p></article>
          </div>

          <div className="historyLayout">
            <div className="stack compactStack">
              <div className="sectionHeader compact"><h2>Threat analyses</h2><p>Saved contract, transaction, and market reviews.</p></div>
              {filtered.threatRuns.map((item) => (
                <button key={item.id} type="button" className="dataCard detailButton" onClick={() => setDetailRecord({ kind: 'analysis', payload: item })}>
                  <div className="listHeader"><div><h3>{item.title}</h3><p className="muted">{item.analysis_type} · {item.service_name}</p></div><StatusBadge state={item.source === 'live' ? 'live' : 'fallback'} compact /></div>
                  <p className="explanation small">{item.summary}</p>
                  <p className="tableMeta">{new Date(item.created_at).toLocaleString()} · {history.workspace.name} · {item.status}</p>
                </button>
              ))}
            </div>

            <div className="stack compactStack">
              <div className="sectionHeader compact"><h2>Compliance screens & governance</h2><p>Transfer checks, residency decisions, and control actions.</p></div>
              {filtered.complianceRuns.map((item) => (
                <button key={item.id} type="button" className="dataCard detailButton" onClick={() => setDetailRecord({ kind: 'analysis', payload: item })}>
                  <div className="listHeader"><div><h3>{item.title}</h3><p className="muted">{item.analysis_type}</p></div><StatusBadge state={item.source === 'live' ? 'live' : 'fallback'} compact /></div>
                  <p className="explanation small">{item.summary}</p>
                  <p className="tableMeta">{new Date(item.created_at).toLocaleString()} · {history.workspace.name} · {item.status}</p>
                </button>
              ))}
              {filtered.governanceActions.map((item) => (
                <button key={item.id} type="button" className="dataCard detailButton" onClick={() => setDetailRecord({ kind: 'governance', payload: item })}>
                  <div className="listHeader"><div><h3>{item.action_type}</h3><p className="muted">{item.target_type} · {item.target_id}</p></div><StatusBadge state="live" compact /></div>
                  <p className="explanation small">{item.reason}</p>
                  <p className="tableMeta">{new Date(item.created_at).toLocaleString()} · {history.workspace.name} · {item.status}</p>
                </button>
              ))}
            </div>

            <div className="stack compactStack">
              <div className="sectionHeader compact"><h2>Resilience runs & incidents</h2><p>Recovery posture, incidents, and backstop evidence.</p></div>
              {filtered.resilienceRuns.map((item) => (
                <button key={item.id} type="button" className="dataCard detailButton" onClick={() => setDetailRecord({ kind: 'analysis', payload: item })}>
                  <div className="listHeader"><div><h3>{item.title}</h3><p className="muted">{item.analysis_type}</p></div><StatusBadge state={item.source === 'live' ? 'live' : 'fallback'} compact /></div>
                  <p className="explanation small">{item.summary}</p>
                  <p className="tableMeta">{new Date(item.created_at).toLocaleString()} · {history.workspace.name} · {item.status}</p>
                </button>
              ))}
              {filtered.incidents.map((item) => (
                <button key={item.id} type="button" className="dataCard detailButton" onClick={() => setDetailRecord({ kind: 'incident', payload: item })}>
                  <div className="listHeader"><div><h3>{item.event_type}</h3><p className="muted">{item.severity} · {item.status}</p></div><StatusBadge state="live" compact /></div>
                  <p className="explanation small">{item.summary}</p>
                  <p className="tableMeta">{new Date(item.created_at).toLocaleString()} · {history.workspace.name} · {item.status}</p>
                </button>
              ))}
            </div>
          </div>

          {detailRecord ? (
            <aside className="detailDrawer">
              <div className="listHeader"><div><p className="sectionEyebrow">Record detail</p><h2>{detailRecord.kind === 'analysis' ? detailRecord.payload.title : detailRecord.kind === 'governance' ? detailRecord.payload.action_type : detailRecord.payload.event_type}</h2></div><button type="button" onClick={() => setDetailRecord(null)}>Close</button></div>
              <pre>{JSON.stringify(detailRecord.payload, null, 2)}</pre>
            </aside>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
