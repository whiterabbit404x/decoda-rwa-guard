import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  fetchGovernanceState,
  governanceTerminalState,
  loadStateFor,
  type GovernanceCall,
  type GovernanceCallResult,
  type GovernanceState,
  type LoadState,
} from '../app/governance-view-model';

const ROOT = path.join(__dirname, '..');
const settings = fs.readFileSync(path.join(ROOT, 'app', 'settings-page-client.tsx'), 'utf-8');

// A recording fake for the same-origin `call(path)` helper. Each path resolves to a
// caller-supplied {status, body}; ok is derived from status like a real Response.
function fakeCall(
  responses: Record<string, { status: number; body?: unknown }>,
  recorder?: { paths: string[] },
): GovernanceCall {
  return async (p: string): Promise<GovernanceCallResult> => {
    recorder?.paths.push(p);
    const r = responses[p] ?? { status: 500 };
    return {
      status: r.status,
      ok: r.status >= 200 && r.status < 300,
      json: async () => r.body ?? {},
    };
  };
}

const ALL_OK = {
  '/workspace/settings': { status: 200, body: { workspace_id: 'w1', name: 'Rabbit', timezone: 'UTC', currency: 'USD', version: 3, can_manage: true } },
  '/workspace/security-settings': { status: 200, body: { mfa_enforcement: 'optional', reauthentication_minutes: 30, can_manage: true } },
  '/workspace/governance/posture': { status: 200, body: { risk_reduction_percent: 80, access_risk: 'low', evidence_status: 'complete' } },
  '/workspace/governance/summary': { status: 200, body: { anomalies: { evaluated: true, open_total: 0, by_severity: {} }, change_safeguards: { status: 'approval_required', detail: '' }, pending_change_requests: 0, can_manage: true } },
};

const GOV_STATES = (s: GovernanceState): LoadState[] => [s.settingsState, s.securityState, s.postureState, s.summaryState];

// Invariant that underpins the whole fix: a resolved governance load can NEVER contain a
// 'loading' state. If it could, an early return / missing branch would strand a card spinning.
function expectNoLoading(s: GovernanceState) {
  expect(GOV_STATES(s)).not.toContain('loading');
  expect(GOV_STATES(s)).not.toContain('idle');
}

// 1. Admin: every Screen 11 GET fires and every card resolves to loaded.
test('admin — the four Screen 11 GETs fire and all cards load', async () => {
  const rec = { paths: [] as string[] };
  const state = await fetchGovernanceState({ hasWorkspace: true, call: fakeCall(ALL_OK, rec) });

  expect(rec.paths.sort()).toEqual([
    '/workspace/governance/posture',
    '/workspace/governance/summary',
    '/workspace/security-settings',
    '/workspace/settings',
  ]);
  expect(GOV_STATES(state)).toEqual(['loaded', 'loaded', 'loaded', 'loaded']);
  expect(state.settings?.can_manage).toBe(true);
  expectNoLoading(state);
});

// 2. Non-admin (read-only) whose reads are permitted: reads STILL fire and load, with
//    can_manage=false — a user who cannot edit can still read what their role may read.
test('non-admin — permitted reads still fire and load (can_manage=false)', async () => {
  const rec = { paths: [] as string[] };
  const readOnly = {
    '/workspace/settings': { status: 200, body: { workspace_id: 'w1', name: 'Rabbit', timezone: 'UTC', currency: 'USD', version: 1, can_manage: false } },
    '/workspace/security-settings': { status: 200, body: { mfa_enforcement: 'optional', reauthentication_minutes: 30, can_manage: false } },
    '/workspace/governance/posture': { status: 200, body: { risk_reduction_percent: 60, access_risk: 'medium', evidence_status: 'complete' } },
    '/workspace/governance/summary': { status: 200, body: { anomalies: { evaluated: true, open_total: 0, by_severity: {} }, change_safeguards: { status: 'approval_required', detail: '' }, pending_change_requests: 0, can_manage: false } },
  };
  const state = await fetchGovernanceState({ hasWorkspace: true, call: fakeCall(readOnly, rec) });

  expect(rec.paths).toHaveLength(4);
  expect(GOV_STATES(state)).toEqual(['loaded', 'loaded', 'loaded', 'loaded']);
  // Read succeeded but write capability is false — reads and writes are NOT conflated.
  expect(state.settings?.can_manage).toBe(false);
  expect(state.summary?.can_manage).toBe(false);
  expectNoLoading(state);
});

