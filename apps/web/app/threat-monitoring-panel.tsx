'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TabStrip,
  TableShell,
  type PillVariant,
} from './components/ui-primitives';
import { resolveApiUrl } from './dashboard-data';
import { usePilotAuth } from './pilot-auth-context';
import { useRuntimeSummary } from './runtime-summary-context';

type TabKey = 'overview' | 'telemetry' | 'detections' | 'anomalies';
type NodeStatus = 'Complete' | 'Pending' | 'Blocked' | 'Degraded';

type TelemetryEvent = {
  id: string;
  asset_name?: string | null;
  source?: string | null;
  event_type?: string | null;
  evidence_source?: string | null;
  received_at?: string | null;
  status?: string | null;
};

type DetectionRow = {
  id: string;
  detection_type?: string | null;
  asset_name?: string | null;
  severity?: string | null;
  confidence?: string | null;
  evidence_source?: string | null;
  created_at?: string | null;
};

type AnomalyRow = {
  id: string;
  pattern?: string | null;
  asset_name?: string | null;
  score?: number | null;
  status?: string | null;
  first_seen?: string | null;
};

const PIPELINE_NODES = [
  'Asset',
  'Target',
  'System',
  'Heartbeat',
  'Poll',
  'Telemetry',
  'Detection',
  'Alert',
  'Incident',
] as const;

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'telemetry', label: 'Telemetry' },
  { key: 'detections', label: 'Detections' },
  { key: 'anomalies', label: 'Anomalies' },
];

const TELEMETRY_HEADERS = [
  'Event ID',
  'Asset',
  'Source',
  'Event Type',
  'Evidence Source',
  'Received At',
  'Status',
];

const DETECTION_HEADERS = [
  'Detection ID',
  'Type',
  'Asset',
  'Severity',
  'Confidence',
  'Evidence Source',
  'Created At',
  'Action',
];

const ANOMALY_HEADERS = [
  'Anomaly ID',
  'Pattern',
  'Asset',
  'Score',
  'Status',
  'First Seen',
  'Action',
];

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

// Simulator evidence must always show evidence_source = simulator.
// Real provider evidence must show live_provider only when real data exists.
// Do not label simulator evidence as live_provider.
function evidencePill(
  rowSource?: string | null,
  workspaceSource?: string,
): { label: string; variant: PillVariant } {
  const raw = (rowSource ?? '').toLowerCase();
  if (
    raw === 'simulator' ||
    raw === 'demo' ||
    raw === 'replay' ||
    workspaceSource === 'simulator'
  ) {
    return { label: 'simulator', variant: 'info' };
  }
  if (raw === 'live' || raw === 'live_provider') {
    return { label: 'live_provider', variant: 'success' };
  }
  return { label: 'none', variant: 'neutral' };
}

function nodeStatusVariant(status: NodeStatus): PillVariant {
  if (status === 'Complete') return 'success';
  if (status === 'Degraded') return 'warning';
  if (status === 'Blocked') return 'danger';
  return 'neutral';
}

function severityVariant(severity?: string | null): PillVariant {
  const s = (severity ?? '').toLowerCase();
  if (s === 'critical' || s === 'high') return 'danger';
  if (s === 'medium') return 'warning';
  if (s === 'low') return 'success';
  return 'neutral';
}

// Deterministic bar height from index (no Math.random, avoids hydration mismatch)
function barHeightPct(index: number, total: number): number {
  const wave = Math.sin((index / Math.max(total - 1, 1)) * Math.PI);
  return Math.round(20 + wave * 55 + (index % 3) * 8);
}

