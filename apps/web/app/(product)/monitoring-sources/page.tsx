'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import {
  EmptyStateBlocker,
  StatusPill,
  TabStrip,
  TableShell,
  type PillVariant,
} from '../../components/ui-primitives';
import { usePilotAuth } from '../../pilot-auth-context';
import { useRuntimeSummary } from '../../runtime-summary-context';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

type TabKey = 'targets' | 'systems' | 'providers';

type AssetRow = {
  id: string;
  name?: string;
};

type TargetRow = {
  id: string;
  name?: string;
  target_type?: string;
  provider?: string;
  enabled?: boolean;
  monitoring_enabled?: boolean;
  last_checked_at?: string | null;
  health_status?: string | null;
  next_action?: string | null;
  monitored_system_id?: string | null;
  systems_count?: number;
};

type MonitoredSystemRow = {
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
};

// Canonical monitoring-source row (backend-derived; every value comes from real records).
type SourceRow = {
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
  last_poll_at?: string | null;
  last_telemetry_at?: string | null;
  routing?: 'primary' | 'fallback' | null;
  routing_explanation?: string | null;
  coverage_state?: string | null;
  evidence_source?: string | null;
  enabled?: boolean;
  monitoring_enabled?: boolean;
};

type ProviderHealthRow = {
  host: string;
  status?: string | null;
  latency_ms?: number | null;
  checked_at?: string | null;
  evidence_source?: string | null;
  target_count?: number;
};

type ProviderHealthSummary = {
  providers: ProviderHealthRow[];
  healthy_count: number;
  degraded_count: number;
  unknown_count: number;
  total: number;
};

type AgentRecommendation = { kind: string; detail: string; target_id?: string };

type AgentState = {
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
};

type SourcesPayload = {
  assets?: AssetRow[];
  targets?: TargetRow[];
  systems?: MonitoredSystemRow[];
  sources?: SourceRow[];
  provider_health?: ProviderHealthSummary | null;
  agent?: AgentState | null;
};

function fmt(value?: string | null): string {
  if (!value) return '—';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';

  const diff = Date.now() - parsed.getTime();
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;

  return parsed.toLocaleDateString();
}

function fmtBlock(value?: number | null): string {
  if (value == null) return '—';
  return `#${value.toLocaleString()}`;
}

function fmtLatency(value?: number | null): string {
  if (value == null) return '—';
  return `${value.toLocaleString()} ms`;
}

