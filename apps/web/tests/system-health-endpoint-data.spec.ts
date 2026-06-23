import { expect, test } from '@playwright/test';

import {
  diagnoseSystemHealthFailure,
  fetchSystemHealth,
  type SystemHealthFetchResult,
} from '../app/(product)/system-health/_components/fetch-system-health';

const API_URL = 'https://api.decoda.example';

type FetchMock = typeof fetch;

async function withMockFetch(implementation: FetchMock, run: () => Promise<void> | void) {
  const originalFetch = global.fetch;
  global.fetch = implementation;
  try {
    await run();
  } finally {
    global.fetch = originalFetch;
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Minimal payload shaped like the real backend /ops/system-health response. */
function buildPayload(overrides: Record<string, unknown> = {}) {
  return {
    generated_at: '2026-06-23T10:00:00Z',
    environment: 'production',
    version: '1.0.0',
    git_commit: 'abcdef1234',
    overall_status: 'failing',
    summary: 'base rpc is failing; telemetry is degraded.',
    primary_action: 'Check EVM_RPC_URL.',
    components: {
      api: { status: 'healthy', message: 'API is responding.' },
      database: { status: 'healthy', message: 'Database is reachable.' },
      redis: { status: 'healthy', message: 'Redis ping succeeded.' },
      worker: { status: 'healthy', message: 'Worker heartbeat is fresh.' },
      base_rpc: { status: 'failing', message: 'eth_blockNumber failed.', action: 'Check EVM_RPC_URL.' },
      live_polling: { status: 'healthy', message: 'Live polling is active.' },
      telemetry: { status: 'degraded', message: 'Last telemetry is stale.' },
      detection: { status: 'healthy', message: 'Detection is recent.' },
      alert_delivery: { status: 'healthy', message: 'Alert delivery is healthy.' },
    },
    live_chain_monitoring: { diagnosis: 'Base RPC is failing.' },
    events: [],
    providers: [],
    reliability: {},
    ...overrides,
  };
}

function assertNotOk(
  result: SystemHealthFetchResult,
): asserts result is Extract<SystemHealthFetchResult, { ok: false }> {
  expect(result.ok).toBe(false);
}

// 1. Fetch throws → endpoint unreachable (network_error), not all-components-down.
test('fetch network error yields an unreachable result, not component statuses', async () => {
  await withMockFetch(async () => {
    throw new TypeError('fetch failed');
  }, async () => {
    const result = await fetchSystemHealth(API_URL, { timeoutMs: 1000 });
    assertNotOk(result);
    expect(result.reason).toBe('network_error');
    expect(result.data).toBeNull();
    expect(result.url).toBe(`${API_URL}/ops/system-health`);
    // Sanitized error must not leak internals.
    expect(result.error).not.toContain(API_URL);
    expect(diagnoseSystemHealthFailure(result).category).toBe('endpoint_unreachable');
  });
});

// 1b. Abort/timeout path is classified as a timeout (dedicated longer budget exists).
test('fetch timeout is reported as a timeout failure', async () => {
  await withMockFetch(async () => {
    throw Object.assign(new Error('The operation was aborted'), { name: 'AbortError' });
  }, async () => {
    const result = await fetchSystemHealth(API_URL, { timeoutMs: 25 });
    assertNotOk(result);
    expect(result.reason).toBe('timeout');
    expect(result.error).toContain('timed out');
  });
});

// 2. HTTP 401/403/404/500 → endpoint/API error, not all components unavailable.
for (const status of [401, 403, 404, 500]) {
  test(`HTTP ${status} is surfaced as an endpoint/API error with the status code`, async () => {
    await withMockFetch(async () => jsonResponse({ detail: 'nope' }, status), async () => {
      const result = await fetchSystemHealth(API_URL, { timeoutMs: 1000 });
      assertNotOk(result);
      expect(result.reason).toBe('http_error');
      expect(result.status).toBe(status);
      expect(result.data).toBeNull();

      const diagnosis = diagnoseSystemHealthFailure(result);
      if (status === 401 || status === 403) {
        expect(diagnosis.category).toBe('auth');
      } else {
        expect(diagnosis.category).toBe('backend_error');
      }
    });
  });
}

// 3 + 4 + 5. Valid JSON → real per-component statuses preserved verbatim.
test('valid response preserves real per-component statuses', async () => {
  await withMockFetch(async () => jsonResponse(buildPayload()), async () => {
    const result = await fetchSystemHealth(API_URL, { timeoutMs: 1000 });
    expect(result.ok).toBe(true);
    if (!result.ok) return;

    const components = result.data.components;
    // 4. Base RPC failing does NOT drag down API/database/redis/worker.
    expect(components.base_rpc.status).toBe('failing');
    expect(components.api.status).toBe('healthy');
    expect(components.database.status).toBe('healthy');
    expect(components.redis.status).toBe('healthy');
    expect(components.worker.status).toBe('healthy');
    // 5. Stale telemetry is reported as degraded (not unavailable, not failing).
    expect(components.telemetry.status).toBe('degraded');
  });
});

// 8. Response shape that doesn't match the contract is rejected (no silent pass).
test('response missing the components contract is rejected as invalid', async () => {
  await withMockFetch(async () => jsonResponse({ generated_at: 'now', overall_status: 'healthy' }), async () => {
    const result = await fetchSystemHealth(API_URL, { timeoutMs: 1000 });
    assertNotOk(result);
    expect(result.reason).toBe('invalid_contract');
    expect(diagnoseSystemHealthFailure(result).category).toBe('backend_error');
  });
});

// Missing API base URL → configuration error (distinct from a reachable endpoint).
test('missing API base URL is reported as a configuration error', async () => {
  const result = await fetchSystemHealth('', { timeoutMs: 1000 });
  assertNotOk(result);
  expect(result.reason).toBe('not_configured');
  expect(diagnoseSystemHealthFailure(result).category).toBe('not_configured');
});
