'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import {
  EmptyStateBlocker,
  StatusPill,
  TabStrip,
  TableShell,
  type PillVariant,
} from '../../components/ui-primitives';
import { usePilotAuth } from '../../pilot-auth-context';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

type TabKey = 'targets' | 'systems';

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

function fmt(value?: string | null): string {
  if (!value) return '-';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';

  const diff = Date.now() - parsed.getTime();
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;

  return parsed.toLocaleDateString();
}

function targetStatusPill(target: TargetRow): { label: string; variant: PillVariant } {
  if (!target.enabled && !target.monitoring_enabled) {
    return { label: 'Disabled', variant: 'neutral' };
  }

  if (!target.monitored_system_id && !target.systems_count) {
    return { label: 'Not Configured', variant: 'warning' };
  }

  const healthStatus = (target.health_status ?? '').toLowerCase();

  if (healthStatus === 'healthy') return { label: 'Healthy', variant: 'success' };
  if (healthStatus === 'degraded') return { label: 'Degraded', variant: 'warning' };
  if (healthStatus === 'error') return { label: 'Error', variant: 'danger' };
  if (healthStatus === 'disabled') return { label: 'Disabled', variant: 'neutral' };

  if (target.monitoring_enabled || target.enabled) {
    return { label: 'Unknown', variant: 'neutral' };
  }

  return { label: 'Not Configured', variant: 'warning' };
}

function targetNextAction(target: TargetRow): string {
  if (target.next_action) return target.next_action;

  if (!target.enabled && !target.monitoring_enabled) return 'Enable target';
  if (!target.monitored_system_id && !target.systems_count) return 'Enable monitored system';

  const healthStatus = (target.health_status ?? '').toLowerCase();

  if (healthStatus === 'degraded') return 'Check provider';
  if (healthStatus === 'error') return 'Repair target';
  if (healthStatus === 'healthy') return 'View telemetry';

  return 'Wait for poll';
}

function runtimeStatusPill(system: MonitoredSystemRow): { label: string; variant: PillVariant } {
  if (!system.is_enabled) return { label: 'Disabled', variant: 'neutral' };
  if (!system.last_heartbeat) return { label: 'Not Started', variant: 'neutral' };

  const runtimeStatus = (system.runtime_status ?? '').toLowerCase();

  if (runtimeStatus === 'reporting') return { label: 'Reporting', variant: 'success' };
  if (runtimeStatus === 'degraded') return { label: 'Degraded', variant: 'warning' };
  if (runtimeStatus === 'offline') return { label: 'Offline', variant: 'danger' };

  return { label: 'Unknown', variant: 'neutral' };
}

function coveragePill(system: MonitoredSystemRow): { label: string; variant: PillVariant } {
  if (!system.is_enabled) return { label: 'Missing', variant: 'danger' };
  if (!system.last_heartbeat) return { label: 'Unknown', variant: 'neutral' };

  const coverageReason = (system.coverage_reason ?? '').toLowerCase();

  if (coverageReason === 'covered' || coverageReason === 'full') {
    return { label: 'Covered', variant: 'success' };
  }

  if (coverageReason === 'partial') return { label: 'Partial', variant: 'warning' };
  if (coverageReason === 'stale') return { label: 'Stale', variant: 'warning' };
  if (coverageReason === 'missing') return { label: 'Missing', variant: 'danger' };

  if (system.last_event_at) return { label: 'Partial', variant: 'warning' };

  return { label: 'Unknown', variant: 'neutral' };
}

function resolveEvidenceSource(system: MonitoredSystemRow): { label: string; variant: PillVariant } {
  const raw = (system.evidence_source ?? system.freshness_status ?? '').toLowerCase();

  if (raw === 'simulator' || raw === 'demo' || raw === 'replay') {
    return { label: 'simulator', variant: 'info' };
  }

  if (raw === 'live' || raw === 'live_provider') {
    if (!system.last_heartbeat || !system.last_event_at) {
      return { label: 'none', variant: 'neutral' };
    }

    return { label: 'live_provider', variant: 'success' };
  }

  return { label: 'none', variant: 'neutral' };
}

