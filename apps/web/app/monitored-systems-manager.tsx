'use client';

import { useEffect, useState } from 'react';

import type { MonitoringRuntimeStatus } from './monitoring-status-contract';
import { usePilotAuth } from './pilot-auth-context';
import { hasLiveTelemetry, resolveWorkspaceMonitoringTruth } from './workspace-monitoring-truth';

type Props = { apiUrl: string };

type SystemRow = {
  id: string;
  asset_id: string;
  target_id: string;
  asset_name?: string;
  target_name?: string;
  chain?: string;
  is_enabled: boolean;
  runtime_status: 'provisioning' | 'healthy' | 'degraded' | 'idle' | 'failed' | 'disabled';
  freshness_status?: 'fresh' | 'stale' | 'unavailable' | null;
  confidence_status?: 'high' | 'medium' | 'low' | 'unavailable' | null;
  last_heartbeat?: string | null;
  last_event_at?: string | null;
  last_error_text?: string | null;
  coverage_reason?: string | null;
};

type ErrorDetail = {
  message: string;
  code?: string;
  stage?: string;
  reason?: string;
};

function extractErrorDetail(payload: unknown): ErrorDetail {
  const fallback: ErrorDetail = { message: '' };
  if (!payload || typeof payload !== 'object') {
    return fallback;
  }

  const value = payload as Record<string, unknown>;
  const nestedDetail = value.detail;
  const errorObject = nestedDetail && typeof nestedDetail === 'object'
    ? nestedDetail as Record<string, unknown>
    : value;

  const message = [
    typeof errorObject.detail === 'string' ? errorObject.detail.trim() : '',
    typeof value.detail === 'string' ? value.detail.trim() : '',
    typeof value.message === 'string' ? value.message.trim() : '',
  ].find((candidate) => candidate.length > 0) ?? '';

  const code = typeof errorObject.code === 'string'
    ? errorObject.code.trim() || undefined
    : (typeof value.code === 'string' ? value.code.trim() || undefined : undefined);
  const stage = typeof errorObject.stage === 'string'
    ? errorObject.stage.trim() || undefined
    : (typeof value.stage === 'string' ? value.stage.trim() || undefined : undefined);
  const reason = typeof errorObject.reason === 'string'
    ? errorObject.reason.trim() || undefined
    : (typeof value.reason === 'string' ? value.reason.trim() || undefined : undefined);

  return { message, code, stage, reason };
}

type ReconcileSummary = {
  state?: 'success' | 'failure';
  reconcile_id?: string | null;
  targets_scanned: number;
  created_or_updated: number;
  invalid_reasons: Record<string, number>;
  skipped_reasons: Record<string, number>;
  invalid_target_details?: Array<{ target_id: string; code: string; reason: string }>;
  skipped_target_details?: Array<{ target_id: string; code: string; reason: string }>;
  repaired_monitored_system_ids: string[];
};

type RepairState = 'idle' | 'pending_request' | 'pending_parse' | 'pending_refresh' | 'success' | 'failure';

type RepairFailureReason = {
  stage: 'request' | 'parse' | 'refresh' | 'reconcile' | 'timeout';
  code: string;
  backendReason: string;
  backendStage?: string | null;
};

type ReconcileTransportDebug = {
  status: number | null;
  detail: string | null;
};

function formatReasonCounts(label: string, reasons: Record<string, number>): string {
  const entries = Object.entries(reasons);
  if (!entries.length) {
    return `${label}: none`;
  }
  const details = entries.map(([reason, count]) => `${reason} (${count})`).join(', ');
  return `${label}: ${details}`;
}

const isDev = process.env.NODE_ENV !== 'production';
const monitoredSystemsClientBuildTag = process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA?.slice(0, 7) ?? 'local';
const REQUEST_TIMEOUT_MS = 15000;

async function fetchWithTimeout(input: string, init: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }
}

