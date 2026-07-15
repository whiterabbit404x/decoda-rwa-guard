'use client';

// Client-side types + SSE/polling helpers for the Autonomous Onboarding Agent
// (Screen 1). Progress is NEVER simulated: the backend session snapshot is the
// single source of truth. SSE events are only "something changed" signals that
// trigger a real GET of the session; a polling fallback covers SSE downtime.

import { parseSseLine } from './alert-stream-client';

export type StepStatus = 'pending' | 'running' | 'completed' | 'needs_attention' | 'failed' | 'skipped';
export type Confidence = 'confirmed' | 'probable' | 'unknown' | 'requires_review';
export type SessionStatus =
  | 'draft' | 'discovering' | 'partial' | 'benchmarking'
  | 'proposal_ready' | 'approved' | 'activating' | 'completed' | 'failed';

export type OnboardingStep = {
  step_key: string;
  title: string;
  sequence: number;
  status: StepStatus;
  result_summary: string | null;
  evidence: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  attempts: number | null;
  started_at: string | null;
  completed_at: string | null;
};

export type OnboardingFinding = {
  finding_type: string;
  value: unknown;
  detection_method: string;
  confidence: Confidence;
  source_contract: string | null;
  block_number: number | null;
  rpc_source_host: string | null;
  evidence: Record<string, unknown>;
  evidence_hash: string | null;
  created_at: string | null;
};

export type BenchmarkResult = {
  endpoint_host: string;
  redacted_url: string | null;
  connection_status: string;
  median_latency_ms: number | null;
  p95_latency_ms: number | null;
  success_rate: number | null;
  error_rate: number | null;
  timeout_count: number | null;
  error_count: number | null;
  latest_block: number | null;
  block_lag: number | null;
  chain_id_returned: number | null;
  chain_id_ok: boolean | null;
  rate_limited: boolean | null;
  archive_supported: boolean | null;
  score: number | null;
  recommendation: 'primary' | 'fallback' | 'degraded' | 'rejected';
  reason: string | null;
};

export type OnboardingProposal = {
  version: number;
  proposal: Record<string, any> | null;
  summary: Record<string, any> | null;
  ai_summary: string | null;
  ai_available: boolean;
  approved: boolean;
};

export type OnboardingSession = {
  id: string;
  workspace_id: string;
  status: SessionStatus;
  current_step: string | null;
  selected_chain_id: number | null;
  chain_network: string | null;
  primary_contract: string | null;
  protocol_name: string | null;
  monitoring_mode: string;
  workspace_name: string | null;
  proposal_version: number;
  activation_status: string;
  error_code: string | null;
  error_message: string | null;
  correlation_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
};

export type OnboardingSnapshot = {
  session: OnboardingSession;
  steps: OnboardingStep[];
  findings: OnboardingFinding[];
  benchmark: { run: any | null; results: BenchmarkResult[] };
  proposal: OnboardingProposal | null;
  approvals: Array<{ proposal_version: number; decision: string; notes: string | null; created_at: string | null }>;
  inputs: Array<{ input_type: string; value: string | null; endpoint_host: string | null; label: string | null }>;
  agent: {
    verified_findings: number;
    review_findings: number;
    total_findings: number;
    total_steps: number;
    completed_steps: number;
  };
  warnings?: string[];
};

export type StreamStatus = 'live' | 'polling' | 'disconnected';

export const ACTIVE_STATUSES: SessionStatus[] = ['discovering', 'benchmarking', 'activating'];

// ---------------------------------------------------------------------------
// Structured, customer-safe error taxonomy.
//
// The backend returns machine-readable error codes (snake_case in discovery step
// results, and the documented SCREAMING_SNAKE taxonomy for API-level failures). The
// browser must NEVER show a raw exception, an HTML error page, or a transport-level
// "Failed to fetch" string. describeOnboardingError() maps a code to an actionable,
// fail-closed message plus an optional recovery affordance.
// ---------------------------------------------------------------------------
export const MONITORING_SOURCES_ROUTE = '/monitoring-sources';
export const INTEGRATIONS_ROUTE = '/integrations';

// Shown whenever the browser fetch itself rejects (offline, DNS, proxy down, aborted)
// or the same-origin proxy cannot reach the backend — never the raw TypeError.
export const ONBOARDING_TRANSPORT_MESSAGE =
  'Decoda could not reach the onboarding service. Please retry shortly.';

export type OnboardingErrorInfo = {
  code: string | null;
  message: string;
  recoverable: boolean;
  correlationId?: string | null;
  suggestion?: { label: string; href: string } | null;
};

