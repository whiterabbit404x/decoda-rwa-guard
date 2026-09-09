import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { PRICING_PLANS } from '../app/pricing-plans';
import {
  REVIEW_NOTICE,
  SECRET_WARNING,
  SUBMITTED_BODY,
  USE_CASE_SUGGESTIONS,
} from '../app/pilot-request-copy';
import {
  canApprove,
  canReject,
  canResendInvitation,
  statusLabel,
  type PilotRequest,
} from '../app/admin/pilot-requests-view';

// Source-level guardrails for approval-only Pilot access. They run without a web
// server so they stay reliable in CI, and they pin the two things a screenshot
// review would miss: that no public surface still offers self-service Pilot
// access, and that the console never labels an un-sent invitation as sent.

const APP_DIR = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(APP_DIR, ...segments), 'utf-8');
}

function baseRequest(overrides: Partial<PilotRequest> = {}): PilotRequest {
  return {
    id: 'req-1',
    email: 'security@company.com',
    company_name: 'Company Treasury',
    role: 'Head of Security',
    company_website: null,
    use_case: 'tokenized treasury monitoring',
    status: 'pending',
    requested_at: '2026-06-01T12:00:00Z',
    reviewed_at: null,
    approved_at: null,
    rejected_at: null,
    internal_note: null,
    invitation_expires_at: null,
    invitation_sent_at: null,
    invitation_accepted_at: null,
    invitation_delivery_error: null,
    invitation_expired: false,
    invitation_not_sent: false,
    organization_id: null,
    ...overrides,
  };
}

// ── the public path no longer self-serves a Pilot ───────────────────────────

test('the Pilot CTA leads to the application, not to sign-up', () => {
  const pilot = PRICING_PLANS.find((plan) => plan.key === 'pilot');
  expect(pilot?.ctaLabel).toBe('Request Pilot →');
  expect(pilot?.ctaHref).toBe('/request-pilot');
});

test('no public marketing surface offers self-service Pilot access', () => {
  for (const file of [
    ['pricing', 'page.tsx'],
    ['trust', 'page.tsx'],
    ['live-proof', 'page.tsx'],
    ['pricing-plans.ts'],
  ] as const) {
    const source = read(...file);
    expect(source, `${file.join('/')} still offers a free trial`).not.toContain('Start free');
    expect(source, `${file.join('/')} still offers a trial`).not.toContain('free trial');
  }
});

test('the pricing page states that a Pilot is reviewed before it is activated', () => {
  const source = read('pricing', 'page.tsx');
  expect(source).toContain('/request-pilot');
  expect(source).toContain('reviews each request individually');
  expect(source).toContain('no self-service sign-up for the Pilot');
});

// ── the application form ────────────────────────────────────────────────────

test('the request form collects the four required fields and the optional website', () => {
  const source = read('request-pilot', 'request-pilot-client.tsx');
  for (const id of ['rp-email', 'rp-company', 'rp-role', 'rp-use-case']) {
    expect(source, `${id} is missing`).toContain(id);
  }
  expect(source).toContain('rp-website');
  expect(source).toContain('(optional)');
});

test('the request form warns against sending secrets and asks for none', () => {
  const source = read('request-pilot', 'request-pilot-client.tsx');
  expect(SECRET_WARNING).toContain('private keys');
  expect(SECRET_WARNING).toContain('credentials');
  expect(SECRET_WARNING).toContain('seed phrases');
  expect(source).toContain('SECRET_WARNING');

  // The form has exactly five inputs, and none of them asks for a secret.
  const inputIds = [...source.matchAll(/id="(rp-[a-z-]+)"/g)].map((match) => match[1]);
  expect(new Set(inputIds)).toEqual(
    new Set(['rp-email', 'rp-company', 'rp-role', 'rp-website', 'rp-use-case']),
  );
  expect(source).not.toContain('type="password"');
});

test('the confirmation promises review, never instant access', () => {
  expect(SUBMITTED_BODY).toContain('review');
  expect(REVIEW_NOTICE).toContain('reviewed and approved');
  for (const copy of [SUBMITTED_BODY, REVIEW_NOTICE]) {
    expect(copy.toLowerCase()).not.toContain('instant');
    expect(copy.toLowerCase()).not.toContain('free trial');
    expect(copy.toLowerCase()).not.toContain('start now');
  }
});

test('the suggested use cases cover the documented evaluations', () => {
  expect(USE_CASE_SUGGESTIONS).toContain('Tokenized treasury monitoring');
  expect(USE_CASE_SUGGESTIONS).toContain('Stablecoin / RWA operations');
  expect(USE_CASE_SUGGESTIONS).toContain('Tokenization platform');
  expect(USE_CASE_SUGGESTIONS).toContain('Custodian / issuer monitoring');
  expect(USE_CASE_SUGGESTIONS).toContain('Security evaluation');
});

