import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { GET as getStatus } from '../app/api/integrations/decoda/route';
import { DELETE as deleteLink, POST as postLink } from '../app/api/integrations/decoda/links/[producer]/route';
import { POST as postRetry } from '../app/api/integrations/decoda/signals/retry/route';
import { DECODA_PRODUCT_STATE, parseDecodaProductsView, signalSummary, stateFor } from '../app/(product)/decoda-products-presentation';

const APP = path.resolve(__dirname, '..', 'app');
const WORKSPACE = '11111111-1111-4111-8111-111111111111';

async function withBackend(run: (calls: Array<{ url: string; method: string; headers: Headers }>) => Promise<void>) {
  const previous = { NODE_ENV: process.env.NODE_ENV, API_URL: process.env.API_URL };
  const originalFetch = global.fetch;
  const calls: Array<{ url: string; method: string; headers: Headers }> = [];
  Object.assign(process.env, { NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' });
  global.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: String(init?.method), headers: new Headers(init?.headers) });
    return Response.json({ ok: true }, { status: 200 });
  }) as typeof fetch;
  try {
    await run(calls);
  } finally {
    global.fetch = originalFetch;
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete (process.env as Record<string, string | undefined>)[key];
      else (process.env as Record<string, string | undefined>)[key] = value;
    }
  }
}

function request(method: string): Request {
  return new Request('https://guard.example/api/integrations/decoda', {
    method,
    headers: { authorization: 'Bearer test-token', 'x-workspace-id': WORKSPACE, 'x-csrf-token': 'csrf-1' },
  });
}

const params = (producer: string) => ({ params: Promise.resolve({ producer }) });

test.describe('Decoda products — proxy routes', () => {
  test('the status read is forwarded with the session and workspace', async () => {
    await withBackend(async (calls) => {
      const response = await getStatus(request('GET'));
      expect(response.status).toBe(200);
      expect(calls).toHaveLength(1);
      expect(calls[0].url).toBe('https://railway.decoda.example/integrations/decoda');
      expect(calls[0].headers.get('Authorization')).toBe('Bearer test-token');
      expect(calls[0].headers.get('X-Workspace-Id')).toBe(WORKSPACE);
    });
  });

  test('linking and unlinking forward the CSRF token to the backend, which enforces the admin role', async () => {
    await withBackend(async (calls) => {
      await postLink(request('POST'), params('decoda-assets'));
      await deleteLink(request('DELETE'), params('decoda-vault'));
      await postRetry(request('POST'));
      expect(calls.map((c) => [c.method, c.url])).toEqual([
        ['POST', 'https://railway.decoda.example/integrations/decoda/links/decoda-assets'],
        ['DELETE', 'https://railway.decoda.example/integrations/decoda/links/decoda-vault'],
        ['POST', 'https://railway.decoda.example/integrations/decoda/signals/retry'],
      ]);
      expect(calls.every((c) => c.headers.get('X-CSRF-Token') === 'csrf-1')).toBe(true);
    });
  });

  test('an unknown product never reaches the backend', async () => {
    await withBackend(async (calls) => {
      const response = await postLink(request('POST'), params('../admin'));
      expect(response.status).toBe(404);
      expect(calls).toHaveLength(0);
    });
  });
});

test.describe('Decoda products — truthful states', () => {
  test('receiving events is never presented as healthy monitoring', () => {
    expect(DECODA_PRODUCT_STATE.receiving.label).toBe('Receiving events');
    expect(DECODA_PRODUCT_STATE.receiving.detail).toContain('Testnet product event');
    expect(DECODA_PRODUCT_STATE.receiving.detail).toContain('not live telemetry');
    expect(DECODA_PRODUCT_STATE.awaiting_first_event.variant).not.toBe('success');
    expect(DECODA_PRODUCT_STATE.not_linked.detail).toContain('not analysed');
    for (const state of Object.values(DECODA_PRODUCT_STATE)) {
      expect(state.label.toLowerCase()).not.toContain('healthy');
    }
  });

  test('signals are summarised from what was actually delivered', () => {
    const none = { configured: true, delivered: 0, pending: 0, failed: 0, dead: 0, not_configured: 0 };
    expect(signalSummary({ ...none, configured: false }).text).toContain('not configured');
    expect(signalSummary(none)).toEqual({ text: 'No signal sent yet', variant: 'neutral' });
    expect(signalSummary({ ...none, delivered: 3 }).variant).toBe('success');
    expect(signalSummary({ ...none, delivered: 3, dead: 1 })).toEqual({ text: '1 signal(s) not delivered', variant: 'danger' });
    expect(signalSummary({ ...none, pending: 2 }).variant).toBe('warning');
  });

  test('an unreadable answer is unavailable, never an empty or healthy state', () => {
    expect(parseDecodaProductsView({})).toBeNull();
    expect(parseDecodaProductsView(null)).toBeNull();
    expect(parseDecodaProductsView({ organization: {}, products: [{ producer: 'decoda-assets' }] })).toBeNull();
    const ok = parseDecodaProductsView({
      organization: { linked: true, has_platform_organization: true },
      can_manage: 'yes',
      products: [{ producer: 'decoda-assets', state: 'receiving', signals: { configured: true } }],
    });
    expect(ok?.products).toHaveLength(1);
    expect(ok?.can_manage).toBe(false);
    expect(stateFor('something_new').label).toBe('Status unknown');
  });

  test('the Integrations page shows the panel and alerts carry their evidence label', () => {
    const page = readFileSync(path.join(APP, '(product)', 'integrations-page-client.tsx'), 'utf8');
    expect(page).toContain('<DecodaProductsPanel />');
    const alerts = readFileSync(path.join(APP, 'alerts-screen.tsx'), 'utf8');
    expect(alerts).toContain('alert.evidence_label');
    expect(alerts).toContain('data-testid="alert-evidence-label"');
  });
});