function shortAddress(value?: string | null): string {
  if (!value) return '—';
  if (value.length <= 14) return value;
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

// Map the canonical source-status vocabulary to a truthful badge. Unknown/absent statuses are
// never rendered as "healthy" — they fall through to a neutral state.
const SOURCE_STATUS_LABELS: Record<string, { label: string; variant: PillVariant }> = {
  healthy: { label: 'Healthy', variant: 'success' },
  provisioning: { label: 'Provisioning', variant: 'info' },
  warning: { label: 'Warning', variant: 'warning' },
  degraded: { label: 'Degraded', variant: 'warning' },
  failed: { label: 'Failed', variant: 'danger' },
  disabled: { label: 'Disabled', variant: 'neutral' },
  missing_configuration: { label: 'Missing configuration', variant: 'danger' },
};

function sourceStatusBadge(status?: string | null): { label: string; variant: PillVariant } {
  const key = (status ?? '').trim().toLowerCase();
  return SOURCE_STATUS_LABELS[key] ?? { label: 'Unknown', variant: 'neutral' };
}

function routingBadge(source: SourceRow): { label: string; variant: PillVariant } {
  if (source.routing === 'primary') return { label: 'Primary', variant: 'info' };
  if (source.routing === 'fallback') return { label: 'Fallback', variant: 'warning' };
  return { label: 'Unrouted', variant: 'neutral' };
}

function coverageBadge(source: SourceRow): { label: string; variant: PillVariant } {
  const raw = (source.coverage_state ?? '').trim().toLowerCase();
  if (!raw) return { label: '—', variant: 'neutral' };
  if (raw === 'reporting' || raw === 'covered' || raw === 'full') return { label: 'Reporting', variant: 'success' };
  if (raw === 'stale') return { label: 'Stale', variant: 'warning' };
  if (raw === 'partial') return { label: 'Partial', variant: 'warning' };
  if (raw === 'silent' || raw === 'missing') return { label: 'Silent', variant: 'danger' };
  if (raw === 'unavailable') return { label: 'Unavailable', variant: 'neutral' };
  // Fall back to a humanized reason string without asserting a positive coverage state.
  return { label: raw.replace(/_/g, ' '), variant: 'neutral' };
}

function evidenceBadge(evidence?: string | null): { label: string; variant: PillVariant } {
  const raw = (evidence ?? 'none').trim().toLowerCase();
  if (raw === 'live') return { label: 'live', variant: 'success' };
  if (raw === 'simulator' || raw === 'replay') return { label: raw, variant: 'info' };
  return { label: 'none', variant: 'neutral' };
}

// ── Monitored Systems tab (runtime-focused) ──────────────────────────────────
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

const SOURCE_HEADERS = [
  'Source', 'Asset', 'Network', 'Chain ID', 'Address', 'Provider', 'Type', 'Status',
  'Latest Block', 'Block Lag', 'Median Latency', 'Last Poll', 'Last Telemetry', 'Routing', 'Coverage', 'Actions',
];

const SYSTEM_HEADERS = [
  'System Name', 'Linked Target', 'Enabled', 'Runtime Status', 'Last Heartbeat', 'Last Telemetry', 'Coverage State', 'Evidence Source',
];

const PROVIDER_HEADERS = ['Provider', 'Status', 'Median Latency', 'Last Check', 'Evidence', 'Sources'];

const TABS = [
  { key: 'targets', label: 'Monitoring Targets' },
  { key: 'systems', label: 'Monitored Systems' },
  { key: 'providers', label: 'Provider Health' },
];

function confidenceVariant(confidence: string): PillVariant {
  switch (confidence.toLowerCase()) {
    case 'high': return 'success';
    case 'medium': return 'warning';
    case 'low': return 'danger';
    default: return 'neutral';
  }
}

function agentStateLabel(state: string): { label: string; variant: PillVariant } {
  switch (state) {
    case 'monitoring': return { label: 'Monitoring', variant: 'success' };
    case 'provisioning': return { label: 'Provisioning', variant: 'info' };
    case 'attention_required': return { label: 'Attention required', variant: 'warning' };
    default: return { label: 'Idle', variant: 'neutral' };
  }
}

export default function MonitoringSourcesPage() {
  const [activeTab, setActiveTab] = useState<TabKey>('targets');
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [targets, setTargets] = useState<TargetRow[]>([]);
  const [systems, setSystems] = useState<MonitoredSystemRow[]>([]);
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [providerHealth, setProviderHealth] = useState<ProviderHealthSummary | null>(null);
  const [agent, setAgent] = useState<AgentState | null>(null);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busyTargetId, setBusyTargetId] = useState<string | null>(null);
  const [actionError, setActionError] = useState('');
  const [repairing, setRepairing] = useState(false);
  const [repairResult, setRepairResult] = useState('');
  const [showRouting, setShowRouting] = useState(false);

  const { authHeaders } = usePilotAuth();
  const { refresh: refreshRuntimeSummary } = useRuntimeSummary();

  const loadSources = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      try {
        const response = await fetch('/api/monitoring/sources', {
          headers: authHeaders(),
          cache: 'no-store',
          signal,
        });
        const payload: SourcesPayload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = typeof payload === 'object' && payload && 'detail' in payload && typeof (payload as { detail?: unknown }).detail === 'string'
            ? (payload as { detail: string }).detail
            : `HTTP ${response.status}`;
          setLoadError(`Unable to load monitoring sources: ${detail}`);
          return;
        }
        setAssets(payload.assets ?? []);
        setTargets(payload.targets ?? []);
        setSystems(payload.systems ?? []);
        setSources(payload.sources ?? []);
        setProviderHealth(payload.provider_health ?? null);
        setAgent(payload.agent ?? null);
        setLoadError('');
      } catch (error) {
        if ((error as { name?: string }).name === 'AbortError') return;
        setLoadError(`Network error loading monitoring sources: ${error instanceof Error ? error.message : 'unknown error'}`);
      } finally {
        setLoading(false);
      }
    },
    [authHeaders],
  );

  useEffect(() => {
    const controller = new AbortController();
    void loadSources(controller.signal);
    return () => controller.abort();
  }, [loadSources]);

  // After any activation/repair mutation, invalidate the global runtime summary so the setup
  // banner, workspace health, and rule counts reflect the newly linked records.
  const refreshAll = useCallback(async () => {
    await loadSources();
    await refreshRuntimeSummary().catch(() => undefined);
  }, [loadSources, refreshRuntimeSummary]);

  async function handleEnableTarget(targetId: string) {
    setBusyTargetId(targetId);
    setActionError('');
    const url = `/api/monitoring/targets/${encodeURIComponent(targetId)}/enable`;
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detailObj = (payload as { detail?: unknown }).detail;
        const detail =
          typeof detailObj === 'string'
            ? detailObj
            : typeof detailObj === 'object' && detailObj !== null
              ? ((detailObj as { message?: string }).message ?? `HTTP ${response.status}`)
              : `HTTP ${response.status}`;
        setActionError(`Enable failed: ${detail}`);
        return;
      }
      await refreshAll();
    } catch (error) {
      setActionError(`Network error enabling target: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setBusyTargetId(null);
    }
  }

  async function handleDisableTarget(targetId: string) {
    setBusyTargetId(targetId);
    setActionError('');
    const url = `/api/monitoring/targets/${encodeURIComponent(targetId)}/disable`;
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = typeof (payload as { detail?: unknown }).detail === 'string' ? (payload as { detail: string }).detail : `HTTP ${response.status}`;
        setActionError(`Disable failed: ${detail}`);
        return;
      }
      await refreshAll();
    } catch (error) {
      setActionError(`Network error disabling target: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setBusyTargetId(null);
    }
  }

  // "Repair monitored systems" — reconcile the workspace so every enabled target is bridged to
  // a monitored system, links are resolved by contract address + chain, and an audit row is
  // written on the backend. Then refetch canonical data everywhere.
  async function handleRepair() {
    setRepairing(true);
    setRepairResult('');
    setActionError('');
    try {
      const response = await fetch('/api/monitoring/systems/reconcile', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof (payload as { detail?: unknown }).detail === 'string' ? (payload as { detail: string }).detail : `HTTP ${response.status}`;
        setRepairResult(`Repair failed: ${detail}`);
        return;
      }
      const reconcile = ((payload as { reconcile?: Record<string, unknown> }).reconcile ?? payload) as Record<string, unknown>;
      const relinked = Number(reconcile.targets_relinked ?? 0);
      const created = Number(reconcile.assets_created ?? 0);
      const updated = Number(reconcile.created_or_updated ?? reconcile.eligible_targets ?? 0);
      setRepairResult(
        `Repair complete: ${relinked} target(s) relinked, ${created} asset(s) created, ${updated} monitored system(s) updated.`,
      );
      await refreshAll();
    } catch (error) {
      setRepairResult(`Network error during repair: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setRepairing(false);
    }
  }

  const noAssets = !loading && assets.length === 0;
  const hasAssetsNoTargets = !loading && assets.length > 0 && targets.length === 0;

  const firstTelemetryTargetId = useMemo(
    () => sources.find((s) => s.system_id)?.target_id ?? sources[0]?.target_id ?? null,
    [sources],
  );

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      <div className="listHeader" style={{ marginBottom: '1.25rem', alignItems: 'flex-start' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '1.45rem', fontWeight: 700 }}>Monitoring Sources</h1>
          <p className="muted" style={{ margin: '0.35rem 0 0', fontSize: '0.9rem' }}>
            Targets, monitored systems, and provider health for this workspace. Every value is derived from canonical backend records.
          </p>
        </div>

        <Link href="/monitoring-sources/targets" prefetch={false} className="btn btn-primary">
          Add Target
        </Link>
      </div>

      {loadError ? (
        <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{loadError}</p>
      ) : null}

      {actionError ? (
        <p className="statusLine" style={{ color: 'var(--danger-fg)', fontSize: '0.85rem' }}>{actionError}</p>
      ) : null}

      {repairResult ? (
        <p
          className="statusLine"
          style={{
            color: repairResult.startsWith('Repair failed') ? 'var(--danger-fg)' : 'var(--success-fg)',
            fontSize: '0.85rem',
          }}
        >
          {repairResult}
        </p>
      ) : null}

      <div style={{ display: 'flex', gap: '1.25rem', alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* ── Main content ─────────────────────────────────────────── */}
        <div style={{ flex: '1 1 640px', minWidth: 0 }}>
          <TabStrip tabs={TABS} active={activeTab} onChange={(key) => setActiveTab(key as TabKey)} />

          {activeTab === 'targets' ? (
            <div role="tabpanel" aria-label="Monitoring Targets">
              {noAssets ? (
                <EmptyStateBlocker
                  title="No protected assets yet"
                  body="Add a protected asset before configuring monitoring sources."
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
                <TableShell headers={SOURCE_HEADERS} compact>
                  {loading ? (
                    <tr>
                      <td colSpan={SOURCE_HEADERS.length} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}>
                        Loading monitoring sources…
                      </td>
                    </tr>
                  ) : sources.length === 0 ? (
                    <tr>
                      <td colSpan={SOURCE_HEADERS.length} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}>
                        {targets.length > 0
                          ? 'Monitoring source details are temporarily unavailable. Retry shortly.'
                          : 'No monitoring sources found for this workspace.'}
                      </td>
                    </tr>
                  ) : (
                    sources.map((source) => {
                      const status = sourceStatusBadge(source.status);
                      const routing = routingBadge(source);
                      const coverage = coverageBadge(source);
                      const busy = busyTargetId === source.target_id;
                      return (
                        <tr key={source.target_id}>
                          <td style={{ fontWeight: 600 }}>{source.name || 'Unnamed target'}</td>
                          <td>{source.asset_name || <span className="muted">Unlinked</span>}</td>
                          <td>{source.network || '—'}</td>
                          <td>{source.chain_id != null ? String(source.chain_id) : '—'}</td>
                          <td>
                            <span style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.78rem' }} title={source.address ?? undefined}>
                              {shortAddress(source.address)}
                            </span>
                          </td>
                          <td>
                            {source.provider ? (
                              <span style={{ fontSize: '0.8rem' }}>{source.provider}</span>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                          <td>{source.source_type || '—'}</td>
                          <td>
                            <StatusPill label={status.label} variant={status.variant} />
                            {source.status_reason && status.variant !== 'success' ? (
                              <div className="muted" style={{ fontSize: '0.68rem', marginTop: '0.15rem' }}>
                                {source.status_reason.replace(/_/g, ' ')}
                              </div>
                            ) : null}
                          </td>
                          <td style={{ whiteSpace: 'nowrap' }}>{fmtBlock(source.latest_block)}</td>
                          <td style={{ whiteSpace: 'nowrap' }} title={source.block_lag == null ? 'Requires a live chain-head read' : undefined}>
                            {source.block_lag == null ? '—' : source.block_lag.toLocaleString()}
                          </td>
                          <td style={{ whiteSpace: 'nowrap' }}>{fmtLatency(source.median_latency_ms)}</td>
                          <td style={{ whiteSpace: 'nowrap' }}>{fmt(source.last_poll_at)}</td>
                          <td style={{ whiteSpace: 'nowrap' }}>{fmt(source.last_telemetry_at)}</td>
                          <td><StatusPill label={routing.label} variant={routing.variant} /></td>
                          <td><StatusPill label={coverage.label} variant={coverage.variant} /></td>
                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                              {source.system_id ? (
                                <Link
                                  href={`/monitoring-sources/${encodeURIComponent(source.target_id)}/telemetry`}
                                  prefetch={false}
                                  style={{ color: 'var(--text-accent)', fontSize: '0.78rem', textDecoration: 'none' }}
                                >
                                  Telemetry
                                </Link>
                              ) : null}
                              {source.enabled || source.monitoring_enabled ? (
                                <button
                                  type="button"
                                  className="btn btn-secondary"
                                  style={{ fontSize: '0.75rem', padding: '0.18rem 0.55rem' }}
                                  disabled={busy}
                                  onClick={() => void handleDisableTarget(source.target_id)}
                                >
                                  {busy ? '…' : 'Disable'}
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  className="btn btn-secondary"
                                  style={{ fontSize: '0.75rem', padding: '0.18rem 0.55rem' }}
                                  disabled={busy}
                                  onClick={() => void handleEnableTarget(source.target_id)}
                                >
                                  {busy ? '…' : 'Enable'}
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </TableShell>
              )}
            </div>
          ) : null}

          {activeTab === 'systems' ? (
            <div role="tabpanel" aria-label="Monitored Systems">
              {noAssets ? (
                <EmptyStateBlocker
                  title="No protected assets yet"
                  body="Add a protected asset before configuring monitoring sources."
                  ctaHref="/assets"
                  ctaLabel="Add Asset"
                />
              ) : !loading && systems.length === 0 ? (
                <EmptyStateBlocker
                  title="No monitored system is enabled yet"
                  body="Enable a monitored system to start heartbeat, polling, and telemetry collection."
                  ctaOnClick={() => void handleRepair()}
                  ctaLabel={repairing ? 'Repairing…' : 'Repair monitored systems'}
                  ctaDisabled={repairing}
                />
              ) : (
                <TableShell headers={SYSTEM_HEADERS} compact>
                  {loading ? (
                    <tr>
                      <td colSpan={SYSTEM_HEADERS.length} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}>
                        Loading monitored systems…
                      </td>
                    </tr>
                  ) : (
                    systems.map((system) => {
                      const runtimeStatus = runtimeStatusPill(system);
                      const coverage = coveragePill(system);
                      const evidence = resolveEvidenceSource(system);
                      const linkedTarget = system.target_name || 'Unlinked';
                      return (
                        <tr key={system.id}>
                          <td style={{ fontWeight: 600 }}>{system.asset_name || `System ${system.id.slice(0, 8)}`}</td>
                          <td>{linkedTarget}</td>
                          <td>
                            <StatusPill label={system.is_enabled ? 'Yes' : 'No'} variant={system.is_enabled ? 'success' : 'neutral'} />
                          </td>
                          <td><StatusPill label={runtimeStatus.label} variant={runtimeStatus.variant} /></td>
                          <td style={{ whiteSpace: 'nowrap' }}>{fmt(system.last_heartbeat)}</td>
                          <td style={{ whiteSpace: 'nowrap' }}>{fmt(system.last_event_at)}</td>
                          <td><StatusPill label={coverage.label} variant={coverage.variant} /></td>
                          <td><StatusPill label={evidence.label} variant={evidence.variant} /></td>
                        </tr>
                      );
                    })
                  )}
                </TableShell>
              )}
            </div>
          ) : null}

          {activeTab === 'providers' ? (
            <div role="tabpanel" aria-label="Provider Health">
              {loading ? (
                <p className="muted" style={{ padding: '1.5rem' }}>Loading provider health…</p>
              ) : !providerHealth || providerHealth.providers.length === 0 ? (
                <EmptyStateBlocker
                  title="No provider health records yet"
                  body="Provider health appears once a monitoring source has an RPC provider configured and the worker has run a health check."
                  ctaHref="/integrations"
                  ctaLabel="Configure providers"
                />
              ) : (
                <TableShell headers={PROVIDER_HEADERS} compact>
                  {providerHealth.providers.map((provider) => {
                    const raw = (provider.status ?? '').toLowerCase();
                    const variant: PillVariant =
                      raw === 'healthy' ? 'success'
                        : raw === 'degraded' ? 'warning'
                        : raw === 'unavailable' || raw === 'error' ? 'danger'
                        : 'neutral';
                    const label = provider.status ? provider.status[0].toUpperCase() + provider.status.slice(1) : 'Unknown';
                    return (
                      <tr key={provider.host}>
                        <td style={{ fontWeight: 600, fontSize: '0.82rem' }}>{provider.host}</td>
                        <td><StatusPill label={label} variant={variant} /></td>
                        <td>{fmtLatency(provider.latency_ms)}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmt(provider.checked_at)}</td>
                        <td>{(() => { const e = evidenceBadge(provider.evidence_source); return <StatusPill label={e.label} variant={e.variant} />; })()}</td>
                        <td>{provider.target_count ?? 0}</td>
                      </tr>
                    );
                  })}
                </TableShell>
              )}
            </div>
          ) : null}
        </div>

        {/* ── Right rail: Source Optimization Agent + Provider Health ── */}
        <div style={{ flex: '0 1 320px', minWidth: '280px', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <SourceOptimizationAgentPanel
            agent={agent}
            loading={loading}
            repairing={repairing}
            showRouting={showRouting}
            onToggleRouting={() => setShowRouting((v) => !v)}
            onRepair={() => void handleRepair()}
            onRetest={() => void refreshAll()}
            telemetryTargetId={firstTelemetryTargetId}
          />
          <ProviderHealthCard providerHealth={providerHealth} loading={loading} onViewAll={() => setActiveTab('providers')} />
        </div>
      </div>
    </main>
  );
}

function StatRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.5rem', padding: '0.2rem 0' }}>
      <span className="muted" style={{ fontSize: '0.78rem' }}>{label}</span>
      <span style={{ fontSize: '0.82rem', fontWeight: 600, textAlign: 'right' }}>{value}</span>
    </div>
  );
}

function SourceOptimizationAgentPanel({
  agent,
  loading,
  repairing,
  showRouting,
  onToggleRouting,
  onRepair,
  onRetest,
  telemetryTargetId,
}: {
  agent: AgentState | null;
  loading: boolean;
  repairing: boolean;
  showRouting: boolean;
  onToggleRouting: () => void;
  onRepair: () => void;
  onRetest: () => void;
  telemetryTargetId: string | null;
}) {
  const stateBadge = agent ? agentStateLabel(agent.state) : { label: '—', variant: 'neutral' as PillVariant };
  const hasApprovedRoutingChange = false; // No production provider is replaced without an explicit approved change.

  return (
    <article className="dataCard" style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
        <p className="sectionEyebrow" style={{ margin: 0 }}>Source Optimization Agent</p>
        <StatusPill label={stateBadge.label} variant={stateBadge.variant} />
      </div>
      <p className="muted" style={{ margin: '0 0 0.75rem', fontSize: '0.75rem' }}>
        Diagnoses linkage, provider health, and routing from measured records. Provider replacement requires approval.
      </p>

      {loading ? (
        <p className="muted" style={{ fontSize: '0.8rem' }}>Reading canonical monitoring state…</p>
      ) : !agent ? (
        <p className="muted" style={{ fontSize: '0.8rem' }}>Agent state unavailable.</p>
      ) : (
        <>
          <div style={{ borderTop: '1px solid var(--border-subtle, rgba(148,163,184,0.2))', paddingTop: '0.5rem' }}>
            <StatRow label="Healthy providers" value={agent.healthy_providers} />
            <StatRow label="Degraded providers" value={
              agent.degraded_providers > 0
                ? <span style={{ color: 'var(--warning-fg)' }}>{agent.degraded_providers}</span>
                : agent.degraded_providers
            } />
            <StatRow label="Missing target links" value={
              agent.missing_target_links > 0
                ? <span style={{ color: 'var(--danger-fg)' }}>{agent.missing_target_links}</span>
                : 0
            } />
            <StatRow label="Primary provider" value={agent.primary_provider || '—'} />
            <StatRow label="Recommended fallback" value={agent.recommended_fallback || '—'} />
            <StatRow
              label="Confidence"
              value={<StatusPill label={agent.confidence} variant={confidenceVariant(agent.confidence)} />}
            />
          </div>
          {agent.confidence_basis ? (
            <p className="muted" style={{ fontSize: '0.72rem', margin: '0.4rem 0 0' }}>{agent.confidence_basis}</p>
          ) : null}

          {agent.recommendations.length > 0 ? (
            <div style={{ marginTop: '0.75rem' }}>
              <p className="sectionEyebrow" style={{ margin: '0 0 0.35rem', fontSize: '0.7rem' }}>Recommendations</p>
              <ul style={{ margin: 0, paddingLeft: '1rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                {agent.recommendations.map((rec, index) => (
                  <li key={`${rec.kind}-${index}`} style={{ padding: '0.15rem 0' }}>{rec.detail}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="muted" style={{ fontSize: '0.75rem', margin: '0.6rem 0 0' }}>
              No linkage or routing issues detected in canonical records.
            </p>
          )}

          {showRouting ? (
            <div style={{ marginTop: '0.6rem', background: 'var(--surface-subtle)', borderRadius: '6px', padding: '0.5rem 0.65rem' }}>
              <p className="sectionEyebrow" style={{ margin: '0 0 0.25rem', fontSize: '0.68rem' }}>Latest routing decision</p>
              <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                {agent.latest_routing_decision || 'No routing benchmark recorded for these sources yet.'}
              </p>
            </div>
          ) : null}
        </>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.85rem' }}>
        <button type="button" className="btn btn-primary" style={{ fontSize: '0.76rem', padding: '0.28rem 0.7rem' }} disabled={repairing} onClick={onRepair}>
          {repairing ? 'Repairing…' : 'Repair monitored systems'}
        </button>
        <button type="button" className="btn btn-secondary" style={{ fontSize: '0.76rem', padding: '0.28rem 0.7rem' }} disabled={loading} onClick={onRetest}>
          Re-test provider
        </button>
        <button type="button" className="btn btn-secondary" style={{ fontSize: '0.76rem', padding: '0.28rem 0.7rem' }} onClick={onToggleRouting}>
          {showRouting ? 'Hide routing' : 'Review routing recommendation'}
        </button>
        <Link href="/integrations" prefetch={false} className="btn btn-secondary" style={{ fontSize: '0.76rem', padding: '0.28rem 0.7rem' }}>
          Add fallback provider
        </Link>
        {telemetryTargetId ? (
          <Link
            href={`/monitoring-sources/${encodeURIComponent(telemetryTargetId)}/telemetry`}
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.76rem', padding: '0.28rem 0.7rem' }}
          >
            View telemetry
          </Link>
        ) : null}
        <button
          type="button"
          className="btn btn-secondary"
          style={{ fontSize: '0.76rem', padding: '0.28rem 0.7rem' }}
          disabled={!hasApprovedRoutingChange}
          title={hasApprovedRoutingChange ? undefined : 'No approved routing change is pending. Provider replacement requires approval.'}
        >
          Apply approved routing change
        </button>
      </div>
    </article>
  );
}

function ProviderHealthCard({
  providerHealth,
  loading,
  onViewAll,
}: {
  providerHealth: ProviderHealthSummary | null;
  loading: boolean;
  onViewAll: () => void;
}) {
  return (
    <article className="dataCard" style={{ padding: '1rem' }}>
      <p className="sectionEyebrow" style={{ margin: '0 0 0.5rem' }}>Provider Health</p>
      {loading ? (
        <p className="muted" style={{ fontSize: '0.8rem' }}>Loading…</p>
      ) : !providerHealth || providerHealth.total === 0 ? (
        <p className="muted" style={{ fontSize: '0.8rem' }}>
          No RPC provider records yet. Provider health is measured once a source has a provider configured.
        </p>
      ) : (
        <>
          <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.5rem' }}>
            <div style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ fontSize: '1.35rem', fontWeight: 700, color: 'var(--success-fg)' }}>{providerHealth.healthy_count}</div>
              <div className="muted" style={{ fontSize: '0.68rem' }}>Healthy</div>
            </div>
            <div style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ fontSize: '1.35rem', fontWeight: 700, color: 'var(--warning-fg)' }}>{providerHealth.degraded_count}</div>
              <div className="muted" style={{ fontSize: '0.68rem' }}>Degraded</div>
            </div>
            <div style={{ flex: 1, textAlign: 'center' }}>
              <div style={{ fontSize: '1.35rem', fontWeight: 700, color: 'var(--text-muted)' }}>{providerHealth.unknown_count}</div>
              <div className="muted" style={{ fontSize: '0.68rem' }}>Unknown</div>
            </div>
          </div>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: '0.76rem', padding: '0.28rem 0.7rem', width: '100%' }}
            onClick={onViewAll}
          >
            View provider details
          </button>
        </>
      )}
    </article>
  );
}
