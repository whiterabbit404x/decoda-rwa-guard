'use client';

import { Suspense, useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import AiInvestigationPanel from './ai-investigation-panel';
import { TabStrip } from './components/ui-primitives';
import IncidentCaseOverview from './incident-case-overview';
import IncidentEvidenceTab from './incident-evidence-tab';
import IncidentForensicTimeline from './incident-forensic-timeline';
import IncidentWorkflowTab from './incident-workflow-tab';
import {
  loadStateFor,
  type ForensicLoadState,
  type IncidentTimelineEvent,
} from './incident-forensics-presentation';
import { usePilotAuth } from './pilot-auth-context';
import { useRuntimeSummary } from './runtime-summary-context';
import { useIncidentEvidence } from './use-incident-forensics';
// Reuse the SAME Case File tab bodies the /incidents Case File links to — but without the
// list shell (table, KPI tiles, filters, pagination, the create-incident control, or the
// Case File panel itself). This is the full investigation workspace's forensic record,
// sitting beneath the Digital Forensics Investigator hero, and it is where the DETAIL
// lives: the lifecycle chronology, the four evidence domains and their artifact
// directory, the policy forensics, the response authorization trail and the AI
// investigation — each at the full width of the main content area rather than squeezed
// into the narrow Case File preview.
//
// It deliberately does NOT render the corroborated evidence — the forensic hero already
// owns that — so the /investigation payload is never fetched twice on this page. The
// Workflow tab reads the dedicated /workflow endpoint instead, and only once its tab is
// selected, so the stage model is available beside the rest of the record without a
// second investigation fetch on load. The AI Investigation panel mounts here (and only
// here) under its own tab: it runs its own /ai-triage lifecycle, which the hero does not
// fetch.
import {
  AlertsTab,
  EvidenceTab,
  ResponseActionsTab,
  TimelineTab,
  onlyResponseActions,
  type AlertRow,
  type EvidenceRow,
  type ResponseActionRow,
  type TimelineEntry,
} from './incidents-panel';

// Same-origin proxy base — the Incidents UI never calls the backend directly (the browser
// only sees NEXT_PUBLIC_API_URL, often unset in production). Every call goes through the
// Next.js /api/* proxy, the same transport the drawer and forensic panel already use.
const API_PROXY_BASE = '/api';

// The canonical /incidents/{id}/timeline response: the legacy newest-first
// `timeline` projection plus the Screen 7 forensic `events` lifecycle.
type ForensicTimelineResponse = {
  incident_id?: string;
  event_id?: string | null;
  timeline?: TimelineEntry[];
  events?: IncidentTimelineEvent[];
  undated_events?: number;
  partial?: boolean;
  unreadable?: string[];
};

const CASE_FILE_TABS = [
  { key: 'overview',         label: 'Overview' },
  { key: 'timeline',         label: 'Timeline' },
  { key: 'alerts',           label: 'Alerts' },
  { key: 'evidence',         label: 'Evidence' },
  { key: 'response-actions', label: 'Response Actions' },
  { key: 'workflow',         label: 'Workflow' },
  { key: 'ai-investigation', label: 'AI Investigation' },
] as const;

type CaseFileTabKey = (typeof CASE_FILE_TABS)[number]['key'];

function isCaseFileTab(value: string | null | undefined): value is CaseFileTabKey {
  return !!value && CASE_FILE_TABS.some((t) => t.key === value);
}

/* ── Public wrapper ────────────────────────────────────────────────────────
   useSearchParams() must sit under a Suspense boundary, so the exported entry
   point provides one (the detail page renders <IncidentCaseFileTabs /> directly). */
export default function IncidentCaseFileTabs({ incidentId }: { incidentId: string }) {
  return (
    <Suspense fallback={<CaseFileFallback />}>
      <CaseFileTabsInner incidentId={incidentId} />
    </Suspense>
  );
}

function CaseFileFallback() {
  return (
    <section className="featureSection" aria-label="Incident record">
      <div className="dataCard sharedSurfaceCard" style={{ padding: '1.15rem' }}>
        <p className="muted" style={{ fontSize: '0.85rem' }}>Loading incident record…</p>
      </div>
    </section>
  );
}

/* ── Tabs (single incident, no list) ──────────────────────────────────────── */
function CaseFileTabsInner({ incidentId }: { incidentId: string }) {
  const { authHeaders } = usePilotAuth();
  const { summary } = useRuntimeSummary();
  const router = useRouter();
  const searchParams = useSearchParams();
  const apiUrl = API_PROXY_BASE;
  const workspaceEvidenceSource: string = summary.evidence_source_summary ?? '';

  // Deep link support: the forensic panel's "View all evidence" links to
  // /incidents/{id}?tab=evidence. Honour ?tab= on load and on client navigation,
  // while a manual tab click still updates local state.
  const tabParam = searchParams?.get('tab') ?? null;
  const [activeTab, setActiveTab] = useState<CaseFileTabKey>(
    isCaseFileTab(tabParam) ? tabParam : 'overview',
  );
  useEffect(() => {
    if (isCaseFileTab(tabParam)) setActiveTab(tabParam);
  }, [tabParam]);

  const [sourceAlertId, setSourceAlertId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [forensicTimeline, setForensicTimeline] = useState<ForensicTimelineResponse | null>(null);
  const [timelineLoad, setTimelineLoad] = useState<ForensicLoadState>('idle');
  const [linkedAlert, setLinkedAlert] = useState<AlertRow | null>(null);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [responseActions, setResponseActions] = useState<ResponseActionRow[]>([]);
  // Whether Screen 8's action records have been READ. An empty array mid-fetch must
  // not render as "no response action recommended".
  const [responseLoad, setResponseLoad] = useState<ForensicLoadState>('idle');
  const [recommending, setRecommending] = useState(false);
  const [recommendError, setRecommendError] = useState('');

  // ONE fetch of the incident's forensic evidence record, feeding both the Overview
  // case summary and the Evidence directory below it.
  const incidentEvidence = useIncidentEvidence(incidentId);

  // Resolve the incident's source alert id (drives the Alerts + Evidence tabs). On the
  // list page this came from the selected row; here we fetch the single incident directly.
  useEffect(() => {
    if (!incidentId) return;
    let cancelled = false;
    void fetch(`${apiUrl}/incidents/${encodeURIComponent(incidentId)}`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        if (cancelled) return;
        const incident = ((json as Record<string, unknown> | null)?.incident ?? json ?? null) as
          | { source_alert_id?: string | null }
          | null;
        setSourceAlertId(incident?.source_alert_id ?? null);
      })
      .catch(() => { if (!cancelled) setSourceAlertId(null); });
    return () => { cancelled = true; };
  }, [apiUrl, authHeaders, incidentId]);

  // Timeline — ONE fetch returns both the legacy projection and the Screen 7
  // forensic lifecycle, so the case record never issues a second round trip.
  useEffect(() => {
    if (!incidentId) { setTimeline([]); setForensicTimeline(null); setTimelineLoad('idle'); return; }
    let cancelled = false;
    setTimelineLoad('loading');
    void fetch(`${apiUrl}/incidents/${encodeURIComponent(incidentId)}/timeline`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then(async (r) => {
        if (cancelled) return;
        if (!r.ok) {
          setTimeline([]);
          setForensicTimeline(null);
          setTimelineLoad(loadStateFor(r.status, false));
          return;
        }
        const json = (await r.json()) as ForensicTimelineResponse;
        if (cancelled) return;
        setTimeline(json?.timeline ?? []);
        setForensicTimeline(json ?? null);
        setTimelineLoad(loadStateFor(r.status, (json?.events ?? []).length > 0));
      })
      .catch(() => {
        if (cancelled) return;
        setTimeline([]);
        setForensicTimeline(null);
        setTimelineLoad('error');
      });
    return () => { cancelled = true; };
  }, [apiUrl, authHeaders, incidentId]);

  // Linked alert + its evidence (only when the incident has a source alert)
  useEffect(() => {
    if (!sourceAlertId) { setLinkedAlert(null); setEvidence([]); return; }
    let cancelled = false;
    void fetch(`${apiUrl}/alerts/${encodeURIComponent(sourceAlertId)}`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => { if (!cancelled) setLinkedAlert((json as { alert?: AlertRow } | null)?.alert ?? (json as AlertRow | null) ?? null); })
      .catch(() => { if (!cancelled) setLinkedAlert(null); });
    void fetch(`${apiUrl}/alerts/${encodeURIComponent(sourceAlertId)}/evidence`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        if (cancelled) return;
        const ev = (json as { evidence?: EvidenceRow | EvidenceRow[] } | null)?.evidence;
        if (!ev) { setEvidence([]); return; }
        setEvidence(Array.isArray(ev) ? ev : [ev]);
      })
      .catch(() => { if (!cancelled) setEvidence([]); });
    return () => { cancelled = true; };
  }, [apiUrl, authHeaders, sourceAlertId]);

  // Response actions (executable actions only — recommendation-review records excluded)
  useEffect(() => {
    if (!incidentId) { setResponseActions([]); setResponseLoad('idle'); return; }
    let cancelled = false;
    setResponseLoad('loading');
    void fetch(`${apiUrl}/response/actions?incident_id=${encodeURIComponent(incidentId)}`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then(async (r) => {
        if (cancelled) return;
        if (!r.ok) { setResponseActions([]); setResponseLoad(loadStateFor(r.status, false)); return; }
        const json = (await r.json().catch(() => null)) as { actions?: unknown } | null;
        if (cancelled) return;
        const rows = onlyResponseActions(json?.actions);
        setResponseActions(rows);
        setResponseLoad(loadStateFor(r.status, rows.length > 0));
      })
      .catch(() => { if (!cancelled) { setResponseActions([]); setResponseLoad('error'); } });
    return () => { cancelled = true; };
  }, [apiUrl, authHeaders, incidentId]);

  // Recommend a response action — the same approval-routed, non-executing hand-off the
  // drawer performs (Screen 8 approval boundary is preserved: nothing is executed here).
  const handleRecommend = useCallback(async () => {
    if (!incidentId || recommending) return;
    setRecommending(true);
    setRecommendError('');
    try {
      const res = await fetch(`${apiUrl}/incidents/${encodeURIComponent(incidentId)}/response-actions/recommend`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        setRecommendError(String(err.detail ?? err.message ?? 'Failed to recommend response action.'));
        return;
      }
      const data = (await res.json()) as { response_action_id?: string };
      router.push(
        `/response-actions?incident_id=${encodeURIComponent(incidentId)}${
          data.response_action_id ? `&action_id=${encodeURIComponent(data.response_action_id)}` : ''
        }`,
      );
    } catch {
      setRecommendError('Network error. Failed to reach the server.');
    } finally {
      setRecommending(false);
    }
  }, [apiUrl, authHeaders, incidentId, recommending, router]);

  const hasLinkedAlert = !!sourceAlertId;

  return (
    <section className="featureSection" aria-label="Incident record">
      <div className="dataCard sharedSurfaceCard" style={{ padding: '1.15rem' }}>
        <p className="sectionEyebrow" style={{ margin: 0 }}>Full investigation</p>
        <h3 style={{ margin: '0.15rem 0 0.9rem', fontSize: '1rem' }}>Case record, timeline, alerts, evidence, response actions &amp; AI investigation</h3>
        <TabStrip
          tabs={CASE_FILE_TABS.map((t) => ({ key: t.key, label: t.label }))}
          active={activeTab}
          onChange={(tab) => setActiveTab(tab as CaseFileTabKey)}
        />
        <div style={{ marginTop: '0.9rem' }}>
          {activeTab === 'overview' && (
            <IncidentCaseOverview
              summary={incidentEvidence.data?.case_summary ?? null}
              load={incidentEvidence.load}
              responseActions={responseActions}
              responseLoad={responseLoad}
              incidentId={incidentId}
              layout="wide"
            />
          )}
          {activeTab === 'timeline' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
              <IncidentForensicTimeline
                events={forensicTimeline?.events ?? []}
                load={timelineLoad}
                partial={forensicTimeline?.partial}
                unreadable={forensicTimeline?.unreadable}
                undatedEvents={forensicTimeline?.undated_events}
              />
              <TimelineTab timeline={timeline} />
            </div>
          )}
          {activeTab === 'alerts' && (
            <AlertsTab
              linkedAlert={linkedAlert}
              hasLinkedAlert={hasLinkedAlert}
              workspaceEvidenceSource={workspaceEvidenceSource}
            />
          )}
          {activeTab === 'evidence' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
              <IncidentEvidenceTab
                data={incidentEvidence.data}
                load={incidentEvidence.load}
                onRetry={incidentEvidence.refresh}
              />
              <EvidenceTab evidence={evidence} workspaceEvidenceSource={workspaceEvidenceSource} />
            </div>
          )}
          {activeTab === 'response-actions' && (
            <ResponseActionsTab
              actions={responseActions}
              incidentId={incidentId}
              onRecommend={handleRecommend}
              recommending={recommending}
              recommendError={recommendError}
            />
          )}
          {/* The canonical seven-stage model and the lifecycle coverage it implies.
              Reads the dedicated /workflow endpoint, and only when its tab is
              selected — so the page still does not fetch the full investigation
              payload twice on load. */}
          {activeTab === 'workflow' && (
            <IncidentWorkflowTab
              incidentId={incidentId}
              summary={incidentEvidence.data?.case_summary ?? null}
              summaryLoad={incidentEvidence.load}
              responseActions={responseActions}
              responseLoad={responseLoad}
            />
          )}
          {/* Evidence-grounded AI investigation for this incident. Mounted only when its
              tab is selected, so its own /ai-triage lifecycle is not started on page
              load, and mounted only HERE — the forensic hero renders the deterministic
              analysis, never this triage flow, so nothing is double-fetched. It fails
              closed to a disabled/unavailable message when triage is off. */}
          {activeTab === 'ai-investigation' && <AiInvestigationPanel incidentId={incidentId} />}
        </div>
      </div>
    </section>
  );
}
