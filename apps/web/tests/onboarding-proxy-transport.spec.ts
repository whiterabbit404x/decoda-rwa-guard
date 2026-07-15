/**
 * Source contracts for the Onboarding "Failed to fetch" fix.
 *
 * Root cause: the onboarding client was the only feature that called the backend API
 * directly from the browser via `${apiUrl}${path}` using the server-resolved backend
 * URL. In production that origin is not browser-reachable (internal Railway URL /
 * cross-origin / http), so the browser fetch rejected with "Failed to fetch" before the
 * request ever reached the backend. Every other feature routes through same-origin
 * /api/* proxy routes. These tests pin onboarding to that same transport so the
 * regression cannot come back.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');
const read = (...segments: string[]) => fs.readFileSync(path.join(appDir, ...segments), 'utf-8');

const clientSrc = read('(product)', 'onboarding-page-client.tsx');
const agentSrc = read('onboarding-agent-client.ts');
const pageSrc = read('(product)', 'onboarding', 'page.tsx');
const onboardingApiDir = path.join(appDir, 'api', 'onboarding', 'sessions');

test.describe('onboarding client uses the same-origin proxy, never the backend directly', () => {
  test('all requests go through the API_PROXY_BASE (same-origin) base', () => {
    expect(clientSrc).toContain("const API_PROXY_BASE = ''");
    expect(clientSrc).toContain('`${API_PROXY_BASE}${path}`');
  });

  test('the client never resolves or concatenates the backend API URL', () => {
    // A direct backend call via resolveApiUrl() silently fails in production.
    expect(clientSrc).not.toContain('resolveApiUrl');
    expect(clientSrc).not.toMatch(/\$\{apiUrl\}/);
    // The backend URL prop is no longer accepted (so it is never serialized to the browser).
    expect(clientSrc).not.toContain('apiUrl: string');
  });

  test('the authenticated session is preserved on every request', () => {
    expect(clientSrc).toContain('...authHeaders()');
  });

  test('the SSE stream also uses the same-origin proxy path', () => {
    expect(agentSrc).toContain('`/api/onboarding/sessions/${encodeURIComponent(sessionId)}/events`');
    expect(agentSrc).not.toMatch(/\$\{apiUrl\}\/api\/onboarding/);
  });

  test('the page does not pass the server-resolved backend URL into the browser', () => {
    expect(pageSrc).not.toContain('resolveApiUrl');
    expect(pageSrc).toContain('<OnboardingPageClient />');
  });
});

test.describe('same-origin onboarding proxy routes exist and forward with the /api prefix', () => {
  const jsonRoutes: Array<{ file: string[]; backendPath: string; method: string }> = [
    { file: ['route.ts'], backendPath: '/api/onboarding/sessions', method: 'POST' },
    { file: ['[sessionId]', 'route.ts'], backendPath: '/api/onboarding/sessions/${encodeURIComponent(sessionId)}', method: 'GET' },
    { file: ['[sessionId]', 'discover', 'route.ts'], backendPath: '/api/onboarding/sessions/${encodeURIComponent(sessionId)}/discover', method: 'POST' },
    { file: ['[sessionId]', 'approve', 'route.ts'], backendPath: '/api/onboarding/sessions/${encodeURIComponent(sessionId)}/approve', method: 'POST' },
    { file: ['[sessionId]', 'activate', 'route.ts'], backendPath: '/api/onboarding/sessions/${encodeURIComponent(sessionId)}/activate', method: 'POST' },
    { file: ['[sessionId]', 'retry', 'route.ts'], backendPath: '/api/onboarding/sessions/${encodeURIComponent(sessionId)}/retry', method: 'POST' },
    { file: ['[sessionId]', 'rpc-benchmark', 'route.ts'], backendPath: '/api/onboarding/sessions/${encodeURIComponent(sessionId)}/rpc-benchmark', method: 'POST' },
    { file: ['[sessionId]', 'report', 'route.ts'], backendPath: '/api/onboarding/sessions/${encodeURIComponent(sessionId)}/report', method: 'GET' },
  ];

  for (const route of jsonRoutes) {
    test(`${route.method} ${route.file.join('/')} proxies to ${route.backendPath}`, () => {
      const src = fs.readFileSync(path.join(onboardingApiDir, ...route.file), 'utf-8');
      expect(src).toContain("from 'app/api/_shared/backend-proxy'");
      // The forwarded path keeps the backend's /api/onboarding/* prefix (single-quoted for the
      // static create route, template-literal for the dynamic [sessionId] routes).
      expect(src).toContain(route.backendPath);
      expect(src).toContain(`method: '${route.method}'`);
    });
  }

  test('the events route is a same-origin SSE proxy (text/event-stream)', () => {
    const src = fs.readFileSync(path.join(onboardingApiDir, '[sessionId]', 'events', 'route.ts'), 'utf-8');
    expect(src).toContain('/api/onboarding/sessions/${encodeURIComponent(sessionId)}/events');
    expect(src).toContain("'Content-Type': 'text/event-stream'");
    expect(src).toContain('getRuntimeConfig');
  });
});

test.describe('duplicate-submit protection and recovery after failure', () => {
  test('only one onboarding request is in flight at a time', () => {
    expect(clientSrc).toContain('if (busy) return;');
  });

  test('the form is always re-enabled so a recoverable failure can be retried', () => {
    // setBusy(null) runs in a finally block regardless of success/failure.
    expect(clientSrc).toMatch(/finally\s*{[\s\S]*setBusy\(null\)/);
    expect(clientSrc).toContain("data-testid=\"onboarding-retry\"");
  });

  test('the submit button is disabled while a request is in flight', () => {
    expect(clientSrc).toContain('const canSubmit = contractValid && busy === null;');
    expect(clientSrc).toContain('disabled={!canSubmit}');
  });
});
