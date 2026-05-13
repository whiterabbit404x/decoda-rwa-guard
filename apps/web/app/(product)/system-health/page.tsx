import Link from 'next/link';
import { normalizeMonitoringPresentation } from '../../monitoring-status-presentation';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { fetchDashboardPageData } from '../../dashboard-data';
import { resolveWorkspaceMonitoringTruthFromSummary } from '../../workspace-monitoring-truth';

export const dynamic = 'force-dynamic';

type ComponentStatus =
  | 'Operational'
  | 'Healthy'
  | 'Degraded'
  | 'Offline'
  | 'Not Configured'
  | 'Disabled'
  | 'Error'
  | 'Unknown';

type Severity = 'Critical' | 'High' | 'Medium' | 'Low' | 'Info' | 'Unknown';

type ComponentRow = {
  component: string;
  status: ComponentStatus;
  uptime: string;
  responseTime: string;
  lastCheck: string;
};

type HealthEvent = {
  time: string;
  component: string;
  event: string;
  severity: Severity;
  result: string;
};

const COMPONENTS = [
  'API Gateway',
  'Telemetry Service',
  'Worker',
  'Detection Engine',
  'Alert Engine',
  'Database',
  'Redis / Queue',
  'Provider Connectors',
  'Evidence Export',
] as const;

const CONTRADICTION_MESSAGES: Record<string, string> = {
  asset_monitoring_attached_but_no_monitored_systems:
    'Asset says monitoring is attached but monitored_systems = 0.',
  ui_healthy_claim_with_zero_reporting_systems:
    'reporting_systems = 0 but UI says healthy.',
  ui_live_monitoring_claim_without_telemetry:
    'Telemetry is unavailable but UI says live.',
  simulator_evidence_claimed_as_live_provider:
    'Simulator evidence displayed as live provider evidence.',
  alert_exists_without_detection: 'Alert exists without detection.',
  incident_exists_without_alert: 'Incident exists without alert.',
  response_action_exists_without_incident: 'Response action exists without incident.',
  evidence_package_without_detection_alert_incident_chain:
    'Evidence package exists without complete chain.',
};

function toArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter(Boolean).map(String) : [];
}

