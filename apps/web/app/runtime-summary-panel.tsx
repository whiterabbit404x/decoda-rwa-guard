'use client';

import { hasLiveTelemetry, hasRealTelemetryBackedChain } from './workspace-monitoring-truth';
import { useRuntimeSummary } from './runtime-summary-context';
import type { WorkspaceMonitoringTruth } from './workspace-monitoring-truth';
import type { ProviderHealthInfo, WorkerHealthInfo } from './runtime-summary-context';

type BannerState = 'LIVE' | 'LIMITED_COVERAGE' | 'SETUP_REQUIRED' | 'OFFLINE';

function deriveBannerState(summary: WorkspaceMonitoringTruth): BannerState {
  if (hasLiveTelemetry(summary) && hasRealTelemetryBackedChain(summary)) return 'LIVE';
  if (summary.db_failure_reason) return 'OFFLINE';
  // Only show OFFLINE when the API returned a confirmed offline status — not when the
  // runtime-status call itself failed (status_reason === 'summary_unavailable').
  const runtimeApiMissing = summary.status_reason === 'summary_unavailable';
  if (!runtimeApiMissing && summary.runtime_status === 'offline' && summary.protected_assets_count === 0 && !summary.last_heartbeat_at) return 'OFFLINE';
  if (!summary.workspace_configured || summary.protected_assets_count === 0) return 'SETUP_REQUIRED';
  if (summary.reporting_systems_count === 0 && !summary.last_poll_at && !summary.last_heartbeat_at) return 'SETUP_REQUIRED';
  return 'LIMITED_COVERAGE';
}

function bannerLabel(state: BannerState): string {
  if (state === 'LIVE') return 'LIVE';
  if (state === 'LIMITED_COVERAGE') return 'LIMITED COVERAGE';
  if (state === 'SETUP_REQUIRED') return 'SETUP REQUIRED';
  return 'OFFLINE';
}

function bannerHeadline(state: BannerState, summary: WorkspaceMonitoringTruth): string {
  const runtimeApiMissing = summary.status_reason === 'summary_unavailable';
  if (state === 'LIVE') return 'Live monitoring active.';
  if (state === 'OFFLINE') {
    if (summary.db_failure_reason) return 'Runtime offline: backend database is unavailable.';
    return 'Runtime offline: backend or worker is unreachable.';
  }
  if (state === 'SETUP_REQUIRED') {
    if (runtimeApiMissing) return 'Monitoring status could not be loaded. Verify API connectivity and workspace setup.';
    if (!summary.workspace_configured) return 'Workspace setup is incomplete. Complete onboarding to begin monitoring.';
    if (summary.protected_assets_count === 0) return 'No protected assets registered. Add an asset to begin.';
    return 'Setup required: protected asset found, but live telemetry is not connected yet.';
  }
  return 'Limited coverage: asset is configured, but live telemetry is missing or stale.';
}

function bannerSubtext(state: BannerState, summary: WorkspaceMonitoringTruth): string {
  if (state === 'LIVE') return 'Telemetry, detection, alert, incident, and evidence flow are verified.';
  if (state === 'OFFLINE') return 'Check API, database, worker, and workspace connectivity.';
  if (state === 'SETUP_REQUIRED') {
    if (summary.status_reason === 'summary_unavailable') return '';
    return 'Complete provider, worker, and telemetry verification to activate live monitoring.';
  }
  return 'Monitoring will become live after provider polling and telemetry verification.';
}

function bannerColor(state: BannerState): string {
  if (state === 'LIVE') return 'var(--success-fg, #16a34a)';
  if (state === 'OFFLINE') return 'var(--danger-fg, #dc2626)';
  if (state === 'SETUP_REQUIRED') return 'var(--warning-fg, #d97706)';
  return 'var(--warning-fg, #b45309)';
}

type CheckStep = { label: string; status: 'done' | 'missing' | 'failed'; hint?: string };

