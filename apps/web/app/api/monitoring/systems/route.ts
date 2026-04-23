import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const PROXY_TIMEOUT_MS = 60000;
const TIMEOUT_RETRY_ATTEMPTS = 1;
const SLOW_REQUEST_LOG_THRESHOLD_MS = 10000;
const LATENCY_SAMPLE_SIZE = 200;

const monitoringSystemsLatencySamples: number[] = [];

function percentile(values: number[], quantile: number): number {
  if (!values.length) {
    return 0;
  }
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * quantile) - 1));
  return sorted[index];
}

function recordMonitoringSystemsLatency(durationMs: number, status?: number) {
  monitoringSystemsLatencySamples.push(durationMs);
  if (monitoringSystemsLatencySamples.length > LATENCY_SAMPLE_SIZE) {
    monitoringSystemsLatencySamples.shift();
  }
  console.info('[monitoring-systems-proxy] latency percentiles', {
    endpoint: '/monitoring/systems',
    status,
    sampleSize: monitoringSystemsLatencySamples.length,
    p50Ms: percentile(monitoringSystemsLatencySamples, 0.5),
    p90Ms: percentile(monitoringSystemsLatencySamples, 0.9),
    p95Ms: percentile(monitoringSystemsLatencySamples, 0.95),
    p99Ms: percentile(monitoringSystemsLatencySamples, 0.99),
    timeoutMs: PROXY_TIMEOUT_MS,
  });
}

function jsonError(status: number, body: Record<string, unknown>) {
  return Response.json(body, {
    status,
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'application/json',
    },
  });
}

async function fetchMonitoringSystemsWithRetry(
  backendApiUrl: string,
  headers: Headers,
): Promise<{ response: Response; durationMs: number; attempts: number }> {
  const maxAttempts = TIMEOUT_RETRY_ATTEMPTS + 1;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const controller = new AbortController();
    const startedAt = Date.now();
    const timeoutId = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);
    console.info('[monitoring-systems-proxy] forwarding request to backend', { backendApiUrl, attempt, maxAttempts, timeoutMs: PROXY_TIMEOUT_MS });

    try {
      const response = await fetch(`${backendApiUrl}/monitoring/systems`, {
        method: 'GET',
        headers,
        cache: 'no-store',
        signal: controller.signal,
      });
      const durationMs = Date.now() - startedAt;
      const logger = durationMs >= SLOW_REQUEST_LOG_THRESHOLD_MS ? console.warn : console.info;
      logger('[monitoring-systems-proxy] backend response received', {
        status: response.status,
        durationMs,
        attempt,
      });
      recordMonitoringSystemsLatency(durationMs, response.status);
      return { response, durationMs, attempts: attempt };
    } catch (error) {
      const durationMs = Date.now() - startedAt;
      if (error instanceof Error && error.name === 'AbortError') {
        console.warn('[monitoring-systems-proxy] backend request timed out', {
          durationMs,
          attempt,
          maxAttempts,
          timeoutMs: PROXY_TIMEOUT_MS,
          willRetry: attempt < maxAttempts,
        });
        recordMonitoringSystemsLatency(durationMs);
        if (attempt < maxAttempts) {
          continue;
        }
      }
      throw error;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  throw new Error('monitoring systems proxy exhausted retries unexpectedly');
}

export async function GET(request: Request) {
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);
  if (!runtimeConfig.configured || !backendApiUrl) {
    return jsonError(500, {
      detail: runtimeConfig.diagnostic ?? 'Web runtime proxy is not configured with a valid backend API URL.',
      code: 'invalid_runtime_config',
      transport: 'same-origin proxy',
      configured: false,
    });
  }
  const headers = new Headers();
  headers.set('Accept', 'application/json');
  const authorization = request.headers.get('authorization');
  if (authorization) {
    headers.set('Authorization', authorization);
  }
  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id'));
  if (workspaceId) {
    headers.set('X-Workspace-Id', workspaceId);
  }

  try {
    const { response, durationMs, attempts } = await fetchMonitoringSystemsWithRetry(backendApiUrl, headers);
    const payload = await response.json().catch(() => ({}));
    return Response.json(payload, {
      status: response.status,
      headers: {
        'Cache-Control': 'no-store',
        'X-Proxy-Duration-Ms': String(durationMs),
        'X-Proxy-Attempts': String(attempts),
      },
    });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return jsonError(504, { detail: 'Timed out waiting for backend monitored systems list.', code: 'backend_timeout', transport: 'same-origin proxy' });
    }
    return jsonError(502, { detail: 'Backend unreachable.', code: 'backend_unreachable', transport: 'same-origin proxy' });
  }
}
