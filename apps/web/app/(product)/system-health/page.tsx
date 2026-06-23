import Link from 'next/link';
import { normalizeMonitoringPresentation } from '../../monitoring-status-presentation';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { fetchDashboardPageData, fetchJson, resolveApiUrl } from '../../dashboard-data';
import { resolveWorkspaceMonitoringTruthFromSummary } from '../../workspace-monitoring-truth';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ComponentStatus = 'healthy' | 'degraded' | 'failing' | 'unavailable';

type ComponentDetail = {
  status: ComponentStatus;
  message: string;
  age?: string | null;
  last_event?: string | null;
  metric?: string | null;
  action?: string | null;
};

type LiveChainMonitoring = {
  expected_chain_id: number;
  rpc_configured: boolean;
  latest_rpc_block: string | null;
  worker_enabled: boolean;
  last_heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  heartbeat_age_human: string | null;
  polling_interval_seconds: number;
  last_poll_at: string | null;
  last_successful_poll_at: string | null;
  latest_polled_block: number | null;
  last_telemetry_at: string | null;
  last_detection_at: string | null;
  recent_telemetry_1h: number;
  recent_telemetry_24h: number;
  recent_detections_1h: number;
  recent_detections_24h: number;
  diagnosis: string;
};

type HealthEvent = {
  time: string;
  component: string;
  event: string;
  severity: string;
  kind?: string;
};

type ProviderEntry = {
  name: string;
  type: string;
  status: string;
  message: string;
  last_event?: string | null;
  metric?: string | null;
  action?: string | null;
};