export default function ThreatMonitoringPanel() {
  const { summary, runtime, loading: runtimeLoading } = useRuntimeSummary();
  const { authHeaders } = usePilotAuth();
  const apiUrl = resolveApiUrl();

  const [activeTab, setActiveTab] = useState<TabKey>('overview');
  const [telemetry, setTelemetry] = useState<TelemetryEvent[]>([]);
  const [detections, setDetections] = useState<DetectionRow[]>([]);
  const [anomalies, setAnomalies] = useState<AnomalyRow[]>([]);
  const [dataLoading, setDataLoading] = useState(false);
  const [loadError, setLoadError] = useState('');

  // Prefer fine-grained runtime counts/timestamps when available
  const counts = runtime?.counts;
  const timestamps = runtime?.timestamps;
  const workspaceEvidenceSource = summary.evidence_source_summary;

  const protectedAssets = counts?.protected_assets ?? summary.protected_assets_count;
  const monitoringTargets = counts?.monitoring_targets ?? 0;
  const monitoredSystems = counts?.monitored_systems ?? summary.monitored_systems_count;
  const lastHeartbeatAt = timestamps?.last_heartbeat_at ?? summary.last_heartbeat_at;
  const lastPollAt = timestamps?.last_poll_at ?? summary.last_poll_at;
  const lastTelemetryAt = timestamps?.last_telemetry_at ?? summary.last_telemetry_at;
  const lastDetectionAt = timestamps?.last_detection_at ?? (summary.last_detection_at ?? null);
  const activeAlerts = counts?.active_alerts ?? summary.active_alerts_count;
  const openIncidents = counts?.open_incidents ?? summary.active_incidents_count;
  const isSimulatorMode =
    workspaceEvidenceSource === 'simulator' || summary.monitoring_mode === 'simulator';
  useEffect(() => {
    if (runtimeLoading) return;
    let cancelled = false;
    setDataLoading(true);

    async function loadData() {
      try {
        const hdrs = authHeaders();
        const results = await Promise.allSettled([
          fetch(`${apiUrl}/telemetry`, { headers: hdrs, cache: 'no-store' }),
          fetch(`${apiUrl}/detections`, { headers: hdrs, cache: 'no-store' }),
          fetch(`${apiUrl}/anomalies`, { headers: hdrs, cache: 'no-store' }),
        ]);

        if (cancelled) return;

        const [telRes, detRes, anomRes] = results;

        if (telRes.status === 'fulfilled' && telRes.value.ok) {
          const json = (await telRes.value.json()) as Record<string, unknown>;
          if (!cancelled) {
            setTelemetry(
              (json.events ?? json.telemetry ?? []) as TelemetryEvent[],
            );
          }
        }

        if (detRes.status === 'fulfilled' && detRes.value.ok) {
          const json = (await detRes.value.json()) as Record<string, unknown>;
          if (!cancelled) {
            setDetections((json.detections ?? []) as DetectionRow[]);
          }
        }

        if (anomRes.status === 'fulfilled' && anomRes.value.ok) {
          const json = (await anomRes.value.json()) as Record<string, unknown>;
          if (!cancelled) {
            setAnomalies((json.anomalies ?? []) as AnomalyRow[]);
          }
        }

        if (!cancelled) setLoadError('');
      } catch {
        if (!cancelled) setLoadError('Unable to load threat data.');
      } finally {
        if (!cancelled) setDataLoading(false);
      }
    }

    void loadData();
    return () => {
      cancelled = true;
    };
  }, [apiUrl, authHeaders, runtimeLoading]);

  // Pipeline node completion logic per spec
  const assetOk = protectedAssets > 0;
  const targetOk = monitoringTargets > 0;
  const systemOk = monitoredSystems > 0;
  const heartbeatOk = !!lastHeartbeatAt;
  const pollOk = !!lastPollAt;
  const telemetryOk = !!lastTelemetryAt || telemetry.length > 0;
  const detectionOk = !!lastDetectionAt || detections.length > 0;
  const alertOk = activeAlerts > 0;
  const incidentOk = openIncidents > 0;

  const nodeStatuses: Record<(typeof PIPELINE_NODES)[number], NodeStatus> = {
    Asset: assetOk ? 'Complete' : 'Pending',
    Target: !assetOk ? 'Blocked' : targetOk ? 'Complete' : 'Pending',
    System: !targetOk ? 'Blocked' : systemOk ? 'Complete' : 'Pending',
    Heartbeat: !systemOk ? 'Blocked' : heartbeatOk ? 'Complete' : 'Pending',
    Poll: !heartbeatOk ? 'Blocked' : pollOk ? 'Complete' : 'Pending',
    Telemetry: !heartbeatOk ? 'Blocked' : telemetryOk ? 'Complete' : 'Pending',
    Detection: !telemetryOk ? 'Blocked' : detectionOk ? 'Complete' : 'Pending',
    Alert: !detectionOk ? 'Blocked' : alertOk ? 'Complete' : 'Pending',
    Incident: !alertOk ? 'Blocked' : incidentOk ? 'Complete' : 'Pending',
  };

  // Empty state / next required action per spec cases A闁炽儲寮?
  type Blocker = { title: string; body: string; ctaHref: string; ctaLabel: string };

  function getBlocker(): Blocker | null {
    // Case A
    if (!assetOk) {
      return {
        title: 'No protected asset exists yet.',
        body: 'Add a protected asset to begin threat monitoring.',
        ctaHref: '/assets',
        ctaLabel: 'Add Asset',
      };
    }
    // Case B
    if (!targetOk) {
      return {
        title: 'No monitoring target is linked to this asset yet.',
        body: 'Create a monitoring target so Decoda can begin collecting runtime signals for this asset.',
        ctaHref: '/monitoring-sources',
        ctaLabel: 'Create Monitoring Target',
      };
    }
    // Case C
    if (!systemOk) {
      return {
        title: 'Target exists, but no monitored system is enabled.',
        body: 'Enable a monitored system to start heartbeat, polling, and telemetry collection.',
        ctaHref: '/monitoring-sources',
        ctaLabel: 'Enable Monitored System',
      };
    }
    // Case D
    if (!heartbeatOk) {
      return {
        title: 'Monitored system is not reporting yet.',
        body: 'No worker heartbeat has been received. Check the worker status.',
        ctaHref: '/system-health',
        ctaLabel: 'Check Worker Status',
      };
    }
    // Case E 闁?only show simulator CTA if simulator mode is enabled
    if (!telemetryOk) {
      return {
        title: 'Worker is reporting, but no telemetry event has been received yet.',
        body: isSimulatorMode
          ? 'Trigger a simulator signal to generate the first telemetry event.'
          : 'Waiting for first telemetry event from the monitoring worker.',
        ctaHref: '/threat',
        ctaLabel: isSimulatorMode ? 'Generate Simulator Signal' : 'Check Worker Status',
      };
    }
    // Case F
    if (!detectionOk) {
      return {
        title: 'Telemetry has been received, but no detection has been generated yet.',
        body: 'Run detection evaluation to generate detections from received telemetry.',
        ctaHref: '/threat',
        ctaLabel: 'Run Detection',
      };
    }
    // Case G
    if (!alertOk) {
      return {
        title: 'Detection exists, but no alert has been opened yet.',
        body: 'Open an alert for the existing detections.',
        ctaHref: '/alerts',
        ctaLabel: 'Open Alert',
      };
    }
    return null;
  }

  // Metric: data freshness 闁?do not show live telemetry when last_telemetry_at is unavailable
  function freshnessLabel(): string {
    if (!lastTelemetryAt) return 'No telemetry';
    return fmt(lastTelemetryAt);
  }

  // Top detection types aggregation
  const detectionTypeCounts = detections.reduce<Record<string, number>>((acc, d) => {
    const type = d.detection_type ?? 'Unknown';
    acc[type] = (acc[type] ?? 0) + 1;
    return acc;
  }, {});
  const topDetectionTypes = Object.entries(detectionTypeCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);

  const blocker = getBlocker();
  const visibleTelemetry = telemetry.slice(0, 12);

  return (
    <div>
      {/* Top metric cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: '1rem',
          marginBottom: '1.25rem',
        }}
      >
        <MetricTile
          label="Telemetry Events"
          value={runtimeLoading || dataLoading ? '-' : String(telemetry.length)}
          meta={lastTelemetryAt ? fmt(lastTelemetryAt) : 'No events'}
        />
        <MetricTile
          label="Detections"
          value={runtimeLoading || dataLoading ? '-' : String(detections.length)}
          meta={lastDetectionAt ? fmt(lastDetectionAt) : 'No detections'}
        />
        <MetricTile
          label="Anomalies"
          value={runtimeLoading || dataLoading ? '闁? : String(anomalies.length)}
          meta={anomalies.length > 0 ? 'Active' : 'None detected'}
        />
        <MetricTile
          label="Data Freshness"
          value={freshnessLabel()}
          meta={summary.telemetry_freshness}
        />
      </div>

      {loadError ? (
        <p className="statusLine" style={{ color: 'var(--danger-fg)', marginBottom: '1rem' }}>
          {loadError}
        </p>
      ) : null}

      {/* Tab strip */}
      <TabStrip
        tabs={TABS}
        active={activeTab}
        onChange={(key) => setActiveTab(key as TabKey)}
      />

      {/* 闁冲厜鍋撻柍鍏夊亾 Overview tab 闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋?*/}
      {activeTab === 'overview' ? (
        <div role="tabpanel" aria-label="Overview">
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '2fr 1fr',
              gap: '1rem',
              marginBottom: '1rem',
            }}
          >
            {/* Telemetry Volume card */}
            <article
              className="dataCard"
              aria-label="Telemetry Volume"
              style={{ minHeight: '12rem' }}
            >
              <p className="sectionEyebrow">Telemetry Volume</p>
              {visibleTelemetry.length === 0 ? (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    height: '8rem',
                    color: 'var(--text-muted)',
                    fontSize: '0.9rem',
                  }}
                >
                  No telemetry events received
                </div>
              ) : (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'flex-end',
                    gap: '0.4rem',
                    height: '8rem',
                    padding: '0.5rem 0 0',
                  }}
                >
                  {visibleTelemetry.map((event, i) => (
                    <div
                      key={event.id}
                      title={fmt(event.received_at)}
                      style={{
                        flex: 1,
                        background: `rgba(59,130,246,${0.45 + (i / visibleTelemetry.length) * 0.45})`,
                        borderRadius: '2px 2px 0 0',
                        height: `${barHeightPct(i, visibleTelemetry.length)}%`,
                        minHeight: '4px',
                      }}
                    />
                  ))}
                </div>
              )}
              <p className="muted" style={{ fontSize: '0.8rem', marginTop: '0.5rem' }}>
                {telemetry.length} total events
                {lastTelemetryAt ? ` 鐠?Last: ${fmt(lastTelemetryAt)}` : ' 鐠?None received'}
              </p>
            </article>
            {/* Top Detection Types card */}
            <article
              className="dataCard"
              aria-label="Top Detection Types"
              style={{ minHeight: '12rem' }}
            >
              <p className="sectionEyebrow">Top Detection Types</p>
              {topDetectionTypes.length === 0 ? (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    height: '8rem',
                    color: 'var(--text-muted)',
                    fontSize: '0.9rem',
                  }}
                >
                  No detections yet
                </div>
              ) : (
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0.55rem',
                    marginTop: '0.75rem',
                  }}
                >
                  {topDetectionTypes.map(([type, count]) => (
                    <div
                      key={type}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        fontSize: '0.85rem',
                      }}
                    >
                      <span style={{ color: 'var(--text-secondary)' }}>{type}</span>
                      <span
                        style={{
                          color: 'var(--text-accent)',
                          fontWeight: 700,
                          minWidth: '2rem',
                          textAlign: 'right',
                        }}
                      >
                        {count}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </article>
          </div>

          {/* Pipeline status 闁?full-width compact chain */}
          <article className="dataCard" aria-label="Pipeline Status" style={{ marginBottom: '1rem' }}>
            <p className="sectionEyebrow">
              Runtime Chain 闁?Asset 闁?Target 闁?System 闁?Heartbeat 闁?Poll 闁?Telemetry 闁?Detection 闁?
              Alert 闁?Incident
            </p>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.3rem',
                flexWrap: 'wrap',
                marginTop: '0.65rem',
              }}
            >
              {PIPELINE_NODES.map((node, i) => {
                const status = nodeStatuses[node];
                return (
                  <div
                    key={node}
                    style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}
                  >
                    <div style={{ textAlign: 'center' }}>
                      <span
                        data-pipeline-node={node}
                        style={{
                          display: 'block',
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          color: 'var(--text-secondary)',
                          marginBottom: '0.25rem',
                          letterSpacing: '0.02em',
                        }}
                      >
                        {node}
                      </span>
                      <StatusPill label={status} variant={nodeStatusVariant(status)} />
                    </div>
                    {i < PIPELINE_NODES.length - 1 ? (
                      <span
                        style={{
                          color: 'var(--text-muted)',
                          fontSize: '0.9rem',
                          userSelect: 'none',
                        }}
                      >
                        闁?
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </article>

          {/* Next Required Action card */}
          {blocker ? (
            <article
              className="dataCard"
              aria-label="Next Required Action"
              style={{ borderColor: 'var(--warning-bdr)' }}
            >
              <p className="sectionEyebrow" style={{ color: 'var(--warning-fg)' }}>
                Next Required Action
              </p>
              <h4 style={{ margin: '0.25rem 0 0.4rem', fontSize: '0.95rem' }}>{blocker.title}</h4>
              <p className="muted" style={{ marginBottom: '0.75rem' }}>
                {blocker.body}
              </p>
              <Link href={blocker.ctaHref} prefetch={false} className="btn btn-secondary">
                {blocker.ctaLabel}
              </Link>
            </article>
          ) : (
            <article
              className="dataCard"
              aria-label="Next Required Action"
              style={{ borderColor: 'var(--success-bdr)' }}
            >
              <p className="sectionEyebrow" style={{ color: 'var(--success-fg)' }}>
                Next Required Action
              </p>
              <p className="muted">
                {isSimulatorMode
                  ? 'All pipeline stages are active (simulator mode). Review simulated detections and signals.'
                  : 'All pipeline stages are operational. Review detections and respond to active alerts.'}
              </p>
            </article>
          )}
        </div>
      ) : null}

      {/* 闁冲厜鍋撻柍鍏夊亾 Telemetry tab 闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋?*/}
      {activeTab === 'telemetry' ? (
        <div role="tabpanel" aria-label="Telemetry">
          {dataLoading ? (
            <p className="muted" style={{ padding: '2rem 0' }}>
              Loading telemetry闁?
            </p>
          ) : telemetry.length === 0 ? (
            <EmptyStateBlocker
              title="No telemetry events"
              body={
                !lastTelemetryAt
                  ? 'No telemetry event has been received yet. The worker may be reporting but no events have arrived.'
                  : 'No telemetry events found.'
              }
              ctaHref={isSimulatorMode ? '/threat' : '/monitoring-sources'}
              ctaLabel={isSimulatorMode ? 'Generate Simulator Signal' : 'Check Monitoring Sources'}
            />
          ) : (
            <TableShell headers={TELEMETRY_HEADERS} compact>
              {telemetry.map((event) => {
                const ep = evidencePill(event.evidence_source, workspaceEvidenceSource);
                return (
                  <tr key={event.id}>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                      {event.id.slice(0, 12)}
                    </td>
                    <td>{event.asset_name ?? '-'}</td>
                    <td>{event.source ?? '-'}</td>
                    <td>{event.event_type ?? '-'}</td>
                    <td>
                      <StatusPill label={ep.label} variant={ep.variant} />
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmt(event.received_at)}</td>
                    <td>
                      <StatusPill
                        label={event.status ?? 'received'}
                        variant={event.status === 'processed' ? 'success' : 'neutral'}
                      />
                    </td>
                  </tr>
                );
              })}
            </TableShell>
          )}
        </div>
      ) : null}

      {/* 闁冲厜鍋撻柍鍏夊亾 Detections tab 闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋?*/}
      {activeTab === 'detections' ? (
        <div role="tabpanel" aria-label="Detections">
          {dataLoading ? (
            <p className="muted" style={{ padding: '2rem 0' }}>
              Loading detections闁?
            </p>
          ) : detections.length === 0 ? (
            <EmptyStateBlocker
              title="No detections"
              body="No detection has been generated yet. Ensure telemetry is flowing and detection evaluation is enabled."
              ctaHref="/monitoring-sources"
              ctaLabel="Check Monitoring Sources"
            />
          ) : (
            <TableShell headers={DETECTION_HEADERS} compact>
              {detections.map((det) => {
                const ep = evidencePill(det.evidence_source, workspaceEvidenceSource);
                return (
                  <tr key={det.id}>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                      {det.id.slice(0, 12)}
                    </td>
                    <td>{det.detection_type ?? '-'}</td>
                    <td>{det.asset_name ?? '-'}</td>
                    <td>
                      <StatusPill
                        label={det.severity ?? '-'}
                        variant={severityVariant(det.severity)}
                      />
                    </td>
                    <td>{det.confidence ?? '-'}</td>
                    <td>
                      <StatusPill label={ep.label} variant={ep.variant} />
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmt(det.created_at)}</td>
                    <td>
                      <Link
                        href="/alerts"
                        prefetch={false}
                        className="btn btn-secondary"
                        style={{ fontSize: '0.78rem', padding: '0.2rem 0.6rem' }}
                      >
                        Open Alert
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </TableShell>
          )}
        </div>
      ) : null}

      {/* 闁冲厜鍋撻柍鍏夊亾 Anomalies tab 闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾闁冲厜鍋撻柍鍏夊亾 */}
      {activeTab === 'anomalies' ? (
        <div role="tabpanel" aria-label="Anomalies">
          {dataLoading ? (
            <p className="muted" style={{ padding: '2rem 0' }}>
              Loading anomalies闁?
            </p>
          ) : anomalies.length === 0 ? (
            <EmptyStateBlocker
              title="No anomalies detected"
              body="No anomalies have been detected yet. Anomaly detection runs automatically when telemetry is flowing."
              ctaHref="/monitoring-sources"
              ctaLabel="Check Monitoring Sources"
            />
          ) : (
            <TableShell headers={ANOMALY_HEADERS} compact>
              {anomalies.map((anom) => {
                const score = anom.score ?? 0;
                const scoreColor =
                  score > 0.8
                    ? 'var(--danger-fg)'
                    : score > 0.5
                      ? 'var(--warning-fg)'
                      : 'var(--success-fg)';
                return (
                  <tr key={anom.id}>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                      {anom.id.slice(0, 12)}
                    </td>
                    <td>{anom.pattern ?? '-'}</td>
                    <td>{anom.asset_name ?? '-'}</td>
                    <td style={{ color: scoreColor, fontWeight: 600 }}>
                      {anom.score != null ? anom.score.toFixed(2) : '-'}
                    </td>
                    <td>
                      <StatusPill
                        label={anom.status ?? 'active'}
                        variant={anom.status === 'resolved' ? 'success' : 'warning'}
                      />
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmt(anom.first_seen)}</td>
                    <td>
                      <Link
                        href="/incidents"
                        prefetch={false}
                        className="btn btn-secondary"
                        style={{ fontSize: '0.78rem', padding: '0.2rem 0.6rem' }}
                      >
                        Investigate
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </TableShell>
          )}
        </div>
      ) : null}
    </div>
  );
}
