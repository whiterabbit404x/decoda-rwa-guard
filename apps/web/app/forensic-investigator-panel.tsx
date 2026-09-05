'use client';

import Link from 'next/link';
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TableShell,
} from './components/ui-primitives';
import { usePilotAuth } from './pilot-auth-context';
import {
  confidenceBandLabel,
  confidenceBandVariant,
  confidencePercent,
  coveragePercent,
  corroborationLabel,
  corroborationVariant,
  evidenceSourceLabel,
  evidenceSourceVariant,
  incidentReference,
  investigationSummaryState,
  isAiActive,
  refKindLabel,
  summaryStateVariant,
  truncateMiddle,
  verificationStateLabel,
  verificationStateVariant,
  workflowStateLabel,
  workflowStateVariant,
  type ForensicEvidenceRow,
  type ForensicInvestigation,
  type Finding,
  type RuleMatch,
  type WorkflowStage,
} from './forensic-investigation-presentation';
import { incidentOriginLabel } from './incident-forensics-presentation';

// The Incidents UI never calls the backend directly (the browser only sees
// NEXT_PUBLIC_API_URL, often unset in production). Every call goes through the
// Next.js /api/* proxy, the same transport the rest of the Incidents views use.
const API_PROXY_BASE = '/api';
const POLL_MS = 5000;

/* ── Date formatting (canonical) ────────────────────────────────── */
function fmtDateTime(value?: string | null): string {
  if (!value) return 'Unknown';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'Unknown';
  return d.toLocaleString();
}

function fmtRelative(value?: string | null): string {
  if (!value) return 'Unknown';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'Unknown';
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return d.toLocaleDateString();
}

/* ── Copyable identifier (truncated, full value copied) ─────────── */
function CopyableId({ value, label }: { value?: string | null; label: string }) {
  const [copied, setCopied] = useState(false);
  const raw = (value ?? '').trim();
  if (!raw) return <span className="muted">Not available</span>;
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(raw);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — the full value is still shown in the title */
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title={raw}
      aria-label={`Copy ${label} ${raw}`}
      style={{
        fontFamily: 'monospace', fontSize: '0.78rem', background: 'rgba(148,163,184,0.1)',
        border: '1px solid rgba(148,163,184,0.2)', borderRadius: '6px', padding: '0.15rem 0.45rem',
        color: 'var(--text-secondary)', cursor: 'pointer', maxWidth: '100%', overflow: 'hidden',
        textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}
    >
      {copied ? 'Copied ✓' : truncateMiddle(raw)}
    </button>
  );
}

/* ── Evidence reference chip ────────────────────────────────────── */
function RefChip({ refValue }: { refValue: string }) {
  return (
    <span
      title={refValue}
      style={{
        fontFamily: 'monospace', fontSize: '0.74rem', background: 'rgba(59,130,246,0.12)',
        border: '1px solid rgba(59,130,246,0.25)', borderRadius: '5px', padding: '0.1rem 0.4rem',
        color: 'var(--text-accent)', maxWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis',
        whiteSpace: 'nowrap', display: 'inline-block', verticalAlign: 'middle',
      }}
    >
      {refKindLabel(refValue)}: {truncateMiddle(refValue.split(':').slice(1).join(':'), 6, 6)}
    </span>
  );
}

function RefList({ refs }: { refs?: string[] }) {
  if (!refs || refs.length === 0) {
    return <span className="muted" style={{ fontSize: '0.78rem' }}>No linked evidence</span>;
  }
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem', marginTop: '0.25rem' }}>
      {refs.map((r) => <RefChip key={r} refValue={r} />)}
    </div>
  );
}

