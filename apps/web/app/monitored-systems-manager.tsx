'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

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

  return { message, code, stage };
}
type ReconcileSummary = {
  targets_scanned: number;
  created_or_updated: number;
  invalid_reasons: Record<string, number>;
  skipped_reasons: Record<string, number>;
  repaired_monitored_system_ids: string[];
};

type WorkspaceMonitoringSummary = {
  runtime_status: 'provisioning' | 'healthy' | 'degraded' | 'idle' | 'failed' | 'disabled' | 'offline';
  freshness_status: 'fresh' | 'stale' | 'unavailable';
  poll_freshness_status?: 'fresh' | 'stale' | 'unavailable';
  confidence_status: 'high' | 'medium' | 'low' | 'unavailable';
  last_heartbeat_at: string | null;
  last_telemetry_at: string | null;
  last_poll_at: string | null;
  evidence_source: 'live' | 'simulator' | 'replay' | 'none';
  coverage_state: {
    configured_systems: number;
    reporting_systems: number;
    protected_assets: number;
  };
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
  const [isReconciling, setIsReconciling] = useState(false);
  const [isTogglingId, setIsTogglingId] = useState<string | null>(null);
  const [reconcileSummary, setReconcileSummary] = useState<ReconcileSummary | null>(null);
  const [lastRepairClickAt, setLastRepairClickAt] = useState<string | null>(null);
  const [summary, setSummary] = useState<WorkspaceMonitoringSummary | null>(null);

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
    setIsReconciling(true);

    let stage: 'fetch' | 'parse' | 'reload' = 'fetch';

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
      if (isDev) {
        console.debug('[monitored-systems] reconcile response received');
      }

      const contentType = response.headers.get('content-type') ?? '';
      if (isDev) {
        console.debug('[monitored-systems] reconcile HTTP status', response.status);
        console.debug('[monitored-systems] reconcile response content-type', contentType || '(none)');
      }

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => null);
        if (isDev) {
          console.debug('[monitored-systems] reconcile non-OK payload', errorPayload);
        }
        const errorDetail = extractErrorDetail(errorPayload);
        const stageSuffix = errorDetail.stage ? ` (stage: ${errorDetail.stage})` : '';
        const codeSuffix = isDev && errorDetail.code ? ` [code: ${errorDetail.code}]` : '';
        setMessage(errorDetail.message
          ? `Repair failed: ${errorDetail.message}${stageSuffix}${codeSuffix}`
          : 'Unable to repair monitored systems.');
        return;
      }

      stage = 'parse';
      if (!contentType.toLowerCase().includes('application/json')) {
        const responseText = await response.text();
        if (isDev) {
          console.debug('[monitored-systems] reconcile response was not JSON', responseText);
        }
        setMessage('Repair response could not be parsed.');
        return;
      }

      const payload = await response.json();
      if (isDev) {
        console.debug('[monitored-systems] reconcile response parsed');
        console.debug('[monitored-systems] reconcile parsed payload', payload);
      }

      const summary: ReconcileSummary = {
        targets_scanned: Number(payload?.reconcile?.targets_scanned ?? 0),
        created_or_updated: Number(payload?.reconcile?.created_or_updated ?? 0),
        invalid_reasons: payload?.reconcile?.invalid_reasons ?? {},
        skipped_reasons: payload?.reconcile?.skipped_reasons ?? {},
        repaired_monitored_system_ids: payload?.reconcile?.repaired_monitored_system_ids ?? [],
      };
      setReconcileSummary(summary);
      const reconciledSystems = Array.isArray(payload?.systems) ? payload.systems : null;
      if (reconciledSystems) {
        setSystems(reconciledSystems);
      }

      stage = 'reload';
      if (isDev) {
        console.debug('[monitored-systems] reloading monitored systems');
      }
      const reloadedSystems = await load({
        failureMessage: 'Repair request completed or failed, but refreshing monitored systems did not succeed.',
        rethrow: true,
      });

      if (isDev) {
        console.debug('[monitored-systems] reconcile reload result count', reloadedSystems?.length ?? 0);
      }

      const visibleSystems = reloadedSystems ?? reconciledSystems ?? [];
      if (summary.created_or_updated > 0 && visibleSystems.length === 0) {
        setMessage('Repair reported success, but no monitored systems were visible after reload.');
        return;
      }

      setMessage(
        `Repair completed. ${summary.created_or_updated} monitored systems created or updated from ${summary.targets_scanned} targets scanned.`,
      );
    } catch (error) {
      if (stage === 'fetch') {
        setMessage('Repair request failed before the server responded.');
      } else if (stage === 'parse') {
        setMessage('Repair response could not be parsed.');
      } else {
        setMessage('Repair request completed or failed, but refreshing monitored systems did not succeed.');
      }

      if (isDev) {
        const normalizedError = error instanceof Error
          ? { name: error.name, message: error.message }
          : { name: typeof error, message: String(error) };
        console.debug('[monitored-systems] reconcile failed', { stage, error: normalizedError });
      }
    } finally {
      if (isDev) {
        console.debug('[monitored-systems] finally clearing isReconciling');
      }
      setIsReconciling(false);
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
        setMessage('Unable to update system status.');
        return;
      }
      await load();
      setMessage(enabled ? 'Monitoring enabled for this target.' : 'Monitoring disabled for this target.');
    } finally {
      setIsTogglingId(null);
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

  const telemetryLabel = summary?.last_telemetry_at ? new Date(summary.last_telemetry_at).toLocaleString() : 'Not available';
  const pollLabel = summary?.last_poll_at ? new Date(summary.last_poll_at).toLocaleString() : 'Not available';
  const hasLiveTelemetry = Boolean(
    summary?.last_telemetry_at
    && summary?.freshness_status === 'fresh'
    && Number(summary?.coverage_state?.reporting_systems ?? 0) > 0,
  );

  return (
    <section className="dataCard stack compactStack" data-monitored-systems-build={monitoredSystemsClientBuildTag}>
      <h1>Monitored Systems</h1>
      <p className="muted">Bridge assets to runtime monitoring through target-linked monitored systems.</p>
      {summary ? (
        <p className="tableMeta">
          Runtime {summary.runtime_status.toUpperCase()} · Live telemetry {hasLiveTelemetry ? telemetryLabel : 'unavailable'} · Last poll {pollLabel} ({summary.poll_freshness_status || 'unavailable'}) · Reporting systems {summary.coverage_state.reporting_systems}/{summary.coverage_state.configured_systems} · Evidence source {summary.evidence_source}
        </p>
      ) : null}
      <div className="buttonRow">
        <button type="button" onClick={() => void runReconcile()} disabled={isReconciling}>
          {isReconciling ? 'Repairing monitored systems…' : 'Repair monitored systems'}
        </button>
      </div>
      {isReconciling ? (
        <p className="statusLine" role="status" aria-live="polite">
          Repairing monitored systems…
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
      {systems.length === 0 ? (
        <p className="muted">
          No monitored systems yet. If you already have enabled targets, use Repair monitored systems to backfill missing links.
        </p>
      ) : null}
      {reconcileSummary ? (
        <div className="stack compactStack">
          <p className="tableMeta">
            Reconcile summary: scanned {reconcileSummary.targets_scanned} targets, created/updated {reconcileSummary.created_or_updated}, repaired IDs{' '}
            {reconcileSummary.repaired_monitored_system_ids.length}
          </p>
          <p className="tableMeta">{formatReasonCounts('Invalid reasons', reconcileSummary.invalid_reasons)}</p>
          <p className="tableMeta">{formatReasonCounts('Skipped reasons', reconcileSummary.skipped_reasons)}</p>
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
              Last event: {system.last_event_at ? new Date(system.last_event_at).toLocaleString() : 'Never'} · Coverage reason: {system.coverage_reason || 'none'}
            </p>
            {system.last_error_text ? <p className="tableMeta">Last error: {system.last_error_text}</p> : null}
          </div>
          <div className="buttonRow">
            <button type="button" onClick={() => void toggle(system)} disabled={isTogglingId === system.id || isReconciling}>
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
