import { expect, test } from '@playwright/test';
import fs from 'fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');
const read = (...segments: string[]) => fs.readFileSync(path.join(appDir, ...segments), 'utf8');

// Regression guard for the reported Screen 8 bug: after a SUCCESSFUL response-action
// approval ("Approval recorded. Approval quorum reached.") the Action History tab still
// showed ONLY the older recommendation-review row — the new "Response Action Approved"
// event was missing.
//
// Root cause was NOT persistence and NOT rendering (both were already correct): the
// approval is persisted as a `response_action.approved` action_history event, and the
// page already renders approval-domain audit rows as a distinct "Action Approval". The
// failure was TRANSPORT. The Action History tab read `${apiUrl}/history/actions`
// DIRECTLY from the browser, but only NEXT_PUBLIC_API_URL is exposed client-side and it
// is unset in this deployment, so `apiUrl` resolves to '' and the request hits the
// same-origin Next server (which serves no `/history/actions` route) instead of the
// backend. The failed response was swallowed, the audit stream rendered empty, and only
// the recommendation-review row (derived from the already-proxied /api/response/actions
// payload) survived. These tests pin the read onto the same-origin /api proxy.

test.describe('Response Actions (Screen 8) Action History same-origin proxy transport', () => {
  test('the Action History read goes through the same-origin /api/history/actions proxy', () => {
    const client = read('(product)', 'response-actions-page-client.tsx');

    // The audit-history read (the source of the "Response Action Approved" row) uses the
    // same-origin proxy path, never a direct `${apiUrl}/history/actions` browser fetch.
    expect(client).toContain('/api/history/actions?limit=50');
    expect(client).not.toContain('${apiUrl}/history/actions');
  });

  test('the sibling Alerts + Incidents reads on this page are also proxied (same failure mode)', () => {
    const client = read('(product)', 'response-actions-page-client.tsx');

    // These feed incident-scope / linked-incident resolution for the same page; a direct
    // `${apiUrl}/…` read silently fails for the identical reason, so they use the proxy too.
    expect(client).toContain('/api/alerts?limit=50');
    expect(client).toContain('/api/incidents?limit=50');
    expect(client).not.toContain('${apiUrl}/alerts');
    expect(client).not.toContain('${apiUrl}/incidents');

    // No customer-facing backend read on this page is fetched directly from the browser.
    expect(client).not.toContain('${apiUrl}/history');
  });

  test('a same-origin /api/history/actions proxy route exists and forwards to the backend', () => {
    const route = read('api', 'history', 'actions', 'route.ts');
    expect(route).toContain('proxyJsonToBackend');
    expect(route).toContain("backendPath: '/history/actions'");
    // Query params (object_type / object_id / limit) are forwarded verbatim.
    expect(route).toContain('searchParams: url.searchParams');

    const helper = read('api', '_shared', 'backend-proxy.ts');
    // The backend URL is resolved SERVER-side (API_URL) — never exposed to the browser —
    // and auth/workspace headers are forwarded, so the workspace-scoped history read works.
    expect(helper).toContain('getRuntimeConfig');
    expect(helper).toContain('X-Workspace-Id');
    expect(helper).toContain('status: response.status');
  });
});
