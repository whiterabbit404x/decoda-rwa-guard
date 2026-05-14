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
import { resolveApiUrl } from '../../dashboard-data';
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

  const apiUrl = resolveApiUrl();
  const { authHeaders } = usePilotAuth();

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);

      try {
        const [assetsResponse, targetsResponse, systemsResponse] = await Promise.all([
          fetch(`${apiUrl}/assets`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/targets`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/monitoring/systems`, { headers: authHeaders(), cache: 'no-store' }),
        ]);

        if (!assetsResponse.ok || !targetsResponse.ok || !systemsResponse.ok) {
          if (!cancelled) {
            setLoadError('Unable to load monitoring sources.');
          }
          return;
        }

        const [assetsPayload, targetsPayload, systemsPayload] = await Promise.all([
          assetsResponse.json(),
          targetsResponse.json(),
          systemsResponse.json(),
        ]);

        if (cancelled) return;

        setAssets(assetsPayload.assets ?? []);
        setTargets(targetsPayload.targets ?? []);
        setSystems(systemsPayload.systems ?? []);
        setLoadError('');
      } catch {
        if (!cancelled) {
          setLoadError('Network error loading monitoring sources.');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [apiUrl, authHeaders]);

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
                      <td style={{ color: 'var(--text-accent)', fontSize: '0.82rem' }}>{targetNextAction(target)}</td>
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