function buildChecklist(summary: WorkspaceMonitoringTruth, workerHealth: WorkerHealthInfo): CheckStep[] {
  const hasAsset = summary.protected_assets_count > 0;
  const hasSource = summary.reporting_systems_count > 0 || summary.monitored_systems_count > 0;
  const hasHeartbeat = Boolean(summary.last_heartbeat_at);
  const hasPoll = Boolean(summary.last_poll_at);
  const hasTelemetry = Boolean(summary.last_telemetry_at);
  const hasDetection = Boolean(summary.last_detection_at);
  const hasAlert = summary.active_alerts_count > 0 || summary.active_incidents_count > 0;
  const workerRunning = workerHealth.status === 'running' || hasHeartbeat;
  const workerFailed = workerHealth.consecutive_failures > 0 && workerHealth.status === 'stopped';

  return [
    {
      label: 'Verify protected asset',
      status: hasAsset ? 'done' : 'missing',
      hint: hasAsset ? undefined : 'Add a wallet, contract, or RWA to register.',
    },
    {
      label: 'Link monitoring source',
      status: hasSource ? 'done' : 'missing',
      hint: hasSource ? undefined : 'Create a monitoring target and enable a monitored system.',
    },
    {
      label: 'Enable worker',
      status: workerFailed ? 'failed' : workerRunning ? 'done' : 'missing',
      hint: workerFailed ? `Worker stopped with ${workerHealth.consecutive_failures} consecutive failure(s).`
        : workerRunning ? undefined
        : 'Start the Railway worker or enable STAGING_WORKER_ENABLED.',
    },
    {
      label: 'Receive first provider poll',
      status: hasPoll ? 'done' : 'missing',
      hint: hasPoll ? undefined : 'No provider poll yet. Check EVM_RPC_URL / STAGING_EVM_RPC_URL.',
    },
    {
      label: 'Receive first telemetry event',
      status: hasTelemetry ? 'done' : 'missing',
      hint: hasTelemetry ? undefined : 'No telemetry event yet. Verify provider and worker are running.',
    },
    {
      label: 'Generate first detection',
      status: hasDetection ? 'done' : 'missing',
      hint: hasDetection ? undefined : 'Detections will appear after telemetry is received.',
    },
    {
      label: 'Create alert / incident evidence',
      status: hasAlert ? 'done' : 'missing',
      hint: hasAlert ? undefined : 'Alerts and incidents are created from detections.',
    },
  ];
}

function CheckIcon({ status }: { status: CheckStep['status'] }) {
  if (status === 'done') return <span style={{ color: 'var(--success-fg, #16a34a)', marginRight: '0.5rem' }}>✓</span>;
  if (status === 'failed') return <span style={{ color: 'var(--danger-fg, #dc2626)', marginRight: '0.5rem' }}>✗</span>;
  return <span style={{ color: 'var(--text-muted)', marginRight: '0.5rem' }}>○</span>;
}

