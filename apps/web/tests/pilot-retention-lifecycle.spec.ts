import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  BACKUP_DISCLOSURE,
  PilotDataLifecycle,
  RetentionPolicyRow,
  deletionModeLabel,
  founderLifecycleRows,
  formatRetentionDate,
  pilotLifecycleNotice,
  retentionPeriodLabel,
  retentionPolicyTone,
  retentionSourceLabel,
} from '../app/pilot-retention';

// Source-level guardrails for the Pilot retention surfaces. No web server, so
// they stay reliable in CI. What they lock in is the one rule the screens exist
// to keep: the product states the retention and deletion that actually happen,
// and states nothing else.

const APP_DIR = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

function lifecycle(overrides: Partial<PilotDataLifecycle> = {}): PilotDataLifecycle {
  return {
    state: 'ACTIVE_PILOT',
    label: 'Pilot active',
    plan: 'pilot',
    ended_at: null,
    grace_ends_at: null,
    scheduled_deletion_at: null,
    security_record_deleted_at: null,
    grace_period_days: 30,
    legal_hold_active: false,
    deletion_blocked_by_legal_hold: false,
    export_available: true,
    ...overrides,
  };
}

function policy(overrides: Partial<RetentionPolicyRow> = {}): RetentionPolicyRow {
  return {
    data_class: 'telemetry',
    retention_days: 90,
    deletion_mode: 'hard_delete',
    enabled: true,
    enforced: true,
    effective_from: null,
    source: 'pilot_default',
    updated_at: null,
    recommended_retention_days: 90,
    rationale: 'Raw monitored-chain observations.',
    ...overrides,
  };
}

test('an active open-ended Pilot shows no deletion notice and no countdown', () => {
  expect(pilotLifecycleNotice(lifecycle())).toBeNull();
  // Including one a founder gave a deadline: a deadline is not an ending.
  expect(pilotLifecycleNotice(lifecycle({ state: 'ACTIVE_PILOT' }))).toBeNull();
  expect(pilotLifecycleNotice(null)).toBeNull();
  expect(pilotLifecycleNotice(lifecycle({ state: 'NOT_APPLICABLE' }))).toBeNull();
});

test('an ended Pilot states the export window, the deletion date, and that upgrading cancels it', () => {
  const notice = pilotLifecycleNotice(
    lifecycle({
      state: 'GRACE_PERIOD',
      ended_at: '2026-09-01T00:00:00+00:00',
      grace_ends_at: '2026-10-01T00:00:00+00:00',
      scheduled_deletion_at: '2026-10-01T00:00:00+00:00',
    }),
  );

  expect(notice).not.toBeNull();
  expect(notice!.title).toBe('Pilot ended');
  expect(notice!.body).toContain('Read and export access is available until');
  expect(notice!.body).toContain('Upgrading before then cancels the deletion.');
  expect(notice!.scheduledDeletionDate).toBe(formatRetentionDate('2026-10-01T00:00:00+00:00'));
  expect(notice!.showExportAction).toBe(true);
  expect(notice!.showRequestDeletionAction).toBe(true);
});

test('a Pilot whose end date was never recorded is stated as such, never given an invented date', () => {
  const notice = pilotLifecycleNotice(lifecycle({ state: 'ENDED_END_DATE_NOT_RECORDED' }));

  expect(notice!.scheduledDeletionDate).toBeNull();
  expect(notice!.graceEndDate).toBeNull();
  expect(notice!.body).toContain('No deletion date has been recorded');
  expect(notice!.body).toContain('nothing is scheduled for deletion');
});

test('a legal hold is stated wherever a deletion date is', () => {
  const notice = pilotLifecycleNotice(
    lifecycle({
      state: 'GRACE_PERIOD',
      ended_at: '2026-09-01T00:00:00+00:00',
      grace_ends_at: '2026-10-01T00:00:00+00:00',
      scheduled_deletion_at: '2026-10-01T00:00:00+00:00',
      legal_hold_active: true,
      deletion_blocked_by_legal_hold: true,
    }),
  );

  expect(notice!.title).toContain('legal hold');
  expect(notice!.body).toContain('will not run until the hold is released');
  expect(notice!.showRequestDeletionAction).toBe(false);
});

test('a deletion that is due is not reported as a deletion that happened', () => {
  const notice = pilotLifecycleNotice(
    lifecycle({
      state: 'DELETION_DUE',
      ended_at: '2026-07-01T00:00:00+00:00',
      grace_ends_at: '2026-07-31T00:00:00+00:00',
      scheduled_deletion_at: '2026-07-31T00:00:00+00:00',
    }),
  );

  expect(notice!.body).toContain('queued');
  expect(notice!.body).not.toContain('has been deleted');
  expect(notice!.showExportAction).toBe(false);
});

test('a policy that has not started sweeping is never rendered as active', () => {
  const pending = policy({ enforced: false, effective_from: '2026-10-18T00:00:00+00:00' });
  expect(retentionPeriodLabel(pending)).toContain('starts');
  expect(retentionPolicyTone(pending)).toBe('pending');

  const live = policy();
  expect(retentionPeriodLabel(live)).toBe('90 days');
  expect(retentionPolicyTone(live)).toBe('ok');
});

