/**
 * How a customer's audit history renders Decoda staff access.
 *
 * The product rule these specs hold:
 *
 *   1  A staff row is labelled "Decoda staff", not "system" and not an internal
 *      employee address — the customer learns that Decoda looked, not who.
 *   2  A read and a write are visibly different events ("Read only" / "Change").
 *   3  Only a row the BACKEND labelled decoda_staff wears that label. The
 *      browser never infers it, so a staff event cannot be faked into a
 *      customer's history from the client, and an ordinary member's row can
 *      never be mistaken for one.
 *   4  The panel renders the server's sentence, so the wording of a staff event
 *      is decided by the audited fact rather than by a client-side guess.
 *
 * Run:
 *     npx playwright test apps/web/tests/staff-access-audit-wording.spec.ts
 */
import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { accessModeLabel, actorTypePill, type AuditRow } from '../app/evidence-audit-panel';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(process.cwd(), 'apps/web', relativePath), 'utf8');
}

test.describe('staff access in the customer audit history', () => {
  test('a Decoda staff row is labelled as such', () => {
    const row: AuditRow = { actor: 'Decoda staff', actor_type: 'decoda_staff' };
    expect(actorTypePill(row)).toEqual({ label: 'Decoda staff', variant: 'info' });
  });

  test('the backend label wins over the local fallback table', () => {
    const row: AuditRow = { actor_type: 'decoda_staff', actor_type_label: 'Decoda Support' };
    expect(actorTypePill(row)?.label).toBe('Decoda Support');
  });

  test('a workspace member is never rendered as Decoda staff', () => {
    expect(actorTypePill({ actor_type: 'workspace_member' })).toEqual({
      label: 'Workspace member',
      variant: 'neutral',
    });
  });

  test('an automated service and the platform are distinguished from both', () => {
    expect(actorTypePill({ actor_type: 'automated_service' })?.label).toBe('Automated service');
    expect(actorTypePill({ actor_type: 'system' })?.label).toBe('System');
  });

  test('a row the backend did not classify carries no actor-type badge', () => {
    // Fail closed on the LABEL: an unclassified row must not be dressed up as
    // anything, least of all as staff access.
    expect(actorTypePill({})).toBeNull();
    expect(actorTypePill({ actor_type: 'made_up' })).toBeNull();
  });

  test('read and write are visibly different', () => {
    expect(accessModeLabel({ access_mode: 'read' })).toBe('Read only');
    expect(accessModeLabel({ access_mode: 'write' })).toBe('Change');
    expect(accessModeLabel({ access_mode_label: 'Read only' })).toBe('Read only');
    expect(accessModeLabel({})).toBeNull();
  });

  test('the panel renders the server sentence and the actor-type badge', () => {
    const source = read('app/evidence-audit-panel.tsx');
    expect(source).toContain('const actorKind = actorTypePill(row);');
    expect(source).toContain('const accessMode = accessModeLabel(row);');
    expect(source).toContain('row.summary ?? row.action');
  });

  test('the client never invents a Decoda staff label of its own', () => {
    const source = read('app/evidence-audit-panel.tsx');
    // The only place the string may appear is the fallback label table keyed by
    // the backend's own actor_type value.
    const occurrences = source.split("'Decoda staff'").length - 1;
    expect(occurrences).toBe(1);
    expect(source).toContain('decoda_staff: ');
  });
});