// Error thrown by the onboarding client's request helper. Carries the resolved,
// customer-safe OnboardingErrorInfo so callers never re-derive a message from a raw
// Error string. `silent` marks failures already surfaced by dedicated UI (401 →
// session-expired card) so the generic error banner is not double-rendered.
export class OnboardingRequestError extends Error {
  readonly info: OnboardingErrorInfo;
  readonly silent: boolean;
  constructor(info: OnboardingErrorInfo, silent = false) {
    super(info.message);
    this.name = 'OnboardingRequestError';
    this.info = info;
    this.silent = silent;
  }
}

// Detects browser-level transport failures (the classic "Failed to fetch" TypeError)
// so they can be converted into ONBOARDING_TRANSPORT_MESSAGE instead of leaking.
export function isTransportError(err: unknown): boolean {
  if (err instanceof TypeError) return true;
  const msg = (err instanceof Error ? err.message : String(err ?? '')).toLowerCase();
  return (
    msg.includes('failed to fetch') ||
    msg.includes('load failed') ||
    msg.includes('networkerror') ||
    msg.includes('network request failed')
  );
}

function safeBackendMessage(message: string | null | undefined): string | null {
  const normalized = (message ?? '').trim();
  if (!normalized) return null;
  // Never surface raw JSON blobs, HTML documents, or Python tracebacks to the customer.
  if (normalized.startsWith('{') || normalized.startsWith('[') || normalized.startsWith('<')) return null;
  if (normalized.toLowerCase().includes('traceback')) return null;
  return normalized;
}

export function describeOnboardingError(
  code: string | null | undefined,
  backendMessage?: string | null,
): OnboardingErrorInfo {
  const normalized = (code ?? '').trim().toLowerCase();
  const safe = safeBackendMessage(backendMessage);
  switch (normalized) {
    case 'address_required':
    case 'invalid_address_format':
    case 'invalid_address':
      return { code: 'INVALID_ADDRESS', recoverable: true,
        message: 'Enter a valid 0x-prefixed, 40-character EVM contract address.' };
    case 'zero_address':
      return { code: 'ZERO_ADDRESS', recoverable: true,
        message: 'The zero address is not a valid contract. Enter the deployed contract address.' };
    case 'no_deployed_contract':
    case 'no_contract_bytecode':
      return { code: 'NO_CONTRACT_BYTECODE', recoverable: false,
        message: 'No smart contract was found at this address. It appears to be a wallet account. Add it through Monitoring Sources to monitor wallet transfers.',
        suggestion: { label: 'Go to Monitoring Sources', href: MONITORING_SOURCES_ROUTE } };
    case 'no_rpc_endpoint':
    case 'rpc_not_configured':
      return { code: 'RPC_NOT_CONFIGURED', recoverable: true,
        message: 'No RPC provider is configured for this network. Add an RPC endpoint above, or configure one in Integrations.',
        suggestion: { label: 'Open Integrations', href: INTEGRATIONS_ROUTE } };
    case 'chain_mismatch':
    case 'rpc_chain_mismatch':
    case 'wrong_chain':
      return { code: 'RPC_CHAIN_MISMATCH', recoverable: true,
        message: 'This contract was not found on the selected network. Check the selected network, or the chain your RPC endpoint serves.' };
    case 'invalid_chain_response':
    case 'rpc_unreachable':
    case 'rpc_unavailable':
      return { code: 'RPC_UNAVAILABLE', recoverable: true,
        message: 'Decoda could not reach an RPC provider for this network. Please retry shortly.' };
    case 'unauthenticated':
    case 'authentication_required':
      return { code: 'AUTHENTICATION_REQUIRED', recoverable: false,
        message: 'Your session has expired. Sign in again to continue.' };
    case 'workspace_access_denied':
      return { code: 'WORKSPACE_ACCESS_DENIED', recoverable: false,
        message: 'You do not have access to this workspace.' };
    case 'discovery_already_running':
      return { code: 'DISCOVERY_ALREADY_RUNNING', recoverable: true,
        message: 'Discovery is already running for this workspace. Please wait for it to finish.' };
    case 'rate_limited':
      return { code: 'RATE_LIMITED', recoverable: true,
        message: 'Too many requests. Please wait a moment and try again.' };
    case 'backend_unreachable':
    case 'backend_timeout':
      return { code: 'RPC_UNAVAILABLE', recoverable: true, message: ONBOARDING_TRANSPORT_MESSAGE };
    case 'invalid_runtime_config':
      return { code: 'INTERNAL_ERROR', recoverable: false,
        message: 'The onboarding service is not configured for this deployment. Please contact support.' };
    default:
      return {
        code: normalized ? normalized.toUpperCase() : 'INTERNAL_ERROR',
        recoverable: true,
        message: safe ?? 'Something went wrong while starting discovery. Please retry.',
      };
  }
}

