'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TableShell,
  type PillVariant,
} from './components/ui-primitives';
import {
  investigationNextAction,
  investigationSummaryState,
  isAwaitingResponseStatus,
  linkedDetectionRef,
  summaryStateVariant,
  type ForensicInvestigation,
  type NextAction,
} from './forensic-investigation-presentation';
import {
  buildIntegritySummary,
  loadStateFor,
  parseIncidentQueueCounts,
  summarizeResponseState,
  summarizeWorkflowProgress,
  type ForensicLoadState,
  type IntegritySummaryRow,
  type IncidentQueueCounts,
  type WorkflowProgress,
} from './incident-forensics-presentation';
import { usePilotAuth } from './pilot-auth-context';
import { useRuntimeSummary } from './runtime-summary-context';
import { useIncidentEvidence, type IncidentEvidenceResponse } from './use-incident-forensics';
// Canonical Detected By resolver + label map (single source of truth, mirrors the
// backend classifier) so an incident's linked wallet-transfer alert names the same
// detection path — QuickNode Stream / Stable RPC Polling / Realtime — as the alert and
// telemetry views. Never re-invents the mapping.
import {
  formatDetectedBy,
  walletTransferDetectedBy,
  type DetectedByRow,
} from './(product)/monitoring-sources/[targetId]/telemetry/detected-by';

// Same-origin proxy base. The Incidents page MUST NOT call the backend directly: the browser
// only sees NEXT_PUBLIC_API_URL (often unset in production), so a direct fetch never reaches the
// backend and the list silently renders empty ("No incidents yet") even when escalated incidents
// exist — which is exactly why /incidents disagreed with the Alerts "Linked Incidents" count.
// Every backend call below goes through the Next.js /api/* proxy, which resolves the backend URL
// server-side — the same transport the Alerts list and telemetry/runtime-status already use.
const API_PROXY_BASE = '/api';

/* ── Types ──────────────────────────────────────────────────────── */

type IncidentRow = {
  id: string;
  title?: string | null;
  severity?: string | null;
  workflow_status?: string | null;
  status?: string | null;
  owner_user_id?: string | null;
  assignee_user_id?: string | null;
  source_alert_id?: string | null;
  linked_alert_ids?: string[] | null;
  linked_detection_id?: string | null;
  linked_evidence_count?: number | null;
  linked_action_id?: string | null;
  target_id?: string | null;
  asset_label?: string | null;
  description?: string | null;
  impact?: string | null;
  risk_score?: number | null;
  normalized_risk?: string | null;
  evidence_source?: string | null;
  evidence_origin?: string | null;
  response_action_mode?: string | null;
  chain_linked_ids?: Record<string, string> | null;
  created_at?: string | null;
  resolved_at?: string | null;
  updated_at?: string | null;
};

type TimelineEntry = {
  id?: string;
  event_type?: string;
  message?: string;
  note?: string;
  actor?: string;
  system?: string;
  result?: string;
  evidence_source?: string;
  created_at?: string;
  timestamp?: string;
};

type AlertRow = {
  id: string;
  title?: string | null;
  severity?: string | null;
  status?: string | null;
  // Canonical alert→incident linkage from the backend list serializer. An alert carrying
  // either of these is ALREADY escalated, so it is never offered as a creation candidate.
  incident_id?: string | null;
  linked_incident_id?: string | null;
  detected_by?: string | null;
  payload?: {
    detection_type?: string | null;
    confidence?: string | null;
    asset_label?: string | null;
    detected_by?: string | null;
    source_type?: string | null;
  } | null;
  detector_kind?: string | null;
  evidence_source?: string | null;
  evidence_origin?: string | null;
  source?: string | null;
};

type EvidenceRow = {
  id?: string;
  type?: string;
  source?: string;
  created_at?: string;
  included_in_package?: boolean;
  tx_hash?: string;
  block_number?: string | number;
};

type ResponseActionRow = {
  id?: string;
  action_type?: string;
  type?: string;
  status?: string;
  requires_approval?: boolean;
  evidence_source?: string;
  mode?: string;
  record_type?: string;
  // Canonical operator-facing fields (same backend source as Screen 8) so the
  // Screen 7 response summary and Screen 8 never disagree.
  display_title?: string;
  lifecycle_state?: string;
  lifecycle_label?: string;
  approval_status?: string;
  simulation_status?: string;
  execution_status?: string;
  provenance?: { primary_source_label?: string };
};

// Load lifecycle for the selected incident's canonical forensic investigation payload
// (`/incidents/{id}/investigation`). 'unavailable' = the forensic layer is not enabled
// for this deployment (fail-closed); 'error' = the fetch failed. Workflow stages, the
// next action, and the linked detection are all derived from this — never re-inferred.
type InvestigationLoad = 'idle' | 'loading' | 'ready' | 'unavailable' | 'error';

/* ── Helpers ────────────────────────────────────────────────────── */

// The Response Actions list now also returns AI recommendation-review records.
// Those are immutable human-review records (never executed), surfaced on the
// Response Actions page and the AI Investigation drawer — not real response
// actions. Exclude them here so the incident drawer's "Response Initiated"
// workflow marker and action list keep their existing (executable-action) meaning.
function onlyResponseActions(rows: unknown): ResponseActionRow[] {
  const list = Array.isArray(rows) ? (rows as ResponseActionRow[]) : [];
  return list.filter((a) => a?.record_type !== 'ai_recommendation_review');
}

function severityPill(severity?: string | null): { label: string; variant: PillVariant } {
  const s = (severity ?? 'unknown').toLowerCase();
  if (s === 'critical') return { label: 'Critical', variant: 'danger' };
  if (s === 'high')     return { label: 'High',     variant: 'danger' };
  if (s === 'medium')   return { label: 'Medium',   variant: 'warning' };
  if (s === 'low')      return { label: 'Low',       variant: 'success' };
  if (s === 'info')     return { label: 'Info',      variant: 'info' };
  return { label: 'Unknown', variant: 'neutral' };
}

function incidentStatusPill(status?: string | null): { label: string; variant: PillVariant } {
  const s = (status ?? 'unknown').toLowerCase();
  if (s === 'open')               return { label: 'Open',               variant: 'danger' };
  if (s === 'investigating')      return { label: 'Investigating',      variant: 'info' };
  if (s === 'awaiting_response')  return { label: 'Awaiting Response',  variant: 'warning' };
  if (s === 'response_initiated') return { label: 'Response Initiated', variant: 'warning' };
  if (s === 'contained')          return { label: 'Awaiting Response',  variant: 'warning' };
  if (s === 'resolved')           return { label: 'Resolved',           variant: 'success' };
  if (s === 'closed')             return { label: 'Closed',             variant: 'neutral' };
  if (s === 'suppressed')         return { label: 'Suppressed',         variant: 'neutral' };
  if (s === 'reopened')           return { label: 'Reopened',           variant: 'warning' };
  return { label: 'Unknown', variant: 'neutral' };
}