test('the public request proxy forwards no Authorization header', () => {
  // The applicant has no account. A proxy that required one would make the
  // public form unusable, and one that invented a session would be worse.
  const source = read('api', 'pilot-requests', 'route.ts');
  expect(source).not.toContain("headers.get('authorization')");
  expect(source).toContain('/pilot-requests');
});

test('accepting an invitation goes through the authenticated proxy', () => {
  const source = read('api', 'pilot-invitations', 'accept', 'route.ts');
  expect(source).toContain('proxyJsonToBackend');
  expect(source).toContain('/pilot-invitations/accept');
});

// ── the product gate ────────────────────────────────────────────────────────

test('an authenticated account with no membership is held at the Pilot gate', () => {
  const source = read('authenticated-route.tsx');
  expect(source).toContain('PilotAccessRequired');
  // The old behaviour — bounce to /workspaces, where a workspace could be
  // created and a tenant minted — must be gone for accounts with no membership.
  expect(source).not.toContain('membership_required');
  const gateAt = source.indexOf('if (!hasMembership)');
  const workspaceAt = source.indexOf("pathname !== '/workspaces'", gateAt);
  expect(gateAt).toBeGreaterThan(-1);
  expect(workspaceAt).toBeGreaterThan(gateAt);
});

test('the gate distinguishes "under review" from "no request at all"', () => {
  const source = read('pilot-access-gate.tsx');
  expect(source).toContain('Pilot request under review');
  expect(source).toContain('Pilot access required');
  expect(source).toContain('/request-pilot');
});

test('the gate offers no monitoring, evidence, or response affordance', () => {
  const source = read('pilot-access-gate.tsx').toLowerCase();
  for (const forbidden of ['/dashboard', '/incidents', '/exports', '/targets', '/assets', 'run-detection']) {
    expect(source, `the gate links to ${forbidden}`).not.toContain(forbidden);
  }
});

// ── the internal console ────────────────────────────────────────────────────

test('an approval whose email failed is never labelled as sent', () => {
  const notSent = baseRequest({ status: 'approved', invitation_not_sent: true, invitation_delivery_error: 'SMTP 403' });
  expect(statusLabel(notSent)).toBe('Approved — invitation not sent');
  expect(statusLabel(notSent)).not.toBe('Invitation sent');
  expect(statusLabel(notSent)).not.toBe('Approved — invitation sent');
});

test('a lapsed invitation reads as expired, whatever the stored status says', () => {
  expect(statusLabel(baseRequest({ status: 'invited', invitation_expired: true }))).toBe('Invitation expired');
  expect(statusLabel(baseRequest({ status: 'approved', invitation_expired: true }))).toBe('Invitation expired');
});

test('each status reads as the state it actually is', () => {
  expect(statusLabel(baseRequest({ status: 'pending' }))).toBe('Pending review');
  expect(statusLabel(baseRequest({ status: 'invited', invitation_sent_at: '2026-06-01T12:00:00Z' })))
    .toBe('Invitation sent');
  expect(statusLabel(baseRequest({ status: 'rejected' }))).toBe('Rejected');
  expect(statusLabel(baseRequest({ status: 'activated' }))).toBe('Pilot activated');
});

test('actions are offered only where the backend would accept them', () => {
  expect(canApprove(baseRequest({ status: 'pending' }))).toBe(true);
  expect(canApprove(baseRequest({ status: 'activated' }))).toBe(false);
  expect(canApprove(baseRequest({ status: 'rejected' }))).toBe(false);

  expect(canReject(baseRequest({ status: 'pending' }))).toBe(true);
  expect(canReject(baseRequest({ status: 'invited' }))).toBe(true);
  expect(canReject(baseRequest({ status: 'activated' }))).toBe(false);

  expect(canResendInvitation(baseRequest({ status: 'approved', invitation_not_sent: true }))).toBe(true);
  expect(canResendInvitation(baseRequest({ status: 'pending' }))).toBe(false);
  expect(canResendInvitation(baseRequest({ status: 'activated' }))).toBe(false);
  expect(canResendInvitation(baseRequest({ status: 'rejected' }))).toBe(false);
});

test('the console renders a Pilot Requests section alongside the customers table', () => {
  const source = read('admin', 'customers', 'admin-customers-client.tsx');
  expect(source).toContain('Pilot requests');
  expect(source).toContain('Customer organizations');
  expect(source).toContain('/api/admin/pilot-requests');
  for (const column of ['Company', 'Primary contact', 'Role', 'Requested', 'Status', 'Actions']) {
    expect(source, `the requests table is missing the ${column} column`).toContain(`<th>${column}</th>`);
  }
});

test('the console never renders an invitation token', () => {
  const source = read('admin', 'customers', 'admin-customers-client.tsx')
    + read('admin', 'pilot-requests-view.ts');
  expect(source).not.toContain('invitation_token');
  expect(source).not.toContain('token_hash');
});
