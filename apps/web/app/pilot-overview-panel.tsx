'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import type {
  DashboardDiagnostics,
  ResilienceDashboardResponse,
  ThreatDashboardResponse,
} from './dashboard-data';
import { formatSourceLabel, statusTone } from './dashboard-data';
import { usePilotAuth } from 'app/pilot-auth-context';

type BackendState = 'online' | 'degraded' | 'offline';

type HistoryPayload = {
  counts: {
    analysis_runs: number;
    alerts: number;
    governance_actions: number;
    incidents: number;
    audit_logs: number;
  };
  analysis_runs: Array<{ id: string; title: string; created_at: string }>;
  alerts: Array<{ id: string; title: string; severity: string; status: string; created_at: string }>;
  incidents: Array<{ id: string; event_type: string; severity: string; status: string; created_at: string }>;
};

type Props = {
  backendState: BackendState;
  threatDashboard: ThreatDashboardResponse;
  resilienceDashboard: ResilienceDashboardResponse;
  diagnostics: DashboardDiagnostics;
};

function formatBackendLabel(state: BackendState) {
  if (state === 'online') {
    return 'Live services connected';
  }

  if (state === 'degraded') {
    return 'Partial fallback coverage';
  }

  return 'Sample data only';
}

function formatBackendDetail(state: BackendState) {
  if (state === 'online') {
    return 'Customer data and service health are updating from the live platform.';
  }

  if (state === 'degraded') {
    return 'At least one live dashboard feed needs attention, but unaffected sections remain connected to live services.';
  }

  return 'The pilot remains explorable with sample coverage while live connectivity is restored.';
}

export default function PilotOverviewPanel({
  backendState,
  threatDashboard,
  resilienceDashboard,
  diagnostics,
}: Props) {
  const {
    apiUrl,
    authHeaders,
    isAuthenticated,
    liveModeConfigured,
    user,
  } = usePilotAuth();
  const [history, setHistory] = useState<HistoryPayload | null>(null);

  useEffect(() => {
    let active = true;

    async function loadHistory() {
      if (!isAuthenticated || !user?.current_workspace?.id) {
        setHistory(null);
        return;
      }

      const response = await fetch(`${apiUrl}/pilot/history?limit=5`, {
        headers: authHeaders(),
        cache: 'no-store',
      }).catch(() => null);

      if (!active || !response?.ok) {
        return;
      }

      const payload = (await response.json()) as HistoryPayload;
      if (active) {
        setHistory(payload);
      }
    }

    void loadHistory();

    return () => {
      active = false;
    };
  }, [apiUrl, authHeaders, isAuthenticated, user?.current_workspace?.id]);

  const recentAlertItems = useMemo(() => threatDashboard.active_alerts.slice(0, 3), [threatDashboard.active_alerts]);
  const recentIncidentItems = useMemo(() => resilienceDashboard.latest_incidents.slice(0, 3), [resilienceDashboard.latest_incidents]);

  return (
    <section className="pilotOverviewGrid">
      <article className="dataCard overviewCard">
        <p className="sectionEyebrow">Current workspace</p>
        <h2>{user?.current_workspace?.name ?? 'Guest workspace view'}</h2>
        <p className="muted">
          {isAuthenticated
            ? `Signed in as ${user?.email}.`
            : 'Sign in to connect this dashboard to a saved workspace.'}
        </p>
        <div className="chipRow">
          <span className="ruleChip">{isAuthenticated ? 'Workspace active' : 'Guest mode'}</span>
          {user?.memberships?.length ? <span className="ruleChip">{user.memberships.length} workspaces</span> : null}
        </div>
        <div className="overviewActions">
          {isAuthenticated ? <Link href="/workspaces" prefetch={false}>Manage workspace</Link> : <Link href="/sign-in">Sign in</Link>}
          {!isAuthenticated ? <Link href="/sign-up">Create account</Link> : <Link href="/dashboard" prefetch={false}>Refresh dashboard</Link>}
        </div>
      </article>

      <article className="dataCard overviewCard">
        <p className="sectionEyebrow">Live status</p>
        <h2>{formatBackendLabel(backendState)}</h2>
        <p className="muted">{formatBackendDetail(backendState)}</p>
        <div className="kvGrid compactKvGrid">
          <p>
            <span>Access</span>
            {liveModeConfigured ? 'Live workspace enabled' : 'Live workspace unavailable'}
          </p>
          <p>
            <span>Threat feed</span>
            {formatSourceLabel(diagnostics.endpoints.threatDashboard.payloadState)}
          </p>
          <p>
            <span>Incident feed</span>
            {formatSourceLabel(diagnostics.endpoints.resilienceDashboard.payloadState)}
          </p>
        </div>
      </article>

      <article className="dataCard overviewCard">
        <p className="sectionEyebrow">Recent alerts</p>
        <h2>{threatDashboard.summary.critical_or_high_alerts} priority items</h2>
        <div className="stack compactStack">
          {recentAlertItems.map((alert) => (
            <div key={alert.id} className="overviewListItem">
              <div>
                <p className="metricLabel">{alert.category}</p>
                <p>{alert.title}</p>
              </div>
              <span className={`severityPill ${statusTone(alert.action)}`}>{alert.severity}</span>
            </div>
          ))}
        </div>
      </article>

      <article className="dataCard overviewCard">
        <p className="sectionEyebrow">Recent incidents</p>
        <h2>{resilienceDashboard.summary.incident_count} tracked events</h2>
        <div className="stack compactStack">
          {recentIncidentItems.map((incident) => (
            <div key={incident.event_id} className="overviewListItem">
              <div>
                <p className="metricLabel">{incident.event_type}</p>
                <p>{incident.summary}</p>
              </div>
              <span className={`severityPill ${statusTone(incident.status)}`}>{incident.severity}</span>
            </div>
          ))}
        </div>
      </article>

      <article className="dataCard overviewCard">
        <p className="sectionEyebrow">Saved history</p>
        <h2>{history ? `${history.counts.analysis_runs} saved runs` : 'Ready for history'}</h2>
        {history ? (
          <>
            <div className="chipRow">
              <span className="ruleChip">{history.counts.alerts} alerts</span>
              <span className="ruleChip">{history.counts.incidents} incidents</span>
              <span className="ruleChip">{history.counts.audit_logs} audit events</span>
            </div>
            <div className="stack compactStack">
              {history.analysis_runs.slice(0, 3).map((item) => (
                <div key={item.id} className="overviewListItem">
                  <div>
                    <p>{item.title}</p>
                    <p className="muted">{new Date(item.created_at).toLocaleString()}</p>
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="muted">
            {isAuthenticated
              ? 'Run live analyses to start building workspace history.'
              : 'Workspace history appears here after sign-in and live activity.'}
          </p>
        )}
      </article>
    </section>
  );
}