// Simulator evidence must not be labeled as live_provider.
// Do not label simulator evidence as live_provider.
function evidenceSourcePill(
  rowSource?: string | null,
  workspaceSource?: string,
): { label: string; variant: PillVariant } {
  const raw = (rowSource ?? '').toLowerCase();
  if (
    raw === 'simulator' ||
    raw === 'demo' ||
    raw === 'replay' ||
    workspaceSource === 'simulator'
  ) {
    return { label: 'simulator', variant: 'info' };
  }
  if (raw === 'live' || raw === 'live_provider') {
    return { label: 'live_provider', variant: 'success' };
  }
  return { label: 'none', variant: 'neutral' };
}

function fmt(value?: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  const diff = Date.now() - parsed.getTime();
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return parsed.toLocaleDateString();
}

function fmtFull(value?: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleString();
}

function incidentAsset(incident: IncidentRow): string {
  return incident.asset_label ?? incident.target_id ?? '-';
}

function incidentStatus(incident: IncidentRow): string {
  return incident.workflow_status ?? incident.status ?? 'unknown';
}

// Statuses that make an alert ineligible for escalation. 'suppressed' mirrors the backend
// guard in escalate_alert_to_incident (a suppressed alert returns 404), so the UI never
// offers a candidate the API is guaranteed to reject.
const NON_ESCALATABLE_ALERT_STATUSES = new Set(['suppressed']);

/**
 * Newest alert that can still become an incident: not suppressed, and not already linked
 * to one. Returns null when every alert is already escalated — that is a truthful "nothing
 * to create from" state, NOT an error, and it keeps Create Incident disabled for the right
 * reason instead of unconditionally.
 *
 * Alerts arrive newest-first from GET /alerts, so the first match is the newest candidate.
 */
function firstEscalatableAlert(rows: AlertRow[]): AlertRow | null {
  for (const row of rows) {
    if (!row?.id) continue;
    if (row.incident_id || row.linked_incident_id) continue;
    if (NON_ESCALATABLE_ALERT_STATUSES.has(String(row.status ?? '').toLowerCase())) continue;
    return row;
  }
  return null;
}

/* ── Constants ──────────────────────────────────────────────────── */

const INCIDENT_TABLE_HEADERS = ['Incident ID', 'Severity', 'Title', 'Asset', 'Status', 'Created', 'Action'];

// One page of recent alerts is scanned for an escalation candidate. The previous limit=1
// probe could only answer "do alerts exist?"; it could not tell an already-escalated alert
// from an escalatable one, so it could not gate a creation control truthfully.
const ESCALATION_CANDIDATE_SCAN_LIMIT = 50;

/* ── Main panel ─────────────────────────────────────────────────── */