// 3. Forbidden read (backend 403) → explicit permission_denied, never a spinner, never green.
test('forbidden read — 403 renders as permission_denied (Restricted), not loading', async () => {
  const forbidden = {
    '/workspace/settings': { status: 200, body: { workspace_id: 'w1', name: 'Rabbit', timezone: 'UTC', currency: 'USD', version: 1, can_manage: false } },
    '/workspace/security-settings': { status: 403 },
    '/workspace/governance/posture': { status: 403 },
    '/workspace/governance/summary': { status: 403 },
  };
  const state = await fetchGovernanceState({ hasWorkspace: true, call: fakeCall(forbidden) });

  expect(state.settingsState).toBe('loaded');
  expect(state.securityState).toBe('permission_denied');
  expect(state.postureState).toBe('permission_denied');
  expect(state.summaryState).toBe('permission_denied');
  expectNoLoading(state);
});

// 4. Missing workspace → explicit unavailable (error) state AND no request is ever made.
test('missing workspace — explicit unavailable state, no request issued', async () => {
  const rec = { paths: [] as string[] };
  const state = await fetchGovernanceState({ hasWorkspace: false, call: fakeCall(ALL_OK, rec) });

  expect(rec.paths).toHaveLength(0); // never call the backend without a workspace to scope to
  expect(GOV_STATES(state)).toEqual(['error', 'error', 'error', 'error']);
  expectNoLoading(state);
});

// 5. Failed API (transport throw) → explicit error state on every card, never left loading.
test('failed API — a throwing transport fails closed to error, never loading', async () => {
  const throwingCall: GovernanceCall = async () => {
    throw new TypeError('Failed to fetch');
  };
  const state = await fetchGovernanceState({ hasWorkspace: true, call: throwingCall });

  expect(GOV_STATES(state)).toEqual(['error', 'error', 'error', 'error']);
  expectNoLoading(state);
});

// 6. Non-200/403 (e.g. 500/502) maps to error, not permission_denied and not loaded.
test('loadStateFor maps status → LoadState (200 loaded, 401/403 denied, else error)', () => {
  expect(loadStateFor(200)).toBe('loaded');
  expect(loadStateFor(401)).toBe('permission_denied');
  expect(loadStateFor(403)).toBe('permission_denied');
  expect(loadStateFor(404)).toBe('error');
  expect(loadStateFor(500)).toBe('error');
  expect(loadStateFor(502)).toBe('error');
});

// 7. The terminal-state helper never yields loading/idle/loaded — only real terminals.
test('governanceTerminalState yields a uniform terminal, never a spinner', () => {
  for (const s of ['error', 'permission_denied'] as const) {
    const state = governanceTerminalState(s);
    expect(GOV_STATES(state)).toEqual([s, s, s, s]);
    expect(state.settings).toBeNull();
    expectNoLoading(state);
  }
});

// --- Component wiring guarantees (source-level; complements the behavioral tests) -------

test('reads use the same-origin /api proxy, not the browser-exposed apiUrl', () => {
  expect(settings).toContain('fetch(`/api${path}`');
  // The direct browser->backend transport (the production bug) must be gone.
  expect(settings).not.toContain('fetch(`${apiUrl}${path}`');
});

test('a workspace switch retriggers the Screen 11 reads', () => {
  // Both on-load effects depend on the active workspace id, so selecting another workspace
  // re-runs loadAll() and loadGovernance().
  expect(settings).toContain('void loadGovernance(); }, [loading, resolvedWorkspace?.id]);');
  expect(settings).toContain('void loadAll(); }, [loading, resolvedWorkspace?.id]);');
});

test('GET reads do not require CSRF; mutations bootstrap it', () => {
  // The read path never calls ensureCsrf(); the write paths do.
  expect(settings).toContain('async function ensureCsrf()');
  for (const writer of ['saveWorkspaceSettings', 'submitSecurityChange', 'runEvaluation']) {
    const body = settings.slice(settings.indexOf(`async function ${writer}`), settings.indexOf(`async function ${writer}`) + 600);
    expect(body).toContain('await ensureCsrf();');
  }
  // The read loader must NOT gate on or fetch a CSRF token.
  const loader = settings.slice(settings.indexOf('async function loadGovernance'), settings.indexOf('async function loadGovernance') + 600);
  expect(loader).not.toContain('ensureCsrf');
});

test('write actions still require the administrative permission', () => {
  // Save is disabled unless the settings write permission is granted (can_manage from the
  // backend), and the security-policy control is gated on the manage permission.
  expect(settings).toContain('const canManageSettings = gov.settings?.can_manage ?? false;');
  expect(settings).toContain('disabled={!canManageSettings || !settingsDirty || savingSettings}');
  expect(settings).toContain('canManageSecurity ?');
});