function humanizeCode(value: string | null | undefined): string {
  if (!value) return 'Unavailable';
  return value
    .replace(/_/g, ' ')
    .replace(/-/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return 'Unavailable';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatShortTime(value: string | null | undefined): string {
  if (!value) return 'Unavailable';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString();
}

function statusBadgeClass(status: ComponentStatus): string {
  if (status === 'Operational' || status === 'Healthy') return 'statusBadge statusBadge-live';
  if (status === 'Degraded') return 'statusBadge statusBadge-degraded';
  if (status === 'Offline' || status === 'Error') return 'statusBadge statusBadge-offline';
  if (status === 'Not Configured' || status === 'Disabled') {
    return 'statusBadge statusBadge-limited_coverage';
  }
  return 'statusBadge statusBadge-unavailable';
}

function severityBadgeClass(severity: Severity): string {
  if (severity === 'Critical' || severity === 'High') return 'statusBadge statusBadge-offline';
  if (severity === 'Medium') return 'statusBadge statusBadge-degraded';
  if (severity === 'Low') return 'statusBadge statusBadge-limited_coverage';
  if (severity === 'Info') return 'statusBadge statusBadge-live';
  return 'statusBadge statusBadge-unavailable';
}

function buildComponentRows(truth: any, summaryMissing: boolean): ComponentRow[] {
  if (summaryMissing) {
    return COMPONENTS.map((component) => ({
      component,
      status: 'Unknown',
      uptime: 'Unavailable',
      responseTime: 'Unavailable',
      lastCheck: 'Unavailable',
    }));
  }

  const reportingSystems = Number(truth.reporting_systems_count ?? 0);
  const monitoredSystems = Number(truth.monitored_systems_count ?? 0);
  const workspaceConfigured = Boolean(truth.workspace_configured);
  const hasHeartbeat = Boolean(truth.last_heartbeat_at);
  const hasPoll = Boolean(truth.last_poll_at);
  const hasTelemetry = Boolean(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at);
  const telemetryFresh = truth.telemetry_freshness === 'fresh';
  const runtimeLive = truth.runtime_status === 'live';
  const monitoringLive = truth.monitoring_status === 'live';
  const monitoringLimited = truth.monitoring_status === 'limited';
  const evidenceSource = String(truth.evidence_source_summary ?? 'none');

  return [
    {
      component: 'API Gateway',
      status: runtimeLive && reportingSystems > 0 ? 'Operational' : hasPoll ? 'Degraded' : 'Unknown',
      uptime: runtimeLive && reportingSystems > 0 ? 'Live' : 'Unavailable',
      responseTime: hasPoll ? 'Poll recorded' : 'Unavailable',
      lastCheck: formatShortTime(truth.last_poll_at),
    },
    {
      component: 'Telemetry Service',
      status: hasTelemetry && telemetryFresh ? 'Operational' : workspaceConfigured ? 'Offline' : 'Not Configured',
      uptime: hasTelemetry && telemetryFresh ? 'Fresh' : hasTelemetry ? 'Stale' : 'Unavailable',
      responseTime: 'Unavailable',
      lastCheck: formatShortTime(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at),
    },
    {
      component: 'Worker',
      status: hasHeartbeat ? 'Operational' : workspaceConfigured ? 'Offline' : 'Unknown',
      uptime: hasHeartbeat ? 'Heartbeat active' : 'Unavailable',
      responseTime: 'Unavailable',
      lastCheck: formatShortTime(truth.last_heartbeat_at),
    },
    {
      component: 'Detection Engine',
      status: truth.last_detection_at ? 'Operational' : monitoringLive ? 'Degraded' : 'Unknown',
      uptime: truth.last_detection_at ? 'Detection recorded' : monitoringLive ? 'Waiting' : 'Unavailable',
      responseTime: 'Unavailable',
      lastCheck: formatShortTime(truth.last_detection_at),
    },
    {
      component: 'Alert Engine',
      status: monitoringLive && reportingSystems > 0 ? 'Operational' : monitoringLimited ? 'Degraded' : 'Unknown',
      uptime: monitoringLive && reportingSystems > 0 ? 'Active' : 'Unavailable',
      responseTime: 'Unavailable',
      lastCheck: formatShortTime(truth.last_poll_at),
    },
    {
      component: 'Database',
      status: truth.db_failure_reason ? 'Error' : workspaceConfigured ? 'Unknown' : 'Unknown',
      uptime: truth.db_failure_reason ? 'Error' : 'Unknown',
      responseTime: 'Unavailable',
      lastCheck: hasPoll ? formatShortTime(truth.last_poll_at) : 'Unavailable',
    },
    {
      component: 'Redis / Queue',
      status: monitoringLive && reportingSystems > 0 ? 'Unknown' : workspaceConfigured ? 'Unknown' : 'Not Configured',
      uptime: 'Unknown',
      responseTime: 'Unavailable',
      lastCheck: hasPoll ? formatShortTime(truth.last_poll_at) : 'Unavailable',
    },
    {
      component: 'Provider Connectors',
      status: reportingSystems > 0 ? 'Operational' : monitoredSystems > 0 ? 'Degraded' : 'Not Configured',
      uptime: reportingSystems > 0 ? `${reportingSystems} reporting` : 'Unavailable',
      responseTime: 'Unavailable',
      lastCheck: formatShortTime(truth.last_poll_at),
    },
    {
      component: 'Evidence Export',
      status: evidenceSource === 'live' ? 'Operational' : evidenceSource === 'none' ? 'Unknown' : 'Degraded',
      uptime: evidenceSource === 'live' ? 'Live evidence' : evidenceSource === 'none' ? 'Unknown' : 'Non-live source',
      responseTime: 'Unavailable',
      lastCheck: formatShortTime(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at),
    },
  ];
}

function buildHealthEvents(truth: any, summaryMissing: boolean): HealthEvent[] {
  if (summaryMissing) {
    return [
      {
        time: 'Unavailable',
        component: 'Runtime Summary',
        event: 'System health unavailable. Runtime health summary could not be loaded.',
        severity: 'Unknown',
        result: 'Health data unavailable',
      },
    ];
  }

  const events: HealthEvent[] = [];
  const contradictionFlags = toArray(truth.contradiction_flags);
  const reasonCodes = toArray(truth.reason_codes);

  for (const flag of contradictionFlags) {
    events.push({
      time: formatShortTime(truth.last_poll_at ?? truth.last_heartbeat_at),
      component: 'Runtime Guard',
      event: CONTRADICTION_MESSAGES[flag] ?? humanizeCode(flag),
      severity: 'Critical',
      result: 'Contradiction detected',
    });
  }

  for (const code of reasonCodes.slice(0, 6)) {
    events.push({
      time: formatShortTime(truth.last_poll_at ?? truth.last_heartbeat_at),
      component: 'Runtime Summary',
      event: humanizeCode(code),
      severity: code.includes('offline') || code.includes('unavailable') ? 'High' : 'Medium',
      result: 'Degraded',
    });
  }

  if (truth.db_failure_reason) {
    events.push({
      time: formatShortTime(truth.last_poll_at),
      component: 'Database',
      event: `Database error: ${truth.db_failure_reason}`,
      severity: 'Critical',
      result: 'Error',
    });
  }

  if (events.length === 0) {
    events.push({
      time: formatShortTime(truth.last_heartbeat_at ?? truth.last_poll_at),
      component: 'Worker',
      event: truth.last_heartbeat_at ? 'Heartbeat received.' : 'No worker heartbeat has been received.',
      severity: truth.last_heartbeat_at ? 'Info' : 'High',
      result: truth.last_heartbeat_at ? 'Recorded' : 'Unavailable',
    });
  }

  return events;
}

function nextActionFor(truth: any, summaryMissing: boolean): { label: string; href: string | null } {
  if (summaryMissing) return { label: 'Refresh Health', href: '/system-health' };
  if (Number(truth.monitored_systems_count ?? 0) === 0) {
    return { label: 'View Monitoring Sources', href: '/monitoring-sources' };
  }
  if (!truth.last_heartbeat_at) return { label: 'View Monitoring Sources', href: '/monitoring-sources' };
  if (!(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at)) {
    return { label: 'View Threat Monitoring', href: '/threat' };
  }
  if (Number(truth.reporting_systems_count ?? 0) === 0) {
    return { label: 'View Integrations', href: '/integrations' };
  }
  return { label: truth.next_required_action ? humanizeCode(truth.next_required_action) : 'No action required', href: null };
}

export default async function SystemHealthPage() {
  const data = await fetchDashboardPageData(undefined, { featureFeeds: ['resilienceDashboard'] });
  const summaryMissing = data.workspaceMonitoringSummary == null;
  const truth = resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary) as any;
  const presentation = normalizeMonitoringPresentation(truth) as any;

  const reportingSystems = Number(truth.reporting_systems_count ?? 0);
  const monitoredSystems = Number(truth.monitored_systems_count ?? 0);
  const hasHeartbeat = Boolean(truth.last_heartbeat_at);
  const hasTelemetry = Boolean(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at);
  const contradictionFlags = toArray(truth.contradiction_flags);
  const reasonCodes = [
    ...toArray(truth.reason_codes),
    ...toArray(truth.continuity_reason_codes),
    ...(summaryMissing ? ['summary_unavailable'] : []),
  ];
  const uniqueReasonCodes = [...new Set(reasonCodes)];

  const isOperational =
    !summaryMissing &&
    reportingSystems > 0 &&
    hasHeartbeat &&
    hasTelemetry &&
    contradictionFlags.length === 0 &&
    (truth.runtime_status === 'live' || truth.monitoring_status === 'live');

  const isOffline = summaryMissing || truth.runtime_status === 'offline' || !hasHeartbeat;
  const overallStatusLabel = isOperational ? 'Operational' : isOffline ? 'Offline' : 'Degraded';
  const overallBadgeClass = isOperational
    ? 'statusBadge statusBadge-live'
    : isOffline
      ? 'statusBadge statusBadge-offline'
      : 'statusBadge statusBadge-degraded';

  const uptimeValue = summaryMissing
    ? 'Unavailable'
    : isOperational
      ? 'Live'
      : truth.runtime_status === 'degraded'
        ? 'Degraded'
        : 'Unavailable';
  const avgResponseValue = summaryMissing ? 'Unavailable' : truth.last_poll_at ? 'Polling active' : 'Unavailable';
  const errorRateValue = summaryMissing
    ? 'Unavailable'
    : Number(truth.active_incidents_count ?? 0) > 0
      ? `${truth.active_incidents_count} active incidents`
      : Number(truth.active_alerts_count ?? 0) > 0
        ? `${truth.active_alerts_count} active alerts`
        : 'No active incidents';
  const activeSystemsValue = summaryMissing ? 'Unavailable' : `${reportingSystems} / ${monitoredSystems}`;

  const componentRows = buildComponentRows(truth, summaryMissing);
  const healthEvents = buildHealthEvents(truth, summaryMissing);
  const nextAction = nextActionFor(truth, summaryMissing);

  const noMonitoredSystems = !summaryMissing && monitoredSystems === 0;
  const noReportingSystems = !summaryMissing && monitoredSystems > 0 && reportingSystems === 0;
  const noHeartbeat = !summaryMissing && !hasHeartbeat;
  const heartbeatButNoTelemetry = !summaryMissing && hasHeartbeat && !hasTelemetry;

  const statusSummary = isOperational
    ? 'All systems are operational.'
    : summaryMissing
      ? 'Health data unavailable. Runtime health summary could not be loaded.'
      : presentation.summary ?? 'Operational health is degraded or unavailable.';

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Status &amp; reliability</p>
          <h1>System Health</h1>
          <p className="lede">
            Monitor platform reliability, runtime services, providers, and operational health.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end' }}>
          <a href="/system-health" className="secondaryCta" style={{ fontSize: '0.9rem' }}>
            Refresh Health
          </a>
        </div>
      </section>

      <section className="fourColumnSection">
        <article className="dataCard">
          <p className="sectionEyebrow">Uptime</p>
          <h2 className="metricValue">{uptimeValue}</h2>
          <p className="metricMeta muted">Runtime status</p>
        </article>
        <article className="dataCard">
          <p className="sectionEyebrow">Avg Response Time</p>
          <h2 className="metricValue">{avgResponseValue}</h2>
          <p className="metricMeta muted">Last poll: {formatShortTime(truth.last_poll_at)}</p>
        </article>
        <article className="dataCard">
          <p className="sectionEyebrow">Error Rate</p>
          <h2 className="metricValue">{errorRateValue}</h2>
          <p className="metricMeta muted">Incidents / alerts</p>
        </article>
        <article className="dataCard">
          <p className="sectionEyebrow">Active Systems</p>
          <h2 className="metricValue">{activeSystemsValue}</h2>
          <p className="metricMeta muted">Reporting / monitored</p>
        </article>
      </section>

      {contradictionFlags.length > 0 && (
        <section className="banner banner-degraded" role="alert">
          <strong>Runtime contradiction detected</strong>
          <ul style={{ margin: '0.5rem 0 0', paddingLeft: '1.25rem' }}>
            {contradictionFlags.map((flag) => (
              <li key={flag}>{CONTRADICTION_MESSAGES[flag] ?? humanizeCode(flag)}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="twoColumnSection">
        <section className="dataCard">
          <div className="sectionHeader compact">
            <div>
              <p className="sectionEyebrow">Component health</p>
              <h2>System Components</h2>
            </div>
          </div>
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Status</th>
                  <th>Uptime</th>
                  <th>Response Time</th>
                  <th>Last Check</th>
                </tr>
              </thead>
              <tbody>
                {componentRows.map((row) => (
                  <tr key={row.component}>
                    <td><strong>{row.component}</strong></td>
                    <td><span className={statusBadgeClass(row.status)}>{row.status}</span></td>
                    <td><span className="tableMeta">{row.uptime}</span></td>
                    <td><span className="tableMeta">{row.responseTime}</span></td>
                    <td><span className="timestamp">{row.lastCheck}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="dataCard">
          <div className="sectionHeader compact">
            <div>
              <p className="sectionEyebrow">Platform status</p>
              <h2>Status Overview</h2>
            </div>
            <span className={overallBadgeClass}>{overallStatusLabel}</span>
          </div>

          <div className="kvGrid compactKvGrid">
            <p><span>Overall status</span>{overallStatusLabel}</p>
            <p><span>Monitoring status</span>{presentation.statusLabel ?? humanizeCode(truth.monitoring_status)}</p>
            <p><span>Freshness status</span>{presentation.freshness ?? humanizeCode(truth.telemetry_freshness)}</p>
            <p><span>Confidence status</span>{presentation.confidence ?? humanizeCode(truth.confidence_status)}</p>
            <p><span>Last heartbeat</span>{formatDateTime(truth.last_heartbeat_at)}</p>
            <p><span>Last poll</span>{formatDateTime(truth.last_poll_at)}</p>
            <p><span>Last telemetry</span>{formatDateTime(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at)}</p>
            <p><span>Last detection</span>{formatDateTime(truth.last_detection_at)}</p>
            <p><span>Next required action</span>{nextAction.label}</p>
          </div>

          {uniqueReasonCodes.length > 0 && (
            <div style={{ marginTop: '0.9rem' }}>
              <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>Reason codes</p>
              <div className="chipRow">
                {uniqueReasonCodes.map((code) => (
                  <span key={code} className="ruleChip">{code}</span>
                ))}
              </div>
            </div>
          )}

          {contradictionFlags.length > 0 && (
            <div style={{ marginTop: '0.9rem' }}>
              <p className="sectionEyebrow" style={{ marginBottom: '0.4rem' }}>Contradiction flags</p>
              <div className="chipRow">
                {contradictionFlags.map((flag) => (
                  <span key={flag} className="ruleChip">{flag}</span>
                ))}
              </div>
            </div>
          )}

          <div className="statusCallout">
            <div className={isOperational ? 'healthIcon healthIconLive' : isOffline ? 'healthIcon healthIconOffline' : 'healthIcon healthIconDegraded'}>
              {isOperational ? 'OK' : isOffline ? 'X' : '!'}
            </div>
            <div>
              <strong>{isOperational ? 'All Systems Operational' : isOffline ? 'System health unavailable' : 'System Degraded'}</strong>
              <p className="muted">{statusSummary}</p>
            </div>
          </div>

          {summaryMissing && (
            <p className="explanation small" style={{ marginTop: '0.75rem' }}>
              <strong>System health unavailable.</strong> Runtime health summary could not be loaded.{' '}
              <Link href="/system-health">Refresh Health</Link>
            </p>
          )}
          {noMonitoredSystems && (
            <p className="explanation small" style={{ marginTop: '0.75rem' }}>
              <strong>No monitored systems reporting.</strong> System health is degraded because no monitored systems are reporting heartbeat.{' '}
              <Link href="/monitoring-sources">View Monitoring Sources</Link>
            </p>
          )}
          {noReportingSystems && (
            <p className="explanation small" style={{ marginTop: '0.75rem' }}>
              <strong>No monitored systems reporting.</strong> System health is degraded because no monitored systems are reporting heartbeat.{' '}
              <Link href="/monitoring-sources">View Monitoring Sources</Link>
            </p>
          )}
          {noHeartbeat && (
            <p className="explanation small" style={{ marginTop: '0.75rem' }}>
              <strong>Worker heartbeat unavailable.</strong> No worker heartbeat has been received.{' '}
              <Link href="/monitoring-sources">View Monitoring Sources</Link>
            </p>
          )}
          {heartbeatButNoTelemetry && (
            <p className="explanation small" style={{ marginTop: '0.75rem' }}>
              <strong>Telemetry unavailable.</strong> The worker is reporting, but no telemetry has been received yet.{' '}
              <Link href="/threat">View Threat Monitoring</Link>
            </p>
          )}
          {nextAction.href ? (
            <Link href={nextAction.href} className="secondaryCta" style={{ display: 'inline-flex', marginTop: '0.9rem' }}>
              {nextAction.label}
            </Link>
          ) : (
            <button className="secondaryCta" disabled style={{ marginTop: '0.9rem', opacity: 0.7 }}>
              Action not configured
            </button>
          )}
        </section>
      </section>

      <section className="dataCard featureSection">
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Recent activity</p>
            <h2>Recent Health Events</h2>
          </div>
        </div>
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Component</th>
                <th>Event</th>
                <th>Severity</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {healthEvents.map((event, index) => (
                <tr key={`${event.component}-${index}`}>
                  <td><span className="timestamp">{event.time}</span></td>
                  <td>{event.component}</td>
                  <td>{event.event}</td>
                  <td><span className={severityBadgeClass(event.severity)}>{event.severity}</span></td>
                  <td><span className="tableMeta">{event.result}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="dataCard featureSection">
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">External Dependencies</p>
            <h2>Provider Health</h2>
          </div>
          <Link href="/integrations" style={{ fontSize: '0.85rem', color: '#6aa9ff', textDecoration: 'none' }}>
            View Integrations �?          </Link>
        </div>
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Type</th>
                <th>Status</th>
                <th>Last Sync</th>
                <th>Last Error</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>Monitoring Systems</strong></td>
                <td>Provider Connectors</td>
                <td>
                  <span className={reportingSystems > 0 ? 'statusBadge statusBadge-live' : 'statusBadge statusBadge-degraded'}>
                    {reportingSystems > 0 ? 'Operational' : monitoredSystems > 0 ? 'Degraded' : 'Not Configured'}
                  </span>
                </td>
                <td><span className="timestamp">{formatShortTime(truth.last_poll_at)}</span></td>
                <td><span className="tableMeta">{reportingSystems > 0 ? 'None' : 'No reporting systems'}</span></td>
                <td><Link href="/monitoring-sources">View Monitoring Sources</Link></td>
              </tr>
              <tr>
                <td><strong>Evidence Provider</strong></td>
                <td>Evidence Export</td>
                <td>
                  <span className={truth.evidence_source_summary === 'live' ? 'statusBadge statusBadge-live' : 'statusBadge statusBadge-degraded'}>
                    {truth.evidence_source_summary === 'live' ? 'Operational' : 'Unknown'}
                  </span>
                </td>
                <td><span className="timestamp">{formatShortTime(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at)}</span></td>
                <td><span className="tableMeta">{truth.evidence_source_summary === 'live' ? 'None' : 'Live evidence unavailable'}</span></td>
                <td><Link href="/evidence">View Evidence</Link></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
