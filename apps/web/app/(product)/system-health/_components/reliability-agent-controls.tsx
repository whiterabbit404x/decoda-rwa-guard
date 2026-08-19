'use client';

import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { usePilotAuth } from 'app/pilot-auth-context';
import {
  type ReliabilityOverview,
  type ReliabilityFinding,
  severityPillClass,
} from './reliability-types';

type Props = {
  overview: ReliabilityOverview;
};

type ActionState = { tone: 'info' | 'success' | 'error' | 'pending'; text: string } | null;

// Distinct, non-collapsed lifecycle language. An attempt is never reported as a
// success, and execution returning is never reported as recovery (Rules 4–6).
function describeRemediation(payload: any): ActionState {
  if (!payload) return { tone: 'error', text: 'No response from server.' };
  const status = String(payload.status ?? '');
  if (status === 'manual_required') {
    return { tone: 'info', text: `Manual execution required — ${payload.note ?? payload.recommended_action ?? 'operator action needed.'}` };
  }
  if (status === 'denied') {
    return { tone: 'error', text: `Remediation denied (${payload.reason_code ?? 'policy'}): ${payload.reason ?? ''}` };
  }
  if (status === 'recovered') {
    return { tone: 'success', text: 'Action executed and recovery verified: component returned to health.' };
  }
  if (status === 'verification_failed') {
    return { tone: 'error', text: 'Action executed, but post-action verification did NOT confirm recovery.' };
  }
  if (status === 'escalation_required') {
    return { tone: 'error', text: 'Action attempted; execution failed and escalation is required.' };
  }
  return { tone: 'info', text: `Remediation status: ${status || 'unknown'}.` };
}

function policyNote(finding: ReliabilityFinding): string | null {
  switch (finding.action_policy) {
    case 'approval_required':
      return 'Requires an authorized approval (Screen 11 Governance).';
    case 'unsupported':
      return finding.action_note ?? 'Manual execution required — no supported automatic mechanism.';
    case 'forbidden':
      return 'This action is not permitted autonomously.';
    case 'none':
      return finding.action_note ?? 'Operator investigation required.';
    default:
      return null;
  }
}