const TARGET_HEADERS = ['Target Name', 'Type', 'Provider', 'Systems', 'Status', 'Last Poll', 'Next Action'];
const SYSTEM_HEADERS = [
  'System Name',
  'Linked Target',
  'Enabled',
  'Runtime Status',
  'Last Heartbeat',
  'Last Telemetry',
  'Coverage',
  'Evidence Source',
];

const TABS = [
  { key: 'targets', label: 'Monitoring Targets' },
  { key: 'systems', label: 'Monitored Systems' },
];

export default function MonitoringSourcesPage() {
  const [activeTab, setActiveTab] = useState<TabKey>('targets');
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [targets, setTargets] = useState<TargetRow[]>([]);
  const [systems, setSystems] = useState<MonitoredSystemRow[]>([]);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const [enablingTargetId, setEnablingTargetId] = useState<string | null>(null);
  const [enableError, setEnableError] = useState('');
  const [orphanTargetId, setOrphanTargetId] = useState<string | null>(null);
  const [repairingTargetId, setRepairingTargetId] = useState<string | null>(null);
  const [repairingTargets, setRepairingTargets] = useState(false);
  const [repairResult, setRepairResult] = useState('');

  const { authHeaders } = usePilotAuth();

  async function loadSources(signal?: AbortSignal) {
    setLoading(true);
    try {
      const response = await fetch('/api/monitoring/sources', {
        headers: authHeaders(),
        cache: 'no-store',
        signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof payload?.detail === 'string' ? payload.detail : `HTTP ${response.status}`;
        setLoadError(`Unable to load monitoring sources: ${detail}`);
        return;
      }
      setAssets(payload.assets ?? []);
      setTargets(payload.targets ?? []);
      setSystems(payload.systems ?? []);
      setLoadError('');
    } catch (error) {
      if ((error as { name?: string }).name === 'AbortError') return;
      setLoadError(`Network error loading monitoring sources: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void loadSources(controller.signal);
    return () => controller.abort();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authHeaders]);

  async function handleEnableTarget(targetId: string) {
    setEnablingTargetId(targetId);
    setEnableError('');
    setOrphanTargetId(null);
    const url = `/api/monitoring/targets/${encodeURIComponent(targetId)}/enable`;
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detailObj = payload?.detail;
        const detail =
          typeof detailObj === 'string'
            ? detailObj
            : typeof detailObj === 'object' && detailObj !== null
              ? ((detailObj as { message?: string; code?: string }).message ?? `HTTP ${response.status}`)
              : `HTTP ${response.status}`;
        setEnableError(`Enable failed (${response.status} ${url}): ${detail}`);
        const errorCode =
          typeof detailObj === 'object' && detailObj !== null
            ? (detailObj as { code?: string }).code
            : undefined;
        const isOrphan =
          errorCode === 'TARGET_LINKED_ASSET_MISSING' ||
          detail.includes('linked asset is missing or deleted') ||
          (response.status === 400 && detail.toLowerCase().includes('asset')) ||
          response.status === 500;
        if (isOrphan) {
          setOrphanTargetId(targetId);
        }
        return;
      }
      await loadSources();
    } catch (error) {
      setEnableError(`Network error enabling target: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setEnablingTargetId(null);
    }
  }

  async function handleRepairTarget(targetId: string) {
    setRepairingTargetId(targetId);
    setRepairResult('');
    setEnableError('');
    const url = `/api/monitoring/targets/${encodeURIComponent(targetId)}/repair`;
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof payload?.detail === 'string'
          ? payload.detail
          : typeof payload?.detail === 'object' && payload.detail !== null
            ? (payload.detail as { message?: string }).message ?? `HTTP ${response.status}`
            : `HTTP ${response.status}`;
        setRepairResult(`Repair failed: ${detail}`);
        return;
      }
      const relinked = Number((payload as Record<string, unknown>).targets_relinked ?? 0);
      const created = Number((payload as Record<string, unknown>).assets_created ?? 0);
      const systems = Number((payload as Record<string, unknown>).systems_created ?? 0);
      setRepairResult(`Repair complete: ${relinked} relinked, ${created} asset(s) created, ${systems} system(s) created.`);
      setOrphanTargetId(null);
      await loadSources();
      // Auto-retry enable after successful repair
      void handleEnableTarget(targetId);
    } catch (error) {
      setRepairResult(`Network error during repair: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setRepairingTargetId(null);
    }
  }

  async function handleRepairTargets() {
    setRepairingTargets(true);
    setRepairResult('');
    setEnableError('');
    try {
      const response = await fetch('/api/monitoring/systems/reconcile', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof payload?.detail === 'string' ? payload.detail : `HTTP ${response.status}`;
        setRepairResult(`Repair failed: ${detail}`);
        return;
      }
      const reconcile = (payload.reconcile ?? payload) as Record<string, unknown>;
      const relinked = Number(reconcile.targets_relinked ?? 0);
      const created = Number(reconcile.assets_created ?? 0);
      const updated = Number(reconcile.created_or_updated ?? reconcile.eligible_targets ?? 0);
      setRepairResult(
        `Repair complete: ${relinked} target(s) relinked, ${created} asset(s) created, ${updated} monitored system(s) updated.`,
      );
      await loadSources();
    } catch (error) {
      setRepairResult(`Network error during repair: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally {
      setRepairingTargets(false);
    }
  }

  const enableErrorIsOrphan =
    orphanTargetId !== null ||
    enableError.includes('linked asset is missing or deleted') ||
    (enableError.includes('400') && enableError.toLowerCase().includes('asset'));

  const targetNameById = useMemo(
    () => new Map(targets.map((target) => [target.id, target.name || 'Unnamed target'])),
    [targets],
  );

  const noAssets = !loading && assets.length === 0;
  const hasAssetsNoTargets = !loading && assets.length > 0 && targets.length === 0;
  const hasTargetsNoSystems = !loading && targets.length > 0 && systems.length === 0;
  const hasSystemsNoHeartbeat = !loading && systems.length > 0 && systems.every((system) => !system.last_heartbeat);
  const hasHeartbeatNoTelemetry =
    !loading && systems.some((system) => system.last_heartbeat) && systems.every((system) => !system.last_event_at);

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      <div className="listHeader" style={{ marginBottom: '1.25rem', alignItems: 'flex-start' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '1.45rem', fontWeight: 700 }}>Monitoring Sources</h1>
          <p className="muted" style={{ margin: '0.35rem 0 0', fontSize: '0.9rem' }}>
            Manage detection coverage by configuring monitoring targets and monitored systems.
          </p>
        </div>

        <Link href="/monitoring-sources/targets" prefetch={false} className="btn btn-primary">
          Add Target
        </Link>
      </div>

      {loadError ? (
        <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>
          {loadError}
        </p>
      ) : null}

      {enableError ? (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="statusLine" style={{ color: 'var(--danger-fg)', fontSize: '0.85rem', margin: 0 }}>
            {enableError}
          </p>
          {enableErrorIsOrphan ? (
            <div style={{ marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ fontSize: '0.8rem', padding: '0.25rem 0.75rem' }}
                disabled={repairingTargets}
                onClick={() => void handleRepairTargets()}
              >
                {repairingTargets ? 'Repairing…' : 'Repair targets'}
              </button>
              <span className="muted" style={{ fontSize: '0.8rem' }}>
                Auto-relink orphaned targets to their matching workspace assets.
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      {repairResult ? (
        <p
          className="statusLine"
          style={{
            color: repairResult.startsWith('Repair failed') ? 'var(--danger-fg)' : 'var(--success-fg)',
            fontSize: '0.85rem',
            marginBottom: '0.75rem',
          }}
        >
          {repairResult}
        </p>
      ) : null}

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
          ) : !loading && targets.length === 0 ? null : (
            <TableShell headers={TARGET_HEADERS} compact>
              {loading ? (
                <tr>
                  <td
                    colSpan={TARGET_HEADERS.length}
                    style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}
                  >
                    Loading targets...                  </td>
                </tr>
              ) : (
                targets.map((target) => {
                  const status = targetStatusPill(target);
                  const systemsDisplay =
                    target.systems_count != null ? String(target.systems_count) : target.monitored_system_id ? '1' : '0';

                  return (
                    <tr key={target.id}>
                      <td style={{ fontWeight: 600 }}>{target.name || 'Unnamed target'}</td>
                      <td>{target.target_type || 'Unknown'}</td>
                      <td>{target.provider || <span className="muted">Default</span>}</td>
                      <td>{systemsDisplay}</td>
                      <td>
                        <StatusPill label={status.label} variant={status.variant} />
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>{fmt(target.last_checked_at)}</td>
                      <td>
                        {orphanTargetId === target.id ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                            <button
                              type="button"
                              className="btn btn-secondary"
                              style={{ fontSize: '0.78rem', padding: '0.2rem 0.65rem' }}
                              disabled={repairingTargetId === target.id}
                              onClick={() => void handleRepairTarget(target.id)}
                            >
                              {repairingTargetId === target.id ? 'Repairing…' : 'Repair target'}
                            </button>
                          </div>
                        ) : targetNextAction(target) === 'Enable target' ? (
                          <button
                            type="button"
                            className="btn btn-secondary"
                            style={{ fontSize: '0.78rem', padding: '0.2rem 0.65rem' }}
                            disabled={enablingTargetId === target.id}
                            onClick={() => void handleEnableTarget(target.id)}
                          >
                            {enablingTargetId === target.id ? 'Enabling…' : 'Enable target'}
                          </button>
                        ) : targetNextAction(target) === 'View telemetry' && target.id ? (
                          <Link
                            href={`/monitoring-sources/${encodeURIComponent(target.id)}/telemetry`}
                            prefetch={false}
                            style={{ color: 'var(--text-accent)', fontSize: '0.82rem', textDecoration: 'none' }}
                          >
                            View telemetry
                          </Link>
                        ) : (
                          <span style={{ color: 'var(--text-accent)', fontSize: '0.82rem' }}>
                            {targetNextAction(target)}
                          </span>
                        )}
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
          ) : hasAssetsNoTargets ? (
            <EmptyStateBlocker
              title="No monitoring target is linked to this asset yet"
              body="Create a monitoring target so Decoda can begin collecting runtime signals for this asset."
              ctaHref="/monitoring-sources/targets"
              ctaLabel="Create monitoring target"
            />
          ) : hasTargetsNoSystems ? (
            <EmptyStateBlocker
              title="Target exists, but no monitored system is enabled"
              body="Enable a monitored system to start heartbeat, polling, and telemetry collection."
              ctaHref="/monitoring-sources/monitored-systems"
              ctaLabel="Enable monitored system"
            />
          ) : hasSystemsNoHeartbeat ? (
            <EmptyStateBlocker
              title="Monitored system is not reporting"
              body="No worker heartbeat has been received yet."
              ctaHref="/system-health"
              ctaLabel="Check worker status"
            />
          ) : hasHeartbeatNoTelemetry ? (
            <EmptyStateBlocker
              title="Waiting for first telemetry"
              body="The worker is reporting, but no telemetry event has been received yet."
              ctaHref="/threat"
              ctaLabel="Generate simulator signal"
            />
          ) : !loading && systems.length === 0 ? null : (
            <TableShell headers={SYSTEM_HEADERS} compact>
              {loading ? (
                <tr>
                  <td
                    colSpan={SYSTEM_HEADERS.length}
                    style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}
                  >
                    Loading monitored systems...                  </td>
                </tr>
              ) : (
                systems.map((system) => {
                  const runtimeStatus = runtimeStatusPill(system);
                  const coverage = coveragePill(system);
                  const evidence = resolveEvidenceSource(system);
                  const linkedTarget = system.target_name || targetNameById.get(system.target_id ?? '') || 'Unlinked';

                  return (
                    <tr key={system.id}>
                      <td style={{ fontWeight: 600 }}>{system.asset_name || `System ${system.id.slice(0, 8)}`}</td>
                      <td>{linkedTarget}</td>
                      <td>
                        <StatusPill
                          label={system.is_enabled ? 'Yes' : 'No'}
                          variant={system.is_enabled ? 'success' : 'neutral'}
                        />
                      </td>
                      <td>
                        <StatusPill label={runtimeStatus.label} variant={runtimeStatus.variant} />
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>{fmt(system.last_heartbeat)}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>{fmt(system.last_event_at)}</td>
                      <td>
                        <StatusPill label={coverage.label} variant={coverage.variant} />
                      </td>
                      <td>
                        <StatusPill label={evidence.label} variant={evidence.variant} />
                      </td>
                    </tr>
                  );
                })
              )}
            </TableShell>
          )}
        </div>
      ) : null}
    </main>
  );
}
