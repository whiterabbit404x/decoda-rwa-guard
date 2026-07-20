// Shared types for the Monitoring Sources (Screen 4) page. Every field mirrors a
// canonical backend value from GET /api/monitoring/sources — the frontend never
// invents metrics; absent fields render as an honest "—" / "No live evidence".

export type SourceRow = {
  target_id: string;
  system_id?: string | null;
  name?: string;
  asset_id?: string | null;
  asset_name?: string | null;
  network?: string | null;
  chain_id?: number | string | null;
  address?: string | null;
  address_kind?: 'contract' | 'wallet' | null;
  provider?: string | null;
  primary_provider?: string | null;
  fallback_provider?: string | null;
  source_type?: string | null;
  status?: string | null;
  status_reason?: string | null;
  runtime_status?: string | null;
  latest_block?: number | null;
  block_lag?: number | null;
  median_latency_ms?: number | null;
  // P95 is only populated once enough measured samples exist. p95_insufficient=true
  // means the UI must show "Insufficient samples", never a fabricated number.
  p95_latency_ms?: number | null;
  p95_sample_count?: number | null;
  p95_insufficient?: boolean;
  // 'no_successful_samples' | 'insufficient_samples' | 'available'. A calculated P95
  // whose freshest successful sample is old (or whose provider is failing now) is
  // flagged historical so the UI never shows it as current provider health.
  p95_status?: string | null;
  p95_is_historical?: boolean;
  p95_last_sample_at?: string | null;
  error_rate?: number | null;
  timeout_rate?: number | null;
  last_poll_at?: string | null;
  last_heartbeat?: string | null;
  last_telemetry_at?: string | null;
  provider_checked_at?: string | null;
  routing?: 'primary' | 'fallback' | null;
  routing_explanation?: string | null;
  coverage_state?: string | null;
  evidence_source?: string | null;
  // Three SEPARATE Screen-4 signals, never conflated:
  //   provider health -> `status` / `health_status`
  //   coverage freshness -> `coverage_fresh` (coverage telemetry inside the window)
  //   evidence/event detection -> `event_detection`
  // A quiet wallet has coverage_fresh=true + event_detection='no_recent_events'; it is
  // Healthy / no recent events, never Degraded / no evidence.
  coverage_fresh?: boolean | null;
  event_detection?: 'events_detected' | 'no_recent_events' | 'none' | null;
  enabled?: boolean;
  monitoring_enabled?: boolean;
  // Deterministic engine output (aux signal; canonical status stays authoritative).
  health_score?: number | null;
  health_status?: 'healthy' | 'warning' | 'critical' | 'unknown' | null;
  has_live_evidence?: boolean;
  triggered_rules?: string[];
  is_oracle?: boolean;
};

export type MonitoredSystemRow = {
  id: string;
  asset_name?: string;
  target_name?: string;
  target_id?: string;
  is_enabled?: boolean;
  runtime_status?: string | null;
  last_heartbeat?: string | null;
  last_event_at?: string | null;
  coverage_reason?: string | null;
  freshness_status?: string | null;
  evidence_source?: string | null;
  system_type?: string | null;
  environment?: string | null;
};

export type ProviderHealthRow = {
  host: string;
  status?: string | null;
  latency_ms?: number | null;
  checked_at?: string | null;
  evidence_source?: string | null;
  target_count?: number;
};

export type ProviderHealthSummary = {
  providers: ProviderHealthRow[];
  healthy_count: number;
  degraded_count: number;
  unknown_count: number;
  total: number;
};

export type AgentRecommendation = { kind: string; detail: string; target_id?: string };

export type AgentActivity = {
  autonomous_actions_24h?: number | null;
  approvals_required?: number | null;
  last_optimization_at?: string | null;
};

export type AgentState = {
  state: string;
  healthy_providers: number;
  degraded_providers: number;
  missing_target_links: number;
  primary_provider?: string | null;
  recommended_fallback?: string | null;
  latest_routing_decision?: string | null;
  confidence: string;
  confidence_basis?: string | null;
  recommendations: AgentRecommendation[];
  activity?: AgentActivity | null;
  auto_routing_enabled?: boolean;
};

export type AgentDecision = {
  id: string;
  target_id?: string | null;
  system_id?: string | null;
  provider_id?: string | null;
  decision_type: string;
  triggered_rule?: string | null;
  status: string;
  approval_required?: boolean;
  confidence?: string | null;
  health_status?: string | null;
  health_score?: number | null;
  previous_route?: string | null;
  new_route?: string | null;
  correlation_id?: string | null;
  actor_type?: string | null;
  summary?: string | null;
  created_at?: string | null;
  executed_at?: string | null;
};

export type SourceSummary = {
  // `quiet` = healthy sources that are live-but-quiet (polling + coverage fresh, no
  // recent event) — an evidence/event-detection signal separate from provider health.
  source_health: { healthy: number; total: number; quiet?: number; health_pct: number | null; trend_24h: number | null };
  active_routes: { primary: number; fallback: number; changed_24h: number | null };
  // coverage_pct/fresh now carry LIVE semantics. configured = enabled monitored
  // systems; live_fresh = targets with fresh canonical live evidence; replay_only /
  // historical_available describe non-live evidence shown separately, never as live.
  telemetry_coverage: {
    coverage_pct: number | null;
    fresh: number;
    stale: number;
    eligible: number;
    configured?: number;
    live_fresh?: number;
    live_coverage_pct?: number | null;
    replay_only?: number;
    historical_available?: boolean;
  };
  oracle_heartbeats: { healthy: number; delayed: number; missed: number; total: number };
  agent_activity: AgentActivity;
};

export type SourceSettings = {
  auto_routing_enabled: boolean;
  failover_cooldown_seconds: number;
  route_recovery_seconds: number;
  thresholds: Record<string, number>;
  persisted: boolean;
  updated_at?: string | null;
};

export type SourcesPayload = {
  assets?: Array<{ id: string; name?: string }>;
  targets?: Array<{ id: string; name?: string }>;
  systems?: MonitoredSystemRow[];
  sources?: SourceRow[];
  provider_health?: ProviderHealthSummary | null;
  agent?: AgentState | null;
  summary?: SourceSummary | null;
  decisions?: AgentDecision[];
  settings?: SourceSettings | null;
  server_time?: string | null;
};

// ── Formatting helpers (pure) ────────────────────────────────────────────────
export function fmtRelative(value?: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  const diff = Date.now() - parsed.getTime();
  if (diff < 60_000) return `${Math.max(0, Math.floor(diff / 1000))}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return parsed.toLocaleDateString();
}

export function fmtExact(value?: string | null): string {
  if (!value) return 'No timestamp recorded';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'No timestamp recorded';
  return parsed.toLocaleString();
}

export function fmtLatency(value?: number | null): string {
  if (value == null) return '—';
  return `${Math.round(value).toLocaleString()} ms`;
}

export function fmtPercent(value?: number | null): string {
  if (value == null) return '—';
  return `${value.toFixed(1)}%`;
}

export function shortAddress(value?: string | null): string {
  if (!value) return '—';
  if (value.length <= 14) return value;
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

// Redact any secret material that must never render in the browser: an API key
// embedded in a path/query, or bare credentials. Only host-level identity remains.
export function redactEndpoint(value?: string | null): string {
  if (!value) return '—';
  try {
    const url = new URL(value.includes('://') ? value : `https://${value}`);
    return url.host; // host only — never path, query, or userinfo
  } catch {
    // Not a URL: strip anything after the first '/', '?', or '@'.
    return value.split(/[/?@]/)[0] || '—';
  }
}
