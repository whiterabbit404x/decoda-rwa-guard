/**
 * The internal (founder) console link is rendered from ONE backend fact, and
 * rendering it is never what authorizes it.
 *
 * What these specs pin down:
 *   1  The link appears only when the backend positively said is_internal_admin.
 *   2  A normal customer — the Pilot test account included — never sees it.
 *   3  An absent, null, or lookalike value reads as "customer", so a hydration
 *      gap can only ever hide the link, never surface it.
 *   4  The decision reads no plan: the founder inside a Pilot workspace still
 *      sees it, and a Scale/Enterprise customer still does not. Privilege and
 *      subscription are independent facts.
 *
 * Run:
 *     npx playwright test apps/web/tests/internal-admin-link.spec.ts
 */
import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import {
  INTERNAL_ADMIN_HREF,
  INTERNAL_ADMIN_LABEL,
  showsInternalAdminLink,
} from '../app/internal-admin';

test.describe('internal admin link visibility', () => {
  test('the founder account sees the link', async () => {
    expect(showsInternalAdminLink({ is_internal_admin: true })).toBe(true);
  });

  test('a normal customer account does not', async () => {
    expect(showsInternalAdminLink({ is_internal_admin: false })).toBe(false);
  });

  test('an API that never sent the field is treated as a customer', async () => {
    expect(showsInternalAdminLink({})).toBe(false);
    expect(showsInternalAdminLink(null)).toBe(false);
    expect(showsInternalAdminLink(undefined)).toBe(false);
  });

  test('only a real boolean true counts', async () => {
    // A truthy lookalike is not a grant. Anything the backend did not state as
    // boolean true renders the customer view.
    expect(showsInternalAdminLink({ is_internal_admin: 'true' } as never)).toBe(false);
    expect(showsInternalAdminLink({ is_internal_admin: 1 } as never)).toBe(false);
  });

  test('the decision does not read a plan', async () => {
    // Founder on Pilot: link shown. Customer on Enterprise: link hidden.
    expect(showsInternalAdminLink({ is_internal_admin: true, plan: 'pilot' } as never)).toBe(true);
    expect(showsInternalAdminLink({ is_internal_admin: false, plan: 'enterprise' } as never)).toBe(false);
  });

  test('the link points at the internal console route', async () => {
    expect(INTERNAL_ADMIN_HREF).toBe('/admin/customers');
    expect(INTERNAL_ADMIN_LABEL).toBe('Customer Admin');
  });
});

test.describe('the shell renders it from the backend fact only', () => {
  const shell = fs.readFileSync(path.join(__dirname, '..', 'app', 'app-shell.tsx'), 'utf-8');

  test('the shell gates the link on showsInternalAdminLink', async () => {
    expect(shell).toContain('showsInternalAdminLink(user)');
    expect(shell).toContain('INTERNAL_ADMIN_HREF');
  });

  test('the shell never decides internal access from an email or a plan', async () => {
    // The failure mode this guards against is a frontend-side authorization rule
    // ("if the email is the founder's, show it"), which would put a second,
    // weaker copy of the rule in the browser.
    const gate = shell.split('\n').find((line) => line.includes('showsInternalAdminLink(user)')) ?? '';
    expect(gate).not.toBe('');
    expect(gate).not.toContain('email');
    expect(gate).not.toContain('plan');
    expect(shell).not.toContain('decoda.guard');
  });
});