export default function IncidentsPanel({ initialSelectedId }: { initialSelectedId?: string } = {}) {
  const { summary, runtime, loading: runtimeLoading } = useRuntimeSummary();
  const { authHeaders } = usePilotAuth();
  const router = useRouter();
  const apiUrl = API_PROXY_BASE;

  const [incidents, setIncidents] = useState<IncidentRow[]>([]);
  const [selectedId, setSelectedId] = useState(initialSelectedId ?? '');
  const [alertsExist, setAlertsExist] = useState(false);
  // Newest alert that has not been escalated yet — the only thing the backend can turn into
  // an incident. null = nothing to escalate (Create Incident stays disabled, truthfully).
  const [escalatableAlert, setEscalatableAlert] = useState<AlertRow | null>(null);
  const [creatingIncident, setCreatingIncident] = useState(false);
  const [createIncidentError, setCreateIncidentError] = useState('');
  const [search, setSearch] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [assigneeFilter, setAssigneeFilter] = useState('');
  const [dataLoading, setDataLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [responseActions, setResponseActions] = useState<ResponseActionRow[]>([]);
  // Whether the response-actions read has SETTLED. Without it an empty array during
  // the fetch would render as "no response action recommended" — a claim the data
  // does not yet support (and a failed read would make it permanent).
  const [responseLoad, setResponseLoad] = useState<ForensicLoadState>('idle');
  const [recommending, setRecommending] = useState(false);
  const [recommendError, setRecommendError] = useState('');
  // Canonical Screen 7 investigation payload for the SELECTED incident only (one
  // request per selection — never one per table row).
  const [investigation, setInvestigation] = useState<ForensicInvestigation | null>(null);
  const [investigationLoad, setInvestigationLoad] = useState<InvestigationLoad>('idle');
  // Canonical queue counters from GET /incidents/summary. null = not available;
  // the tiles then say so instead of rendering a zero that reads as "all clear".
  const [queueCounts, setQueueCounts] = useState<IncidentQueueCounts | null>(null);
  const [queueCountsLoad, setQueueCountsLoad] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');

  const counts = runtime?.counts as Record<string, number> | undefined;
  const workspaceEvidenceSource: string = summary.evidence_source_summary ?? '';
  const telemetryOk = (counts?.telemetry_events ?? 0) > 0 || !!summary.last_telemetry_at;
  const detectionOk = (counts?.detections ?? 0) > 0 || !!(summary as any).last_detection_at;
  const activeAlerts: number =
    (counts?.active_alerts as number | undefined) ?? summary.active_alerts_count ?? 0;

  // Create Incident. There is NO standalone POST /incidents in the backend: an incident is
  // only ever created by escalating an alert (POST /alerts/{id}/escalate), which is what
  // preserves the canonical detection → alert → incident → action chain. This control runs
  // that same canonical endpoint the Alerts screen uses — it does not invent a second
  // creation path, and it does not fabricate an incident with no alert behind it.
  //
  // RBAC is unchanged and still enforced server-side (escalation requires the workspace
  // 'members.manage' permission). A 403 is surfaced verbatim rather than hidden, so a user
  // without the permission is told why instead of seeing a control that silently does
  // nothing. The endpoint is idempotent: an alert already linked to an incident returns that
  // incident with created=false, so a double click can never produce a duplicate.
  const handleCreateIncident = useCallback(async () => {
    if (!escalatableAlert?.id || creatingIncident) return;
    setCreatingIncident(true);
    setCreateIncidentError('');
    setMessage('');
    try {
      const res = await fetch(`${apiUrl}/alerts/${encodeURIComponent(escalatableAlert.id)}/escalate`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
        body: JSON.stringify({
          title: `Escalated alert: ${escalatableAlert.title ?? escalatableAlert.id}`,
          summary: escalatableAlert.title ?? 'Escalated from alert',
        }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        incident_id?: string;
        created?: boolean;
        detail?: unknown;
        message?: unknown;
      };
      if (!res.ok) {
        const detail = data.detail;
        const detailText = typeof detail === 'string'
          ? detail
          : (detail && typeof detail === 'object' && 'message' in detail
            ? String((detail as Record<string, unknown>).message)
            : '');
        setCreateIncidentError(detailText || String(data.message ?? '') || 'Unable to create the incident. Please retry.');
        return;
      }
      if (!data.incident_id) {
        setCreateIncidentError('The alert was escalated but no incident id was returned.');
        return;
      }
      // Land on the persisted incident the backend actually created/linked, so the operator
      // sees the real row (never an optimistic client-side placeholder).
      router.push(`/incidents/${encodeURIComponent(data.incident_id)}`);
    } catch {
      setCreateIncidentError('Network error. Failed to reach the server.');
    } finally {
      setCreatingIncident(false);
    }
  }, [escalatableAlert, creatingIncident, apiUrl, authHeaders, router]);

  const handleRecommend = useCallback(async () => {
    if (!selectedId || recommending) return;
    setRecommending(true);
    setRecommendError('');
    try {
      const res = await fetch(`${apiUrl}/incidents/${encodeURIComponent(selectedId)}/response-actions/recommend`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as Record<string, unknown>;
        setRecommendError(String(err.detail ?? err.message ?? 'Failed to recommend response action.'));
        return;
      }
      const data = await res.json() as { response_action_id?: string; incident_id?: string };
      // Refresh response actions for the panel
      const actionsRes = await fetch(`${apiUrl}/response/actions?incident_id=${encodeURIComponent(selectedId)}`, {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (actionsRes.ok) {
        const actionsJson = await actionsRes.json() as Record<string, unknown>;
        setResponseActions(onlyResponseActions(actionsJson.actions));
      }
      router.push(`/response-actions?incident_id=${encodeURIComponent(selectedId)}${data.response_action_id ? `&action_id=${encodeURIComponent(data.response_action_id)}` : ''}`);
    } catch {
      setRecommendError('Network error. Failed to reach the server.');
    } finally {
      setRecommending(false);
    }
  }, [selectedId, recommending, apiUrl, authHeaders, router]);

  useEffect(() => {
    if (runtimeLoading) return;
    let cancelled = false;
    setDataLoading(true);
    async function loadIncidents() {
      try {
        const params = new URLSearchParams();
        if (severityFilter) params.set('severity', severityFilter);
        if (statusFilter) params.set('status_value', statusFilter);
        if (assigneeFilter) params.set('assignee_user_id', assigneeFilter);
        const res = await fetch(`${apiUrl}/incidents?${params.toString()}`, {
          headers: authHeaders(),
          cache: 'no-store',
        });
        if (!res.ok || cancelled) {
          console.log('frontend_incidents_fetch_response_count', { ok: res.ok, status: res.status, count: 0 });
          return;
        }
        const json = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        const rows = (json.incidents ?? []) as IncidentRow[];
        console.log('frontend_incidents_fetch_response_count', {
          ok: true,
          status: res.status,
          count: rows.length,
          ids: rows.map((r) => r.id),
        });
        if (cancelled) return;
        // Deep link: when /incidents/{id} was opened directly, the target incident may sit
        // outside the current page or filter. Fetch it explicitly and merge it in so "View
        // Incident" always loads the persisted row instead of falling through to the empty state.
        let merged = rows;
        if (initialSelectedId && !rows.some((r) => r.id === initialSelectedId)) {
          const detailRes = await fetch(`${apiUrl}/incidents/${encodeURIComponent(initialSelectedId)}`, {
            headers: authHeaders(),
            cache: 'no-store',
          }).catch(() => null);
          if (detailRes && detailRes.ok) {
            const detailJson = (await detailRes.json().catch(() => ({}))) as Record<string, unknown>;
            const detail = ((detailJson.incident as IncidentRow | undefined) ?? (detailJson as IncidentRow));
            if (detail && detail.id) merged = [detail, ...rows];
          }
        }
        if (!cancelled) {
          setIncidents(merged);
          if (initialSelectedId && merged.some((r) => r.id === initialSelectedId)) {
            setSelectedId(initialSelectedId);
          } else if (!selectedId && merged.length > 0) {
            setSelectedId(merged[0].id);
          }
        }
      } finally {
        if (!cancelled) setDataLoading(false);
      }
    }
    void loadIncidents();
    return () => { cancelled = true; };
  }, [apiUrl, authHeaders, runtimeLoading, severityFilter, statusFilter, assigneeFilter, initialSelectedId]);

  // Real "do alerts exist?" signal for the empty-state copy. The runtime counter only reflects
  // *active* alerts, so a resolved/linked alert would wrongly read as zero and surface the
  // detection-stage message; this fetch keeps the copy truthful (CLAUDE.md: never claim no alert
  // when alerts exist). One linked alert is enough to know escalation is the next step.
  //
  // The same page also resolves the newest ESCALATABLE alert — one that is not suppressed and
  // does not already point at an incident — because the backend has no standalone POST
  // /incidents: an incident is only ever born from an alert via POST /alerts/{id}/escalate.
  // That candidate is what makes "Create Incident" a real, truthful control instead of a dead
  // one: enabled only when the workflow prerequisite genuinely exists, disabled with the
  // reason when it does not (CLAUDE.md: fail-closed, never claim a capability that is absent).
  useEffect(() => {
    if (runtimeLoading) return;
    let cancelled = false;
    void fetch(`${apiUrl}/alerts?limit=${ESCALATION_CANDIDATE_SCAN_LIMIT}`, { headers: authHeaders(), cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        if (cancelled) return;
        const rows = ((json as Record<string, unknown> | null)?.alerts ?? []) as AlertRow[];
        setAlertsExist(rows.length > 0);
        setEscalatableAlert(firstEscalatableAlert(rows));
      })
      .catch(() => {
        if (cancelled) return;
        setAlertsExist(false);
        setEscalatableAlert(null);
      });
    return () => { cancelled = true; };
  }, [apiUrl, authHeaders, runtimeLoading]);

  /* ── Queue counters (canonical, workspace-wide) ───────────────── */
  // The four KPI tiles are read from the backend, which counts EVERY incident in
  // the workspace using the same lifecycle definition the Dashboard's Open
  // Incidents card uses. They are deliberately NOT derived from `incidents`: that
  // array is one capped page, already narrowed by the severity/status filters, so
  // counting it would report a subset as the total and would disagree with Screen 2.
  useEffect(() => {
    if (runtimeLoading) return;
    let cancelled = false;
    const controller = new AbortController();
    setQueueCountsLoad('loading');
    void fetch(`${apiUrl}/incidents/summary`, {
      headers: authHeaders(),
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) { setQueueCounts(null); setQueueCountsLoad('error'); return; }
        const parsed = parseIncidentQueueCounts(await res.json().catch(() => null));
        if (cancelled) return;
        // A payload missing a counter yields null, and the tile says "unavailable".
        // A zero is a claim ("nothing is open") and is never rendered on absent data.
        setQueueCounts(parsed);
        setQueueCountsLoad(parsed ? 'ready' : 'error');
      })
      .catch(() => { if (!cancelled) { setQueueCounts(null); setQueueCountsLoad('error'); } });
    return () => { cancelled = true; controller.abort(); };
  }, [apiUrl, authHeaders, runtimeLoading]);

  const filteredIncidents = useMemo(() => {
    return incidents.filter((inc) => {
      const q = search.toLowerCase();
      return (
        !q ||
        (inc.title ?? '').toLowerCase().includes(q) ||
        (inc.id ?? '').toLowerCase().includes(q) ||
        incidentAsset(inc).toLowerCase().includes(q)
      );
    });
  }, [incidents, search]);

  const selectedIncident = useMemo(
    () => filteredIncidents.find((i) => i.id === selectedId) ?? null,
    [filteredIncidents, selectedId],
  );

  /* ── Case File data loading ─────────────────────────────────────
     The Case File is a SUMMARY: it reads the response record, the canonical
     investigation and the incident's forensic evidence record — the three
     sources its integrity summary and progress line are folded from. The
     detailed record (forensic timeline, artifact directory, linked alert,
     per-action response table, AI triage) belongs to Open Full Investigation,
     which fetches it there; the list page no longer pays for it per selection. */
  useEffect(() => {
    if (!selectedId) { setResponseActions([]); setResponseLoad('idle'); return; }
    let cancelled = false;
    setResponseLoad('loading');
    void fetch(`${apiUrl}/response/actions?incident_id=${selectedId}`, {
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
  }, [apiUrl, authHeaders, selectedId]);

  // Canonical forensic investigation for the selected incident — the SAME source the
  // full /incidents/[incidentId] page uses. Fetched once per selection (never per table
  // row). A `cancelled` guard + AbortController ensures a response for a previously
  // selected/closed incident can never overwrite the current drawer's state.
  useEffect(() => {
    if (!selectedId) {
      setInvestigation(null);
      setInvestigationLoad('idle');
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setInvestigationLoad('loading');
    setInvestigation(null);
    void fetch(`${apiUrl}/incidents/${encodeURIComponent(selectedId)}/investigation`, {
      headers: authHeaders(),
      cache: 'no-store',
      signal: controller.signal,
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => {
        if (cancelled) return;
        const payload = json as ForensicInvestigation | null;
        if (!payload) { setInvestigationLoad('error'); return; }
        setInvestigation(payload);
        setInvestigationLoad(
          payload.status === 'unavailable' || payload.schema_ready === false ? 'unavailable' : 'ready',
        );
      })
      .catch(() => { if (!cancelled) setInvestigationLoad('error'); });
    return () => { cancelled = true; controller.abort(); };
  }, [apiUrl, authHeaders, selectedId]);

  // The selected incident's forensic evidence record — ONE fetch behind the Case
  // File's integrity summary. It is the SAME payload the full investigation reads,
  // so the summary and the detailed record can never describe different evidence.
  const incidentEvidence = useIncidentEvidence(selectedId);

  /* ── Empty state ─────────────────────────────────────────────── */
  type Blocker = { title: string; body: string; ctaHref?: string; ctaLabel?: string };

  // Truthful "alerts exist" signal: a real /alerts probe OR the runtime active-alert counter.
  const anyAlerts = alertsExist || activeAlerts > 0;

  function getBlocker(): Blocker | null {
    if (incidents.length > 0) return null;
    // Alerts exist → the next workflow step is opening an incident. This MUST take precedence
    // over the telemetry/detection-stage copy: an alert proves telemetry and detection already
    // happened, so we never say "no detection" or "no alert has been opened yet" when alerts
    // already exist (CLAUDE.md truthfulness: no data must not be shown as a missing earlier stage).
    if (anyAlerts) {
      return {
        title: 'No incidents opened',
        body: 'Alerts exist, but no incident has been opened yet.',
        ctaHref: '/alerts',
        ctaLabel: 'Open Incident',
      };
    }
    if (!telemetryOk) {
      return {
        title: 'No incidents yet',
        body: 'No incidents can be opened because no telemetry has been received.',
        ctaHref: '/threat',
        ctaLabel: 'View Threat Monitoring',
      };
    }
    if (!detectionOk) {
      return {
        title: 'No incidents yet',
        body: 'Telemetry has been received, but no detection has been generated yet.',
      };
    }
    return {
      title: 'No incidents yet',
      body: 'Detections exist, but no alert has been opened yet.',
      ctaHref: '/alerts',
      ctaLabel: 'Open Alert',
    };
  }

  // Bug-visible guard: if the API returned incidents but none render (with no active search),
  // the list is silently dropping linked incidents — surface it loudly in dev instead of
  // showing a misleading empty state. Never fires in production builds.
  useEffect(() => {
    if (process.env.NODE_ENV === 'production') return;
    if (!dataLoading && incidents.length > 0 && filteredIncidents.length === 0 && !search) {
      console.error('incidents_list_bug_filtered_out', {
        api_count: incidents.length,
        rendered_count: filteredIncidents.length,
        ids: incidents.map((i) => i.id),
      });
    }
  }, [dataLoading, incidents, filteredIncidents, search]);

  const blocker = dataLoading ? null : getBlocker();

  return (
    <section className="featureSection">
      {/* ── Metric row ──────────────────────────────────────────── */}
      {/* Canonical, workspace-wide counters from the backend. Each tile states its
          own definition, so the number and the label can never drift apart. */}
      <div className="incidentsQueueCounters">
        <QueueCounterTile label="Open Incidents" value={queueCounts?.open_incidents}
          load={queueCountsLoad} meta="Not resolved or closed" />
        <QueueCounterTile label="Critical Incidents" value={queueCounts?.critical_incidents}
          load={queueCountsLoad} meta="Critical severity, still open" />
        <QueueCounterTile label="In Investigation" value={queueCounts?.in_investigation}
          load={queueCountsLoad} meta="Status: investigating" />
        <QueueCounterTile label="Awaiting Response" value={queueCounts?.awaiting_response}
          load={queueCountsLoad} meta="Awaiting a response decision" />
      </div>

      {/* ── Filter bar ──────────────────────────────────────────── */}
      <div className="buttonRow" style={{ marginBottom: '1rem', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
        <input placeholder="Search incidents..." value={search} onChange={(e) => setSearch(e.target.value)}
          style={{ flex: '1 1 200px', minWidth: '180px' }} aria-label="Search incidents" />
        <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)} aria-label="Severity filter">
          <option value="">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="info">Info</option>
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="Status filter">
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="investigating">Investigating</option>
          <option value="contained">Awaiting Response</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
          <option value="suppressed">Suppressed</option>
        </select>
        <input placeholder="Assignee user ID..." value={assigneeFilter} onChange={(e) => setAssigneeFilter(e.target.value)}
          style={{ width: '180px' }} aria-label="Assignee filter" />
        {/* Create Incident runs the canonical alert-escalation path (POST /alerts/{id}/escalate).
            It is disabled ONLY when the workflow prerequisite is genuinely absent — no alert is
            left to escalate — never unconditionally. RBAC still lives on the backend; a denied
            request surfaces its reason below instead of being pre-empted here, because workspace
            role permissions are DB-overridable and cannot be inferred client-side. */}
        <button type="button" className="btn btn-primary" data-testid="create-incident"
          disabled={creatingIncident || !escalatableAlert}
          style={{ opacity: creatingIncident || !escalatableAlert ? 0.45 : 1 }}
          title={escalatableAlert
            ? `Open an incident from the latest un-escalated alert (${escalatableAlert.title ?? escalatableAlert.id})`
            : 'No alert is available to escalate. Incidents are created from alerts — open an alert first.'}
          onClick={() => void handleCreateIncident()}>
          {creatingIncident ? 'Creating…' : 'Create Incident'}
        </button>
      </div>

      {/* The backend's own refusal (403 PERMISSION_DENIED, suppressed alert, …) is shown
          verbatim — the control never fails silently and never claims a success it did not get. */}
      {createIncidentError ? (
        <p className="statusLine" role="alert" data-testid="create-incident-error"
          style={{ marginBottom: '0.75rem' }}>{createIncidentError}</p>
      ) : null}

      {/* ── Content ─────────────────────────────────────────────── */}
      {/* The queue owns the main content area; the Case File is a compact preview
          beside it (see styles.css · Screen 7 · Incident queue + Case File panel).
          The column width and its responsive stacking live in CSS rather than an
          inline pixel value, so the panel narrows and stacks with the layout. */}
      {blocker ? (
        <EmptyStateBlocker title={blocker.title} body={blocker.body} ctaHref={blocker.ctaHref} ctaLabel={blocker.ctaLabel} />
      ) : (
        <div className={`incidentsQueueLayout${selectedIncident ? ' incidentsQueueLayout-withCase' : ''}`}>
          {/* ── Incidents table ────────────────────────────────── */}
          <div>
            {filteredIncidents.length === 0 && !dataLoading ? (
              <div className="emptyStatePanel sharedEmptyStateBlocker">
                <h4>No incidents match current filters</h4>
                <p className="muted">Adjust the filters above to see more results.</p>
              </div>
            ) : (
              <TableShell headers={INCIDENT_TABLE_HEADERS} compact>
                {filteredIncidents.map((incident) => {
                  const sev = severityPill(incident.severity);
                  const st  = incidentStatusPill(incidentStatus(incident));
                  const isSelected = incident.id === selectedId;
                  return (
                    <tr key={incident.id} onClick={() => setSelectedId(incident.id)}
                      style={{ cursor: 'pointer', background: isSelected ? 'rgba(59,130,246,0.08)' : undefined }}>
                      <td style={{ fontFamily: 'monospace', fontSize: '0.75rem', whiteSpace: 'nowrap',
                        maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis' }} title={incident.id}>
                        {incident.id}
                      </td>
                      <td><StatusPill label={sev.label} variant={sev.variant} /></td>
                      <td style={{ maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {incident.title ?? '-'}
                      </td>
                      <td style={{ fontSize: '0.8rem' }}>{incidentAsset(incident)}</td>
                      <td><StatusPill label={st.label} variant={st.variant} /></td>
                      <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>{fmt(incident.created_at)}</td>
                      <td>
                        {/* Full-page navigation is a SEPARATE action from drawer selection. This routes to
                            the canonical incident detail page (Digital Forensics Investigator) using the
                            incident's canonical UUID (incident.id) — never the displayed reference — and is a
                            real accessible <Link>, so a single click (or keyboard Enter) always navigates,
                            even when this row is already the selected/open drawer. stopPropagation keeps the
                            row's onClick (drawer select) from also firing; preventDefault is NEVER called, so
                            the link is not swallowed and navigation is never blocked. */}
                        <Link href={`/incidents/${encodeURIComponent(incident.id)}`} prefetch={false}
                          className="btn btn-secondary"
                          style={{ fontSize: '0.73rem', padding: '0.2rem 0.5rem' }}
                          onClick={(e) => { e.stopPropagation(); }}>
                          View Incident
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </TableShell>
            )}
          </div>

          {/* ── Detail panel ────────────────────────────────────── */}
          {selectedIncident && (
            <IncidentDetailPanel
              incident={selectedIncident}
              incidentEvidence={incidentEvidence.data}
              incidentEvidenceLoad={incidentEvidence.load}
              responseActions={responseActions}
              responseLoad={responseLoad}
              investigation={investigation}
              investigationLoad={investigationLoad}
              workspaceEvidenceSource={workspaceEvidenceSource}
              onRecommend={handleRecommend}
              recommending={recommending}
              recommendError={recommendError}
            />
          )}
        </div>
      )}

      {message ? (
        <p className="statusLine" style={{ marginTop: '0.5rem' }}>{message}</p>
      ) : null}
    </section>
  );
}

/* ── Case File panel (compact case preview) ─────────────────────────
   The narrow right-hand panel answers exactly two questions — "what is this
   case?" and "what state is it in?" — and hands the forensic record to Open
   Full Investigation, which has the width to render it.

   Nothing here re-reads the backend: the integrity summary is folded from the
   SAME canonical case record, response actions and investigation payload the
   full investigation renders, so the summary and the detailed record can never
   disagree. Every row fails closed — an absent, unreadable or still-loading
   record is stated as such and carries no state badge, so absence can never be
   read as a verdict (CLAUDE.md: no data must not be shown as safe). */
function IncidentDetailPanel({ incident, incidentEvidence, incidentEvidenceLoad,
  responseActions, responseLoad, investigation, investigationLoad,
  workspaceEvidenceSource, onRecommend, recommending, recommendError }: {
  incident: IncidentRow;
  incidentEvidence: IncidentEvidenceResponse | null;
  incidentEvidenceLoad: ForensicLoadState;
  responseActions: ResponseActionRow[];
  responseLoad: ForensicLoadState;
  investigation: ForensicInvestigation | null;
  investigationLoad: InvestigationLoad;
  workspaceEvidenceSource: string;
  onRecommend: () => void;
  recommending: boolean;
  recommendError: string;
}) {
  const sev = severityPill(incident.severity);
  const st  = incidentStatusPill(incidentStatus(incident));
  const evSrc = evidenceSourcePill(incident.evidence_source ?? incident.evidence_origin, workspaceEvidenceSource);
  const hasLinkedAlert = !!incident.source_alert_id;

  // Everything below is derived from the SAME canonical investigation payload the full
  // /incidents/[incidentId] page renders — the drawer keeps no second definition of state.
  const analysis = investigation?.analysis ?? null;
  const workflowStages = analysis?.workflow_stages ?? [];
  const awaitingResponse = isAwaitingResponseStatus(incidentStatus(incident));
  const nextAction: NextAction = investigationNextAction(investigation, { awaitingResponse });
  // Linked Detection: the incident row's own detection id when the source alert carries
  // one, else the canonical originating detection/rule reference from the investigation
  // snapshot. Never "none" while the full page shows an originating detection rule.
  const rowDetectionId = incident.chain_linked_ids?.detection_id ?? incident.linked_detection_id ?? null;
  const detectionRef = linkedDetectionRef(investigation);

  // Canonical case facts from the incident's own forensic evidence record.
  const caseSummary = incidentEvidence?.case_summary ?? null;
  // Response state folded from Screen 8's own action records — never restated
  // from a Screen 7 copy of them.
  const responseState = summarizeResponseState(responseActions);
  // The six operational-integrity answers, one compact line each. Reason-code
  // lists, reconciliation detail and artifact metadata stay in the full record.
  const integrityRows = buildIntegritySummary({
    summary: caseSummary,
    summaryLoad: incidentEvidenceLoad,
    response: responseState,
    responseLoad,
  });
  const progress = summarizeWorkflowProgress(workflowStages);
  const summaryState = analysis
    ? investigationSummaryState(analysis.status, investigation?.ai_triage?.status)
    : null;

  return (
    <aside className="dataCard sharedSurfaceCard caseFilePanel" aria-label="Incident detail">
      {/* ── Case identity + metadata ───────────────────────────── */}
      <div className="caseFileHeader">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem' }}>
          <p className="caseFileEyebrow">Case File</p>
          <StatusPill label={sev.label} variant={sev.variant} />
        </div>
        <h4 className="caseFileTitle">{incident.title ?? 'Untitled Incident'}</h4>
        <div className="caseFileMeta">
          <CaseFileField label="Incident ID" wide value={<span className="caseFileMonoValue">{incident.id}</span>} />
          <CaseFileField label="Status" value={<StatusPill label={st.label} variant={st.variant} />} />
          <CaseFileField label="Evidence Source" value={<StatusPill label={evSrc.label} variant={evSrc.variant} />} />
          <CaseFileField label="Created" value={fmtFull(incident.created_at)} />
          <CaseFileField label="Updated" value={
            incident.updated_at ? fmtFull(incident.updated_at) : <span className="muted">Not recorded</span>
          } />
          <CaseFileField label="Asset" value={incidentAsset(incident)} />
          <CaseFileField label="Assigned" value={incident.owner_user_id ?? incident.assignee_user_id ?? 'Unassigned'} />
          <CaseFileField label="Linked Alert" value={
            hasLinkedAlert
              ? <Link href="/alerts" prefetch={false} className="caseFileMonoValue" style={{ color: 'var(--text-accent)' }}>{incident.source_alert_id}</Link>
              : <span className="muted">Linked alert unavailable</span>
          } />
          <CaseFileField label="Linked Detection" value={
            rowDetectionId
              ? <span className="caseFileMonoValue">{rowDetectionId}</span>
              : detectionRef
                ? <span className="caseFileMonoValue" title={detectionRef.reference}>{detectionRef.title ?? detectionRef.reference}</span>
                : investigationLoad === 'loading'
                  ? <span className="muted">Loading…</span>
                  : <span className="muted">No linked detection</span>
          } />
          {/* The canonical correlation id the whole workflow is stamped with — the
              same value Screens 3, 5, 8, 9 and 11 carry for this case. Screen 7
              displays it; it never mints one. */}
          <CaseFileField label="Canonical Event" wide value={
            caseSummary?.event_id
              ? <span className="caseFileMonoValue" title={caseSummary.event_id}>{caseSummary.event_id}</span>
              : <ForensicFieldFallback load={incidentEvidenceLoad} absent="No canonical event linked" />
          } />
        </div>
        {/* The primary action of the whole panel. The forensic record — reason
            codes, reconciliation detail, the artifact directory, the lifecycle
            chronology, the response authorization trail and the AI investigation
            — is rendered there, at the width it needs. An accessible <Link>, so
            keyboard activation and browser back navigation both work; it routes
            on the incident's canonical UUID, never the displayed reference. */}
        <Link
          href={`/incidents/${encodeURIComponent(incident.id)}`}
          prefetch={false}
          className="btn btn-primary caseFileCta"
        >
          Open Full Investigation
        </Link>
      </div>

      <div className="caseFileBody">
        {/* ── Integrity summary ─────────────────────────────────── */}
        <section className="caseFileSection" aria-label="Integrity summary">
          <p className="caseFileSectionTitle">Integrity Summary</p>
          <div className="caseFileIntegrity">
            {integrityRows.map((row) => <IntegritySummaryLine key={row.key} row={row} />)}
          </div>
          {/* Screen 8 owns the response. Screen 7 reports where it stands and hands
              over: the approval workflow when an action exists, the (approval-routed,
              non-executing) recommendation when none does. */}
          {responseLoad === 'ready' && responseState.total > 0 ? (
            <Link
              href={`/response-actions?incident_id=${encodeURIComponent(incident.id)}`}
              prefetch={false}
              className="btn btn-secondary"
              style={{ fontSize: '0.75rem', padding: '0.2rem 0.55rem', alignSelf: 'flex-start' }}
            >
              Open in Response Actions
            </Link>
          ) : responseLoad === 'empty' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', alignItems: 'flex-start' }}>
              {recommendError ? (
                <p style={{ fontSize: '0.6875rem', color: 'var(--danger-fg)', margin: 0 }} role="alert">{recommendError}</p>
              ) : null}
              <button
                type="button"
                className="btn btn-secondary"
                style={{ fontSize: '0.75rem', padding: '0.2rem 0.55rem' }}
                onClick={onRecommend}
                disabled={recommending}
              >
                {recommending ? 'Recommending…' : 'Recommend Response'}
              </button>
            </div>
          ) : null}
        </section>

        {/* ── Investigation progress (compact) ──────────────────── */}
        <section className="caseFileSection" aria-label="Investigation progress">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
            <p className="caseFileSectionTitle">Investigation</p>
            {summaryState
              ? <StatusPill label={summaryState} variant={summaryStateVariant(summaryState)} />
              : null}
          </div>
          <CompactWorkflowProgress progress={progress} load={investigationLoad} />
          <p className="caseFileIntegrityDetail" style={{ margin: 0 }}>
            Next: {nextAction.label}
          </p>
        </section>
      </div>
    </aside>
  );
}

/* ── One integrity summary line ──────────────────────────────────── */
// Heading, one primary value, at most one short secondary line and one machine
// identifier. A row with no record renders muted with NO badge — a coloured pill
// is reserved for a state the backend actually asserted.
function IntegritySummaryLine({ row }: { row: IntegritySummaryRow }) {
  return (
    <div className="caseFileIntegrityRow">
      <span className="caseFileIntegrityLabel">{row.label}</span>
      <div className="caseFileIntegrityBody">
        <div className="caseFileIntegrityHead">
          {/* An empty value means the badge already states the fact verbatim. */}
          {row.value ? (
            <span className={row.recorded ? 'caseFileIntegrityValue' : 'caseFileIntegrityValue caseFileIntegrityValue-absent'}>
              {row.value}
            </span>
          ) : null}
          {row.badge ? <StatusPill label={row.badge.label} variant={row.badge.variant} /> : null}
        </div>
        {row.detail ? <span className="caseFileIntegrityDetail">{row.detail}</span> : null}
        {row.code ? <span className="caseFileIntegrityCode">{row.code}</span> : null}
      </div>
    </div>
  );
}

/* ── Compact investigation progress ──────────────────────────────── */
// The persisted workflow stages, counted. The full seven-stage checklist stays on
// the full investigation workspace, where it is not pushed below the fold. Fails
// closed: a loading, unavailable or unreadable investigation never renders a bar
// that implies progress.
function CompactWorkflowProgress({ progress, load }: {
  progress: WorkflowProgress | null; load: InvestigationLoad;
}) {
  if (load === 'loading' || load === 'idle') {
    return <p className="caseFileIntegrityDetail" style={{ margin: 0 }}>Loading investigation workflow…</p>;
  }
  if (load === 'unavailable') {
    return <p className="caseFileIntegrityDetail" style={{ margin: 0 }}>Investigation workflow is not available for this deployment yet.</p>;
  }
  if (load === 'error') {
    return <p className="caseFileIntegrityDetail" style={{ margin: 0 }}>Unable to load the investigation workflow.</p>;
  }
  if (!progress) {
    return <p className="caseFileIntegrityDetail" style={{ margin: 0 }}>No workflow stages recorded yet.</p>;
  }
  return (
    <>
      <div className="caseFileProgressHead">
        <span className="caseFileProgressCount">{progress.completed} / {progress.total} complete</span>
        {progress.failed > 0 ? <StatusPill label={`${progress.failed} failed`} variant="danger" /> : null}
      </div>
      <div
        className="caseFileProgressTrack"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={progress.completed}
        aria-label="Investigation stages completed"
      >
        <div
          className={progress.failed > 0 ? 'caseFileProgressFill caseFileProgressFill-failed' : 'caseFileProgressFill'}
          style={{ width: `${progress.percent}%` }}
        />
      </div>
      {progress.current
        ? <span className="caseFileProgressCurrent">Current: {progress.current}</span>
        : null}
    </>
  );
}

/* ── Queue counter tile ──────────────────────────────────────────── */
// A KPI tile that fails closed. While the canonical count is loading, or when the
// backend could not supply it, the tile shows "—" and says why — it NEVER renders a
// zero, because a zero on this row is a claim ("nothing is open") that missing data
// does not support (CLAUDE.md: no data must not be shown as safe).
function QueueCounterTile({ label, value, load, meta }: {
  label: string; value: number | undefined; load: 'idle' | 'loading' | 'ready' | 'error'; meta: string;
}) {
  const known = load === 'ready' && typeof value === 'number';
  return (
    <MetricTile
      label={label}
      value={known ? value : '—'}
      meta={known ? meta : load === 'error' ? 'Count unavailable' : 'Loading…'}
    />
  );
}

/* ── Forensic field fallback ─────────────────────────────────────── */
// A case-file field whose record has not (or could not) be read. It distinguishes
// "still loading", "we could not read it", and "there genuinely is none" — three
// different facts that must never collapse into one reassuring blank.
function ForensicFieldFallback({ load, absent }: { load: ForensicLoadState; absent: string }) {
  const text =
    load === 'idle' || load === 'loading' ? 'Loading…'
      : load === 'unauthorized' ? 'Not permitted in this workspace'
      : load === 'not_found' ? 'Incident not found'
      : load === 'error' ? 'Unavailable'
      : absent;
  return <span className="muted" style={{ fontSize: '0.78rem' }}>{text}</span>;
}

/* ── Case File metadata field ────────────────────────────────────── */
// One labelled case fact. `wide` spans both metadata columns, for values that
// must wrap rather than truncate (identifiers an auditor copies).
function CaseFileField({ label, value, wide = false }: { label: string; value: ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? 'caseFileMetaField caseFileMetaField-wide' : 'caseFileMetaField'}>
      <p className="caseFileMetaLabel">{label}</p>
      <div className="caseFileMetaValue">{value}</div>
    </div>
  );
}

/* ── Timeline tab ────────────────────────────────────────────────── */
const TIMELINE_HEADERS = ['Time', 'Event', 'Actor / System', 'Result', 'Evidence Source'];

function TimelineTab({ timeline }: { timeline: TimelineEntry[] }) {
  if (timeline.length === 0) {
    return <p className="muted" style={{ fontSize: '0.85rem' }}>No timeline events yet.</p>;
  }
  return (
    <TableShell headers={TIMELINE_HEADERS} compact>
      {timeline.map((entry, i) => (
        <tr key={entry.id ?? i}>
          <td style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{fmt(entry.created_at ?? entry.timestamp)}</td>
          <td style={{ fontSize: '0.8rem' }}>{entry.event_type ?? entry.message ?? entry.note ?? 'Event'}</td>
          <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{entry.actor ?? entry.system ?? 'System'}</td>
          <td style={{ fontSize: '0.78rem' }}>{entry.result ?? '-'}</td>
          <td style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{entry.evidence_source ?? '-'}</td>
        </tr>
      ))}
    </TableShell>
  );
}

/* ── Alerts tab ──────────────────────────────────────────────────── */
const ALERTS_TAB_HEADERS = ['Alert ID', 'Severity', 'Title', 'Detection Type', 'Detected By', 'Confidence', 'Status', 'Action'];

// Fail-closed Detected By label for a linked wallet-transfer alert, resolved from the
// same canonical facts the Alerts + Telemetry views use (top-level detected_by, then
// the alert payload's detected_by / source_type). Never a fake default.
function linkedAlertDetectedBy(a: AlertRow): string {
  const rowLike: DetectedByRow = {
    detected_by: a.detected_by ?? a.payload?.detected_by ?? null,
    provider_type: null,
    evidence_source: a.evidence_source ?? null,
    payload_json: (a.payload ?? null) as Record<string, unknown> | null,
  };
  return formatDetectedBy(walletTransferDetectedBy(rowLike));
}

function AlertsTab({ linkedAlert, hasLinkedAlert, workspaceEvidenceSource }: {
  linkedAlert: AlertRow | null; hasLinkedAlert: boolean; workspaceEvidenceSource: string;
}) {
  if (!hasLinkedAlert) {
    return (
      <div className="emptyStatePanel sharedEmptyStateBlocker" style={{ padding: '0.75rem' }}>
        <h4 style={{ fontSize: '0.9rem', marginBottom: '0.35rem' }}>Linked alert unavailable</h4>
        <p className="muted" style={{ fontSize: '0.82rem', marginBottom: '0.5rem' }}>
          This incident has no linked alert. No alert link will be shown without a valid alert.
        </p>
        <Link href="/alerts" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.78rem' }}>View Alerts</Link>
      </div>
    );
  }
  if (!linkedAlert) return <p className="muted" style={{ fontSize: '0.85rem' }}>Loading linked alert…</p>;
  const sev = severityPill(linkedAlert.severity);
  const alertStatus = linkedAlert.status ?? 'unknown';
  return (
    <TableShell headers={ALERTS_TAB_HEADERS} compact>
      <tr>
        <td style={{ fontFamily: 'monospace', fontSize: '0.72rem', maxWidth: '90px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {linkedAlert.id}
        </td>
        <td><StatusPill label={sev.label} variant={sev.variant} /></td>
        <td style={{ fontSize: '0.8rem', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {linkedAlert.title ?? '-'}
        </td>
        <td style={{ fontSize: '0.78rem' }}>{linkedAlert.payload?.detection_type ?? linkedAlert.detector_kind ?? '-'}</td>
        <td style={{ fontSize: '0.78rem' }}>{linkedAlertDetectedBy(linkedAlert)}</td>
        <td style={{ fontSize: '0.78rem' }}>{linkedAlert.payload?.confidence ?? '-'}</td>
        <td><StatusPill label={alertStatus} variant="neutral" /></td>
        <td>
          {/* Screen 6 has no per-alert route, so this opens the Alerts list rather
              than pretending to deep-link to one alert. The label says where it
              actually goes — the same destination the Case File's Linked Alert uses. */}
          <Link href="/alerts" prefetch={false} className="btn btn-secondary"
            style={{ fontSize: '0.72rem', padding: '0.15rem 0.4rem' }}>
            View in Alerts
          </Link>
        </td>
      </tr>
    </TableShell>
  );
}

/* ── Evidence tab ────────────────────────────────────────────────── */
const EVIDENCE_HEADERS = ['Evidence ID', 'Type', 'Source', 'Created', 'In Package', 'Action'];

function EvidenceTab({ evidence, workspaceEvidenceSource }: { evidence: EvidenceRow[]; workspaceEvidenceSource: string }) {
  if (evidence.length === 0) {
    return (
      <div>
        <p className="muted" style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}>No evidence collected for this incident yet.</p>
        <Link href="/evidence" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.78rem' }}>View Evidence</Link>
      </div>
    );
  }
  return (
    <TableShell headers={EVIDENCE_HEADERS} compact>
      {evidence.map((ev, i) => {
        const rawSrc = (ev.source ?? '').toLowerCase();
        const isSimulator = rawSrc === 'simulator' || rawSrc === 'demo' || rawSrc === 'replay' || workspaceEvidenceSource === 'simulator';
        const srcLabel = isSimulator ? 'simulator' : rawSrc === 'live' || rawSrc === 'live_provider' ? 'live_provider' : (ev.source ?? '-');
        const srcVariant: PillVariant = isSimulator ? 'info' : rawSrc === 'live' || rawSrc === 'live_provider' ? 'success' : 'neutral';
        return (
          <tr key={ev.id ?? i}>
            <td style={{ fontFamily: 'monospace', fontSize: '0.72rem' }}>{ev.id ?? `EV-${i + 1}`}</td>
            <td style={{ fontSize: '0.78rem' }}>{ev.type ?? 'blockchain'}</td>
            <td><StatusPill label={srcLabel} variant={srcVariant} /></td>
            <td style={{ fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{fmt(ev.created_at)}</td>
            <td><StatusPill label={ev.included_in_package ? 'Yes' : 'No'} variant={ev.included_in_package ? 'success' : 'neutral'} /></td>
            <td>
              <Link href="/evidence" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.72rem', padding: '0.15rem 0.4rem' }}>
                Export Evidence
              </Link>
            </td>
          </tr>
        );
      })}
    </TableShell>
  );
}

/* ── Response Actions tab ────────────────────────────────────────── */
const RESPONSE_HEADERS = ['Action', 'Type', 'Status', 'Requires Approval', 'Evidence Source', 'Action'];

function ResponseActionsTab({ actions, incidentId, onRecommend, recommending, recommendError }: {
  actions: ResponseActionRow[];
  incidentId: string;
  onRecommend: () => void;
  recommending: boolean;
  recommendError: string;
}) {
  if (actions.length === 0) {
    return (
      <div>
        <p className="muted" style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}>No response action recommended yet.</p>
        {recommendError && (
          <p style={{ fontSize: '0.82rem', color: 'var(--danger-fg)', marginBottom: '0.5rem' }}>{recommendError}</p>
        )}
        <button
          type="button"
          className="btn btn-secondary"
          style={{ fontSize: '0.78rem' }}
          onClick={onRecommend}
          disabled={recommending}
        >
          {recommending ? 'Recommending…' : 'Recommend Response'}
        </button>
      </div>
    );
  }
  // Concise response summary (Screen 7) — counts derived from persisted action
  // state, plus a button that opens the Screen 8 Playbook Execution workspace
  // scoped to this incident. Screen 7 (investigation) and Screen 8 (mitigation)
  // stay separate routes.
  // Counts derive from the SAME canonical backend state Screen 8 uses
  // (approval_status / execution_status / lifecycle_state), so the two screens
  // can never disagree — never from display-string matching.
  const recommendedN = actions.length;
  const awaitingApprovalN = actions.filter((a) => a.approval_status === 'pending').length;
  const executedN = actions.filter((a) => a.execution_status === 'executed').length;
  const failedN = actions.filter(
    (a) => a.execution_status === 'failed' || a.lifecycle_state === 'execution_failed',
  ).length;
  return (
    <div>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '0.75rem',
          marginBottom: '0.75rem',
        }}
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }} aria-label="Response summary">
          <StatusPill label={`Recommended ${recommendedN}`} variant="info" />
          <StatusPill label={`Awaiting approval ${awaitingApprovalN}`} variant={awaitingApprovalN > 0 ? 'warning' : 'neutral'} />
          <StatusPill label={`Executed ${executedN}`} variant={executedN > 0 ? 'success' : 'neutral'} />
          <StatusPill label={`Failed ${failedN}`} variant={failedN > 0 ? 'danger' : 'neutral'} />
        </div>
        <Link
          href={`/response-actions?incident_id=${encodeURIComponent(incidentId)}`}
          prefetch={false}
          className="btn btn-primary"
          style={{ fontSize: '0.75rem', padding: '0.25rem 0.6rem' }}
        >
          Open in Response Actions
        </Link>
      </div>
    <TableShell headers={RESPONSE_HEADERS} compact>
      {actions.map((action, i) => (
        <tr key={action.id ?? i}>
          <td style={{ fontSize: '0.8rem', maxWidth: '130px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={action.display_title ?? action.action_type ?? ''}>
            {/* Canonical display_title — never the raw snake_case action_type key. */}
            {action.display_title ?? action.action_type ?? '-'}
          </td>
          <td><StatusPill label={action.mode ?? 'simulated'} variant="info" /></td>
          <td><StatusPill label={action.lifecycle_label ?? action.status ?? 'Recommended'}
            variant={action.lifecycle_state === 'executed' ? 'success' : action.lifecycle_state === 'execution_failed' ? 'danger' : action.lifecycle_state === 'awaiting_approval' ? 'warning' : 'neutral'} /></td>
          <td><StatusPill label={action.approval_status === 'pending' ? 'Yes' : action.approval_status === 'approved' ? 'Approved' : 'No'} variant={action.approval_status === 'pending' ? 'warning' : 'neutral'} /></td>
          <td style={{ fontSize: '0.75rem' }}>{action.provenance?.primary_source_label ?? action.evidence_source ?? '-'}</td>
          <td>
            <Link href={`/response-actions?incident_id=${encodeURIComponent(incidentId)}`} prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.72rem', padding: '0.15rem 0.4rem' }}>
              View Response
            </Link>
          </td>
        </tr>
      ))}
    </TableShell>
    </div>
  );
}

/* ── Re-exports for the standalone incident detail route ──────────────────
   The full /incidents/[incidentId] page reuses these Case File tab bodies
   (Timeline / Alerts / Evidence / Response Actions) WITHOUT the list shell —
   no table, KPIs, filters, pagination, Create Incident, or the Case File
   drawer. Keeping a single source for the tab content means the drawer and the
   full page can never render a different Timeline/Evidence/Response Actions. */
export {
  TimelineTab,
  AlertsTab,
  EvidenceTab,
  ResponseActionsTab,
  onlyResponseActions,
};
export type { TimelineEntry, AlertRow, EvidenceRow, ResponseActionRow };
