'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

const API_PROXY_BASE = '/api';

// Terminal vs active triage states drive the poll loop and which controls render.
const ACTIVE_STATES = new Set(['queued', 'running']);

type Recommendation = {
  recommendation_id: string;
  action_type: string;
  runbook_id: string | null;
  reason: string | null;
  risk_level: string;
  requires_human_approval: boolean;
  evidence_refs: string[];
  review_state: string;
};

type TriageResult = {
  summary?: string;
  reason_triggered?: string;
  severity_assessment?: { recommended_severity?: string; confidence?: number; reason?: string };
  affected_entities?: { type: string; value: string; evidence_refs?: string[] }[];
  timeline?: { timestamp?: string; event?: string; evidence_refs?: string[] }[];
  risk_findings?: { title?: string; description?: string; confidence?: number; evidence_refs?: string[] }[];
  missing_information?: string[];
  recommended_runbook_id?: string | null;
  citations?: { ref: string; description?: string }[];
};

type TriageState = {
  status: string;
  enabled?: boolean;
  triage_job_id?: string;
  provider?: string;
  model?: string;
  prompt_version?: string;
  evidence_snapshot_hash?: string;
  completed_at?: string | null;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  estimated_cost_usd?: number | null;
  error_code?: string | null;
  result?: TriageResult;
  warnings?: string[];
  recommendations?: Recommendation[];
  label?: string;
};

const STATUS_LABELS: Record<string, string> = {
  not_requested: 'Ready to analyze',
  disabled: 'AI triage disabled',
  queued: 'Queued',
  running: 'Investigating…',
  completed: 'Completed',
  completed_with_warnings: 'Completed with warnings',
  failed: 'Failed',
  validation_failed: 'Validation failed',
  budget_blocked: 'Budget blocked',
  cancelled: 'Cancelled',
};