test('a data class with no policy reads as no automatic deletion, not as the default', () => {
  const none = policy({ source: 'none', retention_days: null, deletion_mode: null, enabled: false, enforced: false });

  expect(retentionPeriodLabel(none)).toBe('Not configured — no automatic deletion');
  expect(retentionPeriodLabel(none)).not.toContain('90');
  expect(retentionPolicyTone(none)).toBe('none');
  expect(deletionModeLabel(none)).toBe('—');
  expect(retentionSourceLabel(none)).toBe('No policy');
});

test('a disabled policy is not rendered as a period in force', () => {
  const disabled = policy({ enabled: false, enforced: false });
  expect(retentionPeriodLabel(disabled)).toContain('disabled, no automatic deletion');
  expect(retentionPolicyTone(disabled)).toBe('none');
});

test('a seeded default is never presented as the customer’s own configuration', () => {
  expect(retentionSourceLabel(policy({ source: 'pilot_default' }))).toBe('Decoda Pilot default');
  expect(retentionSourceLabel(policy({ source: 'workspace' }))).toBe('Configured by this workspace');
});

test('anonymize is never labelled as deletion', () => {
  expect(deletionModeLabel(policy({ deletion_mode: 'anonymize' }))).toBe('Anonymized');
  expect(deletionModeLabel(policy({ deletion_mode: 'hard_delete' }))).toBe('Deleted');
});

test('the founder view reports "Not recorded" rather than filling a blank date', () => {
  const rows = founderLifecycleRows(lifecycle({ state: 'ENDED_END_DATE_NOT_RECORDED' }));
  const byLabel = Object.fromEntries(rows.map((row) => [row.label, row.value]));

  expect(byLabel['Pilot ended']).toBe('Not recorded');
  expect(byLabel['Grace period ends']).toBe('Not recorded');
  expect(byLabel['Scheduled deletion']).toBe('Not recorded');
  expect(byLabel['Legal hold']).toBe('None');
});

test('the founder view shows a hold instead of a deletion date it would override', () => {
  const rows = founderLifecycleRows(
    lifecycle({
      state: 'GRACE_PERIOD',
      scheduled_deletion_at: '2026-10-01T00:00:00+00:00',
      legal_hold_active: true,
      deletion_blocked_by_legal_hold: true,
    }),
  );
  const byLabel = Object.fromEntries(rows.map((row) => [row.label, row.value]));

  expect(byLabel['Scheduled deletion']).toBe('Blocked by legal hold');
  expect(byLabel['Legal hold']).toBe('Active');
});

test('an unparseable or missing date renders as nothing, never as today', () => {
  expect(formatRetentionDate(null)).toBeNull();
  expect(formatRetentionDate('')).toBeNull();
  expect(formatRetentionDate('not-a-date')).toBeNull();
});

test('the backup disclosure states removal from active systems and does not claim more', () => {
  expect(BACKUP_DISCLOSURE).toContain('active systems');
  expect(BACKUP_DISCLOSURE).toContain('Residual encrypted copies may remain');
  expect(BACKUP_DISCLOSURE.toLowerCase()).not.toContain('permanently erased');
  expect(BACKUP_DISCLOSURE.toLowerCase()).not.toContain('all customer data is');
});

test('the settings screen reads the retention period instead of asserting one', () => {
  const settings = read(APP_DIR, 'settings-page-client.tsx');

  expect(settings).toContain("call('/workspace/retention-policies')");
  expect(settings).toContain('auditRetentionLabel');
  // The claim this card replaced: a period no code enforced.
  expect(settings).not.toContain('90 days (default)');
  // A failed or forbidden read must not fall back to a number.
  expect(settings).toContain("setRetentionState(retentionRes.status === 401 || retentionRes.status === 403 ? 'denied' : 'error')");
});

test('a 200 that is not a policy payload is treated as unreadable, not as no policy', () => {
  const settings = read(APP_DIR, 'settings-page-client.tsx');

  // A response body without a `policies` array must not reach the render: doing
  // so would either crash the Security tab or, worse, print "no retention
  // configured" for a workspace that has a policy.
  expect(settings).toContain('Array.isArray(payload.policies)');
  expect(settings).toContain('(retention.policies ?? [])');
  expect(settings).toContain("typeof retention.grace_period_days === 'number'");
});

test('the public policy pages state the schedule and the backup limitation', () => {
  const privacy = read(APP_DIR, 'privacy', 'page.tsx');

  expect(privacy).toContain('90 days');
  expect(privacy).toContain('180 days');
  expect(privacy).toContain('365 days');
  expect(privacy).toContain('Grace period — 30 days');
  expect(privacy).toContain('Legal holds');
  expect(privacy).toContain('Backups');
  // The schedule covers operational records, not workspace configuration. Saying
  // so is what keeps "your data is deleted" from being an overstatement.
  expect(privacy).toContain('What the schedule does not cover');
  expect(privacy).toContain('asset registry');
  expect(privacy).not.toContain('until contractual retention ends');
  expect(privacy.toLowerCase()).not.toContain('zero residual');

  const terms = read(APP_DIR, 'terms', 'page.tsx');
  expect(terms).toContain('Data retention and deletion');

  const trust = read(APP_DIR, 'trust', 'page.tsx');
  expect(trust).toContain('What happens to our data when the Pilot ends?');
  expect(trust).toContain('Can we have our data deleted sooner?');
});