/* ── Card shell ─────────────────────────────────────────────────── */
function Card({ title, eyebrow, action, children, ariaLabel }: {
  title: string; eyebrow?: string; action?: ReactNode; children: ReactNode; ariaLabel?: string;
}) {
  return (
    <section className="dataCard sharedSurfaceCard" aria-label={ariaLabel ?? title}
      style={{ padding: '1.15rem', minWidth: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '0.5rem', marginBottom: '0.9rem' }}>
        <div style={{ minWidth: 0 }}>
          {eyebrow ? <p className="sectionEyebrow" style={{ margin: 0 }}>{eyebrow}</p> : null}
          <h3 style={{ margin: '0.15rem 0 0', fontSize: '1rem' }}>{title}</h3>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

/* ── Main panel ─────────────────────────────────────────────────── */
export default function ForensicInvestigatorPanel({ incidentId }: { incidentId: string }) {
  const { authHeaders } = usePilotAuth();
  const [data, setData] = useState<ForensicInvestigation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notFound, setNotFound] = useState(false);
  const [authError, setAuthError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reporting, setReporting] = useState(false);
  const [reportError, setReportError] = useState('');
  const [reportReady, setReportReady] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/investigation`, {
        headers: authHeaders(), cache: 'no-store',
      });
      if (res.status === 404) { setNotFound(true); return; }
      if (res.status === 401 || res.status === 403) { setAuthError(true); return; }
      if (!res.ok) { setError('Unable to load the forensic investigation.'); return; }
      const json = (await res.json()) as ForensicInvestigation;
      setData(json);
      setError('');
      setNotFound(false);
      setAuthError(false);
    } catch {
      setError('Network error. Failed to reach the server.');
    } finally {
      setLoading(false);
    }
  }, [incidentId, authHeaders]);

  useEffect(() => { void load(); }, [load]);

  // Bounded polling: only while the AI narrative hand-off is queued/running. The
  // deterministic analysis is already terminal on first load. Cleaned up on unmount.
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (data && isAiActive(data.ai_triage?.status)) {
      pollRef.current = setInterval(() => void load(), POLL_MS);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [data, load]);

  const rerun = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/investigations`, {
        method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, cache: 'no-store',
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        setError(String(body.detail ?? 'Unable to re-run the investigation.'));
      }
      await load();
    } finally {
      setBusy(false);
    }
  }, [incidentId, authHeaders, load]);

  const generateReport = useCallback(async () => {
    setReporting(true);
    setReportError('');
    setReportReady(false);
    try {
      const res = await fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/reports`, {
        method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, cache: 'no-store',
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        setReportError(String(body.detail ?? 'Report generation failed.'));
        return;
      }
      setReportReady(true);
      await load();
    } catch {
      setReportError('Network error while generating the report.');
    } finally {
      setReporting(false);
    }
  }, [incidentId, authHeaders, load]);

  /* ── Terminal UI states ───────────────────────────────────────── */
  if (loading) return <LoadingSkeleton />;
  if (authError) {
    return (
      <EmptyStateBlocker
        title="Not authorized"
        body="You do not have permission to view this incident's investigation in the current workspace."
        ctaHref="/incidents" ctaLabel="Back to incidents"
      />
    );
  }
  if (notFound) {
    return (
      <EmptyStateBlocker
        title="No incident found"
        body="This incident could not be found in the current workspace. It may have been resolved, or belongs to another workspace."
        ctaHref="/incidents" ctaLabel="Back to incidents"
      />
    );
  }
  if (error && !data) {
    return (
      <EmptyStateBlocker title="Investigation unavailable" body={error} ctaOnClick={() => { setLoading(true); void load(); }} ctaLabel="Retry" />
    );
  }
  if (data && (data.status === 'unavailable' || data.schema_ready === false)) {
    return (
      <div className="dataCard sharedSurfaceCard" role="alert" style={{ padding: '1rem' }}>
        <p className="sectionEyebrow" style={{ margin: 0 }}>Digital Forensics Investigator</p>
        <h3 style={{ margin: '0.25rem 0' }}>Forensic investigation unavailable</h3>
        <p className="muted" style={{ fontSize: '0.85rem' }}>
          {data.message ?? 'The forensic investigation layer is not available for this deployment yet.'}
        </p>
        <button type="button" className="btn btn-secondary" onClick={() => void load()}>Retry</button>
      </div>
    );
  }
  if (!data || !data.analysis) return <LoadingSkeleton />;

  const analysis = data.analysis;
  const incident = data.incident ?? {};
  const aiStatus = data.ai_triage?.status;
  const summaryState = investigationSummaryState(analysis.status, aiStatus);

  return (
    <div className="forensicInvestigator" aria-label="Digital Forensics Investigator" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {/* Mandatory generated-content + non-execution disclaimer. */}
      <p className="statusLine" style={{ margin: 0, fontSize: '0.82rem' }}>
        <strong>Evidence-grounded forensic analysis.</strong> Deterministic facts are derived from an immutable
        evidence snapshot; the AI narrative is labelled and verify-before-action. No fund-moving, contract-changing,
        pause, or key-rotation action is executed here — response steps are recommendations routed to approval.
      </p>

      <HeaderCard investigation={data} />

      {error ? <p role="alert" className="statusLine statusLine-warning" style={{ margin: 0 }}>{error}</p> : null}

      {/* Readable two-column investigation layout (see styles.css · Screen 7).
          Standard desktop (1280–1439px): the main investigation content sits at
          ~2fr beside the agent panel at ~1fr; inside the main column the AI
          Summary and Workflow are side by side with Evidence at full width
          below. Large widths cap at three practical columns; tablet stacks the
          agent below; mobile is a single column with no page-level overflow. */}
      <div className="forensicLayout">
        <div className="forensicMain">
          <div className="forensicMainTop">
            <AiSummaryCard investigation={data} summaryState={summaryState} />
            <WorkflowCard stages={analysis.workflow_stages ?? []} />
          </div>
          <EvidenceCorroboratedCard investigation={data} />
        </div>
        <InvestigatorAgentPanel
          investigation={data}
          onRerun={rerun}
          onGenerateReport={generateReport}
          busy={busy}
          reporting={reporting}
          reportError={reportError}
          reportReady={reportReady}
        />
      </div>
    </div>
  );
}

/* ── Loading skeleton ───────────────────────────────────────────── */
function LoadingSkeleton() {
  const block = (h: string): ReactNode => (
    <div style={{ background: 'rgba(148,163,184,0.08)', borderRadius: '10px', height: h, animation: 'none' }} aria-hidden="true" />
  );
  return (
    <div aria-busy="true" aria-label="Loading investigation" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {block('90px')}
      <div className="forensicLayout">
        <div className="forensicMain">
          <div className="forensicMainTop">{block('220px')}{block('220px')}</div>
          {block('200px')}
        </div>
        {block('420px')}
      </div>
      <p className="muted" style={{ fontSize: '0.85rem' }}>Loading forensic investigation…</p>
    </div>
  );
}

/* ── Incident header ────────────────────────────────────────────── */
function HeaderField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ minWidth: 0 }}>
      <p className="tableMeta" style={{ margin: '0 0 0.15rem', fontSize: '0.75rem' }}>{label}</p>
      <div style={{ fontSize: '0.85rem', overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
    </div>
  );
}

/** Operator label for the canonical detection category key. Unknown keys are
 *  title-cased rather than dropped, so a new backend category stays readable. */
function detectionCategoryLabel(category: string): string {
  const key = category.trim().toUpperCase();
  if (key === 'OPERATIONAL_INTEGRITY') return 'Operational Integrity';
  if (key === 'CYBER_SECURITY') return 'Cyber Security';
  return titleCaseToken(category);
}

/** Operator label for a snake_case detection type (e.g. unmatched_issuance). */
function detectionTypeLabel(detectionType: string): string {
  return titleCaseToken(detectionType);
}

function titleCaseToken(raw: string): string {
  return raw
    .split(/[_.\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(' ');
}

function severityVariant(severity?: string | null) {
  const s = (severity ?? '').toLowerCase();
  if (s === 'critical' || s === 'high') return 'danger' as const;
  if (s === 'medium') return 'warning' as const;
  if (s === 'low') return 'success' as const;
  return 'neutral' as const;
}

function HeaderCard({ investigation }: { investigation: ForensicInvestigation }) {
  const incident = investigation.incident ?? {};
  const analysis = investigation.analysis;
  const linked = investigation.linked ?? {};
  const reference = incidentReference(incident.reference, incident.incident_id);
  return (
    <section className="dataCard sharedSurfaceCard" aria-label="Incident header"
      style={{ padding: '1.25rem', background: 'rgba(59,130,246,0.06)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: '0.9rem' }}>{reference}</span>
            <StatusPill label={(incident.severity ?? 'unknown').toString()} variant={severityVariant(incident.severity)} />
            <StatusPill label={(incident.status ?? 'unknown').toString()} variant="info" />
          </div>
          <h2 style={{ margin: '0.45rem 0 0', fontSize: '1.1rem' }}>{incident.title ?? 'Untitled incident'}</h2>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.9rem 1.25rem', marginTop: '1.1rem' }}>
        <HeaderField label="Opened" value={fmtDateTime(incident.detected_at)} />
        <HeaderField label="Last updated" value={fmtRelative(incident.updated_at)} />
        {/* The registered ASSET this incident concerns, resolved by the backend from
            the canonical detection/target link. A raw target UUID is a routing key,
            not an asset name, so it is only shown when no asset resolved. */}
        <HeaderField label="Asset" value={
          incident.asset_label
            ? <span title={incident.asset_id ?? undefined}>{incident.asset_label}</span>
            : incident.target_id
              ? <CopyableId value={incident.target_id} label="target" />
              : <span className="muted">Not available</span>
        } />
        {/* Detection category + type from the canonical Screen 5 detection. Absent
            when no detection is linked — never a default category. */}
        <HeaderField label="Category" value={
          incident.detection_category
            ? <span>{detectionCategoryLabel(incident.detection_category)}</span>
            : <span className="muted">Not classified</span>
        } />
        <HeaderField label="Detection" value={
          incident.detection_type
            ? <span title={incident.detection_id ?? undefined}>{detectionTypeLabel(incident.detection_type)}</span>
            : <span className="muted">Not available</span>
        } />
        {/* How the case originated, from persisted linkage only. It is what makes an
            empty Detection field legible: an incident escalated from an alert or
            opened by hand never HAD a Screen 5 detection. */}
        <HeaderField label="Origin" value={
          incident.origin?.origin
            ? incidentOriginLabel(incident.origin.origin)
            : <span className="muted">Not recorded</span>
        } />
        <HeaderField label="Source alerts" value={String(linked.alerts ?? 0)} />
        {/* The analyst this case is assigned to, as the incident row records it. */}
        <HeaderField label="Assigned analyst" value={
          incident.assigned_to_user_id
            ? <CopyableId value={incident.assigned_to_user_id} label="analyst" />
            : <span className="muted">Unassigned</span>
        } />
        {/* The canonical correlation id the whole workflow is stamped with — the same
            value Screens 3, 5, 8, 9 and 11 carry. Screen 7 displays it; it never
            mints one. */}
        <HeaderField label="Canonical event" value={
          incident.event_id
            ? <CopyableId value={incident.event_id} label="canonical event" />
            : <span className="muted">Not linked</span>
        } />
        <HeaderField label="Risk score" value={
          typeof incident.risk_score === 'number'
            ? `${Math.round(incident.risk_score)} / 100`
            : <span className="muted">Not available</span>
        } />
        <HeaderField label="Confidence" value={
          analysis
            ? <span><StatusPill label={confidenceBandLabel(analysis.confidence_band)} variant={confidenceBandVariant(analysis.confidence_band)} /> {confidencePercent(analysis.confidence)}%</span>
            : <span className="muted">Unknown</span>
        } />
      </div>
    </section>
  );
}

/* ── AI Investigation Summary ───────────────────────────────────── */
function AiSummaryCard({ investigation, summaryState }: { investigation: ForensicInvestigation; summaryState: string }) {
  const analysis = investigation.analysis;
  const incident = investigation.incident ?? {};
  const linked = investigation.linked ?? {};
  const ai = investigation.ai_triage ?? {};
  const facts = (analysis?.findings ?? []).filter((f) => f.verification_state === 'verified_fact' && f.finding_type !== 'evidence_gap');
  return (
    <Card eyebrow="Digital Forensics Investigator" title="AI Investigation Summary" ariaLabel="AI Investigation Summary"
      action={<StatusPill label={summaryState} variant={summaryStateVariant(summaryState as never)} />}>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: '0 0 0.75rem' }}>
        {analysis?.confidence_explanation
          ?? 'Deterministic analysis derived from the immutable evidence snapshot.'}
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem 1rem' }}>
        <HeaderField label="Detection time" value={fmtDateTime(incident.detected_at)} />
        {/* Same resolution as the case header: the registered asset name when the
            backend resolved one, so the two cards in this panel can never name the
            impacted asset differently. */}
        <HeaderField label="Impacted asset" value={
          incident.asset_label
            ? <span title={incident.asset_id ?? undefined}>{incident.asset_label}</span>
            : incident.target_id
              ? <CopyableId value={incident.target_id} label="target" />
              : <span className="muted">Not available</span>
        } />
        <HeaderField label="Confidence" value={<span><StatusPill label={confidenceBandLabel(analysis?.confidence_band)} variant={confidenceBandVariant(analysis?.confidence_band)} /> {confidencePercent(analysis?.confidence)}%</span>} />
        <HeaderField label="Evidence coverage" value={`${coveragePercent(analysis?.evidence_coverage)}%`} />
        <HeaderField label="Investigation status" value={<StatusPill label={summaryState} variant={summaryStateVariant(summaryState as never)} />} />
        <HeaderField label="Estimated loss" value={<span className="muted">Not available</span>} />
      </div>
      <div style={{ display: 'flex', gap: '1rem', marginTop: '0.75rem', flexWrap: 'wrap' }}>
        <MetricTile label="Linked alerts" value={linked.alerts ?? 0} />
        <MetricTile label="Verified facts" value={facts.length} />
        <MetricTile label="Evidence records" value={linked.evidence_records ?? 0} />
      </div>
      {ai.status && ai.status !== 'not_requested' ? (
        <p className="muted" style={{ fontSize: '0.78rem', marginTop: '0.6rem' }}>
          AI narrative: {ai.status}{ai.status === 'unavailable' || ai.status === 'disabled' ? ' — deterministic findings preserved.' : ''}
        </p>
      ) : null}
    </Card>
  );
}

/* ── Evidence — Corroborated ────────────────────────────────────── */
const EVIDENCE_HEADERS = ['Type', 'Title', 'Source', 'Tx / Block', 'Observed', 'State'];

function EvidenceCorroboratedCard({ investigation }: { investigation: ForensicInvestigation }) {
  const evidence = investigation.evidence;
  const rows = evidence?.rows ?? [];
  const total = evidence?.total ?? rows.length;
  const isDegraded = investigation.status === 'degraded';
  return (
    <Card eyebrow="Evidence" title="Evidence — Corroborated" ariaLabel="Corroborated evidence"
      action={<Link href={`/incidents/${encodeURIComponent(investigation.incident?.incident_id ?? '')}?tab=evidence`} prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.78rem' }}>View all evidence</Link>}>
      {rows.length === 0 ? (
        isDegraded ? (
          <p className="muted" style={{ fontSize: '0.85rem' }}>
            No corroborating evidence has been resolved yet. The investigation is degraded until evidence arrives —
            this is shown as a gap, never as “safe”.
          </p>
        ) : (
          <p className="muted" style={{ fontSize: '0.85rem' }}>Waiting for linked evidence to be collected for this incident.</p>
        )
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <TableShell headers={EVIDENCE_HEADERS} compact className="forensicEvidenceTable">
            {rows.map((ev: ForensicEvidenceRow, i: number) => (
              <tr key={ev.reference ?? i}>
                <td style={{ fontSize: '0.82rem' }}>{ev.type ?? 'Evidence'}</td>
                <td style={{ fontSize: '0.82rem' }} title={ev.title ?? ''}>{ev.title ?? '—'}</td>
                <td>{ev.kind === 'telemetry'
                  ? <StatusPill label={evidenceSourceLabel(ev.evidence_source)} variant={evidenceSourceVariant(ev.evidence_source)} />
                  : <span style={{ fontSize: '0.82rem' }}>{ev.source ?? '—'}</span>}</td>
                <td>{ev.tx_hash
                  ? <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                      <CopyableId value={String(ev.tx_hash)} label="transaction hash" />
                      {ev.block_number != null ? <span className="muted" style={{ fontSize: '0.75rem' }}>Block {String(ev.block_number)}{ev.chain_id != null ? ` · chain ${String(ev.chain_id)}` : ''}</span> : null}
                    </div>
                  : <span className="muted" style={{ fontSize: '0.8rem' }}>—</span>}</td>
                <td style={{ fontSize: '0.8rem' }}>{ev.observed_at ? fmtRelative(ev.observed_at) : '—'}</td>
                <td><StatusPill label={corroborationLabel(ev.corroboration)} variant={corroborationVariant(ev.corroboration)} /></td>
              </tr>
            ))}
          </TableShell>
          {total > rows.length ? (
            <p className="muted" style={{ fontSize: '0.78rem', marginTop: '0.5rem' }}>Showing {rows.length} of {total} evidence records.</p>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/* ── Investigation Workflow ─────────────────────────────────────── */
function WorkflowCard({ stages }: { stages: WorkflowStage[] }) {
  return (
    <Card eyebrow="Workflow" title="Investigation Workflow" ariaLabel="Investigation workflow">
      {stages.length === 0 ? (
        <p className="muted" style={{ fontSize: '0.85rem' }}>No workflow stages recorded yet.</p>
      ) : (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {stages.map((s, i) => {
            const done = s.state === 'completed';
            const active = s.state === 'in_progress' || s.state === 'queued';
            const failed = s.state === 'failed';
            return (
              <li key={s.stage} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', fontSize: '0.83rem' }}>
                <span aria-hidden="true" style={{
                  width: '20px', height: '20px', borderRadius: '50%', flexShrink: 0,
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.65rem', fontWeight: 700,
                  background: done ? 'rgba(34,197,94,0.18)' : failed ? 'rgba(239,68,68,0.18)' : active ? 'rgba(59,130,246,0.18)' : 'rgba(148,163,184,0.12)',
                  border: `1px solid ${done ? 'rgba(34,197,94,0.5)' : failed ? 'rgba(239,68,68,0.5)' : active ? 'rgba(59,130,246,0.5)' : 'rgba(148,163,184,0.25)'}`,
                  color: done ? 'var(--success-fg)' : failed ? 'var(--danger-fg)' : 'var(--text-secondary)',
                }}>{done ? '✓' : failed ? '!' : i + 1}</span>
                <span style={{ flex: 1, color: done ? 'var(--text-primary)' : 'var(--text-secondary)' }}>{s.label}</span>
                <StatusPill label={workflowStateLabel(s.state)} variant={workflowStateVariant(s.state)} />
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

/* ── Digital Forensics Investigator agent panel ─────────────────── */
function FindingItem({ finding }: { finding: Finding }) {
  const isGap = finding.finding_type === 'evidence_gap';
  return (
    <li style={{ padding: '0.5rem 0', borderBottom: '1px solid rgba(148,163,184,0.1)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', alignItems: 'flex-start' }}>
        <strong style={{ fontSize: '0.82rem' }}>{finding.title ?? 'Finding'}</strong>
        <StatusPill
          label={isGap ? 'Evidence gap' : verificationStateLabel(finding.verification_state)}
          variant={isGap ? 'warning' : verificationStateVariant(finding.verification_state)}
        />
      </div>
      <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', margin: '0.25rem 0' }}>{finding.description}</p>
      <RefList refs={finding.evidence_refs} />
    </li>
  );
}

function RuleMatchItem({ match }: { match: RuleMatch }) {
  return (
    <li style={{ padding: '0.5rem 0', borderBottom: '1px solid rgba(148,163,184,0.1)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', alignItems: 'flex-start' }}>
        <strong style={{ fontSize: '0.82rem' }}>
          <span style={{ fontFamily: 'monospace', fontSize: '0.76rem', color: 'var(--text-muted)' }}>{match.rule_id}</span> {match.rule_name}
        </strong>
        <StatusPill label="Potential rule match" variant="info" />
      </div>
      <p style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', margin: '0.25rem 0' }}>{match.match_rationale}</p>
      <p className="muted" style={{ fontSize: '0.75rem', margin: '0 0 0.1rem' }}>{match.framework} v{match.framework_version}</p>
      <RefList refs={match.evidence_refs} />
    </li>
  );
}

function InvestigatorAgentPanel({ investigation, onRerun, onGenerateReport, busy, reporting, reportError, reportReady }: {
  investigation: ForensicInvestigation;
  onRerun: () => void;
  onGenerateReport: () => void;
  busy: boolean;
  reporting: boolean;
  reportError: string;
  reportReady: boolean;
}) {
  const analysis = investigation.analysis;
  const findings = analysis?.findings ?? [];
  const facts = findings.filter((f) => f.verification_state === 'verified_fact' && f.finding_type !== 'evidence_gap');
  const gaps = findings.filter((f) => f.finding_type === 'evidence_gap');
  const ruleMatches = analysis?.rule_matches ?? [];
  const ai = investigation.ai_triage ?? {};
  const incidentId = investigation.incident?.incident_id ?? '';
  const showHandoff = (ai.recommendation_count ?? 0) > 0 || ai.report_available;

  return (
    <Card eyebrow="Agent" title="Digital Forensics Investigator" ariaLabel="Digital Forensics Investigator agent"
      action={<StatusPill label={investigation.status === 'degraded' ? 'Degraded' : 'Active'} variant={investigation.status === 'degraded' ? 'warning' : 'success'} />}>
      {/* Verified findings — distinct from rule matches / AI interpretation / hypotheses. */}
      <p className="sectionEyebrow" style={{ marginBottom: '0.25rem' }}>Top verified findings</p>
      {facts.length === 0 ? (
        <p className="muted" style={{ fontSize: '0.82rem' }}>No verified facts derived yet.</p>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {facts.slice(0, 4).map((f) => <FindingItem key={f.finding_id} finding={f} />)}
        </ul>
      )}

      <p className="sectionEyebrow" style={{ margin: '0.75rem 0 0.25rem' }}>Potential rule matches</p>
      {ruleMatches.length === 0 ? (
        <p className="muted" style={{ fontSize: '0.82rem' }}>No rule matches. Nothing is asserted without a deterministic match.</p>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {ruleMatches.slice(0, 4).map((m) => <RuleMatchItem key={m.rule_id} match={m} />)}
        </ul>
      )}

      {gaps.length > 0 ? (
        <div style={{ marginTop: '0.6rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.25rem' }}>Evidence gaps</p>
          <ul style={{ margin: 0, paddingLeft: '1rem', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
            {gaps.map((g) => <li key={g.finding_id}>{g.description}</li>)}
          </ul>
        </div>
      ) : null}

      <div style={{ display: 'flex', gap: '1rem', margin: '0.75rem 0', flexWrap: 'wrap' }}>
        <MetricTile label="Evidence coverage" value={`${coveragePercent(analysis?.evidence_coverage)}%`} />
        <MetricTile label="Confidence" value={`${confidencePercent(analysis?.confidence)}%`}
          meta={confidenceBandLabel(analysis?.confidence_band)} />
      </div>

      {/* Recommended next step — always a recommendation, never executed here. */}
      <div style={{ padding: '0.65rem', background: 'rgba(148,163,184,0.06)', borderRadius: '8px', marginBottom: '0.6rem' }}>
        <p className="sectionEyebrow" style={{ margin: 0 }}>Recommended next step</p>
        <p style={{ fontSize: '0.82rem', margin: '0.25rem 0 0', color: 'var(--text-secondary)' }}>
          {investigation.status === 'degraded'
            ? 'Collect additional evidence to close the identified gaps before any response.'
            : ruleMatches.length > 0
              ? 'Hand off to the approval-controlled response workflow for review — no action is executed here.'
              : 'Continue monitoring; evidence does not yet warrant a response recommendation.'}
        </p>
      </div>

      {reportError ? <p role="alert" className="statusLine statusLine-warning" style={{ margin: '0 0 0.5rem' }}>{reportError}</p> : null}
      {reportReady && ai.report_available ? (
        <p className="statusLine" style={{ margin: '0 0 0.5rem', fontSize: '0.82rem' }} aria-live="polite">Report generated and persisted.</p>
      ) : null}

      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-primary" onClick={onGenerateReport} disabled={reporting}
          aria-busy={reporting}>
          {reporting ? 'Generating report…' : 'Generate Report'}
        </button>
        <button type="button" className="btn btn-secondary" onClick={onRerun} disabled={busy} aria-busy={busy}>
          {busy ? 'Re-running…' : 'Re-run Investigation'}
        </button>
        {showHandoff ? (
          <Link href={`/response-actions?incident_id=${encodeURIComponent(incidentId)}`} prefetch={false}
            className="btn btn-secondary" style={{ fontSize: '0.8rem' }}>
            View response recommendations
          </Link>
        ) : null}
      </div>
    </Card>
  );
}
