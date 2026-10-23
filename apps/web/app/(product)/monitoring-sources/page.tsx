'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { connectAlertStream, type AlertStreamStatus } from '../../alert-stream-client';
import {
  EmptyStateBlocker,
  StatusPill,
  TabStrip,
  TableShell,
  type PillVariant,
} from '../../components/ui-primitives';
import { usePilotAuth } from '../../pilot-auth-context';
import { useRuntimeSummary } from '../../runtime-summary-context';
import { SourceOptimizationAgentPanel } from './agent-panel';
import { DecisionEvidenceDrawer, SourceDetailDrawer } from './detail-drawer';
import { SummaryFigures } from './summary-cards';
import { PageHeader } from '../../components/page-header';
import {
  fmtExact,
  fmtLatency,
  fmtRelative,
  shortAddress,
  type AgentDecision,
  type AgentState,
  type MonitoredSystemRow,
  type ProviderHealthSummary,
  type SourceRow,
  type SourceSettings,
  type SourceSummary,
  type SourcesPayload,
} from './source-types';

type TabKey = 'targets' | 'systems';

const PAGE_SIZE = 8;
const POLL_INTERVAL_MS = 30_000;

// ── Truthful status/badge helpers ────────────────────────────────────────────
const SOURCE_STATUS_LABELS: Record<string, { label: string; variant: PillVariant }> = {
  healthy: { label: 'Healthy', variant: 'success' },
  provisioning: { label: 'Provisioning', variant: 'info' },
  warning: { label: 'Warning', variant: 'warning' },
  degraded: { label: 'Degraded', variant: 'warning' },
  failed: { label: 'Critical', variant: 'danger' },
  critical: { label: 'Critical', variant: 'danger' },
  // Latest scheduled provider observation failed (all RPC providers unavailable).
  provider_unavailable: { label: 'Provider Unavailable', variant: 'danger' },
  // Prior failure now followed by successful poll(s), before the required streak.
  recovering: { label: 'Recovering', variant: 'warning' },
  failover_active: { label: 'Failover Active', variant: 'warning' },
  paused: { label: 'Paused', variant: 'neutral' },
  disabled: { label: 'Disabled', variant: 'neutral' },
  missing_configuration: { label: 'Configuration Required', variant: 'danger' },
  unknown: { label: 'Unknown', variant: 'neutral' },
};

function sourceStatusBadge(status?: string | null): { label: string; variant: PillVariant } {
  const key = (status ?? '').trim().toLowerCase();
  return SOURCE_STATUS_LABELS[key] ?? { label: 'Unknown', variant: 'neutral' };
}

// Reasons that mean "live but quiet" (provider poll + coverage telemetry succeeded, no
// wallet/transfer event this window). These are NOT failure reasons and must never be
// rendered under a Degraded pill — the row reads "Healthy / No recent events".
const QUIET_STATUS_REASONS = new Set(['live_no_recent_events', 'no_events_detected_yet', 'no_evidence']);

function isQuietReason(reason?: string | null): boolean {
  return QUIET_STATUS_REASONS.has((reason ?? '').trim().toLowerCase());
}

// Evidence/event-detection sub-line, kept SEPARATE from the provider-health pill so a
// quiet wallet reads "Healthy / No recent events" (neutral), never a failure. Returns
// null when there is no truthful event signal to show.
function eventDetectionLine(source: SourceRow): { label: string; muted: boolean } | null {
  const ev = (source.event_detection ?? '').trim().toLowerCase();
  if (ev === 'events_detected') return { label: 'Events detected', muted: false };
  if (ev === 'no_recent_events') return { label: 'No recent events', muted: true };
  return null;
}

function routingBadge(source: SourceRow): { label: string; variant: PillVariant } {
  const status = (source.status ?? '').trim().toLowerCase();
  // No operational route exists when the provider is unavailable / critical — the
  // routing state is "Unavailable", never "Standby" (Standby implies a valid, ready
  // configured route). A configured provider being down is not a standby route.
  if (status === 'provider_unavailable' || status === 'failed' || status === 'critical') {
    return { label: 'Unavailable', variant: 'danger' };
  }
  if (source.routing === 'primary') return { label: 'Primary', variant: 'info' };
  if (source.routing === 'fallback') return { label: 'Fallback', variant: 'warning' };
  if (!source.enabled && !source.monitoring_enabled) return { label: 'Disabled', variant: 'neutral' };
  return { label: 'Standby', variant: 'neutral' };
}