export default function AiInvestigationPanel({ incidentId }: { incidentId: string }) {
  const { authHeaders } = usePilotAuth();
  const [state, setState] = useState<TriageState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/ai-triage`, {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (!res.ok) {
        setError('Unable to load AI triage state.');
        return;
      }
      setState((await res.json()) as TriageState);
      setError('');
    } catch {
      setError('Unable to load AI triage state.');
    } finally {
      setLoading(false);
    }
  }, [incidentId, authHeaders]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while a job is active so the section updates automatically on completion.
  // (The backend also publishes incident.ai_triage.* events to the workspace
  // incidents Redis stream for a future dedicated SSE transport.)
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (state && ACTIVE_STATES.has(state.status)) {
      pollRef.current = setInterval(() => void load(), 4000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [state, load]);

  const startAnalysis = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/ai-triage`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        setError(String(body.detail ?? 'Unable to start AI triage.'));
      }
      await load();
    } finally {
      setBusy(false);
    }
  }, [incidentId, authHeaders, load]);

  const regenerate = useCallback(async () => {
    const reason = window.prompt('Reason for regenerating the AI analysis?');
    if (!reason || !reason.trim()) return;
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/ai-triage/regenerate`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason.trim() }),
        cache: 'no-store',
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        setError(String(body.detail ?? 'Unable to regenerate AI triage.'));
      }
      await load();
    } finally {
      setBusy(false);
    }
  }, [incidentId, authHeaders, load]);

  const review = useCallback(
    async (recommendationId: string, decision: 'approve' | 'reject') => {
      setBusy(true);
      setError('');
      try {
        const res = await fetch(
          `${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/recommendations/${encodeURIComponent(recommendationId)}/${decision}`,
          {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
            cache: 'no-store',
          },
        );
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
          const detail = body.detail as Record<string, unknown> | string | undefined;
          setError(typeof detail === 'string' ? detail : 'You are not authorized to review recommendations, or it was already reviewed.');
        }
        await load();
      } finally {
        setBusy(false);
      }
    },
    [incidentId, authHeaders, load],
  );

  const statusLabel = useMemo(() => (state ? STATUS_LABELS[state.status] ?? state.status : ''), [state]);
  const result = state?.result;
  const sev = result?.severity_assessment;

  return (
    <section className="card" style={{ marginTop: '1.5rem' }} aria-label="AI Investigation">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
        <div>
          <p className="eyebrow">AI Investigation</p>
          <h2 style={{ margin: 0 }}>Evidence-grounded AI incident investigation</h2>
        </div>
        <span className="pill" data-status={state?.status}>{statusLabel}</span>
      </div>

      <p className="lede" style={{ marginTop: '0.5rem' }}>
        <strong>AI-generated analysis — verify before action.</strong> Recommendations are policy-controlled and
        require human approval. No on-chain action is executed automatically.
      </p>

      {loading && <p>Loading AI investigation…</p>}
      {error && <p role="alert" className="errorText">{error}</p>}

      {!loading && state?.status === 'disabled' && (
        <p>AI triage is disabled for this deployment. An administrator can enable it via configuration.</p>
      )}

      {!loading && state && (state.status === 'not_requested') && (
        <button type="button" className="primaryButton" onClick={startAnalysis} disabled={busy}>
          {busy ? 'Starting…' : 'Start AI analysis'}
        </button>
      )}

      {!loading && state && ACTIVE_STATES.has(state.status) && (
        <p aria-live="polite">The AI agent is investigating this incident from the immutable evidence snapshot. This updates automatically.</p>
      )}

      {!loading && state && ['failed', 'validation_failed', 'budget_blocked'].includes(state.status) && (
        <div>
          <p role="alert" className="errorText">
            {state.status === 'budget_blocked'
              ? 'AI triage was blocked by a budget limit and did not run.'
              : state.status === 'validation_failed'
                ? 'The AI response failed grounding/schema validation and was not published as analysis.'
                : 'AI triage failed.'}
            {state.error_code ? ` (${state.error_code})` : ''}
          </p>
          <button type="button" className="primaryButton" onClick={startAnalysis} disabled={busy}>Retry analysis</button>
        </div>
      )}

      {!loading && result && ['completed', 'completed_with_warnings'].includes(state?.status ?? '') && (
        <div className="aiResult">
          {(state?.warnings?.length ?? 0) > 0 && (
            <ul className="warnList">
              {state?.warnings?.map((w, i) => <li key={i}>⚠︎ {w}</li>)}
            </ul>
          )}

          <h3>Summary</h3>
          <p>{result.summary}</p>

          <h3>Why the alert triggered</h3>
          <p>{result.reason_triggered}</p>

          {sev && (
            <p><strong>AI severity:</strong> {sev.recommended_severity ?? 'n/a'} · <strong>confidence:</strong> {sev.confidence ?? 'n/a'}</p>
          )}

          {(result.timeline?.length ?? 0) > 0 && (
            <>
              <h3>Timeline</h3>
              <ul>{result.timeline?.map((t, i) => <li key={i}><code>{t.timestamp}</code> — {t.event} {renderRefs(t.evidence_refs)}</li>)}</ul>
            </>
          )}

          {(result.affected_entities?.length ?? 0) > 0 && (
            <>
              <h3>Affected entities</h3>
              <ul>{result.affected_entities?.map((e, i) => <li key={i}>{e.type}: <code>{e.value}</code> {renderRefs(e.evidence_refs)}</li>)}</ul>
            </>
          )}

          {(result.risk_findings?.length ?? 0) > 0 && (
            <>
              <h3>Risk findings</h3>
              <ul>{result.risk_findings?.map((f, i) => <li key={i}><strong>{f.title}</strong> ({f.confidence}): {f.description} {renderRefs(f.evidence_refs)}</li>)}</ul>
            </>
          )}

          <h3>Missing information</h3>
          {(result.missing_information?.length ?? 0) > 0
            ? <ul>{result.missing_information?.map((m, i) => <li key={i}>{m}</li>)}</ul>
            : <p>None reported.</p>}

          <h3>Recommended runbook &amp; actions</h3>
          {result.recommended_runbook_id ? <p>Runbook: <code>{result.recommended_runbook_id}</code></p> : <p>No runbook recommended.</p>}
          {(state?.recommendations?.length ?? 0) > 0 ? (
            <ul className="recList">
              {state?.recommendations?.map((r) => (
                <li key={r.recommendation_id}>
                  <strong>{r.action_type}</strong> ({r.risk_level}) — {r.reason} {renderRefs(r.evidence_refs)}
                  <div className="recControls">
                    {r.review_state === 'pending_review' ? (
                      <>
                        <button type="button" onClick={() => review(r.recommendation_id, 'approve')} disabled={busy}>Approve</button>
                        <button type="button" onClick={() => review(r.recommendation_id, 'reject')} disabled={busy}>Reject</button>
                      </>
                    ) : (
                      <span className="pill">{r.review_state}</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ) : <p>No recommendations.</p>}

          {(result.citations?.length ?? 0) > 0 && (
            <>
              <h3>Evidence citations</h3>
              <ul>{result.citations?.map((c, i) => <li key={i}><code>{c.ref}</code>: {c.description}</li>)}</ul>
            </>
          )}

          <div className="aiMeta">
            <p><strong>Model:</strong> {state?.provider} / {state?.model ?? 'default'} · <strong>prompt:</strong> {state?.prompt_version}</p>
            <p><strong>Generated at:</strong> {state?.completed_at ?? 'n/a'} · <strong>latency:</strong> {state?.latency_ms ?? 'n/a'}ms</p>
            <p><strong>Tokens:</strong> {state?.input_tokens ?? 0} in / {state?.output_tokens ?? 0} out · <strong>est. cost:</strong> ${state?.estimated_cost_usd ?? 0}</p>
            <p><strong>Evidence snapshot hash:</strong> <code>{state?.evidence_snapshot_hash}</code></p>
          </div>

          <button type="button" className="secondaryButton" onClick={regenerate} disabled={busy}>Regenerate (requires reason)</button>
        </div>
      )}
    </section>
  );
}

function renderRefs(refs?: string[]) {
  if (!refs || refs.length === 0) return null;
  return <span className="refs">[{refs.join(', ')}]</span>;
}