export default function MonitoredSystemsManager({ apiUrl }: Props) {
  const { apiUrl: runtimeApiUrl, authHeaders } = usePilotAuth();
  const effectiveApiUrl = runtimeApiUrl || apiUrl;
  const [systems, setSystems] = useState<SystemRow[]>([]);
  const [message, setMessage] = useState('');
  const [repairState, setRepairState] = useState<RepairState>('idle');
  const [repairFailureReason, setRepairFailureReason] = useState<RepairFailureReason | null>(null);
  const [isTogglingId, setIsTogglingId] = useState<string | null>(null);
  const [reconcileSummary, setReconcileSummary] = useState<ReconcileSummary | null>(null);
  const [lastReconcileId, setLastReconcileId] = useState<string | null>(null);
  const [lastRepairClickAt, setLastRepairClickAt] = useState<string | null>(null);
  const [summary, setSummary] = useState<MonitoringRuntimeStatus['workspace_monitoring_summary'] | null>(null);
  const [retryPausedReason, setRetryPausedReason] = useState<string | null>(null);
  const [reconcileTransportDebug, setReconcileTransportDebug] = useState<ReconcileTransportDebug | null>(null);
  const [isCreatingTreasuryTarget, setIsCreatingTreasuryTarget] = useState(false);

  async function load(options?: { failureMessage?: string; rethrow?: boolean }) {
    if (isDev) {
      console.debug('[monitored-systems] loading monitored systems');
    }
    try {
      const response = await fetchWithTimeout(`${effectiveApiUrl}/monitoring/systems`, { headers: authHeaders(), cache: 'no-store' });
      if (!response.ok) {
        setMessage(options?.failureMessage ?? 'Unable to load monitored systems.');
        if (isDev) {
          console.debug('[monitored-systems] reload failure', { status: response.status });
        }
        return null;
      }
      const payload = await response.json();
      const loadedSystems = payload.systems ?? [];
      setSystems(loadedSystems);
      setSummary(payload.workspace_monitoring_summary ?? null);
      if (isDev) {
        console.debug('[monitored-systems] reload success', { count: loadedSystems.length });
      }
      return loadedSystems;
    } catch (error) {
      setMessage(options?.failureMessage ?? 'Unable to load monitored systems.');
      if (isDev) {
        const normalizedError = error instanceof Error
          ? { name: error.name, message: error.message }
          : { name: typeof error, message: String(error) };
        console.debug('[monitored-systems] reload failure', normalizedError);
      }
      if (options?.rethrow) {
        throw error;
      }
      return null;
    }
  }

  async function runReconcile() {
    if (isDev) {
      console.debug('[monitored-systems] repair click received');
      console.debug('[monitored-systems] client build tag', monitoredSystemsClientBuildTag);
      console.debug('[monitored-systems] runtime config apiUrl', runtimeApiUrl || '(missing)');
      console.debug('[monitored-systems] server-rendered apiUrl', apiUrl || '(missing)');
      console.debug('[monitored-systems] effective apiUrl', effectiveApiUrl || '(missing)');
      setLastRepairClickAt(new Date().toISOString());
    }

    setMessage('');
    setRepairFailureReason(null);
    setReconcileTransportDebug(null);
    setRetryPausedReason(null);
    setRepairState('pending_request');

    let stage: 'request' | 'parse' | 'refresh' = 'request';
    let shouldRefreshAfterResponse = false;
    let failedReason: RepairFailureReason | null = null;
    let localSummary: ReconcileSummary | null = null;
    let didResolveTerminalState = false;

    try {
      if (isDev) {
        console.debug('[monitored-systems] reconcile request started');
      }

      const reconcileUrl = '/api/monitoring/systems/reconcile';
      console.info('[monitored-systems] reconcile URL', reconcileUrl);
      if (isDev) {
        console.debug('[monitored-systems] reconcile request origin', window.location.origin);
      }

      const response = await fetchWithTimeout(reconcileUrl, {
        method: 'POST',
        headers: authHeaders(),
      });
      shouldRefreshAfterResponse = true;
      if (isDev) {
        console.debug('[monitored-systems] reconcile response received');
      }

      const contentType = response.headers.get('content-type') ?? '';
      if (isDev) {
        console.debug('[monitored-systems] reconcile HTTP status', response.status);
        console.debug('[monitored-systems] reconcile response content-type', contentType || '(none)');
      }

      stage = 'parse';
      setRepairState('pending_parse');

      if (!contentType.toLowerCase().includes('application/json')) {
        const responseText = await response.text();
        setReconcileTransportDebug({
          status: response.status,
          detail: responseText.trim() || null,
        });
        if (isDev) {
          console.debug('[monitored-systems] reconcile response was not JSON', responseText);
        }
        failedReason = {
          stage: 'parse',
          code: 'invalid_response_content_type',
          backendReason: 'Repair response was not valid JSON.',
        };
      } else {
        const payload = await response.json();
        const errorDetail = extractErrorDetail(payload);
        setReconcileTransportDebug({
          status: response.status,
          detail: errorDetail.reason || errorDetail.message || null,
        });
        if (isDev) {
          console.debug('[monitored-systems] reconcile response parsed');
          console.debug('[monitored-systems] reconcile parsed payload', payload);
        }

        localSummary = {
          state: payload?.state,
          reconcile_id: payload?.reconcile_id ?? null,
          targets_scanned: Number(payload?.reconcile?.targets_scanned ?? 0),
          created_or_updated: Number(payload?.reconcile?.created_or_updated ?? 0),
          invalid_reasons: payload?.reconcile?.invalid_reasons ?? {},
          skipped_reasons: payload?.reconcile?.skipped_reasons ?? {},
          invalid_target_details: payload?.reconcile?.invalid_target_details ?? [],
          skipped_target_details: payload?.reconcile?.skipped_target_details ?? [],
          repaired_monitored_system_ids: payload?.reconcile?.repaired_monitored_system_ids ?? [],
        };
        setReconcileSummary(localSummary);
        setLastReconcileId(localSummary.reconcile_id ?? null);

        if (!response.ok) {
          failedReason = {
            stage: 'reconcile',
            code: errorDetail.code || 'reconcile_request_rejected',
            backendReason: errorDetail.reason || errorDetail.message || 'Reconcile request was rejected by the API.',
            backendStage: errorDetail.stage ?? null,
          };
        }

        if (!failedReason && localSummary.state === 'failure') {
          failedReason = {
            stage: 'reconcile',
            code: 'monitoring_reconcile_failed',
            backendReason: 'Reconcile reported failure.',
          };
        }
        if (!failedReason && Array.isArray(payload?.unresolved_reasons) && payload.unresolved_reasons.length > 0) {
          const firstReason = payload.unresolved_reasons[0];
          failedReason = {
            stage: 'reconcile',
            code: typeof firstReason?.code === 'string' && firstReason.code ? firstReason.code : 'reconcile_unresolved_reasons',
            backendReason: typeof firstReason?.backendReason === 'string' && firstReason.backendReason
              ? firstReason.backendReason
              : 'Repair completed with unresolved target reasons.',
            backendStage: typeof firstReason?.stage === 'string' ? firstReason.stage : null,
          };
        }
      }

      stage = 'refresh';
      setRepairState('pending_refresh');
      if (shouldRefreshAfterResponse) {
        const reloadedSystems = await load({
          failureMessage: 'Repair finished, but refreshing monitored systems failed.',
          rethrow: true,
        });

        if (isDev) {
          console.debug('[monitored-systems] reconcile reload result count', reloadedSystems?.length ?? 0);
        }

        if (!failedReason && localSummary && localSummary.created_or_updated > 0 && (reloadedSystems ?? []).length === 0) {
          failedReason = {
            stage: 'refresh',
            code: 'refresh_empty_after_updates',
            backendReason: 'Repair reported updates, but no monitored systems were visible after refresh.',
          };
        }
      }

      if (failedReason) {
        setRepairFailureReason(failedReason);
        setRetryPausedReason(failedReason.backendReason);
        setRepairState('failure');
        didResolveTerminalState = true;
      } else {
        setRepairState('success');
        didResolveTerminalState = true;
      }
    } catch (error) {
      await load({
        failureMessage: 'Repair failed and refresh from workspace truth also failed.',
      });
      const isTimeout = error instanceof Error && error.name === 'AbortError';
      if (stage === 'request') {
        setRepairFailureReason({
          stage: 'request',
          code: isTimeout ? 'repair_request_timeout' : 'repair_request_failed',
          backendReason: isTimeout
            ? 'Repair request timed out before the server responded.'
            : 'Repair request failed before the server responded.',
        });
        setRetryPausedReason(isTimeout
          ? 'Repair request timed out before the server responded.'
          : 'Repair request failed before the server responded.');
      } else if (stage === 'parse') {
        setRepairFailureReason({
          stage: 'parse',
          code: isTimeout ? 'repair_parse_timeout' : 'repair_parse_failed',
          backendReason: isTimeout ? 'Repair response timed out while parsing.' : 'Repair response could not be parsed.',
        });
        setRetryPausedReason(isTimeout ? 'Repair response timed out while parsing.' : 'Repair response could not be parsed.');
      } else {
        setRepairFailureReason({
          stage: 'refresh',
          code: isTimeout ? 'repair_refresh_timeout' : 'repair_refresh_failed',
          backendReason: isTimeout ? 'Repair refresh timed out.' : 'Repair finished, but refreshing monitored systems failed.',
        });
        setRetryPausedReason(isTimeout ? 'Repair refresh timed out.' : 'Repair finished, but refreshing monitored systems failed.');
      }
      setRepairState('failure');
      didResolveTerminalState = true;

      if (isDev) {
        const normalizedError = error instanceof Error
          ? { name: error.name, message: error.message }
          : { name: typeof error, message: String(error) };
        console.debug('[monitored-systems] reconcile failed', { stage, error: normalizedError });
      }
    } finally {
      if (!didResolveTerminalState) {
        setRepairFailureReason({
          stage: 'timeout',
          code: 'repair_terminal_state_timeout',
          backendReason: 'Repair did not reach a terminal state and was safely failed.',
        });
        setRetryPausedReason('Repair did not reach a terminal state and was safely failed.');
        setRepairState('failure');
      }
    }
  }

  async function toggle(system: SystemRow) {
    setMessage('');
    setIsTogglingId(system.id);
    const enabled = !system.is_enabled;
    try {
      const response = await fetchWithTimeout(`${effectiveApiUrl}/monitoring/systems/${system.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ enabled }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const errorDetail = extractErrorDetail(payload);
        await load();
        setMessage(
          `Unable to update system status.${errorDetail.stage ? ` [stage:${errorDetail.stage}]` : ''}${errorDetail.code ? ` [${errorDetail.code}]` : ''} ${errorDetail.reason || errorDetail.message || 'Unknown backend reason.'}`.trim(),
        );
        return;
      }
      const refreshedSystems = await load();
      if (Array.isArray(refreshedSystems)) {
        const authoritative = refreshedSystems.find((row) => row.id === system.id);
        if (!authoritative || authoritative.is_enabled !== enabled) {
          setMessage(`Toggle was rolled back by server truth.${enabled ? ' [toggle_enable_conflict]' : ' [toggle_disable_conflict]'}`);
          return;
        }
      }
      setMessage(enabled ? 'Monitoring enabled for this target.' : 'Monitoring disabled for this target.');
    } finally {
      setIsTogglingId(null);
    }
  }

  async function createTreasurySettlementTarget() {
    setMessage('');
    setIsCreatingTreasuryTarget(true);
    try {
      const response = await fetchWithTimeout('/api/monitoring/systems/repair/treasury-settlement-target', {
        method: 'POST',
        headers: authHeaders(),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const errorDetail = extractErrorDetail(payload);
        setMessage(errorDetail.reason || errorDetail.message || 'Unable to create monitoring target.');
        return;
      }
      await load();
      setMessage('Monitoring target is ready for US Treasury Settlement Contract.');
    } finally {
      setIsCreatingTreasuryTarget(false);
    }
  }

  async function remove(systemId: string) {
    const response = await fetch(`${effectiveApiUrl}/monitoring/systems/${systemId}`, { method: 'DELETE', headers: authHeaders() });
    if (!response.ok) {
      setMessage('Unable to delete monitored system.');
      return;
    }
    setMessage('Monitored system deleted.');
    void load();
  }

  useEffect(() => {
    void load();
  }, []);

  const runtimeLike: MonitoringRuntimeStatus = { mode: 'OFFLINE', workspace_monitoring_summary: summary ?? undefined };
  const truth = resolveWorkspaceMonitoringTruth(runtimeLike);
  const telemetryLabel = truth.last_telemetry_at ? new Date(truth.last_telemetry_at).toLocaleString() : 'Not available';
  const pollLabel = truth.last_poll_at ? new Date(truth.last_poll_at).toLocaleString() : 'Not available';
  const showLiveTelemetry = hasLiveTelemetry(truth);
  const isRepairPending = repairState === 'pending_request' || repairState === 'pending_parse' || repairState === 'pending_refresh';
  const isRetryPaused = Boolean(retryPausedReason) && !isRepairPending;

  return (
    <section className="dataCard stack compactStack" data-monitored-systems-build={monitoredSystemsClientBuildTag}>
      <h1>Monitored Systems</h1>
      <p className="muted">Bridge assets to runtime monitoring through target-linked monitored systems.</p>
      {summary ? (
        <p className="tableMeta">
          Runtime {truth.runtime_status.toUpperCase()} · Monitoring {truth.monitoring_status.toUpperCase()} · Live telemetry {showLiveTelemetry ? telemetryLabel : 'unavailable'} · Last poll {pollLabel} · Reporting systems {truth.reporting_systems_count}/{truth.monitored_systems_count} · Evidence source {truth.evidence_source_summary}
        </p>
      ) : null}
      <div className="buttonRow">
        <button type="button" onClick={() => void runReconcile()} disabled={isRepairPending || isRetryPaused} title={isRetryPaused ? 'Resolve backend reconcile reason before retrying.' : undefined}>
          {isRepairPending ? 'Repairing monitored systems…' : 'Repair monitored systems'}
        </button>
      </div>
      {isRepairPending ? (
        <p className="tableMeta" role="status" aria-live="polite">
          If “Repairing monitored systems…” appears stuck for over 20 seconds, hard refresh and reopen /monitored-systems to verify whether changes persisted.
        </p>
      ) : null}
      {repairState === 'pending_request' ? (
        <p className="statusLine" role="status" aria-live="polite">
          Sending repair request…
        </p>
      ) : null}
      {repairState === 'pending_parse' ? (
        <p className="statusLine" role="status" aria-live="polite">
          Parsing repair response…
        </p>
      ) : null}
      {repairState === 'pending_refresh' ? (
        <p className="statusLine" role="status" aria-live="polite">
          Refreshing monitored systems from workspace truth…
        </p>
      ) : null}
      {isDev && lastRepairClickAt ? (
        <p className="tableMeta" data-testid="repair-click-debug">
          Debug: repair click received at {lastRepairClickAt} · build {monitoredSystemsClientBuildTag} · api {effectiveApiUrl || '(missing)'}
        </p>
      ) : null}
      {message ? (
        <p className="statusLine" role="alert" aria-live="assertive">
          {message}
        </p>
      ) : null}
      {repairState === 'success' && reconcileSummary ? (
        <p className="statusLine" role="status" aria-live="polite">
          Repair {reconcileSummary.state || 'success'} (reconcile id: {reconcileSummary.reconcile_id || 'unknown'}): {reconcileSummary.created_or_updated} monitored systems created or updated from {reconcileSummary.targets_scanned} targets scanned.
        </p>
      ) : null}
      {repairState === 'failure' && repairFailureReason ? (
        <p className="statusLine" role="alert" aria-live="assertive">
          {repairFailureReason.code ? `Code ${repairFailureReason.code}. ` : ''}
          Repair failed during {repairFailureReason.backendStage || repairFailureReason.stage}.{' '}
          {repairFailureReason.backendReason}
        </p>
      ) : null}
      {retryPausedReason ? (
        <p className="statusLine" role="alert" aria-live="assertive">
          Retry loop paused until backend reason is resolved: {retryPausedReason}
        </p>
      ) : null}
      {repairState === 'failure' ? (
        <p className="tableMeta" role="status" aria-live="polite">
          After hard refresh, verify /monitored-systems: if data persisted, treat this as a frontend state-sync issue; if data did not persist, treat this as an API reconcile failure.
        </p>
      ) : null}
      {reconcileTransportDebug ? (
        <p className="tableMeta" role="status" aria-live="polite">
          Reconcile backend response: status {reconcileTransportDebug.status ?? 'unknown'} · detail {reconcileTransportDebug.detail || 'none'}
        </p>
      ) : null}
      {systems.length === 0 ? (
        <div className="stack compactStack">
          <p className="muted">No monitoring target is linked to this asset yet.</p>
          <div className="buttonRow">
            <button type="button" onClick={() => void createTreasurySettlementTarget()} disabled={isCreatingTreasuryTarget || isRepairPending}>
              {isCreatingTreasuryTarget ? 'Creating monitoring target…' : 'Create monitoring target for US Treasury Settlement Contract'}
            </button>
          </div>
        </div>
      ) : null}
      {reconcileSummary ? (
        <div className="stack compactStack">
          <p className="tableMeta">
            Reconcile summary: scanned {reconcileSummary.targets_scanned} targets, created/updated {reconcileSummary.created_or_updated}, repaired IDs{' '}
            {reconcileSummary.repaired_monitored_system_ids.length}
          </p>
          <p className="tableMeta">State: {reconcileSummary.state || 'unknown'} · Reconcile ID: {reconcileSummary.reconcile_id || lastReconcileId || 'unknown'}</p>
          <p className="tableMeta">{formatReasonCounts('Invalid reasons', reconcileSummary.invalid_reasons)}</p>
          <p className="tableMeta">{formatReasonCounts('Skipped reasons', reconcileSummary.skipped_reasons)}</p>
          {(reconcileSummary.invalid_target_details ?? []).map((detail) => (
            <p key={`invalid-${detail.target_id}-${detail.code}`} className="tableMeta">
              Invalid target {detail.target_id}: [{detail.code}] {detail.reason}
            </p>
          ))}
          {(reconcileSummary.skipped_target_details ?? []).map((detail) => (
            <p key={`skipped-${detail.target_id}-${detail.code}`} className="tableMeta">
              Skipped target {detail.target_id || 'n/a'}: [{detail.code}] {detail.reason}
            </p>
          ))}
        </div>
      ) : null}
      {systems.map((system) => (
        <article key={system.id} className="overviewListItem">
          <div>
            <p>
              <strong>{system.asset_name || system.asset_id}</strong> → {system.target_name || system.target_id}
            </p>
            <p className="tableMeta">
              {system.chain || 'unknown chain'} · Config: {system.is_enabled ? 'Enabled' : 'Disabled'} · Runtime: {system.runtime_status} · Freshness: {system.freshness_status || 'unavailable'} · Confidence: {system.confidence_status || 'unavailable'}
              {' · '}
              Last heartbeat: {system.last_heartbeat ? new Date(system.last_heartbeat).toLocaleString() : 'Never'}
            </p>
            <p className="tableMeta">
              Last telemetry event: {system.last_event_at ? new Date(system.last_event_at).toLocaleString() : 'No telemetry recorded yet'} · Coverage reason: {system.coverage_reason || 'none'}
            </p>
            {!system.last_event_at && system.last_heartbeat ? <p className="tableMeta">Heartbeat is present, but telemetry is still unavailable for this system.</p> : null}
            {system.last_error_text ? <p className="tableMeta">Last error: {system.last_error_text}</p> : null}
          </div>
          <div className="buttonRow">
            <button type="button" onClick={() => void toggle(system)} disabled={isTogglingId === system.id || isRepairPending}>
              {system.is_enabled ? 'Disable' : 'Enable'}
            </button>
            <button type="button" onClick={() => void remove(system.id)}>
              Delete
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}