function healthScoreCell(source: SourceRow) {
  if (source.health_score == null) {
    return <span className="muted" title="No live health evidence received">No live evidence</span>;
  }
  const score = source.health_score;
  // Same bands as before (80 / 50); the tone class only colours the number and bar.
  const tone = score >= 80 ? 'good' : score >= 50 ? 'warn' : 'bad';
  return (
    <span className={`msrcScore msrcScore--${tone}`}>
      <span className="msrcScoreValue">{score.toFixed(0)}</span>
      <progress className="msrcScoreBar" aria-hidden max={100} value={Math.max(4, Math.min(100, score))} />
    </span>
  );
}

// ── Monitored Systems tab helpers (runtime-focused, fail-closed) ─────────────
function runtimeStatusPill(system: MonitoredSystemRow): { label: string; variant: PillVariant } {
  if (!system.is_enabled) return { label: 'Disabled', variant: 'neutral' };
  if (!system.last_heartbeat) return { label: 'Provisioning', variant: 'info' };
  const runtimeStatus = (system.runtime_status ?? '').toLowerCase();
  if (runtimeStatus === 'healthy' || runtimeStatus === 'reporting') return { label: 'Reporting', variant: 'success' };
  if (runtimeStatus === 'degraded') return { label: 'Degraded', variant: 'warning' };
  if (runtimeStatus === 'failed' || runtimeStatus === 'offline') return { label: 'Failed', variant: 'danger' };
  if (runtimeStatus === 'provisioning' || runtimeStatus === 'idle') return { label: 'Provisioning', variant: 'info' };
  return { label: 'Unknown', variant: 'neutral' };
}

function coveragePill(system: MonitoredSystemRow): { label: string; variant: PillVariant } {
  if (!system.is_enabled) return { label: 'Missing', variant: 'danger' };
  if (!system.last_heartbeat) return { label: 'Unknown', variant: 'neutral' };
  const coverageReason = (system.coverage_reason ?? '').toLowerCase();
  if (coverageReason === 'covered' || coverageReason === 'full') return { label: 'Covered', variant: 'success' };
  if (coverageReason === 'partial') return { label: 'Partial', variant: 'warning' };
  if (coverageReason === 'stale') return { label: 'Stale', variant: 'warning' };
  if (coverageReason === 'missing') return { label: 'Missing', variant: 'danger' };
  if (system.last_event_at) return { label: 'Partial', variant: 'warning' };
  return { label: 'Unknown', variant: 'neutral' };
}

function resolveEvidenceSource(system: MonitoredSystemRow): { label: string; variant: PillVariant } {
  const raw = (system.evidence_source ?? system.freshness_status ?? '').toLowerCase();
  if (raw === 'simulator' || raw === 'demo' || raw === 'replay') return { label: 'simulator', variant: 'info' };
  if (raw === 'live' || raw === 'live_provider') {
    if (!system.last_heartbeat || !system.last_event_at) return { label: 'none', variant: 'neutral' };
    return { label: 'live_provider', variant: 'success' };
  }
  return { label: 'none', variant: 'neutral' };
}

function systemType(system: MonitoredSystemRow): string {
  const raw = (system.system_type ?? '').toLowerCase();
  if (raw) return raw;
  const name = (system.asset_name ?? system.target_name ?? '').toLowerCase();
  if (name.includes('oracle')) return 'Oracle feed';
  if (name.includes('ws') || name.includes('websocket')) return 'WebSocket';
  if (name.includes('custod')) return 'Custodian API';
  return 'RPC endpoint';
}

// Seven columns a person scans: what is watched, where, its state, its health,
// when data last arrived, and how it is routed. Provider, role, P95 latency,
// block lag, error rate and heartbeat/poll ages are per-source evidence and
// live in the source's detail drawer (Details), which reads the same row.
const TARGET_HEADERS = [
  'Target / System', 'Network', 'Status', 'Health Score', 'Last Event', 'Routing', 'Actions',
];

const SYSTEM_HEADERS = [
  'System', 'Type', 'Environment', 'Provider', 'Status', 'Availability',
  'Response Time', 'Last Successful Check', 'Last Failure', 'Current Route', 'Actions',
];

const TABS = [
  { key: 'targets', label: 'Monitoring Targets' },
  { key: 'systems', label: 'Monitored Systems' },
];

type SortKey = 'name' | 'health' | 'latency' | 'lag' | 'error' | 'heartbeat';

function streamStatusPill(status: AlertStreamStatus): { label: string; variant: PillVariant } {
  switch (status) {
    case 'live': return { label: 'Live', variant: 'success' };
    case 'reconnecting': return { label: 'Reconnecting…', variant: 'warning' };
    case 'polling-fallback': return { label: 'Polling', variant: 'info' };
    default: return { label: 'Offline', variant: 'neutral' };
  }
}

