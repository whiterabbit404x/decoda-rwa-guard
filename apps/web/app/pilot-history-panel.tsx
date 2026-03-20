'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

type HistoryPayload = {
  workspace: { id: string; name: string; slug: string };
  role: string;
  counts: {
    analysis_runs: number;
    alerts: number;
    governance_actions: number;
    incidents: number;
    audit_logs: number;
  };
  analysis_runs: Array<{ id: string; title: string; analysis_type: string; service_name: string; summary: string; created_at: string }>;
  alerts: Array<{ id: string; title: string; severity: string; status: string; created_at: string }>;
  governance_actions: Array<{ id: string; action_type: string; target_id: string; status: string; created_at: string }>;
  incidents: Array<{ id: string; event_type: string; severity: string; status: string; created_at: string }>;
  audit_logs: Array<{ id: string; action: string; entity_type: string; created_at: string }>;
};

export default function PilotHistoryPanel() {
  const { apiUrl, authHeaders, isAuthenticated, user } = usePilotAuth();
  const [history, setHistory] = useState<HistoryPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function loadHistory() {
      if (!isAuthenticated || !user?.current_workspace?.id) {
        setHistory(null);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(`${apiUrl}/pilot/history?limit=10`, {
          headers: authHeaders(),
          cache: 'no-store',
        });
        const payload = (await response.json()) as HistoryPayload | { detail?: string };
        if (!response.ok) {
          throw new Error('detail' in payload ? payload.detail ?? 'Unable to load live history.' : 'Unable to load live history.');
        }
        if (active) {
          setHistory(payload as HistoryPayload);
        }
      } catch (historyError) {
        if (active) {
          setError(historyError instanceof Error ? historyError.message : String(historyError));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void loadHistory();
    window.addEventListener('pilot-history-refresh', loadHistory as EventListener);
    return () => {
      active = false;
      window.removeEventListener('pilot-history-refresh', loadHistory as EventListener);
    };
  }, [apiUrl, authHeaders, isAuthenticated, user?.current_workspace?.id]);

  if (!isAuthenticated) {
    return (
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <h2>Live pilot workspace history</h2>
            <p>Sign in to save alerts, analysis runs, governance actions, incidents, and audit logs in Neon.</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="featureSection">
      <div className="sectionHeader">
        <div>
          <h2>Live pilot workspace history</h2>
          <p>
            Current workspace: <strong>{user?.current_workspace?.name ?? 'Unselected'}</strong>
          </p>
        </div>
        <p className="tableMeta">{loading ? 'Loading Neon-backed history…' : error ?? 'Workspace-scoped records persisted through the API gateway.'}</p>
      </div>

      {history ? (
        <>
          <div className="summaryGrid threatSummaryGrid">
            <article className="metricCard"><p className="metricLabel">Analysis runs</p><p className="metricValue">{history.counts.analysis_runs}</p></article>
            <article className="metricCard"><p className="metricLabel">Alerts</p><p className="metricValue">{history.counts.alerts}</p></article>
            <article className="metricCard"><p className="metricLabel">Governance actions</p><p className="metricValue">{history.counts.governance_actions}</p></article>
            <article className="metricCard"><p className="metricLabel">Incidents</p><p className="metricValue">{history.counts.incidents}</p></article>
          </div>
          <div className="threeColumnSection">
            <div className="stack compactStack">
              <div className="sectionHeader compact"><h3>Recent analysis runs</h3></div>
              {history.analysis_runs.map((item) => (
                <article key={item.id} className="dataCard">
                  <h3>{item.title}</h3>
                  <p className="muted">{item.analysis_type} · {item.service_name}</p>
                  <p className="explanation small">{item.summary}</p>
                </article>
              ))}
            </div>
            <div className="stack compactStack">
              <div className="sectionHeader compact"><h3>Recent alerts & incidents</h3></div>
              {history.alerts.map((item) => (
                <article key={item.id} className="dataCard">
                  <h3>{item.title}</h3>
                  <p className="muted">{item.severity} · {item.status}</p>
                </article>
              ))}
              {history.incidents.map((item) => (
                <article key={item.id} className="dataCard">
                  <h3>{item.event_type}</h3>
                  <p className="muted">{item.severity} · {item.status}</p>
                </article>
              ))}
            </div>
            <div className="stack compactStack">
              <div className="sectionHeader compact"><h3>Governance & audit</h3></div>
              {history.governance_actions.map((item) => (
                <article key={item.id} className="dataCard">
                  <h3>{item.action_type}</h3>
                  <p className="muted">{item.target_id} · {item.status}</p>
                </article>
              ))}
              {history.audit_logs.slice(0, 5).map((item) => (
                <article key={item.id} className="dataCard">
                  <h3>{item.action}</h3>
                  <p className="muted">{item.entity_type} · {new Date(item.created_at).toLocaleString()}</p>
                </article>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}
