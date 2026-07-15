'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { usePilotAuth } from '../pilot-auth-context';
import { SurfaceCard, StatusPill, Button, TableShell, Select } from '../components/ui-primitives';
import {
  ACTIVE_STATUSES, connectOnboardingStream, confidenceVariant, deriveAgentView, derivePhaseStatuses,
  describeOnboardingError, isTransportError, ONBOARDING_TRANSPORT_MESSAGE, OnboardingRequestError,
  recommendationVariant, stepVariant,
  type BenchmarkResult, type OnboardingErrorInfo, type OnboardingFinding, type OnboardingSession,
  type OnboardingSnapshot, type StreamStatus,
} from '../onboarding-agent-client';

const CHAINS = [
  { id: 8453, label: 'Base Mainnet (8453)' },
  { id: 1, label: 'Ethereum Mainnet (1)' },
  { id: 42161, label: 'Arbitrum One (42161)' },
  { id: 10, label: 'Optimism (10)' },
  { id: 137, label: 'Polygon (137)' },
];

const MODES = [
  { id: 'recommended', label: 'Recommended', detail: 'Agent generates appropriate monitoring coverage.' },
  { id: 'strict', label: 'Strict', detail: 'Higher sensitivity and more aggressive alerting.' },
  { id: 'custom', label: 'Custom', detail: 'Review individual policies before activation.' },
];

const STORAGE_KEY = 'decoda.onboarding.session';
const HEX_ADDRESS = /^0x[0-9a-fA-F]{40}$/;

// Same-origin proxy base. All onboarding requests go through the Next.js /api/onboarding/*
// proxy routes; the browser must NEVER call the backend origin directly (in production it is
// not browser-reachable, which surfaces as a raw "Failed to fetch"). Onboarding paths already
// carry the /api prefix, so the base is empty. Mirrors the alerts / incidents / targets transport.
const API_PROXY_BASE = '';

type Busy = null | 'create' | 'discover' | 'approve' | 'activate' | 'retry' | 'benchmark' | 'report' | 'refresh';

function findValue(findings: OnboardingFinding[], type: string): OnboardingFinding | undefined {
  return findings.find((f) => f.finding_type === type);
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '—';
  }
}

