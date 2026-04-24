import { expect, test } from '@playwright/test';

import { clearRuntimeStatusCacheForTests, fetchRuntimeStatusDeduped } from '../app/runtime-status-client';

test.describe('runtime status client dedupe', () => {
  test.afterEach(() => {
    clearRuntimeStatusCacheForTests();
  });

  test('dedupes inflight requests across header casing variants', async () => {
    let calls = 0;
    const originalFetch = global.fetch;
    global.fetch = (async () => {
      calls += 1;
      return new Response(JSON.stringify({ monitoring_status: 'active' }), { status: 200 });
    }) as typeof fetch;

    try {
      const [first, second] = await Promise.all([
        fetchRuntimeStatusDeduped({ 'X-Workspace-Id': 'workspace-a' }),
        fetchRuntimeStatusDeduped({ 'x-workspace-id': 'workspace-a' }),
      ]);

      expect(first?.monitoring_status).toBe('active');
      expect(second?.monitoring_status).toBe('active');
      expect(calls).toBe(1);
    } finally {
      global.fetch = originalFetch;
    }
  });

  test('forceRefresh bypasses recent cache and performs a new request', async () => {
    let calls = 0;
    const originalFetch = global.fetch;
    global.fetch = (async () => {
      calls += 1;
      return new Response(JSON.stringify({ monitoring_status: calls === 1 ? 'active' : 'limited' }), { status: 200 });
    }) as typeof fetch;

    try {
      const first = await fetchRuntimeStatusDeduped({ 'X-Workspace-Id': 'workspace-b' });
      const second = await fetchRuntimeStatusDeduped({ 'X-Workspace-Id': 'workspace-b' });
      const forced = await fetchRuntimeStatusDeduped({ 'X-Workspace-Id': 'workspace-b' }, { forceRefresh: true });

      expect(first?.monitoring_status).toBe('active');
      expect(second?.monitoring_status).toBe('active');
      expect(forced?.monitoring_status).toBe('limited');
      expect(calls).toBe(2);
    } finally {
      global.fetch = originalFetch;
    }
  });
});