type SystemHealthPayload = {
  generated_at: string;
  environment: string;
  version: string | null;
  git_commit: string | null;
  overall_status: 'healthy' | 'degraded' | 'failing' | 'unavailable';
  summary: string;
  primary_action: string | null;
  components: Record<string, ComponentDetail>;
  live_chain_monitoring: LiveChainMonitoring;
  events: HealthEvent[];
  providers: ProviderEntry[];
  reliability: Record<string, string | number | null>;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const COMPONENT_META: Record<string, { label: string; what: string }> = {
  api: { label: 'API', what: 'HTTP endpoint reachability' },
  database: { label: 'Database', what: 'SELECT 1 query' },
  redis: { label: 'Redis', what: 'PING connectivity' },
  worker: { label: 'Worker', what: 'Heartbeat freshness' },
  base_rpc: { label: 'Base RPC', what: 'eth_blockNumber call' },
  live_polling: { label: 'Live Polling', what: 'Last monitoring poll time' },
  telemetry: { label: 'Telemetry Ingestion', what: 'Last telemetry event age' },
  detection: { label: 'Detection', what: 'Last wallet_transfer_detected' },
  alert_delivery: { label: 'Alert Delivery', what: 'Outbox + stream health' },
};

function statusBadgeClass(status: string): string {
  if (status === 'healthy') return 'statusBadge statusBadge-live';
  if (status === 'degraded') return 'statusBadge statusBadge-degraded';
  if (status === 'failing') return 'statusBadge statusBadge-offline';
  return 'statusBadge statusBadge-unavailable';
}

function statusLabel(status: string): string {
  if (status === 'healthy') return 'Healthy';
  if (status === 'degraded') return 'Degraded';
  if (status === 'failing') return 'Failing';
  return 'Unavailable';
}

function overallBadgeClass(status: string): string {
  if (status === 'healthy') return 'statusBadge statusBadge-live';
  if (status === 'degraded') return 'statusBadge statusBadge-degraded';
  if (status === 'failing') return 'statusBadge statusBadge-offline';
  return 'statusBadge statusBadge-unavailable';
}

function overallLabel(status: string): string {
  if (status === 'healthy') return 'All Systems Operational';
  if (status === 'degraded') return 'Degraded';
  if (status === 'failing') return 'Action Required';
  return 'Unavailable';
}

function healthIconClass(status: string): string {
  if (status === 'healthy') return 'healthIcon healthIconLive';
  if (status === 'failing') return 'healthIcon healthIconOffline';
  return 'healthIcon healthIconDegraded';
}

function healthIconGlyph(status: string): string {
  if (status === 'healthy') return '✓';
  if (status === 'failing') return '✕';
  return '!';
}

function severityBadgeClass(severity: string): string {
  if (severity === 'critical' || severity === 'high') return 'statusBadge statusBadge-offline';
  if (severity === 'medium') return 'statusBadge statusBadge-degraded';
  return 'statusBadge statusBadge-unavailable';
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatShortTime(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString();
}

function diagnosisClass(diagnosis: string | undefined): string {
  if (!diagnosis) return 'banner banner-degraded';
  const lower = diagnosis.toLowerCase();
  if (lower.includes('operational') || lower.includes('all monitored')) return 'banner banner-online';
  if (lower.includes('failing') || lower.includes('failed') || lower.includes('unavailable') || lower.includes('not configured')) {
    return 'banner banner-offline';
  }
  return 'banner banner-degraded';
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchSystemHealth(apiUrl: string): Promise<SystemHealthPayload | null> {
  return fetchJson<SystemHealthPayload>('/ops/system-health', apiUrl);
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default async function SystemHealthPage() {
  const apiUrl = resolveApiUrl();
  const [data, systemHealth] = await Promise.all([
    fetchDashboardPageData(undefined, { featureFeeds: ['resilienceDashboard'] }),
    fetchSystemHealth(apiUrl),
  ]);

  const summaryMissing = data.workspaceMonitoringSummary == null;
  const truth = resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary) as any;
  const presentation = normalizeMonitoringPresentation(truth) as any;

  // Canonical truth from existing workspace monitoring summary (truth guards)
  const reportingSystems = Number(truth.reporting_systems_count ?? 0);
  const monitoredSystems = Number(truth.monitored_systems_count ?? 0);
  const hasHeartbeat = Boolean(truth.last_heartbeat_at);
  const hasTelemetry = Boolean(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at);
  const contradictionFlags: string[] = Array.isArray(truth.contradiction_flags) ? truth.contradiction_flags : [];

  // Overall operational truth guard: cannot claim operational without reporting systems and heartbeat and telemetry
  const isOperational =
    !summaryMissing &&
    reportingSystems > 0 &&
    hasHeartbeat &&
    hasTelemetry &&
    contradictionFlags.length === 0 &&
    (truth.runtime_status === 'live' || truth.monitoring_status === 'live');

  const isOffline = summaryMissing || truth.runtime_status === 'offline' || !hasHeartbeat;

  // Use system health payload if available, else derive from truth
  const overallStatus: string = systemHealth?.overall_status ?? (isOperational ? 'healthy' : isOffline ? 'failing' : 'degraded');
  const summaryText = systemHealth?.summary ?? (
    isOperational ? 'All monitored systems are operational.' :
    summaryMissing ? 'Health data unavailable. Runtime health summary could not be loaded.' :
    presentation.summary ?? 'Operational health is degraded or unavailable.'
  );
  const primaryAction = systemHealth?.primary_action ?? null;

  const components = systemHealth?.components ?? {};
  const chainMonitoring = systemHealth?.live_chain_monitoring ?? null;
  const events = systemHealth?.events ?? [];
  const providers = systemHealth?.providers ?? [];
  const reliability = systemHealth?.reliability ?? {};

  const generatedAt = systemHealth?.generated_at ? new Date(systemHealth.generated_at).toLocaleString() : null;
  const environment = systemHealth?.environment ?? null;
  const gitCommit = systemHealth?.git_commit ? systemHealth.git_commit.slice(0, 8) : null;

  const noSystemHealthData = systemHealth == null;

  // Summary cards: derive from systemHealth.components or fall back to truth
  const componentOrder = ['api', 'database', 'redis', 'worker', 'base_rpc', 'telemetry', 'detection', 'alert_delivery'] as const;

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Status &amp; operations</p>
          <h1>System Health</h1>
          <p className="lede">
            Live operational status for Decoda RWA Guard infrastructure, monitoring workers,
            RPC connectivity, telemetry ingestion, and alert delivery.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end', gap: '0.75rem', flexWrap: 'wrap' }}>
          <a href="/system-health" className="secondaryCta" style={{ fontSize: '0.9rem' }}>
            Refresh Health
          </a>
        </div>
      </section>

      {/* ── Status hero card ─────────────────────────────────────────────── */}
      <section className="dataCard" style={{ marginTop: '0.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <div className={healthIconClass(overallStatus)} style={{ width: '3rem', height: '3rem', fontSize: '1.3rem' }}>
              {healthIconGlyph(overallStatus)}
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
                <span className={overallBadgeClass(overallStatus)} style={{ fontSize: '0.9rem', padding: '0.4rem 1rem' }}>
                  {overallLabel(overallStatus)}
                </span>
                {environment && (
                  <span className="statusBadge statusBadge-unavailable" style={{ fontSize: '0.75rem' }}>{environment}</span>
                )}
                {gitCommit && (
                  <span className="tableMeta muted" style={{ fontSize: '0.78rem' }}>commit {gitCommit}</span>
                )}
              </div>
              <p style={{ margin: '0.4rem 0 0', color: 'var(--color-text-secondary, #94a3b8)', fontSize: '0.9rem', maxWidth: '600px' }}>
                {summaryText}
              </p>
            </div>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <p className="tableMeta muted" style={{ margin: 0, fontSize: '0.78rem' }}>Last checked</p>
            <p style={{ margin: '0.15rem 0 0', fontSize: '0.85rem', color: 'var(--color-text-secondary, #94a3b8)' }}>
              {generatedAt ?? 'Unavailable'}
            </p>
          </div>
        </div>

        {primaryAction && (
          <div style={{
            marginTop: '1rem',
            padding: '0.8rem 1rem',
            borderRadius: '0.6rem',
            background: 'rgba(255, 186, 73, 0.08)',
            border: '1px solid rgba(255, 186, 73, 0.22)',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.6rem',
          }}>
            <span style={{ fontSize: '0.95rem', flexShrink: 0 }}>⚠</span>
            <div>
              <strong style={{ fontSize: '0.85rem', color: '#ffd280' }}>Action required</strong>
              <p style={{ margin: '0.15rem 0 0', fontSize: '0.85rem', color: '#ffd280' }}>{primaryAction}</p>
            </div>
          </div>
        )}

        {contradictionFlags.length > 0 && (
          <div style={{
            marginTop: '0.75rem',
            padding: '0.75rem 1rem',
            borderRadius: '0.6rem',
            background: 'rgba(255, 130, 130, 0.08)',
            border: '1px solid rgba(255, 130, 130, 0.22)',
          }}>
            <strong style={{ fontSize: '0.85rem', color: '#ffb3b3' }}>Runtime contradictions detected</strong>
            <ul style={{ margin: '0.4rem 0 0', paddingLeft: '1.25rem', color: '#ffb3b3', fontSize: '0.82rem' }}>
              {contradictionFlags.map((flag: string) => <li key={flag}>{flag.replace(/_/g, ' ')}</li>)}
            </ul>
          </div>
        )}
      </section>

      {/* ── Summary cards ────────────────────────────────────────────────── */}
      <div className="fourColumnSection" style={{ marginTop: '1rem' }}>
        {componentOrder.map((key) => {
          const meta = COMPONENT_META[key];
          const comp: ComponentDetail | undefined = components[key];
          const status = comp?.status ?? 'unavailable';
          return (
            <article key={key} className="dataCard" style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              <p className="sectionEyebrow" style={{ margin: 0 }}>{meta?.label ?? key}</p>
              <span className={statusBadgeClass(status)} style={{ alignSelf: 'flex-start', fontSize: '0.78rem' }}>
                {statusLabel(status)}
              </span>
              <p style={{ margin: 0, fontSize: '0.82rem', color: 'var(--color-text-secondary, #94a3b8)', lineHeight: 1.4 }}>
                {comp?.message ?? 'Unavailable'}
              </p>
              {comp?.metric && (
                <p className="tableMeta muted" style={{ margin: 0, fontSize: '0.75rem' }}>{comp.metric}</p>
              )}
              {comp?.age && (
                <p className="tableMeta muted" style={{ margin: 0, fontSize: '0.75rem' }}>Last: {comp.age}</p>
              )}
              {noSystemHealthData && (
                <p className="tableMeta muted" style={{ margin: 0, fontSize: '0.75rem' }}>Backend health endpoint unreachable</p>
              )}
            </article>
          );
        })}
      </div>

      {/* ── Section 1: Operational Overview ─────────────────────────────── */}
      <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Component health</p>
            <h2>Operational Overview</h2>
          </div>
        </div>
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>Component</th>
                <th>Status</th>
                <th>What It Checks</th>
                <th>Current Signal</th>
                <th>Last Event</th>
                <th>Age</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(COMPONENT_META).map(([key, meta]) => {
                const comp: ComponentDetail | undefined = components[key];
                const status = comp?.status ?? 'unavailable';
                return (
                  <tr key={key}>
                    <td><strong>{meta.label}</strong></td>
                    <td><span className={statusBadgeClass(status)}>{statusLabel(status)}</span></td>
                    <td><span className="tableMeta">{meta.what}</span></td>
                    <td>
                      <span className="tableMeta" style={{ maxWidth: '220px', display: 'block' }}>
                        {comp?.message ?? 'Unavailable'}
                      </span>
                    </td>
                    <td><span className="timestamp">{comp?.last_event ? formatShortTime(comp.last_event) : '—'}</span></td>
                    <td><span className="tableMeta">{comp?.age ?? '—'}</span></td>
                    <td>
                      {comp?.action ? (
                        <span className="tableMeta" style={{ color: '#ffd280', fontSize: '0.8rem' }}>{comp.action}</span>
                      ) : (
                        <span className="tableMeta muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {noSystemHealthData && (
          <p className="explanation small" style={{ marginTop: '0.75rem' }}>
            <strong>Health data unavailable.</strong> The system health endpoint could not be reached.{' '}
            <a href="/system-health">Refresh</a>
          </p>
        )}
      </section>

      {/* ── Section 2: Live Chain Monitoring ────────────────────────────── */}
      <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Base chain (chain ID 8453)</p>
            <h2>Live Chain Monitoring</h2>
          </div>
        </div>

        {chainMonitoring ? (
          <>
            <div className={diagnosisClass(chainMonitoring.diagnosis)} role="status" style={{ marginBottom: '1rem' }}>
              <strong>Diagnosis</strong>
              <p style={{ margin: '0.3rem 0 0' }}>{chainMonitoring.diagnosis}</p>
            </div>

            <div className="twoColumnSection" style={{ marginTop: 0 }}>
              <div>
                <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>Worker &amp; Polling</p>
                <div className="kvGrid compactKvGrid">
                  <p><span>Worker enabled</span>{chainMonitoring.worker_enabled ? 'Yes' : 'No'}</p>
                  <p><span>Last heartbeat</span>{chainMonitoring.heartbeat_age_human ?? '—'}</p>
                  <p>
                    <span>Heartbeat at</span>
                    {chainMonitoring.last_heartbeat_at ? formatDateTime(chainMonitoring.last_heartbeat_at) : '—'}
                  </p>
                  <p><span>Poll interval</span>{chainMonitoring.polling_interval_seconds}s</p>
                  <p><span>Last poll</span>{chainMonitoring.last_poll_at ? formatDateTime(chainMonitoring.last_poll_at) : '—'}</p>
                  <p>
                    <span>Last successful poll</span>
                    {chainMonitoring.last_successful_poll_at ? formatDateTime(chainMonitoring.last_successful_poll_at) : '—'}
                  </p>
                  {chainMonitoring.latest_polled_block && (
                    <p><span>Latest polled block</span>#{chainMonitoring.latest_polled_block}</p>
                  )}
                </div>
              </div>
              <div>
                <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>RPC &amp; Telemetry</p>
                <div className="kvGrid compactKvGrid">
                  <p><span>Expected chain ID</span>{chainMonitoring.expected_chain_id}</p>
                  <p><span>RPC configured</span>{chainMonitoring.rpc_configured ? 'Yes' : 'No'}</p>
                  {chainMonitoring.latest_rpc_block && (
                    <p><span>Latest RPC block</span>{chainMonitoring.latest_rpc_block}</p>
                  )}
                  <p>
                    <span>Last telemetry</span>
                    {chainMonitoring.last_telemetry_at ? formatDateTime(chainMonitoring.last_telemetry_at) : '—'}
                  </p>
                  <p><span>Telemetry 1h / 24h</span>{chainMonitoring.recent_telemetry_1h} / {chainMonitoring.recent_telemetry_24h}</p>
                  <p>
                    <span>Last detection</span>
                    {chainMonitoring.last_detection_at ? formatDateTime(chainMonitoring.last_detection_at) : '—'}
                  </p>
                  <p><span>Detections 1h / 24h</span>{chainMonitoring.recent_detections_1h} / {chainMonitoring.recent_detections_24h}</p>
                </div>
              </div>
            </div>
          </>
        ) : (
          <p className="explanation small">
            Live chain monitoring data unavailable. Backend health endpoint could not be reached.
          </p>
        )}
      </section>

      {/* ── Section 3: Status Overview (canonical truth panel) ───────────── */}
      <section className="twoColumnSection" style={{ marginTop: '1rem' }}>
        <section className="dataCard">
          <div className="sectionHeader compact">
            <div>
              <p className="sectionEyebrow">Canonical runtime truth</p>
              <h2>Status Overview</h2>
            </div>
          </div>
          <div className="kvGrid compactKvGrid">
            <p><span>Overall status</span>{isOperational ? 'Operational' : isOffline ? 'Offline' : 'Degraded'}</p>
            <p><span>Monitoring status</span>{presentation.statusLabel ?? String(truth.monitoring_status ?? '—')}</p>
            <p><span>Freshness status</span>{presentation.freshness ?? String(truth.telemetry_freshness ?? '—')}</p>
            <p><span>Confidence status</span>{presentation.confidence ?? String(truth.confidence_status ?? '—')}</p>
            <p><span>Reporting systems</span>{reportingSystems} / {monitoredSystems}</p>
            <p><span>Last heartbeat</span>{formatDateTime(truth.last_heartbeat_at)}</p>
            <p><span>Last poll</span>{formatDateTime(truth.last_poll_at)}</p>
            <p><span>Last telemetry</span>{formatDateTime(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at)}</p>
            <p><span>Last detection</span>{formatDateTime(truth.last_detection_at)}</p>
          </div>

          {/* Truth guards */}
          {!summaryMissing && !isOperational && (
            <div style={{ marginTop: '0.75rem', fontSize: '0.82rem', color: 'var(--color-text-muted, #6b7280)' }}>
              {!hasHeartbeat && (
                <p style={{ margin: '0.25rem 0' }}>• Worker heartbeat not received.</p>
              )}
              {!hasTelemetry && hasHeartbeat && (
                <p style={{ margin: '0.25rem 0' }}>• No telemetry received from chain.</p>
              )}
              {reportingSystems === 0 && monitoredSystems > 0 && (
                <p style={{ margin: '0.25rem 0' }}>• No monitored systems reporting.</p>
              )}
            </div>
          )}
        </section>

        <section className="dataCard">
          <div className="sectionHeader compact">
            <div>
              <p className="sectionEyebrow">Reliability snapshot</p>
              <h2>Reliability &amp; Coverage</h2>
            </div>
          </div>
          <div className="kvGrid compactKvGrid">
            <p>
              <span>Active monitoring targets</span>
              {reliability.active_targets != null ? String(reliability.active_targets) : 'Unavailable: metric not implemented'}
            </p>
            <p>
              <span>Monitored chains</span>
              {reliability.monitored_chains != null ? String(reliability.monitored_chains) : 'Unavailable: metric not implemented'}
            </p>
            <p>
              <span>RPC success rate</span>
              {reliability.rpc_success_rate != null ? String(reliability.rpc_success_rate) : 'Unavailable: metric not implemented'}
            </p>
            <p>
              <span>Active alerts</span>
              {Number(truth.active_alerts_count ?? 0) > 0 ? `${truth.active_alerts_count} active` : '0 active'}
            </p>
            <p>
              <span>Active incidents</span>
              {Number(truth.active_incidents_count ?? 0) > 0 ? `${truth.active_incidents_count} active` : '0 active'}
            </p>
            <p>
              <span>Evidence source</span>
              {String(truth.evidence_source_summary ?? '—')}
            </p>
          </div>
        </section>
      </section>

      {/* ── Section 4: Incident & Health Timeline ────────────────────────── */}
      <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Recent activity</p>
            <h2>Incident &amp; Health Timeline</h2>
          </div>
        </div>

        {events.length > 0 ? (
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Component</th>
                  <th>Event</th>
                  <th>Severity</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event, index) => (
                  <tr key={`${event.component}-${index}`}>
                    <td><span className="timestamp">{formatDateTime(event.time)}</span></td>
                    <td>{event.component}</td>
                    <td><span style={{ fontSize: '0.85rem' }}>{event.event}</span></td>
                    <td><span className={severityBadgeClass(event.severity)}>{event.severity}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="explanation small" style={{ marginTop: '0.5rem' }}>
            No recent health events.{noSystemHealthData ? ' (Backend health endpoint could not be reached.)' : ''}
          </p>
        )}
      </section>

      {/* ── Section 5: Provider Health ───────────────────────────────────── */}
      <section className="dataCard featureSection" style={{ marginTop: '1rem' }}>
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">External dependencies</p>
            <h2>Provider Health</h2>
          </div>
          <Link href="/integrations" style={{ fontSize: '0.85rem', color: '#6aa9ff', textDecoration: 'none' }}>
            View Integrations →
          </Link>
        </div>

        {providers.length > 0 ? (
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Signal</th>
                  <th>Last Check</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((provider, index) => (
                  <tr key={`${provider.name}-${index}`}>
                    <td><strong>{provider.name}</strong></td>
                    <td><span className="tableMeta">{provider.type}</span></td>
                    <td><span className={statusBadgeClass(provider.status)}>{statusLabel(provider.status)}</span></td>
                    <td><span className="tableMeta">{provider.message}</span></td>
                    <td><span className="timestamp">{provider.last_event ? formatShortTime(provider.last_event) : '—'}</span></td>
                    <td>
                      {provider.action ? (
                        <span className="tableMeta" style={{ color: '#ffd280', fontSize: '0.8rem' }}>{provider.action}</span>
                      ) : (
                        <span className="tableMeta muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Signal</th>
                  <th>Last Check</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>Monitoring Systems</strong></td>
                  <td>Provider Connectors</td>
                  <td>
                    <span className={reportingSystems > 0 ? 'statusBadge statusBadge-live' : 'statusBadge statusBadge-degraded'}>
                      {reportingSystems > 0 ? 'Healthy' : monitoredSystems > 0 ? 'Degraded' : 'Unavailable'}
                    </span>
                  </td>
                  <td><span className="tableMeta">{reportingSystems > 0 ? `${reportingSystems} reporting` : 'No reporting systems'}</span></td>
                  <td><span className="timestamp">{formatShortTime(truth.last_poll_at)}</span></td>
                  <td><Link href="/monitoring-sources">View Sources</Link></td>
                </tr>
                <tr>
                  <td><strong>Evidence Provider</strong></td>
                  <td>Evidence Export</td>
                  <td>
                    <span className={truth.evidence_source_summary === 'live' ? 'statusBadge statusBadge-live' : 'statusBadge statusBadge-degraded'}>
                      {truth.evidence_source_summary === 'live' ? 'Healthy' : 'Unavailable'}
                    </span>
                  </td>
                  <td><span className="tableMeta">{truth.evidence_source_summary === 'live' ? 'Live evidence' : 'Live evidence unavailable'}</span></td>
                  <td><span className="timestamp">{formatShortTime(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at)}</span></td>
                  <td><Link href="/evidence">View Evidence</Link></td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── API documentation ─────────────────────────────────────────────── */}
      <section style={{ marginTop: '1rem' }}>
        <h2>API Documentation</h2>
        <p style={{ marginBottom: '0.5rem', color: 'var(--color-text-muted, #6b7280)', fontSize: '0.875rem' }}>
          Machine-readable API schema for enterprise integration and review.
        </p>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/openapi.json`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: '0.875rem' }}
          >
            OpenAPI Schema (JSON)
          </a>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: '0.875rem' }}
          >
            Interactive API Docs (Swagger UI)
          </a>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/redoc`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: '0.875rem' }}
          >
            API Reference (ReDoc)
          </a>
        </div>
      </section>
    </main>
  );
}