export default function OnboardingPageClient() {
  const { authHeaders } = usePilotAuth();
  const [snapshot, setSnapshot] = useState<OnboardingSnapshot | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<OnboardingErrorInfo | null>(null);
  const [lastAction, setLastAction] = useState<{ kind: Busy; fn: () => Promise<unknown> } | null>(null);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>('disconnected');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [ask, setAsk] = useState('');
  const [initializing, setInitializing] = useState(true);

  const [form, setForm] = useState({
    workspace_name: '', chain_id: 8453, primary_contract: '', rpc_endpoints: '',
    monitoring_mode: 'recommended', protocol_name: '',
  });

  const sessionId = snapshot?.session.id ?? null;
  const status = snapshot?.session.status ?? null;
  const isActive = status !== null && ACTIVE_STATUSES.includes(status);

  const api = useCallback(async (path: string, method: 'GET' | 'POST' = 'GET', body?: unknown) => {
    let res: Response;
    try {
      res = await fetch(`${API_PROXY_BASE}${path}`, {
        method,
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
        cache: 'no-store',
      });
    } catch (err) {
      // Browser-level transport failure (offline / DNS / proxy down / aborted). Convert the
      // raw "Failed to fetch" TypeError into a customer-safe, recoverable message.
      throw new OnboardingRequestError(describeOnboardingError(isTransportError(err) ? 'backend_unreachable' : null));
    }
    if (res.status === 401) {
      setSessionExpired(true);
      // Surfaced by the dedicated session-expired card, so mark silent to avoid a duplicate banner.
      throw new OnboardingRequestError(describeOnboardingError('unauthenticated'), true);
    }
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) {
      const detail = (data as { detail?: unknown }).detail;
      const detailObj = detail && typeof detail === 'object' ? (detail as Record<string, unknown>) : null;
      const code = (detailObj?.code as string | undefined) ?? (data.code as string | undefined) ?? null;
      const backendMessage = detailObj
        ? (detailObj.message as string | undefined) ?? null
        : (typeof detail === 'string' ? detail : null);
      const correlationId = (data.correlation_id as string | undefined)
        ?? (detailObj?.correlation_id as string | undefined)
        ?? null;
      throw new OnboardingRequestError({ ...describeOnboardingError(code, backendMessage), correlationId });
    }
    return data;
  }, [authHeaders]);

  const refresh = useCallback(async (id: string) => {
    try {
      const data = await api(`/api/onboarding/sessions/${id}`) as OnboardingSnapshot;
      setSnapshot(data);
      setError(null);
    } catch (err) {
      // Background refresh: surface genuine structured errors, but never clobber the last good
      // snapshot (or flicker the banner) on a transient transport blip during polling.
      if (err instanceof OnboardingRequestError && !err.silent && err.info.message !== ONBOARDING_TRANSPORT_MESSAGE) {
        setError(err.info);
      }
    }
  }, [api]);

  // Restore an in-progress session after refresh.
  useEffect(() => {
    let stored: string | null = null;
    try { stored = window.localStorage.getItem(STORAGE_KEY); } catch { stored = null; }
    if (!stored) { setInitializing(false); return; }
    (async () => {
      try {
        const data = await api(`/api/onboarding/sessions/${stored}`) as OnboardingSnapshot;
        setSnapshot(data);
      } catch {
        try { window.localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
      } finally {
        setInitializing(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live SSE stream; on any event refetch the authoritative snapshot.
  useEffect(() => {
    if (!sessionId) return;
    const disconnect = connectOnboardingStream(sessionId, authHeaders(), {
      onEvent: () => { void refresh(sessionId); },
      onStatus: (s) => setStreamStatus(s),
    });
    return () => { disconnect(); setStreamStatus('disconnected'); };
  }, [sessionId, authHeaders, refresh]);

  // Polling fallback while active and SSE is not live. Progress is never simulated.
  useEffect(() => {
    if (!sessionId || !isActive || streamStatus === 'live') return;
    const t = setInterval(() => { void refresh(sessionId); }, 2500);
    return () => clearInterval(t);
  }, [sessionId, isActive, streamStatus, refresh]);

  const persist = (id: string) => { try { window.localStorage.setItem(STORAGE_KEY, id); } catch { /* ignore */ } };

  async function run<T>(kind: Busy, fn: () => Promise<T>) {
    if (busy) return; // duplicate-submit protection: only one in-flight request at a time
    setLastAction({ kind, fn });
    setBusy(kind);
    setError(null);
    try {
      await fn();
      setError(null);
    } catch (err) {
      if (err instanceof OnboardingRequestError) {
        if (!err.silent) setError(err.info);
      } else if (isTransportError(err)) {
        setError(describeOnboardingError('backend_unreachable'));
      } else {
        setError(describeOnboardingError(null, err instanceof Error ? err.message : null));
      }
    } finally {
      // Always re-enable the form so a recoverable failure can be retried.
      setBusy(null);
    }
  }

  // Retry the most recent user-initiated action (create / discover / approve / …).
  const onRetryAction = () => {
    if (!lastAction || busy) return;
    void run(lastAction.kind, lastAction.fn);
  };

  const contractValid = HEX_ADDRESS.test(form.primary_contract.trim());

  // The single "Run Automated Discovery" button must (1) create/resume the session,
  // (2) persist the returned session id, and (3) START discovery via the canonical
  // discover endpoint. Creating the session alone leaves it in `draft` with every step
  // pending (the reported "stuck at 0/10" state), because the backend only enqueues the
  // durable discovery job on POST …/discover. Progress is never simulated: we render the
  // authoritative snapshot the backend returns (running/partial/failed/proposal_ready).
  const onCreate = () => run('create', async () => {
    const rpc = form.rpc_endpoints.split('\n').map((s) => s.trim()).filter(Boolean);
    const created = await api('/api/onboarding/sessions', 'POST', {
      workspace_name: form.workspace_name || undefined,
      chain_id: form.chain_id,
      primary_contract: form.primary_contract.trim(),
      rpc_endpoints: rpc,
      monitoring_mode: form.monitoring_mode,
      protocol_name: form.protocol_name || undefined,
      force_new: true,
    }) as OnboardingSnapshot;
    persist(created.session.id);
    setSnapshot(created);
    // Immediately transition the just-created draft into a running backend discovery job.
    // If this call raises (HTTP-level error) the created draft stays visible so the panel's
    // own "Run Automated Discovery" / Retry can re-drive discovery on the same session; a
    // gating failure (EOA / RPC not configured / chain mismatch) comes back as a 200 snapshot
    // with status `partial` + a structured error_code, rendered by SessionErrorNotice.
    setSnapshot(await api(`/api/onboarding/sessions/${created.session.id}/discover`, 'POST') as OnboardingSnapshot);
  });

  const onDiscover = () => run('discover', async () => {
    if (!sessionId) return;
    setSnapshot(await api(`/api/onboarding/sessions/${sessionId}/discover`, 'POST') as OnboardingSnapshot);
  });
  const onApprove = () => run('approve', async () => {
    if (!sessionId) return;
    setSnapshot(await api(`/api/onboarding/sessions/${sessionId}/approve`, 'POST', { decision: 'approved' }) as OnboardingSnapshot);
  });
  const onActivate = () => run('activate', async () => {
    if (!sessionId) return;
    setSnapshot(await api(`/api/onboarding/sessions/${sessionId}/activate`, 'POST') as OnboardingSnapshot);
  });
  const onRetry = () => run('retry', async () => {
    if (!sessionId) return;
    setSnapshot(await api(`/api/onboarding/sessions/${sessionId}/retry`, 'POST') as OnboardingSnapshot);
  });
  const onBenchmark = () => run('benchmark', async () => {
    if (!sessionId) return;
    setSnapshot(await api(`/api/onboarding/sessions/${sessionId}/rpc-benchmark`, 'POST') as OnboardingSnapshot);
  });
  const onExport = () => run('report', async () => {
    if (!sessionId) return;
    const data = await api(`/api/onboarding/sessions/${sessionId}/report`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `onboarding-report-${sessionId.slice(0, 8)}.json`;
    a.click(); URL.revokeObjectURL(url);
  });

  const onReset = () => {
    try { window.localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
    setSnapshot(null); setError(null); setLastAction(null); setStreamStatus('disconnected');
  };

  const phases = useMemo(() => derivePhaseStatuses(snapshot), [snapshot]);
  const findings = snapshot?.findings ?? [];

  return (
    <main className="productPage onbAgent" data-testid="onboarding-page">
      <section className="featureSection">
        <header className="onbHeader">
          <div>
            <h1 className="onboardingTitle">Welcome to Decoda RWA Guard</h1>
            <p className="onboardingSubtitle">AI-powered protection for your digital asset infrastructure.</p>
          </div>
          {snapshot ? (
            <div className="onbHeaderMeta" data-testid="onboarding-stream-status">
              <StreamBadge status={streamStatus} active={isActive} />
              <Button variant="ghost" onClick={onReset} disabled={busy === 'create'}>Start over</Button>
            </div>
          ) : null}
        </header>

        {/* 5-step progress header */}
        <div className="onboardingStepper" role="list" aria-label="Onboarding steps" data-testid="onboarding-top-stepper">
          {phases.map((phase, index) => {
            const state = phase.status === 'completed' ? 'complete'
              : phase.status === 'running' ? 'current'
              : phase.status === 'failed' ? 'failed'
              : phase.status === 'needs_attention' ? 'attention' : 'upcoming';
            return (
              <div key={phase.label} role="listitem" className="onboardingStepItem" data-step-status={state}>
                {index > 0 && <div className={`stepConnector${phase.status === 'completed' ? ' stepConnectorComplete' : ''}`} aria-hidden="true" />}
                <div className="stepCircle" data-state={state} aria-current={state === 'current' ? 'step' : undefined}>
                  {phase.status === 'completed' ? (
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8l3.5 3.5L13 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  ) : <span>{index + 1}</span>}
                </div>
                <span className="stepLabel">{phase.label}</span>
              </div>
            );
          })}
        </div>

        {sessionExpired ? (
          <SurfaceCard className="onbNotice">
            <p className="onboardingError" data-testid="session-expired-notice">
              Session expired. Please <Link href="/sign-in" prefetch={false} style={{ color: 'var(--text-accent)' }}>sign in again</Link> to continue.
            </p>
          </SurfaceCard>
        ) : null}

        {error ? <ErrorNotice error={error} onRetry={onRetryAction} busy={busy} /> : null}
        {snapshot && snapshot.session.error_code && (status === 'partial' || status === 'failed') ? (
          <SessionErrorNotice session={snapshot.session} onRetry={onRetry} busy={busy} />
        ) : null}
        {snapshot?.warnings?.length ? (
          <SurfaceCard className="onbNotice"><p className="onbWarn" data-testid="onboarding-warning">{snapshot.warnings.join(' ')}</p></SurfaceCard>
        ) : null}

        <div className="onbGrid">
          <div className="onbMain">
            {!snapshot && !initializing ? (
              <IntakeForm form={form} setForm={setForm} onCreate={onCreate} busy={busy} contractValid={contractValid} />
            ) : null}
            {initializing ? <SurfaceCard className="onbSkeleton"><div className="skeletonRow" /><div className="skeletonRow" /><div className="skeletonRow" /></SurfaceCard> : null}

            {snapshot ? (
              <AgentTimeline snapshot={snapshot} expanded={expanded} setExpanded={setExpanded} onRetry={onRetry} busy={busy} />
            ) : null}

            {snapshot && findings.length > 0 ? <DiscoverySummary findings={findings} /> : null}

            {snapshot && snapshot.benchmark.results.length > 0 ? (
              <RpcBenchmark snapshot={snapshot} onBenchmark={onBenchmark} busy={busy} />
            ) : null}

            {snapshot?.proposal ? (
              <ProposalReview snapshot={snapshot} onApprove={onApprove} onActivate={onActivate} busy={busy} />
            ) : null}

            {status === 'completed' ? <ProtectionActive snapshot={snapshot!} /> : null}
          </div>

          <aside className="onbSide">
            <AgentPanel snapshot={snapshot} streamStatus={streamStatus} busy={busy}
              onDiscover={onDiscover} onBenchmark={onBenchmark} onApprove={onApprove}
              onActivate={onActivate} onExport={onExport} ask={ask} setAsk={setAsk} />
          </aside>
        </div>
      </section>
    </main>
  );
}

function StreamBadge({ status, active }: { status: StreamStatus; active: boolean }) {
  if (status === 'live') return <StatusPill label="Live updates" variant="success" />;
  if (active) return <StatusPill label="Polling" variant="info" />;
  return <StatusPill label="Idle" variant="neutral" />;
}

// Actionable, customer-safe error banner: message + error code + correlation reference, with a
// Retry for recoverable failures and a recovery link (e.g. Monitoring Sources for a wallet
// address). Never renders a raw exception, HTML body, or "Failed to fetch" string.
function ErrorNotice({ error, onRetry, busy }: { error: OnboardingErrorInfo; onRetry: () => void; busy: Busy }) {
  return (
    <SurfaceCard className="onbNotice">
      <div className="onbErrorNotice" data-testid="onboarding-error-notice" data-error-code={error.code ?? undefined}>
        <p className="onboardingError" data-testid="onboarding-error">{error.message}</p>
        <div className="onbErrorMeta">
          {error.code ? <span className="onbErrorCode" data-testid="onboarding-error-code">Error code: {error.code}</span> : null}
          {error.correlationId ? <span className="onbErrorRef" data-testid="onboarding-error-ref">Reference: {error.correlationId}</span> : null}
        </div>
        {error.recoverable || error.suggestion ? (
          <div className="buttonRow onbErrorActions">
            {error.recoverable ? (
              <Button variant="secondary" onClick={onRetry} disabled={busy !== null} data-testid="onboarding-retry">
                {busy !== null ? 'Retrying…' : 'Retry'}
              </Button>
            ) : null}
            {error.suggestion ? (
              <Link href={error.suggestion.href} prefetch={false} className="btn btn-secondary" data-testid="onboarding-error-suggestion">
                {error.suggestion.label}
              </Link>
            ) : null}
          </div>
        ) : null}
      </div>
    </SurfaceCard>
  );
}

// Discovery gating failures (EOA / missing RPC / wrong chain) are returned inside the session
// snapshot (status 'partial' / 'failed'), not as an HTTP error. Map the canonical backend code
// to the same actionable UX so a normal wallet address explains itself instead of looking healthy.
function SessionErrorNotice({ session, onRetry, busy }: { session: OnboardingSession; onRetry: () => void; busy: Busy }) {
  const info = describeOnboardingError(session.error_code, session.error_message);
  return (
    <SurfaceCard className="onbNotice">
      <div className="onbErrorNotice" data-testid="onboarding-session-error" data-error-code={session.error_code ?? undefined}>
        <p className="onboardingError">{info.message}</p>
        <div className="onbErrorMeta">
          {info.code ? <span className="onbErrorCode">Error code: {info.code}</span> : null}
          {session.correlation_id ? <span className="onbErrorRef">Reference: {session.correlation_id}</span> : null}
        </div>
        <div className="buttonRow onbErrorActions">
          {info.recoverable ? (
            <Button variant="secondary" onClick={onRetry} disabled={busy !== null} data-testid="onboarding-session-retry">
              {busy === 'retry' ? 'Retrying…' : 'Retry discovery'}
            </Button>
          ) : null}
          {info.suggestion ? (
            <Link href={info.suggestion.href} prefetch={false} className="btn btn-secondary" data-testid="onboarding-session-suggestion">
              {info.suggestion.label}
            </Link>
          ) : null}
        </div>
      </div>
    </SurfaceCard>
  );
}

function IntakeForm({ form, setForm, onCreate, busy, contractValid }: {
  form: any; setForm: (f: any) => void; onCreate: () => void; busy: Busy; contractValid: boolean;
}) {
  const canSubmit = contractValid && busy === null;
  return (
    <SurfaceCard className="onbCard">
      <div className="onbCardHead">
        <div>
          <p className="sectionEyebrow">AI Onboarding Agent</p>
          <h2 className="onbCardTitle">Automated infrastructure discovery and security configuration</h2>
        </div>
      </div>
      <p className="muted onbLead">
        Provide a contract address and one or more RPC endpoints. The agent verifies the chain, inspects deployed
        bytecode, detects standards, roles and capabilities, benchmarks providers, and drafts a monitoring workspace
        for your review.
      </p>
      <div className="onbForm">
        <label className="onbField">
          <span>Workspace name</span>
          <input value={form.workspace_name} onChange={(e) => setForm({ ...form, workspace_name: e.target.value })}
            placeholder="Acme Capital" data-testid="input-workspace-name" />
        </label>
        <div className="onbField">
          <span id="onb-field-network">Network</span>
          <Select
            testId="input-chain"
            ariaLabelledBy="onb-field-network"
            value={String(form.chain_id)}
            onValueChange={(v) => setForm({ ...form, chain_id: Number(v) })}
            options={CHAINS.map((c) => ({ value: String(c.id), label: c.label }))}
          />
        </div>
        <label className="onbField onbFieldWide">
          <span>Primary contract address <em className="req">required</em></span>
          <input value={form.primary_contract} onChange={(e) => setForm({ ...form, primary_contract: e.target.value })}
            placeholder="0x…" spellCheck={false} data-testid="input-contract"
            data-valid={form.primary_contract === '' ? undefined : contractValid} />
          {form.primary_contract !== '' && !contractValid ? (
            <span className="onbFieldError">Enter a valid 0x-prefixed 40-hex-character address.</span>
          ) : null}
        </label>
        <label className="onbField onbFieldWide">
          <span>RPC endpoints <em className="hint">one per line — keys are encrypted and never stored in the clear</em></span>
          <textarea value={form.rpc_endpoints} onChange={(e) => setForm({ ...form, rpc_endpoints: e.target.value })}
            placeholder="https://…" rows={3} spellCheck={false} data-testid="input-rpc" />
        </label>
        <div className="onbField">
          <span id="onb-field-mode">Monitoring mode</span>
          <Select
            testId="input-mode"
            ariaLabelledBy="onb-field-mode"
            value={form.monitoring_mode}
            onValueChange={(v) => setForm({ ...form, monitoring_mode: v })}
            options={MODES.map((m) => ({ value: m.id, label: m.label }))}
          />
          <span className="onbFieldHint">{MODES.find((m) => m.id === form.monitoring_mode)?.detail}</span>
        </div>
        <label className="onbField">
          <span>Protocol name <em className="hint">optional</em></span>
          <input value={form.protocol_name} onChange={(e) => setForm({ ...form, protocol_name: e.target.value })} placeholder="e.g. Acme RWA" />
        </label>
      </div>
      <div className="buttonRow">
        <Button variant="primary" onClick={onCreate} disabled={!canSubmit} data-testid="btn-create">
          {busy === 'create' ? 'Starting discovery…' : 'Run Automated Discovery'}
        </Button>
      </div>
    </SurfaceCard>
  );
}

function AgentTimeline({ snapshot, expanded, setExpanded, onRetry, busy }: {
  snapshot: OnboardingSnapshot; expanded: Record<string, boolean>;
  setExpanded: (fn: any) => void; onRetry: () => void; busy: Busy;
}) {
  const hasFailure = snapshot.steps.some((s) => s.status === 'failed');
  return (
    <SurfaceCard className="onbCard">
      <div className="onbCardHead">
        <div>
          <p className="sectionEyebrow">AI Onboarding Agent</p>
          <h2 className="onbCardTitle">Automated infrastructure discovery and security configuration</h2>
        </div>
        {hasFailure ? (
          <Button variant="secondary" onClick={onRetry} disabled={busy !== null} data-testid="btn-retry">
            {busy === 'retry' ? 'Retrying…' : 'Retry failed steps'}
          </Button>
        ) : null}
      </div>
      <ol className="onbTimeline" data-testid="agent-timeline">
        {snapshot.steps.map((step) => {
          const key = step.step_key;
          const isOpen = !!expanded[key];
          const hasEvidence = step.evidence && Object.keys(step.evidence).length > 0;
          return (
            <li key={key} className="onbTimelineItem" data-step={key} data-status={step.status}>
              <span className={`onbDot onbDot-${step.status}`} aria-hidden="true">
                {step.status === 'completed' ? '✓' : step.status === 'failed' ? '✕' : step.status === 'needs_attention' ? '!' : ''}
              </span>
              <div className="onbTimelineBody">
                <div className="onbTimelineTop">
                  <span className="onbStepTitle">{step.title}</span>
                  <StatusPill label={step.status.replace('_', ' ')} variant={stepVariant(step.status)} />
                </div>
                {step.result_summary ? <p className="onbStepSummary">{step.result_summary}</p> : null}
                {step.error_message ? <p className="onbStepError" data-testid="step-error">{step.error_message}</p> : null}
                <div className="onbStepMeta">
                  <span>Start {fmtTime(step.started_at)}</span>
                  <span>Done {fmtTime(step.completed_at)}</span>
                  {hasEvidence ? (
                    <button type="button" className="onbLinkBtn" data-testid="evidence-toggle"
                      onClick={() => setExpanded((e: any) => ({ ...e, [key]: !e[key] }))}>
                      {isOpen ? 'Hide evidence' : 'Show evidence'}
                    </button>
                  ) : null}
                </div>
                {isOpen && hasEvidence ? (
                  <pre className="onbEvidence" data-testid="evidence-body">{JSON.stringify(step.evidence, null, 2)}</pre>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </SurfaceCard>
  );
}

const SUMMARY_FIELDS: Array<{ type: string; label: string }> = [
  { type: 'network', label: 'Network' },
  { type: 'chain_id', label: 'Chain ID' },
  { type: 'token_standard', label: 'Contract type' },
  { type: 'token_symbol', label: 'Token symbol' },
  { type: 'token_decimals', label: 'Decimals' },
  { type: 'total_supply', label: 'Total supply' },
  { type: 'proxy_type', label: 'Proxy type' },
  { type: 'implementation_address', label: 'Implementation' },
  { type: 'owner_address', label: 'Owner / admin' },
  { type: 'access_model', label: 'Access model' },
  { type: 'oracle_dependency', label: 'Oracle dependency' },
];

const CAPABILITY_FIELDS = ['mint_capability', 'burn_capability', 'pausable', 'upgrade_capability', 'blacklist_capability', 'freeze_capability'];

function DiscoverySummary({ findings }: { findings: OnboardingFinding[] }) {
  const caps = CAPABILITY_FIELDS.map((t) => findValue(findings, t)).filter(Boolean) as OnboardingFinding[];
  return (
    <SurfaceCard className="onbCard">
      <div className="onbCardHead"><div><p className="sectionEyebrow">Discovery</p><h2 className="onbCardTitle">Verified findings</h2></div></div>
      <div className="onbSummaryGrid" data-testid="discovery-summary">
        {SUMMARY_FIELDS.map(({ type, label }) => {
          const f = findValue(findings, type);
          if (!f) return null;
          return (
            <div key={type} className="onbSummaryCell" data-finding={type}>
              <span className="onbSummaryLabel">{label}</span>
              <span className="onbSummaryValue" title={String(f.value)}>{formatVal(f.value)}</span>
              <StatusPill label={f.confidence.replace('_', ' ')} variant={confidenceVariant(f.confidence)} />
            </div>
          );
        })}
      </div>
      {caps.length > 0 ? (
        <div className="onbCaps">
          <span className="onbSummaryLabel">Detected capabilities</span>
          <div className="onbCapRow">
            {caps.map((c) => <StatusPill key={c.finding_type} label={`${c.value} · ${c.confidence}`} variant={confidenceVariant(c.confidence)} />)}
          </div>
        </div>
      ) : null}
    </SurfaceCard>
  );
}

function formatVal(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.join(', ');
  const s = String(value);
  if (HEX_ADDRESS.test(s)) return `${s.slice(0, 10)}…${s.slice(-6)}`;
  return s;
}

function RpcBenchmark({ snapshot, onBenchmark, busy }: { snapshot: OnboardingSnapshot; onBenchmark: () => void; busy: Busy }) {
  const run = snapshot.benchmark.run;
  const results = snapshot.benchmark.results;
  return (
    <SurfaceCard className="onbCard">
      <div className="onbCardHead">
        <div><p className="sectionEyebrow">Infrastructure</p><h2 className="onbCardTitle">RPC provider benchmark</h2></div>
        <Button variant="secondary" onClick={onBenchmark} disabled={busy !== null} data-testid="btn-benchmark">
          {busy === 'benchmark' ? 'Re-testing…' : 'Re-test providers'}
        </Button>
      </div>
      {run?.explanation ? <p className="onbExplain" data-testid="rpc-explanation">{run.explanation}</p> : null}
      <div className="onbTableScroll">
        <TableShell headers={['Provider', 'Status', 'Median', 'P95', 'Errors', 'Latest block', 'Lag', 'Chain', 'Recommendation']} compact>
          {results.map((r: BenchmarkResult) => (
            <tr key={r.endpoint_host} data-testid="rpc-row" data-recommendation={r.recommendation}>
              <td title={r.redacted_url ?? undefined}>{r.endpoint_host}</td>
              <td>{r.connection_status}</td>
              <td>{r.median_latency_ms != null ? `${r.median_latency_ms} ms` : '—'}</td>
              <td>{r.p95_latency_ms != null ? `${r.p95_latency_ms} ms` : '—'}</td>
              <td>{r.error_rate != null ? `${Math.round(r.error_rate * 100)}%` : '—'}</td>
              <td>{r.latest_block ?? '—'}</td>
              <td>{r.block_lag != null ? r.block_lag : '—'}</td>
              <td>{r.chain_id_ok ? '✓' : (r.chain_id_returned ?? '✕')}</td>
              <td><StatusPill label={r.recommendation} variant={recommendationVariant(r.recommendation)} /></td>
            </tr>
          ))}
        </TableShell>
      </div>
    </SurfaceCard>
  );
}

function ProposalReview({ snapshot, onApprove, onActivate, busy }: {
  snapshot: OnboardingSnapshot; onApprove: () => void; onActivate: () => void; busy: Busy;
}) {
  const p = snapshot.proposal!;
  const prop = p.proposal ?? {};
  const s = snapshot.session.status;
  const rules: any[] = prop.baseline_rules ?? [];
  const limitations: string[] = prop.limitations ?? [];
  const review: any[] = prop.findings_requiring_review ?? [];
  const canActivate = p.approved && (s === 'approved' || s === 'activating');
  return (
    <SurfaceCard className="onbCard">
      <div className="onbCardHead">
        <div><p className="sectionEyebrow">Review &amp; Secure</p><h2 className="onbCardTitle">Proposed workspace configuration</h2></div>
        <StatusPill label={p.approved ? `Approved v${p.version}` : `Draft v${p.version}`} variant={p.approved ? 'success' : 'warning'} />
      </div>
      {p.ai_summary ? <p className="onbExplain" data-testid="ai-summary">{p.ai_summary}</p> : null}

      <div className="onbApprovalGrid" data-testid="approval-summary">
        <ProposalStat label="Assets to create" value={prop.protected_assets?.length ?? 0} />
        <ProposalStat label="Monitoring targets" value={prop.monitoring_targets?.length ?? 0} />
        <ProposalStat label="Rules to enable" value={rules.filter((r) => r.enabled).length} />
        <ProposalStat label="Event subscriptions" value={prop.event_subscriptions?.length ?? 0} />
        <ProposalStat label="Primary RPC" value={prop.rpc_sources?.primary_host ?? '—'} />
        <ProposalStat label="Fallback RPC" value={prop.rpc_sources?.fallback_host ?? '—'} />
      </div>

      <div className="onbTwoCol">
        <div>
          <p className="onbSubhead">Baseline monitoring rules</p>
          <ul className="onbRuleList" data-testid="rule-list">
            {rules.map((r) => (
              <li key={r.key}>
                <StatusPill label={r.severity} variant={r.severity === 'critical' ? 'danger' : r.severity === 'high' ? 'warning' : 'info'} />
                <span className="onbRuleTitle">{r.title}</span>
                {!r.enabled ? <span className="muted"> (disabled)</span> : null}
              </li>
            ))}
          </ul>
        </div>
        <div>
          {review.length > 0 ? (
            <>
              <p className="onbSubhead">Findings requiring review</p>
              <ul className="onbReviewList" data-testid="review-findings">
                {review.map((f, i) => (
                  <li key={i}><StatusPill label={(f.confidence ?? '').replace('_', ' ')} variant="warning" /> {f.finding_type}: {formatVal(f.value)}</li>
                ))}
              </ul>
            </>
          ) : null}
          {limitations.length > 0 ? (
            <>
              <p className="onbSubhead">Known limitations</p>
              <ul className="onbLimitList" data-testid="limitations">{limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
            </>
          ) : null}
        </div>
      </div>

      <div className="buttonRow onbApproveRow">
        {!p.approved ? (
          <Button variant="primary" onClick={onApprove} disabled={busy !== null} data-testid="btn-approve">
            {busy === 'approve' ? 'Recording approval…' : 'Approve configuration'}
          </Button>
        ) : null}
        <Button variant="primary" onClick={onActivate} disabled={!canActivate || busy !== null} data-testid="btn-activate">
          {busy === 'activate' ? 'Activating…' : 'Activate Protection'}
        </Button>
        {!p.approved ? <span className="onbApproveHint">Activation is disabled until the proposal is approved.</span> : null}
      </div>
    </SurfaceCard>
  );
}

function ProposalStat({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="onbProposalStat">
      <span className="onbSummaryLabel">{label}</span>
      <span className="onbProposalValue">{formatVal(value)}</span>
    </div>
  );
}

function ProtectionActive({ snapshot }: { snapshot: OnboardingSnapshot }) {
  const p = snapshot.proposal?.proposal ?? {};
  const summary = snapshot.proposal?.summary ?? {};
  return (
    <SurfaceCard className="onbCard onbProtected">
      <div className="onbProtectedHead">
        <span className="onbProtectedIcon" aria-hidden="true">✓</span>
        <div>
          <h2 className="onbCardTitle">Protection Active</h2>
          <p className="muted">Your monitoring workspace is live and provisioning coverage.</p>
        </div>
      </div>
      <div className="onbApprovalGrid">
        <ProposalStat label="Assets protected" value={(p.protected_assets?.length) ?? summary.assets_to_create ?? 0} />
        <ProposalStat label="Monitoring sources" value={(p.monitoring_targets?.length) ?? summary.targets_to_create ?? 0} />
        <ProposalStat label="Rules enabled" value={summary.rules_to_enable ?? 0} />
        <ProposalStat label="Coverage" value="Provisioning" />
      </div>
      <div className="buttonRow">
        <Link href="/dashboard" prefetch={false} className="btn btn-primary" data-testid="btn-dashboard">Open Security Dashboard</Link>
        <Link href="/monitored-systems" prefetch={false} className="btn btn-secondary">View Monitored Systems</Link>
      </div>
    </SurfaceCard>
  );
}

function AgentPanel({ snapshot, streamStatus, busy, onDiscover, onBenchmark, onApprove, onActivate, onExport, ask, setAsk }: {
  snapshot: OnboardingSnapshot | null; streamStatus: StreamStatus; busy: Busy;
  onDiscover: () => void; onBenchmark: () => void; onApprove: () => void; onActivate: () => void; onExport: () => void;
  ask: string; setAsk: (v: string) => void;
}) {
  const s = snapshot?.session;
  const agent = snapshot?.agent;
  // Truthful, canonical view: state label + current operation derived from backend
  // session + step state. A not-started draft never shows a fake "current operation",
  // and an unknown backend status is surfaced instead of silently reading as "Ready".
  const view = deriveAgentView(snapshot);
  const totalSteps = agent?.total_steps ?? 10;
  const completed = agent?.completed_steps ?? 0;
  const answer = useMemo(() => groundedAnswer(ask, snapshot), [ask, snapshot]);

  return (
    <SurfaceCard className="onbCard onbAgentPanel">
      <div className="onbCardHead"><div><p className="sectionEyebrow">Onboarding Agent</p><h2 className="onbCardTitle">{view.stateLabel}</h2></div></div>

      {view.unknownStatus ? (
        <p className="onbWarn" data-testid="agent-unknown-status">
          Unrecognized session status “{s?.status}”. Showing the last known details; refresh to reconcile the timeline.
        </p>
      ) : null}

      {s ? (
        <>
          <div className="onbAgentState" data-testid="agent-state">
            <Row label="Current operation" value={view.currentOperation ?? '—'} />
            <Row label="Overall progress" value={`${completed}/${totalSteps} steps`} />
            <Row label="Confidence" value={confidenceLabel(agent)} />
            <Row label="Verified findings" value={agent?.verified_findings ?? 0} />
            <Row label="Findings to review" value={agent?.review_findings ?? 0} />
            <Row label="Est. remaining steps" value={Math.max(0, totalSteps - completed)} />
            <Row label="Live updates" value={streamStatus === 'live' ? 'SSE connected' : streamStatus === 'polling' ? 'Polling fallback' : 'Idle'} />
          </div>
          <div className="onbProgressBar" aria-hidden="true"><span style={{ width: `${Math.round((completed / totalSteps) * 100)}%` }} /></div>

          <div className="onbAgentActions" data-testid="agent-actions">
            {(s.status === 'draft' || s.status === 'partial') ? (
              <Button variant="primary" onClick={onDiscover} disabled={busy !== null} data-testid="btn-run-discovery">
                {busy === 'discover' ? 'Running…' : 'Run Automated Discovery'}
              </Button>
            ) : null}
            {snapshot!.benchmark.results.length > 0 ? (
              <Button variant="secondary" onClick={onBenchmark} disabled={busy !== null}>Re-test providers</Button>
            ) : null}
            {s.status === 'proposal_ready' && snapshot!.proposal && !snapshot!.proposal.approved ? (
              <Button variant="primary" onClick={onApprove} disabled={busy !== null}>Review &amp; approve</Button>
            ) : null}
            {snapshot!.proposal?.approved && s.status === 'approved' ? (
              <Button variant="primary" onClick={onActivate} disabled={busy !== null}>Apply configuration</Button>
            ) : null}
            {snapshot!.findings.length > 0 ? (
              <Button variant="ghost" onClick={onExport} disabled={busy !== null} data-testid="btn-export">
                {busy === 'report' ? 'Exporting…' : 'Export discovery report'}
              </Button>
            ) : null}
          </div>

          <div className="onbAsk">
            <label className="onbSummaryLabel" htmlFor="onb-ask">Ask about this setup</label>
            <input id="onb-ask" value={ask} onChange={(e) => setAsk(e.target.value)}
              placeholder="e.g. proxy, owner, rpc, oracle" data-testid="ask-input" />
            {ask ? <p className="onbAskAnswer" data-testid="ask-answer">{answer}</p> : (
              <p className="onbAskHint">Answers are grounded in the current discovery evidence — no speculation.</p>
            )}
          </div>
        </>
      ) : (
        <p className="muted">Start a discovery run to see live agent status, findings and provider recommendations here.</p>
      )}
    </SurfaceCard>
  );
}

function Row({ label, value }: { label: string; value: unknown }) {
  return <div className="onbAgentRow"><span>{label}</span><strong>{String(value)}</strong></div>;
}

function confidenceLabel(agent: OnboardingSnapshot['agent'] | undefined): string {
  if (!agent || agent.total_findings === 0) return '—';
  const ratio = agent.verified_findings / agent.total_findings;
  if (ratio >= 0.6) return 'High';
  if (ratio >= 0.3) return 'Medium';
  return 'Low';
}

// Grounded local responder — surfaces matching evidence from the current snapshot
// instead of generating free-form text (no chatbot, no speculation).
function groundedAnswer(query: string, snapshot: OnboardingSnapshot | null): string {
  if (!snapshot || !query.trim()) return '';
  const q = query.toLowerCase();
  const f = snapshot.findings;
  const match = (type: string) => f.find((x) => x.finding_type === type);
  if (q.includes('proxy') || q.includes('upgrade') || q.includes('implementation')) {
    const proxy = match('proxy_type'); const impl = match('implementation_address');
    if (proxy) return `Proxy: ${proxy.value} (${proxy.confidence}, via ${proxy.detection_method})${impl ? `; implementation ${formatVal(impl.value)}` : ''}.`;
  }
  if (q.includes('owner') || q.includes('admin') || q.includes('role')) {
    const o = match('owner_address'); const a = match('access_model');
    if (o || a) return `${o ? `Owner ${formatVal(o.value)} (${o.confidence}). ` : ''}${a ? `Access model: ${a.value} (${a.confidence}).` : ''}`.trim();
  }
  if (q.includes('rpc') || q.includes('provider') || q.includes('latency')) {
    return snapshot.benchmark.run?.explanation ?? 'No RPC benchmark has completed yet.';
  }
  if (q.includes('oracle')) {
    const o = match('oracle_dependency');
    return o ? `Oracle dependency: ${o.value} (${o.confidence}).` : 'No oracle finding recorded.';
  }
  if (q.includes('standard') || q.includes('token') || q.includes('erc')) {
    const t = match('token_standard');
    return t ? `Token standard: ${t.value} (${t.confidence}, ${t.detection_method}).` : 'No token standard detected.';
  }
  const hits = f.filter((x) => x.finding_type.includes(q) || String(x.value).toLowerCase().includes(q));
  if (hits.length) return hits.slice(0, 3).map((x) => `${x.finding_type}: ${formatVal(x.value)} (${x.confidence})`).join('; ');
  return 'No matching finding in the current discovery evidence.';
}
