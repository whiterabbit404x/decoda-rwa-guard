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

// Connect to the backend SSE stream. Returns a disconnect function. On every
// server event (or heartbeat) the caller's callbacks fire; the caller refetches
// the authoritative session snapshot rather than trusting event bodies.
export function connectOnboardingStream(
  apiUrl: string,
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
        response = await fetch(`${apiUrl}/api/onboarding/sessions/${sessionId}/events`, {
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
