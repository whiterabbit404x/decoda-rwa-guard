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
  schema_ready?: boolean;
  message?: string;
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
  simulated?: boolean;
  version_count?: number;
  regenerated_from_job_id?: string | null;
  result?: TriageResult;
  warnings?: string[];
  recommendations?: Recommendation[];
  label?: string;
};

const STATUS_LABELS: Record<string, string> = {
  not_requested: 'Ready to analyze',
  disabled: 'AI triage disabled',
  unavailable: 'Unavailable',
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
  // In-app regeneration modal state (replaces the browser prompt()).
  const [regenOpen, setRegenOpen] = useState(false);
  const [regenReason, setRegenReason] = useState('');
  const [regenError, setRegenError] = useState('');
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

  const openRegenerate = useCallback(() => {
    setRegenReason('');
    setRegenError('');
    setRegenOpen(true);
  }, []);

  const closeRegenerate = useCallback(() => {
    setRegenOpen(false);
    setRegenError('');
  }, []);

  const submitRegenerate = useCallback(async () => {
    const reason = regenReason.trim();
    if (!reason) {
      // In-app validation message — never a raw browser alert.
      setRegenError('A reason is required to regenerate the analysis.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await fetch(`${API_PROXY_BASE}/incidents/${encodeURIComponent(incidentId)}/ai-triage/regenerate`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
        cache: 'no-store',
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        setRegenError(String(body.detail ?? 'Unable to regenerate AI triage.'));
        return;
      }
      setRegenOpen(false);
      await load();
    } finally {
      setBusy(false);
    }
  }, [incidentId, authHeaders, load, regenReason]);

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

      {!loading && state?.status === 'unavailable' && (
        <p role="alert" className="errorText">
          {state?.message ?? 'AI investigation is currently unavailable for this deployment.'}
        </p>
      )}

      {!loading && state && (state.status === 'not_requested') && (
        <button type="button" className="primaryButton" onClick={startAnalysis} disabled={busy}>
          {busy ? 'Starting…' : 'Start AI Investigation'}
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
            <p>
              <strong>Provider:</strong> {formatProviderLabel(state?.provider)} · <strong>Model:</strong> {formatModelLabel(state?.simulated, state?.model)}
              {state?.simulated && (
                <span className="pill" style={{ marginLeft: '0.4rem' }} title="Deterministic offline mock — no live model was called.">
                  Simulated (mock)
                </span>
              )}
              {' '}· <strong>prompt:</strong> {state?.prompt_version}
            </p>
            <p><strong>Generated at:</strong> {state?.completed_at ?? 'n/a'} · <strong>latency:</strong> {state?.latency_ms ?? 'n/a'}ms</p>
            <p>
              <strong>Tokens:</strong> {state?.input_tokens ?? 0} in / {state?.output_tokens ?? 0} out{state?.simulated ? ' (synthetic)' : ''}
              {' '}· <strong>est. cost:</strong> ${Number(state?.estimated_cost_usd ?? 0).toFixed(2)}
              {state?.simulated ? ' — Synthetic test result — not billed' : ''}
            </p>
            <p><strong>Evidence snapshot hash:</strong> <code>{state?.evidence_snapshot_hash}</code></p>
            {(state?.version_count ?? 0) > 1 && (
              <p><strong>Analysis version:</strong> {state?.version_count} (prior versions preserved)</p>
            )}
          </div>

          <button type="button" className="secondaryButton" onClick={openRegenerate} disabled={busy}>Regenerate (requires reason)</button>
        </div>
      )}

      {regenOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Regenerate AI analysis"
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200 }}
          onClick={closeRegenerate}
        >
          <div
            style={{ background: 'var(--card-bg, #0d1627)', border: '1px solid var(--border)', borderRadius: '12px', padding: '1.5rem', maxWidth: '520px', width: '100%' }}
            onClick={(e) => e.stopPropagation()}
          >
            <p className="eyebrow">Regenerate analysis</p>
            <h3 style={{ margin: '0.25rem 0 0.75rem' }}>Regenerate AI investigation</h3>
            <p style={{ fontSize: '0.9rem', marginBottom: '0.75rem' }}>
              Regeneration creates a new analysis version and preserves the previous result. A reason is required and recorded in the audit log.
            </p>
            <label htmlFor="regen-reason" style={{ display: 'block', fontWeight: 600, marginBottom: '0.35rem' }}>Reason<span aria-hidden="true"> *</span></label>
            <textarea
              id="regen-reason"
              value={regenReason}
              onChange={(e) => { setRegenReason(e.target.value); if (regenError) setRegenError(''); }}
              rows={3}
              required
              aria-required="true"
              aria-invalid={regenError ? true : undefined}
              placeholder="Why is this analysis being regenerated?"
              style={{ width: '100%', boxSizing: 'border-box', padding: '0.5rem', borderRadius: '8px', border: '1px solid var(--border)', background: 'rgba(255,255,255,0.04)', color: 'inherit' }}
            />
            {regenError && <p role="alert" className="errorText" style={{ marginTop: '0.5rem' }}>{regenError}</p>}
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '1rem' }}>
              <button type="button" className="secondaryButton" onClick={closeRegenerate} disabled={busy}>Cancel</button>
              <button type="button" className="primaryButton" onClick={submitRegenerate} disabled={busy}>{busy ? 'Regenerating…' : 'Regenerate'}</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function renderRefs(refs?: string[]) {
  if (!refs || refs.length === 0) return null;
  return <span className="refs">[{refs.join(', ')}]</span>;
}

// Truthful provider label: a synthetic (mock) run must read "Mock", never a live
// model name. OpenAI keeps its conventional capitalization.
function formatProviderLabel(provider?: string) {
  const p = (provider ?? '').trim().toLowerCase();
  if (!p) return 'n/a';
  if (p === 'mock') return 'Mock';
  if (p === 'openai') return 'OpenAI';
  if (p === 'anthropic') return 'Anthropic';
  return p;
}

// A simulated run always displays model "Mock" — never the configured live
// AI_MODEL_TRIAGE value (e.g. an OpenAI model name) for a run that never called it.
function formatModelLabel(simulated?: boolean, model?: string) {
  if (simulated) return 'Mock';
  return model && model.trim() ? model : 'default';
}