// The 5-phase progress header maps to the 10 backend steps.
export const PHASES: Array<{ key: string; label: string; steps: string[] }> = [
  { key: 'connect', label: 'Connect Workspace', steps: ['validate_inputs', 'connect_chain'] },
  { key: 'detect', label: 'Detect Assets', steps: ['verify_bytecode', 'detect_standard', 'resolve_proxy', 'discover_roles', 'discover_oracles'] },
  { key: 'configure', label: 'Configure Monitoring', steps: ['benchmark_rpc', 'generate_policies', 'create_config'] },
  { key: 'review', label: 'Review & Secure', steps: [] },
  { key: 'protected', label: "You're Protected", steps: [] },
];

export type PhaseStatus = 'pending' | 'running' | 'completed' | 'needs_attention' | 'failed';

export function derivePhaseStatuses(snapshot: OnboardingSnapshot | null): Array<{ label: string; status: PhaseStatus }> {
  const byKey = new Map((snapshot?.steps ?? []).map((s) => [s.step_key, s.status]));
  const session = snapshot?.session;
  return PHASES.map((phase) => {
    if (phase.key === 'review') {
      let status: PhaseStatus = 'pending';
      if (session) {
        if (['completed'].includes(session.status)) status = 'completed';
        else if (session.status === 'approved' || session.status === 'activating') status = 'completed';
        else if (session.status === 'proposal_ready') status = 'running';
      }
      return { label: phase.label, status };
    }
    if (phase.key === 'protected') {
      return { label: phase.label, status: session?.status === 'completed' ? 'completed' : 'pending' };
    }
    const statuses = phase.steps.map((k) => byKey.get(k) ?? 'pending');
    let status: PhaseStatus = 'pending';
    if (statuses.some((s) => s === 'failed')) status = 'failed';
    else if (statuses.some((s) => s === 'running')) status = 'running';
    else if (statuses.every((s) => s === 'completed')) status = 'completed';
    else if (statuses.some((s) => s === 'needs_attention') && statuses.every((s) => s === 'completed' || s === 'needs_attention')) status = 'needs_attention';
    else if (statuses.some((s) => s === 'completed' || s === 'needs_attention')) status = 'running';
    return { label: phase.label, status };
  });
}

export function confidenceVariant(confidence: Confidence): 'success' | 'info' | 'warning' | 'neutral' {
  switch (confidence) {
    case 'confirmed': return 'success';
    case 'probable': return 'info';
    case 'requires_review': return 'warning';
    default: return 'neutral';
  }
}

export function stepVariant(status: StepStatus): 'success' | 'info' | 'warning' | 'danger' | 'neutral' {
  switch (status) {
    case 'completed': return 'success';
    case 'running': return 'info';
    case 'needs_attention': return 'warning';
    case 'failed': return 'danger';
    default: return 'neutral';
  }
}

export function recommendationVariant(rec: BenchmarkResult['recommendation']): 'success' | 'info' | 'warning' | 'danger' {
  switch (rec) {
    case 'primary': return 'success';
    case 'fallback': return 'info';
    case 'degraded': return 'warning';
    default: return 'danger';
  }
}

// Connect to the onboarding SSE stream through the same-origin proxy
// (/api/onboarding/sessions/{id}/events). The browser never opens a cross-origin
// EventSource against the backend. Returns a disconnect function. On every server
// event (or heartbeat) the caller's callbacks fire; the caller refetches the
// authoritative session snapshot rather than trusting event bodies.
export function connectOnboardingStream(
  sessionId: string,
  headers: Record<string, string>,
  callbacks: { onEvent: () => void; onStatus: (s: StreamStatus) => void },
): () => void {
  const abort = new AbortController();
  let closed = false;

  async function loop(): Promise<void> {
    while (!closed) {
      let response: Response;
      try {
        response = await fetch(`/api/onboarding/sessions/${encodeURIComponent(sessionId)}/events`, {
          headers: { ...headers, Accept: 'text/event-stream', 'Cache-Control': 'no-cache' },
          signal: abort.signal,
          cache: 'no-store',
        });
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return;
        callbacks.onStatus('polling');
        await delay(3000, abort.signal);
        continue;
      }
      if (!response.ok || !response.body) {
        // 503 => stream backend unavailable; caller relies on polling.
        callbacks.onStatus('polling');
        await delay(4000, abort.signal);
        continue;
      }
      callbacks.onStatus('live');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        while (!closed) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let idx: number;
          while ((idx = buffer.indexOf('\n')) !== -1) {
            const rawLine = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 1);
            const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
            if (line === '') continue;
            const parsed = parseSseLine(line);
            if (!parsed) continue;
            if (parsed.field === 'data') callbacks.onEvent();
          }
        }
      } catch {
        // fallthrough to reconnect
      } finally {
        try { reader.releaseLock(); } catch { /* already released */ }
      }
      if (!closed) {
        callbacks.onStatus('polling');
        await delay(3000, abort.signal);
      }
    }
  }

  void loop();
  return () => { closed = true; abort.abort(); };
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
  });
}
