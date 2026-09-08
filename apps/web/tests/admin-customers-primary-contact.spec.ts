/**
 * The PRIMARY CONTACT column on /admin/customers.
 *
 * Organization names alone ("Rabbit", "a", "decoda") do not say which human is
 * behind a tenant, so the internal console names one contact per row. What
 * these specs pin down:
 *
 *   1  The cell reports what the BACKEND resolved. The browser never re-derives
 *      a contact, and never substitutes another address when one is missing.
 *   2  A missing contact renders the em dash — an obvious blank, not a
 *      plausible address a founder might actually email.
 *   3  A long address cannot break the row: it truncates inside a fixed-width
 *      block and stays readable in full through the title tooltip.
 *   4  The column sits immediately after Organization, and the rest of the
 *      table — the row actions included — is untouched.
 *
 * Run:
 *     npx playwright test apps/web/tests/admin-customers-primary-contact.spec.ts
 */
import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import {
  NO_PRIMARY_CONTACT,
  otherMembersLabel,
  primaryContactEmail,
  primaryContactLabel,
} from '../app/admin-customer-contact';

const CLIENT_PATH = path.join(__dirname, '..', 'app', 'admin', 'customers', 'admin-customers-client.tsx');
const HELPER_PATH = path.join(__dirname, '..', 'app', 'admin-customer-contact.ts');
const STYLES_PATH = path.join(__dirname, '..', 'app', 'styles.css');

const client = fs.readFileSync(CLIENT_PATH, 'utf-8');
const helper = fs.readFileSync(HELPER_PATH, 'utf-8');
const styles = fs.readFileSync(STYLES_PATH, 'utf-8');

test.describe('what the cell reports', () => {
  test('the resolved address is rendered as-is', () => {
    expect(primaryContactLabel({ primary_contact_email: 'security@customer.com', members: 1 }))
      .toBe('security@customer.com');
  });

  test('a missing contact renders the em dash', () => {
    expect(primaryContactLabel({ primary_contact_email: null, members: 0 })).toBe(NO_PRIMARY_CONTACT);
    expect(primaryContactLabel({ members: 0 })).toBe(NO_PRIMARY_CONTACT);
    expect(primaryContactLabel(null)).toBe(NO_PRIMARY_CONTACT);
    expect(primaryContactLabel(undefined)).toBe(NO_PRIMARY_CONTACT);
  });

  test('a blank or non-string value is treated as missing, never rendered', () => {
    // An empty cell that looked like a resolved person would be a quieter lie
    // than an obvious dash.
    expect(primaryContactEmail({ primary_contact_email: '   ' })).toBeNull();
    expect(primaryContactEmail({ primary_contact_email: '' })).toBeNull();
    expect(primaryContactEmail({ primary_contact_email: 42 as never })).toBeNull();
    expect(primaryContactLabel({ primary_contact_email: '   ' })).toBe(NO_PRIMARY_CONTACT);
  });

  test('a surrounding-whitespace address still renders', () => {
    expect(primaryContactLabel({ primary_contact_email: ' owner@example.com ' })).toBe('owner@example.com');
  });
});

test.describe('the member count is a count, not a second contact', () => {
  test('the members this row is not showing are summarised', () => {
    expect(otherMembersLabel({ primary_contact_email: 'a@example.com', members: 3 })).toBe('+2 members');
    expect(otherMembersLabel({ primary_contact_email: 'a@example.com', members: 2 })).toBe('+1 member');
  });

  test('a sole member adds nothing', () => {
    expect(otherMembersLabel({ primary_contact_email: 'a@example.com', members: 1 })).toBeNull();
  });

  test('an unknown or unusable count adds nothing', () => {
    expect(otherMembersLabel({ primary_contact_email: 'a@example.com' })).toBeNull();
    expect(otherMembersLabel({ primary_contact_email: 'a@example.com', members: null })).toBeNull();
    expect(otherMembersLabel({ primary_contact_email: 'a@example.com', members: Number.NaN })).toBeNull();
  });

  test('with no contact resolved, nobody is described as an "other" member', () => {
    // "+3 members" beside a dash would imply a contact had been found and
    // withheld. Nothing was found.
    expect(otherMembersLabel({ primary_contact_email: null, members: 4 })).toBeNull();
  });

  test('no email but the count is never a substitute for the contact', () => {
    expect(primaryContactLabel({ primary_contact_email: null, members: 4 })).toBe(NO_PRIMARY_CONTACT);
  });
});

test.describe('a long address does not break the row', () => {
  const longEmail = `${'a'.repeat(64)}@${'subdomain.'.repeat(6)}example.com`;

  test('the full address is preserved for the tooltip, never shortened in data', () => {
    // Truncation is presentation only: nothing here rewrites the address, so
    // the tooltip and any copy-paste get the real value.
    expect(primaryContactLabel({ primary_contact_email: longEmail })).toBe(longEmail);
    expect(primaryContactLabel({ primary_contact_email: longEmail })).not.toContain('…');
  });

  test('the cell truncates in CSS inside a bounded block', () => {
    const rule = styles.split('.adminContactEmail {')[1]?.split('}')[0] ?? '';
    expect(rule).not.toBe('');
    expect(rule).toContain('max-width');
    expect(rule).toContain('overflow: hidden');
    expect(rule).toContain('text-overflow: ellipsis');
    // .adminTable td is nowrap; a block element is what makes max-width bite.
    expect(rule).toContain('display: block');
  });

  test('the truncated address stays readable through the title tooltip', () => {
    expect(client).toContain('className="adminContactEmail"');
    expect(client).toContain('title={primaryContactEmail(customer) ?? undefined}');
  });
});

test.describe('the column in the table', () => {
  test('Primary contact sits immediately after Organization', () => {
    const headers = [...client.matchAll(/<th[^>]*>([^<]+)<\/th>/g)].map((match) => match[1].trim());
    expect(headers.slice(0, 3)).toEqual(['Organization', 'Primary contact', 'Plan']);
  });

  test('the header order is unchanged apart from the insertion', () => {
    const headers = [...client.matchAll(/<th[^>]*>([^<]+)<\/th>/g)].map((match) => match[1].trim());
    expect(headers.slice(0, 11)).toEqual([
      'Organization',
      'Primary contact',
      'Plan',
      'Status',
      'Evaluation expires',
      'Workspaces',
      'Contracts',
      'Evidence',
      'Last activity',
      'Feedback',
      'Actions',
    ]);
  });

  test('the row actions are still rendered', () => {
    expect(client).toContain('adminRowActions');
    expect(client).toContain('Extend 30d');
    expect(client).toContain('Suspend');
    expect(client).toContain('Reactivate');
    expect(client).toContain('Upgrade to Scale');
  });

  test('the cell reads the backend field and derives nothing itself', () => {
    expect(client).toContain('primary_contact_email: string | null;');
    expect(client).toContain('primaryContactLabel(customer)');
    // No frontend fallback to a name, slug, feedback address, or signed-in user.
    const cell = client.split('<th>Primary contact</th>')[1]?.split('</td>')[1] ?? client;
    expect(cell).not.toContain('user_email');
  });

  test('no address is written into the console or its helper', () => {
    // The founder's own organization shows its address because membership says
    // so — never because an address was hard-coded here.
    for (const source of [client, helper]) {
      expect(source).not.toContain('decoda.guard');
      expect(source).not.toContain('@gmail.com');
    }
  });
});
