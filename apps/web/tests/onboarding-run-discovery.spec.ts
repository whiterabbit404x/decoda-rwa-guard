/**
 * Root-cause regression guard for "Onboarding Agent stuck at 0/10 pending".
 *
 * The single "Run Automated Discovery" button must (1) create/resume the session,
 * (2) persist the returned session id, and (3) START discovery via the canonical
 * POST …/discover endpoint. Previously onCreate stopped after creating the session,
 * which left it in `draft` with every step pending and NO backend job enqueued — the
 * timeline appeared but never progressed. Progress must stay backend-driven (SSE/poll
 * refetch the authoritative snapshot); the fix must never simulate step progress.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const clientSrc = fs.readFileSync(
  path.join(__dirname, '..', 'app', '(product)', 'onboarding-page-client.tsx'),
  'utf-8',
);

function onCreateBody(): string {
  const start = clientSrc.indexOf('const onCreate =');
  const end = clientSrc.indexOf('const onDiscover =');
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return clientSrc.slice(start, end);
}

test('onCreate creates/resumes the session and persists its id', () => {
  const body = onCreateBody();
  expect(body).toContain(`api('/api/onboarding/sessions', 'POST'`);
  expect(body).toContain('persist(created.session.id)');
});

test('onCreate then STARTS discovery via the canonical discover endpoint', () => {
  const body = onCreateBody();
  // The previously-missing step: the same click must POST to …/{sessionId}/discover.
  expect(body).toMatch(/\/api\/onboarding\/sessions\/\$\{created\.session\.id\}\/discover['"`],\s*'POST'/);
});

test('the intake button label reflects that it starts discovery', () => {
  expect(clientSrc).toContain('Starting discovery…');
});

test('progress stays backend-driven — SSE/poll refetch the canonical snapshot, panel view is derived', () => {
  // Polling fallback refetches the authoritative session snapshot (never a local timer that advances steps).
  expect(clientSrc).toContain('void refresh(sessionId)');
  // The right-panel current operation / state is derived from canonical backend state.
  expect(clientSrc).toContain('deriveAgentView(snapshot)');
});

test('unknown backend status renders a surfaced diagnostic (not silent pending)', () => {
  expect(clientSrc).toContain('agent-unknown-status');
  expect(clientSrc).toContain('view.unknownStatus');
});