export default function MonitoringSourcesPage() {
  const [activeTab, setActiveTab] = useState<TabKey>('targets');
  const [systems, setSystems] = useState<MonitoredSystemRow[]>([]);
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [providerHealth, setProviderHealth] = useState<ProviderHealthSummary | null>(null);
  const [agent, setAgent] = useState<AgentState | null>(null);
  const [summary, setSummary] = useState<SourceSummary | null>(null);
  const [decisions, setDecisions] = useState<AgentDecision[]>([]);
  const [settings, setSettings] = useState<SourceSettings | null>(null);
  const [assetsCount, setAssetsCount] = useState(0);
  const [targetsCount, setTargetsCount] = useState(0);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<AlertStreamStatus>('polling-fallback');

  // Filters / search / sort / pagination.
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [networkFilter, setNetworkFilter] = useState('all');
  const [providerFilter, setProviderFilter] = useState('all');
  const [routingFilter, setRoutingFilter] = useState('all');
  const [sortKey, setSortKey] = useState<SortKey>('health');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);

  // Action state.
  const [busyTargetId, setBusyTargetId] = useState<string | null>(null);
  const [autoRoutingBusy, setAutoRoutingBusy] = useState(false);
  const [healthCheckBusy, setHealthCheckBusy] = useState(false);
  const [healthCheckResult, setHealthCheckResult] = useState('');
  const [diagnosticBusy, setDiagnosticBusy] = useState(false);
  const [diagnosticResult, setDiagnosticResult] = useState('');
  const [selectedSource, setSelectedSource] = useState<SourceRow | null>(null);
  const [selectedDecision, setSelectedDecision] = useState<AgentDecision | null>(null);

  const { authHeaders, refreshCsrfToken } = usePilotAuth();
  const { refresh: refreshRuntimeSummary } = useRuntimeSummary();

  const applyPayload = useCallback((payload: SourcesPayload) => {
    setSystems(payload.systems ?? []);
    setSources(payload.sources ?? []);
    setProviderHealth(payload.provider_health ?? null);
    setAgent(payload.agent ?? null);
    setSummary(payload.summary ?? null);
    setDecisions(payload.decisions ?? []);
    setSettings(payload.settings ?? null);
    setAssetsCount((payload.assets ?? []).length);
    setTargetsCount((payload.targets ?? []).length);
    setLastRefreshed(payload.server_time ?? new Date().toISOString());
  }, []);

  const loadSources = useCallback(
    async (signal?: AbortSignal, opts?: { quiet?: boolean }) => {
      if (!opts?.quiet) setLoading(true);
      try {
        const response = await fetch('/api/monitoring/sources', { headers: authHeaders(), cache: 'no-store', signal });
        const payload: SourcesPayload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = (payload as { detail?: unknown }).detail;
          setLoadError(`Unable to load monitoring sources: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
          return;
        }
        applyPayload(payload);
        setLoadError('');
      } catch (error) {
        if ((error as { name?: string }).name === 'AbortError') return;
        setLoadError(`Network error loading monitoring sources: ${error instanceof Error ? error.message : 'unknown error'}`);
      } finally {
        if (!opts?.quiet) setLoading(false);
      }
    },
    [authHeaders, applyPayload],
  );

  // Initial load + read tab from URL.
  useEffect(() => {
    const controller = new AbortController();
    void loadSources(controller.signal);
    if (typeof window !== 'undefined') {
      const tabParam = new URLSearchParams(window.location.search).get('tab');
      if (tabParam === 'systems' || tabParam === 'targets') setActiveTab(tabParam);
    }
    return () => controller.abort();
  }, [loadSources]);

  // Persist selected tab to the URL query string (no full navigation).
  const changeTab = useCallback((key: TabKey) => {
    setActiveTab(key);
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', key);
      window.history.replaceState(null, '', url.toString());
    }
  }, []);

  // Live updates via the shared Redis-backed SSE backbone; refetch canonical data
  // on each source event (replay-safe — the DB is the source of truth, so a
  // replayed event only triggers an idempotent refetch, never a duplicate record).
  const refetchRef = useRef(loadSources);
  refetchRef.current = loadSources;
  useEffect(() => {
    let debounce: ReturnType<typeof setTimeout> | null = null;
    const disconnect = connectAlertStream(
      authHeaders(),
      {
        onConnected: () => setStreamStatus('live'),
        onHeartbeat: () => undefined,
        onStatusChange: (status) => setStreamStatus(status),
        onEvent: (event) => {
          const payload = event.payload as { type?: string } | null;
          if (payload && payload.type === 'source') {
            if (debounce) clearTimeout(debounce);
            debounce = setTimeout(() => void refetchRef.current(undefined, { quiet: true }), 400);
          }
        },
      },
      '/api/stream/sources',
    );
    return () => {
      if (debounce) clearTimeout(debounce);
      disconnect();
    };
  }, [authHeaders]);

  // Polling fallback: only poll when the live stream is NOT connected.
  useEffect(() => {
    if (streamStatus === 'live') return;
    const id = setInterval(() => void loadSources(undefined, { quiet: true }), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [streamStatus, loadSources]);

  const refreshAll = useCallback(async () => {
    await loadSources();
    await refreshRuntimeSummary().catch(() => undefined);
  }, [loadSources, refreshRuntimeSummary]);

  // ── Mutations ───────────────────────────────────────────────
  async function mutate(url: string, method: 'POST' | 'PUT', body?: unknown): Promise<Response> {
    await refreshCsrfToken().catch(() => undefined);
    return fetch(url, {
      method,
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  async function handleToggleAutoRouting() {
    if (!settings) return;
    setAutoRoutingBusy(true);
    setActionError('');
    try {
      const response = await mutate('/api/monitoring/sources/settings', 'PUT', {
        auto_routing_enabled: !settings.auto_routing_enabled,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = (payload as { detail?: unknown }).detail;
        setActionError(`Auto-Routing update failed: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
        return;
      }
      if ((payload as { settings?: SourceSettings }).settings) {
        setSettings((payload as { settings: SourceSettings }).settings);
      }
      await loadSources(undefined, { quiet: true });
    } catch (error) {
      setActionError(`Network error updating Auto-Routing: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setAutoRoutingBusy(false);
    }
  }

  async function handleRunHealthCheck() {
    setHealthCheckBusy(true);
    setActionError('');
    setHealthCheckResult('');
    try {
      const response = await mutate('/api/monitoring/sources/health-check', 'POST');
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = (payload as { detail?: unknown }).detail;
        setActionError(`Health check failed: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
        return;
      }
      const p = payload as { sources_evaluated?: number; criticals?: number; warnings?: number };
      setHealthCheckResult(
        `Health check evaluated ${p.sources_evaluated ?? 0} source(s): ${p.criticals ?? 0} critical, ${p.warnings ?? 0} warning.`,
      );
      await refreshAll();
    } catch (error) {
      setActionError(`Network error running health check: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setHealthCheckBusy(false);
    }
  }

  // Run Diagnostic: a REAL bounded provider probe that polls the RPC endpoint and
  // persists live evidence (provider health + heartbeat), then refreshes the page so
  // a freshly onboarded source can move from "Provisioning" to real, measured status.
  async function handleRunDiagnostic() {
    setDiagnosticBusy(true);
    setActionError('');
    setDiagnosticResult('');
    try {
      const response = await mutate('/api/monitoring/sources/diagnostic', 'POST', {});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = (payload as { detail?: unknown }).detail;
        setActionError(`Diagnostic failed: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
        return;
      }
      const s = (payload as { summary?: { targets_evaluated?: number; healthy?: number; degraded?: number; errors?: number; reachable?: number } }).summary;
      if (s) {
        setDiagnosticResult(
          `Diagnostic polled ${s.targets_evaluated ?? 0} source(s): ${s.healthy ?? 0} healthy, ${s.degraded ?? 0} degraded, ${s.errors ?? 0} unreachable.`,
        );
      } else {
        setDiagnosticResult('Diagnostic completed.');
      }
      await refreshAll();
    } catch (error) {
      setActionError(`Network error running diagnostic: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setDiagnosticBusy(false);
    }
  }

  async function handleToggleTarget(source: SourceRow) {
    const targetId = source.target_id;
    const enable = !(source.enabled || source.monitoring_enabled);
    setBusyTargetId(targetId);
    setActionError('');
    try {
      const response = await mutate(
        `/api/monitoring/targets/${encodeURIComponent(targetId)}/${enable ? 'enable' : 'disable'}`,
        'POST',
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = (payload as { detail?: unknown }).detail;
        setActionError(`${enable ? 'Enable' : 'Disable'} failed: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
        return;
      }
      await refreshAll();
    } catch (error) {
      setActionError(`Network error: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setBusyTargetId(null);
    }
  }

  // ── Derived: filter options + filtered/sorted/paged sources ──
  const networkOptions = useMemo(
    () => Array.from(new Set(sources.map((s) => s.network).filter(Boolean))) as string[],
    [sources],
  );
  const providerOptions = useMemo(
    () => Array.from(new Set(sources.map((s) => s.provider || s.primary_provider).filter(Boolean))) as string[],
    [sources],
  );
  const statusOptions = useMemo(
    () => Array.from(new Set(sources.map((s) => (s.status ?? '').toLowerCase()).filter(Boolean))),
    [sources],
  );

  const filteredSources = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = sources.filter((s) => {
      if (statusFilter !== 'all' && (s.status ?? '').toLowerCase() !== statusFilter) return false;
      if (networkFilter !== 'all' && s.network !== networkFilter) return false;
      if (providerFilter !== 'all' && (s.provider || s.primary_provider) !== providerFilter) return false;
      if (routingFilter !== 'all') {
        const role = s.routing ?? 'standby';
        if (routingFilter === 'unrouted' ? Boolean(s.routing) : role !== routingFilter) return false;
      }
      if (q) {
        const haystack = [s.name, s.address, s.provider, s.primary_provider, s.network, String(s.chain_id ?? '')]
          .join(' ')
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });

    const dir = sortDir === 'asc' ? 1 : -1;
    const nullLast = (v: number | null | undefined) => (v == null ? Number.POSITIVE_INFINITY : v);
    const timeVal = (v?: string | null) => (v ? new Date(v).getTime() : Number.POSITIVE_INFINITY);
    const sorted = [...filtered].sort((a, b) => {
      switch (sortKey) {
        case 'health': return dir * (nullLast(b.health_score) - nullLast(a.health_score)) * -1;
        case 'latency': return dir * (nullLast(a.median_latency_ms) - nullLast(b.median_latency_ms));
        case 'lag': return dir * (nullLast(a.block_lag) - nullLast(b.block_lag));
        case 'error': return dir * (nullLast(a.error_rate) - nullLast(b.error_rate));
        case 'heartbeat': return dir * (timeVal(a.last_heartbeat) - timeVal(b.last_heartbeat));
        default: return dir * (a.name ?? '').localeCompare(b.name ?? '');
      }
    });
    return sorted;
  }, [sources, search, statusFilter, networkFilter, providerFilter, routingFilter, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filteredSources.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pagedSources = filteredSources.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  // Reset to first page whenever the filter set changes.
  useEffect(() => setPage(1), [search, statusFilter, networkFilter, providerFilter, routingFilter, sortKey, sortDir]);


  const routingHistoryForSelected = useMemo(
    () => (selectedSource ? decisions.filter((d) => d.target_id === selectedSource.target_id) : []),
    [decisions, selectedSource],
  );

  const noAssets = !loading && assetsCount === 0;
  const hasAssetsNoTargets = !loading && assetsCount > 0 && targetsCount === 0;
  const streamPill = streamStatusPill(streamStatus);

  const agentPanel = (
    <SourceOptimizationAgentPanel
      agent={agent}
      providerHealth={providerHealth}
      summary={summary}
      settings={settings}
      decisions={decisions}
      loading={loading}
      autoRoutingBusy={autoRoutingBusy}
      healthCheckBusy={healthCheckBusy}
      diagnosticBusy={diagnosticBusy}
      onToggleAutoRouting={() => void handleToggleAutoRouting()}
      onRunHealthCheck={() => void handleRunHealthCheck()}
      onRunDiagnostic={() => void handleRunDiagnostic()}
      onOpenDecision={(decision) => setSelectedDecision(decision)}
    />
  );

  return (
    <main className="productPage msrcPage">
      {/* Source-specific health (connection, RPC/provider state, last poll, last
          telemetry, verify/diagnose) is kept below in the summary cards and the
          Source Optimization agent panel. The full global System Health dashboard is
          NOT repeated here — it lives on /system-health. */}

      <PageHeader
        title="Monitoring Sources"
        description="The systems Guard watches for each protected asset, and whether their data is arriving."
        meta={(
          <>
            {/* Page live-update channel (SSE) — a fact about THIS page's connection,
                kept apart from provider / ingestion health. */}
            <span className="msrcConn">
              Live updates <StatusPill label={streamPill.label} variant={streamPill.variant} />
            </span>
            <span className="msrcRefreshed" title={fmtExact(lastRefreshed)}>
              Last refreshed {fmtRelative(lastRefreshed)}
              {streamStatus === 'reconnecting' ? ' · refreshed by polling while live updates reconnect' : ''}
            </span>
          </>
        )}
        actions={(
          <>
            <button type="button" className="btn btn-secondary msrcHeaderBtn msrcIcoPulse" disabled={healthCheckBusy} onClick={() => void handleRunHealthCheck()}>
              {healthCheckBusy ? 'Running…' : 'Run Health Check'}
            </button>
            <Link href="/monitoring-sources/targets" prefetch={false} className="btn btn-primary msrcHeaderBtn msrcIcoPlus">
              Add Source
            </Link>
          </>
        )}
      />

      {loadError ? <p className="statusLine msrcNotice msrcNotice--danger">{loadError}</p> : null}
      {actionError ? <p className="statusLine msrcNotice msrcNotice--danger">{actionError}</p> : null}
      {/* Completed-run summaries are neutral: the counts inside them (critical,
          degraded, unreachable) must never be tinted as a success. */}
      {healthCheckResult ? <p className="statusLine msrcNotice msrcNotice--result">{healthCheckResult}</p> : null}
      {diagnosticResult ? <p className="statusLine msrcNotice msrcNotice--result">{diagnosticResult}</p> : null}

      {/* ── Key figures ────────────────────────────────────── */}
      <SummaryFigures summary={summary} loading={loading} />

      <div className="msrcLayout">
        <div className="msrcLayoutGrid">
        {/* ── Main content ─────────────────────────────────── */}
        <div className="msrcMain">
          <TabStrip tabs={TABS} active={activeTab} onChange={(key) => changeTab(key as TabKey)} />

          {activeTab === 'targets' ? (
            <div role="tabpanel" aria-label="Monitoring Targets" className="msrcPanel">
              {noAssets ? (
                <EmptyStateBlocker
                  title="No monitoring source is configured"
                  body="Add an RPC, oracle, custodian, or telemetry provider to begin source-health monitoring."
                  ctaHref="/assets"
                  ctaLabel="Add Asset"
                />
              ) : hasAssetsNoTargets ? (
                <EmptyStateBlocker
                  title="No monitoring target is linked to this asset yet"
                  body="Create a monitoring target so Decoda can begin collecting runtime signals for this asset."
                  ctaHref="/monitoring-sources/targets"
                  ctaLabel="Create monitoring target"
                />
              ) : (
                <>
                  <SourceFilters
                    search={search}
                    onSearch={setSearch}
                    statusFilter={statusFilter}
                    onStatus={setStatusFilter}
                    statusOptions={statusOptions}
                    networkFilter={networkFilter}
                    onNetwork={setNetworkFilter}
                    networkOptions={networkOptions}
                    providerFilter={providerFilter}
                    onProvider={setProviderFilter}
                    providerOptions={providerOptions}
                    routingFilter={routingFilter}
                    onRouting={setRoutingFilter}
                    sortKey={sortKey}
                    onSortKey={setSortKey}
                    sortDir={sortDir}
                    onSortDir={setSortDir}
                    onRefresh={() => void loadSources(undefined, { quiet: true })}
                  />
                  <TableShell headers={TARGET_HEADERS} compact className="msrcTable msrcTargetsTable">
                    {loading ? (
                      <tr>
                        <td colSpan={TARGET_HEADERS.length} className="msrcTableNote">
                          Loading monitoring sources…
                        </td>
                      </tr>
                    ) : pagedSources.length === 0 ? (
                      <tr>
                        <td colSpan={TARGET_HEADERS.length} className="msrcTableNote">
                          {sources.length === 0 ? 'No monitoring sources found for this workspace.' : 'No sources match the current filters.'}
                        </td>
                      </tr>
                    ) : (
                      pagedSources.map((source) => {
                        const status = sourceStatusBadge(source.status);
                        const routing = routingBadge(source);
                        const busy = busyTargetId === source.target_id;
                        return (
                          <tr key={source.target_id} className="msrcRow" onClick={() => setSelectedSource(source)}>
                            <td className="msrcCellName">
                              <span className="msrcName">{source.name || 'Unnamed target'}</span>
                              {source.address ? <span className="msrcSubMono" title={source.address}>{shortAddress(source.address)}</span> : null}
                            </td>
                            <td>{source.network || '—'}</td>
                            <td className="msrcCellStatus">
                              {/* Provider/runtime health. */}
                              <StatusPill label={status.label} variant={status.variant} />
                              {/* Failure reason — only for genuine non-healthy states, never a
                                  live-but-quiet reason (that surfaces below as event detection). */}
                              {source.status_reason && status.variant !== 'success' && !isQuietReason(source.status_reason) ? (
                                <div className="msrcReason">{source.status_reason.replace(/_/g, ' ')}</div>
                              ) : null}
                              {/* Evidence/event detection — separate signal from provider health. */}
                              {(() => {
                                const ev = eventDetectionLine(source);
                                return ev ? (
                                  <div
                                    className={ev.muted ? 'msrcEvent' : 'msrcEvent msrcEvent--detected'}
                                    title={ev.muted ? 'Provider healthy and coverage telemetry fresh; no wallet/transfer event in the current window' : 'A wallet/transfer event was observed in the current window'}
                                  >
                                    {ev.label}
                                  </div>
                                ) : null;
                              })()}
                            </td>
                            <td>{healthScoreCell(source)}</td>
                            <td className="msrcNum" title={fmtExact(source.last_telemetry_at)}>{fmtRelative(source.last_telemetry_at)}</td>
                            <td><StatusPill label={routing.label} variant={routing.variant} /></td>
                            <td className="msrcCellActions" onClick={(e) => e.stopPropagation()}>
                              <div className="msrcRowActions">
                                <button type="button" className="btn btn-secondary msrcRowBtn" onClick={() => setSelectedSource(source)}>
                                  Details
                                </button>
                                {source.system_id ? (
                                  <Link
                                    href={`/monitoring-sources/${encodeURIComponent(source.target_id)}/telemetry`}
                                    prefetch={false}
                                    className="msrcRowLink"
                                  >
                                    View telemetry
                                  </Link>
                                ) : null}
                                <button type="button" className="btn btn-secondary msrcRowBtn" disabled={busy} onClick={() => void handleToggleTarget(source)}>
                                  {busy ? '…' : source.enabled || source.monitoring_enabled ? 'Disable' : 'Enable'}
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </TableShell>
                  {filteredSources.length > PAGE_SIZE ? (
                    <Pagination page={currentPage} totalPages={totalPages} total={filteredSources.length} onPage={setPage} />
                  ) : null}
                </>
              )}
            </div>
          ) : null}

          {activeTab === 'systems' ? (
            <div role="tabpanel" aria-label="Monitored Systems" className="msrcPanel">
              {!loading && systems.length === 0 ? (
                <EmptyStateBlocker
                  title="No monitored system is enabled yet"
                  body="Enable a monitored system to start heartbeat, polling, and telemetry collection."
                  ctaHref="/monitoring-sources/targets"
                  ctaLabel="Configure monitoring"
                />
              ) : (
                <TableShell headers={SYSTEM_HEADERS} compact className="msrcTable msrcSystemsTable">
                  {loading ? (
                    <tr>
                      <td colSpan={SYSTEM_HEADERS.length} className="msrcTableNote">
                        Loading monitored systems…
                      </td>
                    </tr>
                  ) : (
                    systems.map((system) => {
                      const runtimeStatus = runtimeStatusPill(system);
                      const coverage = coveragePill(system);
                      const evidence = resolveEvidenceSource(system);
                      return (
                        <tr key={system.id}>
                          <td className="msrcCellName"><span className="msrcName">{system.asset_name || `System ${system.id.slice(0, 8)}`}</span></td>
                          <td>{systemType(system)}</td>
                          <td>{system.environment || 'production'}</td>
                          <td>{system.target_name || <span className="muted">Unlinked</span>}</td>
                          <td><StatusPill label={runtimeStatus.label} variant={runtimeStatus.variant} /></td>
                          <td><StatusPill label={coverage.label} variant={coverage.variant} /></td>
                          <td className="muted" title="Response time requires a live probe worker">—</td>
                          <td className="msrcNum" title={fmtExact(system.last_event_at)}>{fmtRelative(system.last_event_at)}</td>
                          <td className="muted">—</td>
                          <td><StatusPill label={evidence.label} variant={evidence.variant} /></td>
                          <td className="muted">—</td>
                        </tr>
                      );
                    })
                  )}
                </TableShell>
              )}
            </div>
          ) : null}
        </div>

        </div>
      </div>

      {/* ── Source optimization agent ─────────────────────── */}
      {/* Routing decisions, health checks and diagnostics the agent ran -- the
          same panel as before, on demand rather than beside the table. A
          decision that needs approval says so in the figures row above. */}
      <details className="pageDisclosure msrcAgentDisclosure">
        <summary className="pageDisclosureSummary">
          Source optimization agent
          <span className="pageDisclosureHint">Routing decisions, health checks and diagnostics</span>
        </summary>
        <div className="pageDisclosureBody">{agentPanel}</div>
      </details>

      {/* ── Detail drawers ────────────────────────────────── */}
      {selectedSource ? (
        <SourceDetailDrawer
          source={selectedSource}
          routingHistory={routingHistoryForSelected}
          onClose={() => setSelectedSource(null)}
          onRunHealthCheck={() => void handleRunHealthCheck()}
          healthCheckBusy={healthCheckBusy}
        />
      ) : null}
      {selectedDecision ? (
        <DecisionEvidenceDrawer decision={selectedDecision} onClose={() => setSelectedDecision(null)} />
      ) : null}

      {/* The narrow-screen rail collapse lives in styles.css — see above. */}
    </main>
  );
}

// ── Filters toolbar ──────────────────────────────────────────────────────────
function SourceFilters(props: {
  search: string; onSearch: (v: string) => void;
  statusFilter: string; onStatus: (v: string) => void; statusOptions: string[];
  networkFilter: string; onNetwork: (v: string) => void; networkOptions: string[];
  providerFilter: string; onProvider: (v: string) => void; providerOptions: string[];
  routingFilter: string; onRouting: (v: string) => void;
  sortKey: SortKey; onSortKey: (v: SortKey) => void;
  sortDir: 'asc' | 'desc'; onSortDir: (v: 'asc' | 'desc') => void;
  onRefresh: () => void;
}) {
  return (
    <div className="msrcToolbar">
      <label className="msrcSearch">
        <input
          type="search"
          aria-label="Search sources"
          placeholder="Search name, address, provider, chain…"
          value={props.search}
          onChange={(e) => props.onSearch(e.target.value)}
          className="msrcSearchInput"
        />
      </label>
      <div className="msrcFilterGroup">
        <select aria-label="Filter by status" value={props.statusFilter} onChange={(e) => props.onStatus(e.target.value)} className="msrcSelect">
          <option value="all">All statuses</option>
          {props.statusOptions.map((s) => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
        </select>
        <select aria-label="Filter by network" value={props.networkFilter} onChange={(e) => props.onNetwork(e.target.value)} className="msrcSelect">
          <option value="all">All networks</option>
          {props.networkOptions.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <button type="button" className="btn btn-secondary msrcToolBtn msrcIcoRefresh" onClick={props.onRefresh}>
          Refresh
        </button>
      </div>
      {/* Provider, routing and sort are for an operator tuning sources; they stay
          one click away and keep their state while closed. */}
      <details className="msrcMoreFilters">
        <summary className="msrcMoreFiltersSummary">More filters</summary>
        <div className="msrcFilterGroup">
        <select aria-label="Filter by provider" value={props.providerFilter} onChange={(e) => props.onProvider(e.target.value)} className="msrcSelect">
          <option value="all">All providers</option>
          {props.providerOptions.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select aria-label="Filter by routing role" value={props.routingFilter} onChange={(e) => props.onRouting(e.target.value)} className="msrcSelect">
          <option value="all">All routing</option>
          <option value="primary">Primary</option>
          <option value="fallback">Fallback</option>
          <option value="unrouted">Unrouted</option>
        </select>
        <select aria-label="Sort by" value={props.sortKey} onChange={(e) => props.onSortKey(e.target.value as SortKey)} className="msrcSelect">
          <option value="health">Sort: Health</option>
          <option value="latency">Sort: Latency</option>
          <option value="lag">Sort: Block lag</option>
          <option value="error">Sort: Error rate</option>
          <option value="heartbeat">Sort: Last heartbeat</option>
        </select>
        <button
          type="button"
          className="btn btn-secondary msrcToolBtn"
          aria-label={props.sortDir === 'asc' ? 'Sort direction: ascending' : 'Sort direction: descending'}
          title={props.sortDir === 'asc' ? 'Ascending — click for descending' : 'Descending — click for ascending'}
          onClick={() => props.onSortDir(props.sortDir === 'asc' ? 'desc' : 'asc')}
        >
          {props.sortDir === 'asc' ? '↑' : '↓'}
        </button>
        </div>
      </details>
    </div>
  );
}

function Pagination({ page, totalPages, total, onPage }: { page: number; totalPages: number; total: number; onPage: (p: number) => void }) {
  return (
    <div className="msrcPager">
      <span className="msrcPagerInfo">{total} source(s) · page {page} of {totalPages}</span>
      <div className="msrcPagerBtns">
        <button type="button" className="btn btn-secondary msrcRowBtn" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <button type="button" className="btn btn-secondary msrcRowBtn" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </div>
  );
}