function ChecklistRow({ step }: { step: CheckStep }) {
  const textColor = step.status === 'done' ? 'var(--text-secondary)' : step.status === 'failed' ? 'var(--danger-fg, #dc2626)' : 'var(--text-primary)';
  const badge = step.status === 'done' ? null : step.status === 'failed' ? (
    <span style={{ fontSize: '0.7rem', background: 'var(--danger-bg, #fee2e2)', color: 'var(--danger-fg, #dc2626)', borderRadius: '4px', padding: '1px 6px', marginLeft: '0.5rem' }}>Failed</span>
  ) : (
    <span style={{ fontSize: '0.7rem', background: 'var(--surface-subtle)', color: 'var(--text-muted)', borderRadius: '4px', padding: '1px 6px', marginLeft: '0.5rem' }}>Pending</span>
  );
  return (
    <li style={{ padding: '0.3rem 0', fontSize: '0.875rem', color: textColor }}>
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <CheckIcon status={step.status} />
        {step.label}
        {badge}
      </div>
      {step.hint && step.status !== 'done' ? (
        <p style={{ margin: '0.1rem 0 0 1.5rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>{step.hint}</p>
      ) : null}
    </li>
  );
}

function formatAge(iso: string | null): string {
  if (!iso) return 'never';
  const diffMs = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diffMs / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

function ProviderCard({ info }: { info: ProviderHealthInfo }) {
  const statusColor =
    info.status === 'connected' ? 'var(--success-fg, #16a34a)'
    : info.status === 'not_connected' ? 'var(--danger-fg, #dc2626)'
    : 'var(--text-muted)';
  const statusLabel =
    info.status === 'connected' ? 'Connected'
    : info.status === 'not_connected' ? 'Not connected'
    : 'Not configured';
  const actionLabel = info.status === 'connected' ? 'Verify provider' : 'Configure provider';
  return (
    <article className="dataCard" style={{ padding: '1rem', flex: '1 1 220px' }}>
      <p className="sectionEyebrow" style={{ marginBottom: '0.5rem' }}>Provider Health</p>
      <p style={{ margin: '0 0 0.4rem', fontWeight: 600, fontSize: '0.875rem' }}>{info.name}</p>
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.8rem' }}>
        Status: <span style={{ color: statusColor, fontWeight: 600 }}>{statusLabel}</span>
      </p>
      {info.chain
        ? <p style={{ margin: '0 0 0.25rem', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Network: {info.chain}</p>
        : <p style={{ margin: '0 0 0.25rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>Network: —</p>}
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Last check: {formatAge(info.last_check)}</p>
      {info.error_message ? (
        <p style={{ margin: '0.5rem 0 0.5rem', fontSize: '0.75rem', color: 'var(--danger-fg, #dc2626)', background: 'var(--danger-bg, #fee2e2)', padding: '0.25rem 0.5rem', borderRadius: '4px' }}>
          {info.error_message}
        </p>
      ) : null}
      {info.status !== 'connected' ? (
        <p style={{ margin: '0.5rem 0 0', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          Set <code style={{ fontSize: '0.72rem' }}>EVM_RPC_URL</code> or <code style={{ fontSize: '0.72rem' }}>STAGING_EVM_RPC_URL</code> to connect.
        </p>
      ) : null}
      <p style={{ margin: '0.5rem 0 0', fontSize: '0.75rem' }}>
        <span style={{ color: 'var(--text-accent)' }}>{actionLabel}</span>
      </p>
    </article>
  );
}

function WorkerCard({ info }: { info: WorkerHealthInfo }) {
  const statusColor =
    info.status === 'running' ? 'var(--success-fg, #16a34a)'
    : info.status === 'stopped' ? 'var(--danger-fg, #dc2626)'
    : 'var(--text-muted)';
  const statusLabel =
    info.status === 'running' ? 'Running'
    : info.status === 'stopped' ? 'Stopped'
    : 'Not started';
  const helpText = info.status === 'running' ? null
    : info.status === 'stopped' && info.consecutive_failures > 0
      ? `Worker stopped after ${info.consecutive_failures} failure(s). Check worker logs.`
      : 'Worker has not reported a heartbeat yet. Start the Railway worker or enable STAGING_WORKER_ENABLED.';
  return (
    <article className="dataCard" style={{ padding: '1rem', flex: '1 1 220px' }}>
      <p className="sectionEyebrow" style={{ marginBottom: '0.5rem' }}>Worker Health</p>
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.8rem' }}>
        Status: <span style={{ color: statusColor, fontWeight: 600 }}>{statusLabel}</span>
      </p>
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Last heartbeat: {formatAge(info.last_heartbeat)}</p>
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Last poll: {formatAge(info.last_poll)}</p>
      <p style={{ margin: '0 0 0.25rem', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Last telemetry: {formatAge(info.last_telemetry)}</p>
      {info.consecutive_failures > 0 ? (
        <p style={{ margin: '0.25rem 0 0', fontSize: '0.75rem', color: 'var(--danger-fg, #dc2626)' }}>
          Consecutive failures: {info.consecutive_failures}
        </p>
      ) : null}
      {info.next_poll ? (
        <p style={{ margin: '0.25rem 0 0', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Next poll: {formatAge(info.next_poll)}</p>
      ) : null}
      {helpText ? (
        <p style={{ margin: '0.5rem 0 0', fontSize: '0.75rem', color: 'var(--warning-fg, #d97706)', background: 'var(--warning-bg, #fef3c7)', padding: '0.25rem 0.5rem', borderRadius: '4px' }}>
          {helpText}
        </p>
      ) : null}
    </article>
  );
}

const PENDING_SEQUENCE = [
  'Provider poll',
  'Chain telemetry event',
  'Detection',
  'Alert',
  'Incident / evidence',
];

function TelemetryTimeline({ summary }: { summary: WorkspaceMonitoringTruth }) {
  const hasAny = summary.last_poll_at || summary.last_telemetry_at || summary.last_detection_at;
  return (
    <article className="dataCard" style={{ padding: '1rem' }}>
      <p className="sectionEyebrow" style={{ marginBottom: '0.5rem' }}>Telemetry Timeline</p>
      {!hasAny ? (
        <div>
          <p style={{ margin: '0 0 0.75rem', fontSize: '0.875rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
            No telemetry received yet.
          </p>
          <p style={{ margin: '0 0 0.4rem', fontSize: '0.8rem', color: 'var(--text-secondary)', fontWeight: 600 }}>
            Waiting for first provider poll and first live event:
          </p>
          <ol style={{ margin: 0, paddingLeft: '1.25rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            {PENDING_SEQUENCE.map((step) => (
              <li key={step} style={{ padding: '0.1rem 0' }}>{step}</li>
            ))}
          </ol>
        </div>
      ) : (
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          {summary.last_poll_at ? <li style={{ padding: '0.15rem 0' }}>Provider poll: {formatAge(summary.last_poll_at)}</li> : null}
          {summary.last_heartbeat_at ? <li style={{ padding: '0.15rem 0' }}>Worker heartbeat: {formatAge(summary.last_heartbeat_at)}</li> : null}
          {summary.last_telemetry_at ? <li style={{ padding: '0.15rem 0' }}>Telemetry event: {formatAge(summary.last_telemetry_at)}</li> : null}
          {summary.last_detection_at ? <li style={{ padding: '0.15rem 0' }}>Detection: {formatAge(summary.last_detection_at)}</li> : null}
          {summary.active_alerts_count > 0 ? <li style={{ padding: '0.15rem 0' }}>Active alerts: {summary.active_alerts_count}</li> : null}
          {summary.active_incidents_count > 0 ? <li style={{ padding: '0.15rem 0' }}>Open incidents: {summary.active_incidents_count}</li> : null}
        </ul>
      )}
    </article>
  );
}

export default function RuntimeSummaryPanel() {
  const { summary, loading, providerHealth, workerHealth } = useRuntimeSummary();

  if (loading) return null;

  const bannerState = deriveBannerState(summary);
  const checklist = buildChecklist(summary, workerHealth);
  const subtext = bannerSubtext(bannerState, summary);

  return (
    <section style={{ marginBottom: '1.5rem' }}>
      {/* ── State banner ──────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: '1rem',
        padding: '0.875rem 1.25rem', borderRadius: '8px', marginBottom: '1rem',
        background: 'var(--surface-subtle)',
        borderLeft: `4px solid ${bannerColor(bannerState)}`,
      }}>
        <div style={{ flex: 1 }}>
          <span style={{ fontWeight: 700, fontSize: '0.8rem', letterSpacing: '0.05em', color: bannerColor(bannerState) }}>
            {bannerLabel(bannerState)}
          </span>
          <p style={{ margin: '0.2rem 0 0', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
            {bannerHeadline(bannerState, summary)}
          </p>
          {subtext ? (
            <p style={{ margin: '0.15rem 0 0', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              {subtext}
            </p>
          ) : null}
          {summary.contradiction_flags.length > 0 ? (
            <p style={{ margin: '0.35rem 0 0', fontSize: '0.75rem', color: 'var(--warning-fg, #d97706)' }}>
              Contradictions detected: {summary.contradiction_flags.join(', ')}
            </p>
          ) : null}
        </div>
        <div style={{ textAlign: 'right', minWidth: '120px' }}>
          <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {summary.protected_assets_count} asset{summary.protected_assets_count !== 1 ? 's' : ''}
          </p>
          <p style={{ margin: '0.1rem 0 0', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {summary.reporting_systems_count} reporting system{summary.reporting_systems_count !== 1 ? 's' : ''}
          </p>
        </div>
      </div>

      {/* ── Setup checklist ───────────────────────────────────────── */}
      <div className="dataCard" style={{ padding: '1rem', marginBottom: '1rem' }}>
        <p className="sectionEyebrow" style={{ marginBottom: '0.5rem' }}>Monitoring Setup Checklist</p>
        <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          {checklist.map((step) => <ChecklistRow key={step.label} step={step} />)}
        </ul>
      </div>

      {/* ── Health cards ──────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        <ProviderCard info={providerHealth} />
        <WorkerCard info={workerHealth} />
      </div>

      {/* ── Telemetry timeline ────────────────────────────────────── */}
      <TelemetryTimeline summary={summary} />
    </section>
  );
}