export function ReliabilityAgentControls({ overview }: Props) {
  const router = useRouter();
  const { authHeaders, csrfReady, refreshCsrfToken } = usePilotAuth();
  const [busy, setBusy] = useState<string | null>(null);
  const [diagMsg, setDiagMsg] = useState<ActionState>(null);
  const [findingMsg, setFindingMsg] = useState<Record<string, ActionState>>({});
  const [reportOpen, setReportOpen] = useState(false);
  const [report, setReport] = useState<any>(null);
  const [reportState, setReportState] = useState<'idle' | 'loading' | 'error'>('idle');

  const authedFetch = useCallback(
    async (path: string, init?: RequestInit) => {
      return fetch(`/api${path}`, { cache: 'no-store', ...init, headers: { ...(init?.headers ?? {}), ...authHeaders() } });
    },
    [authHeaders],
  );

  const ensureCsrf = useCallback(async () => {
    if (!csrfReady) await refreshCsrfToken();
  }, [csrfReady, refreshCsrfToken]);

  const runDiagnostic = useCallback(async () => {
    setBusy('diagnostic');
    setDiagMsg({ tone: 'pending', text: 'Running…' });
    try {
      await ensureCsrf();
      const res = await authedFetch('/ops/reliability/diagnostics/run', { method: 'POST' });
      const body = await res.json().catch(() => ({}));
      if (res.status === 401 || res.status === 403) {
        setDiagMsg({ tone: 'error', text: body.detail ?? 'You do not have permission to run diagnostics.' });
      } else if (!res.ok) {
        setDiagMsg({ tone: 'error', text: body.detail ?? 'Diagnostic failed.' });
      } else {
        const n = Array.isArray(body.findings) ? body.findings.length : 0;
        setDiagMsg({
          tone: 'success',
          text: `Completed — ${n} finding${n === 1 ? '' : 's'}. ${body.response_ms != null ? `Probe ${body.response_ms} ms.` : ''}`,
        });
        router.refresh();
      }
    } catch {
      setDiagMsg({ tone: 'error', text: 'Network error running diagnostic.' });
    } finally {
      setBusy(null);
    }
  }, [authedFetch, ensureCsrf, router]);

  const remediate = useCallback(
    async (finding: ReliabilityFinding) => {
      if (!finding.finding_id) return;
      setBusy(finding.finding_id);
      setFindingMsg((m) => ({ ...m, [finding.finding_id!]: { tone: 'pending', text: 'Attempting…' } }));
      try {
        await ensureCsrf();
        const res = await authedFetch(`/ops/reliability/findings/${encodeURIComponent(finding.finding_id)}/remediate`, {
          method: 'POST',
        });
        const body = await res.json().catch(() => ({}));
        if (res.status === 401 || res.status === 403) {
          setFindingMsg((m) => ({
            ...m,
            [finding.finding_id!]: { tone: 'error', text: body.detail ?? 'Elevated permission required to execute remediation.' },
          }));
        } else if (!res.ok) {
          setFindingMsg((m) => ({ ...m, [finding.finding_id!]: { tone: 'error', text: body.detail ?? 'Remediation failed.' } }));
        } else {
          setFindingMsg((m) => ({ ...m, [finding.finding_id!]: describeRemediation(body) }));
          router.refresh();
        }
      } catch {
        setFindingMsg((m) => ({ ...m, [finding.finding_id!]: { tone: 'error', text: 'Network error.' } }));
      } finally {
        setBusy(null);
      }
    },
    [authedFetch, ensureCsrf, router],
  );

  const openReport = useCallback(async () => {
    setReportOpen(true);
    setReportState('loading');
    try {
      const res = await authedFetch('/ops/reliability/report');
      if (!res.ok) {
        setReportState('error');
        return;
      }
      setReport(await res.json());
      setReportState('idle');
    } catch {
      setReportState('error');
    }
  }, [authedFetch]);

  return (
    <div className="shRelAgentControls">
      {overview.findings.length > 0 ? (
        <div className="shRelAgentBlock">
          <p className="shRelAgentBlockTitle">Findings &amp; recommended actions</p>
          <ul className="shRelFindingList">
            {overview.findings.map((f, idx) => {
              const key = f.finding_id ?? `${f.component}:${f.signal}:${idx}`;
              const msg = f.finding_id ? findingMsg[f.finding_id] : null;
              const note = policyNote(f);
              const canExecute = f.action_policy === 'auto' && Boolean(f.finding_id);
              return (
                <li key={key} className="shRelFinding">
                  <div className="shRelFindingHead">
                    <span className={severityPillClass(f.severity)} style={{ fontSize: '0.68rem' }}>
                      {f.severity}
                    </span>
                    <span className="shRelFindingSummary">{f.summary}</span>
                  </div>
                  {f.recommended_action ? (
                    <p className="shRelFindingAction">Recommended: {f.recommended_action}</p>
                  ) : null}
                  {note ? <p className="shRelFindingNote">{note}</p> : null}
                  {canExecute ? (
                    <button
                      type="button"
                      className="secondaryCta shRelSmallCta"
                      onClick={() => remediate(f)}
                      disabled={busy !== null}
                    >
                      {busy === f.finding_id ? 'Attempting…' : 'Execute remediation'}
                    </button>
                  ) : f.action_policy === 'auto' && !f.finding_id ? (
                    <p className="shRelFindingNote">Run a diagnostic to record this finding before it can be executed.</p>
                  ) : null}
                  {msg ? <p className={`shRelActionMsg shRelActionMsg-${msg.tone}`}>{msg.text}</p> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <p className="shRelAgentEmpty">No active reliability anomalies. No remediation required.</p>
      )}

      <div className="shRelAgentActions">
        <button type="button" className="primaryCta shRelSmallCta" onClick={runDiagnostic} disabled={busy !== null}>
          {busy === 'diagnostic' ? 'Running…' : 'Run Diagnostic'}
        </button>
        <button type="button" className="secondaryCta shRelSmallCta" onClick={openReport} disabled={busy !== null}>
          View full health report
        </button>
      </div>
      {diagMsg ? <p className={`shRelActionMsg shRelActionMsg-${diagMsg.tone}`}>{diagMsg.text}</p> : null}

      {reportOpen ? (
        <ReliabilityReportModal
          state={reportState}
          report={report}
          onClose={() => setReportOpen(false)}
          onRetry={openReport}
        />
      ) : null}
    </div>
  );
}

function ReliabilityReportModal({
  state,
  report,
  onClose,
  onRetry,
}: {
  state: 'idle' | 'loading' | 'error';
  report: any;
  onClose: () => void;
  onRetry: () => void;
}) {
  return (
    <div className="shRelModalOverlay" role="dialog" aria-modal="true" aria-label="Full health report" onClick={onClose}>
      <div className="shRelModal" onClick={(e) => e.stopPropagation()}>
        <div className="shRelModalHeader">
          <h3>Full Health Report</h3>
          <button type="button" className="tertiaryCta shRelSmallCta" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>
        {state === 'loading' ? (
          <p className="shSummarySignal">Loading report…</p>
        ) : state === 'error' ? (
          <div>
            <p className="shSummarySignal">Could not load the health report.</p>
            <button type="button" className="secondaryCta shRelSmallCta" onClick={onRetry}>
              Retry
            </button>
          </div>
        ) : report ? (
          <div className="shRelModalBody">
            <p className="shSummaryTime">
              Generated {report.generated_at ? new Date(report.generated_at).toLocaleString() : '—'} · Overall:{' '}
              <strong>{report.overall_status}</strong>
            </p>
            <p className="shSummarySignal">{report.summary}</p>

            <div className="shRelReportGrid">
              <div>
                <p className="shRelAgentBlockTitle">Runtime</p>
                <p className="shSummaryTime">Environment: {report.runtime?.environment ?? '—'}</p>
                <p className="shSummaryTime">API SHA: {report.runtime?.api_commit ? String(report.runtime.api_commit).slice(0, 12) : 'unknown'}</p>
                <p className="shSummaryTime">API build: {report.runtime?.api_version ?? 'unknown'}</p>
              </div>
              <div>
                <p className="shRelAgentBlockTitle">Queue / SLO</p>
                {report.slo?.metrics ? (
                  <>
                    <p className="shSummaryTime">Queue depth: {report.slo.metrics.queue_depth ?? '—'}</p>
                    <p className="shSummaryTime">Active workers: {report.slo.metrics.active_workers ?? '—'}</p>
                    <p className="shSummaryTime">
                      Ingestion age: {report.slo.metrics.ingestion_age_seconds != null ? `${Math.round(report.slo.metrics.ingestion_age_seconds)}s` : '—'}
                    </p>
                  </>
                ) : (
                  <p className="shSummaryTime">SLO metrics unavailable.</p>
                )}
              </div>
            </div>

            <p className="shRelAgentBlockTitle">Unresolved findings ({report.unresolved_findings?.length ?? 0})</p>
            {report.unresolved_findings?.length ? (
              <ul className="shRelAgentList">
                {report.unresolved_findings.map((e: any) => (
                  <li key={e.id}>
                    [{e.severity}] {e.summary} — {e.status}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="shSummaryTime">No unresolved reliability findings.</p>
            )}

            <p className="shRelAgentBlockTitle">Remediation history ({report.remediation_history?.length ?? 0})</p>
            {report.remediation_history?.length ? (
              <ul className="shRelAgentList">
                {report.remediation_history.map((e: any) => (
                  <li key={e.id}>
                    {e.component}: {e.event_type} → {e.status}
                    {e.action_result ? ` (execution: ${e.action_result})` : ''} · {e.detected_ago ?? ''}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="shSummaryTime">No automatic remediations have been executed.</p>
            )}
          </div>
        ) : (
          <p className="shSummarySignal">No report data.</p>
        )}
      </div>
    </div>
  );
}
