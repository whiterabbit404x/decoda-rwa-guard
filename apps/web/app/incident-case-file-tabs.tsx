'use client';

import { Suspense, useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { TabStrip } from './components/ui-primitives';
import { usePilotAuth } from './pilot-auth-context';
import { useRuntimeSummary } from './runtime-summary-context';
// Reuse the SAME Case File tab bodies the /incidents drawer renders — but without the
// list shell (table, KPI tiles, filters, pagination, the create-incident control, or the
// drawer itself). This is the standalone incident detail route's supplementary "case
// record", sitting beneath the Digital Forensics Investigator hero. It deliberately does
// NOT render the AI Investigation tab (the standalone triage panel) or the canonical
// workflow / corroborated evidence — the forensic hero already owns those — so the
// investigation payload is never fetched twice on this page.
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

const CASE_FILE_TABS = [
  { key: 'timeline',         label: 'Timeline' },
  { key: 'alerts',           label: 'Alerts' },
  { key: 'evidence',         label: 'Evidence' },
  { key: 'response-actions', label: 'Response Actions' },
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
    isCaseFileTab(tabParam) ? tabParam : 'timeline',
  );
  useEffect(() => {
    if (isCaseFileTab(tabParam)) setActiveTab(tabParam);
  }, [tabParam]);

  const [sourceAlertId, setSourceAlertId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [linkedAlert, setLinkedAlert] = useState<AlertRow | null>(null);
  const [evidence, setEvidence] = useState<EvidenceRow[]>([]);
  const [responseActions, setResponseActions] = useState<ResponseActionRow[]>([]);
  const [recommending, setRecommending] = useState(false);
  const [recommendError, setRecommendError] = useState('');

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

  // Timeline
  useEffect(() => {
    if (!incidentId) { setTimeline([]); return; }
    let cancelled = false;
    void fetch(`${apiUrl}/incidents/${encodeURIComponent(incidentId)}/timeline`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => { if (!cancelled) setTimeline((json as { timeline?: TimelineEntry[] } | null)?.timeline ?? []); })
      .catch(() => { if (!cancelled) setTimeline([]); });
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
    if (!incidentId) { setResponseActions([]); return; }
    let cancelled = false;
    void fetch(`${apiUrl}/response/actions?incident_id=${encodeURIComponent(incidentId)}`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => { if (!cancelled) setResponseActions(onlyResponseActions((json as { actions?: unknown } | null)?.actions)); })
      .catch(() => { if (!cancelled) setResponseActions([]); });
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
        <p className="sectionEyebrow" style={{ margin: 0 }}>Incident record</p>
        <h3 style={{ margin: '0.15rem 0 0.9rem', fontSize: '1rem' }}>Timeline, alerts, evidence &amp; response actions</h3>
        <TabStrip
          tabs={CASE_FILE_TABS.map((t) => ({ key: t.key, label: t.label }))}
          active={activeTab}
          onChange={(tab) => setActiveTab(tab as CaseFileTabKey)}
        />
        <div style={{ marginTop: '0.9rem' }}>
          {activeTab === 'timeline' && <TimelineTab timeline={timeline} />}
          {activeTab === 'alerts' && (
            <AlertsTab
              linkedAlert={linkedAlert}
              hasLinkedAlert={hasLinkedAlert}
              workspaceEvidenceSource={workspaceEvidenceSource}
            />
          )}
          {activeTab === 'evidence' && (
            <EvidenceTab evidence={evidence} workspaceEvidenceSource={workspaceEvidenceSource} />
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
        </div>
      </div>
    </section>
  );
}
