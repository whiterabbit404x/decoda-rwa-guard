// ─────────────────────────────────────────────────────────────
// Pilot retention / deletion presentation.
//
// Pure adapters over the backend's `data_lifecycle` block (GET /account/plan and
// GET /workspace/retention-policies). No fetching, no React, so every rule below
// is directly unit-testable.
//
// Rules this module keeps
//   * NO countdown for an ACTIVE open-ended Pilot. An open-ended Pilot has no end
//     date, so there is no grace window and no deletion date to render. The panel
//     says what retention applies and nothing about deletion.
//   * A date is rendered only when the BACKEND recorded it. Nothing here adds
//     `grace_period_days` to anything — if the server sent no `scheduled_deletion_at`,
//     the screen shows none, because no deletion request exists to match it.
//   * "Ended, end date not recorded" is its own state and is stated as such. It
//     is never shown as an active Pilot and never given an invented deadline.
//   * A policy that exists but has not started sweeping is not "Active". The
//     backend sends `enforced` separately from `enabled` precisely so this
//     distinction survives to the screen.
//   * A data class with NO policy row reads "Not configured — no automatic
//     deletion", never the recommended default, because nothing applies it.
//   * A legal hold is stated wherever a deletion date is stated. A date that a
//     hold is currently overriding would otherwise read as a promise.
// ─────────────────────────────────────────────────────────────

export type PilotDataState =
  | 'ACTIVE_PILOT'
  | 'GRACE_PERIOD'
  | 'DELETION_DUE'
  | 'ENDED_END_DATE_NOT_RECORDED'
  | 'NOT_APPLICABLE';

/** Mirrors services/api/app/pilot_retention.py pilot_data_lifecycle(). */
export interface PilotDataLifecycle {
  state: PilotDataState;
  label: string;
  plan: string;
  /** null for an active Pilot, and for one whose end was never recorded. */
  ended_at: string | null;
  grace_ends_at: string | null;
  scheduled_deletion_at: string | null;
  security_record_deleted_at: string | null;
  end_reason?: string | null;
  grace_period_days: number;
  legal_hold_active: boolean;
  deletion_blocked_by_legal_hold: boolean;
  export_available: boolean;
}

/** One row of GET /workspace/retention-policies. */
export interface RetentionPolicyRow {
  data_class: string;
  /** null when no policy row exists for this class. */
  retention_days: number | null;
  deletion_mode: 'hard_delete' | 'anonymize' | null;
  enabled: boolean;
  /** Enabled AND past its effective_from. This is what the worker acts on. */
  enforced: boolean;
  effective_from: string | null;
  source: 'pilot_default' | 'workspace' | 'none';
  updated_at: string | null;
  recommended_retention_days: number;
  rationale: string;
}

export interface RetentionPoliciesResponse {
  workspace_id: string;
  policies: RetentionPolicyRow[];
  pilot_lifecycle: PilotDataLifecycle;
  grace_period_days: number;
  security_record_days: number;
}

export const DATA_CLASS_LABELS: Record<string, string> = {
  telemetry: 'Telemetry',
  detections: 'Detections',
  alerts: 'Alerts and findings',
  incidents: 'Incidents',
  exports: 'Evidence exports',
  audit_logs: 'Audit and security logs',
  user_data: 'Individual user data',
};

export function dataClassLabel(key: string): string {
  return DATA_CLASS_LABELS[key] ?? key;
}

/** A date the backend recorded, or null. Never today's date as a stand-in. */
export function formatRetentionDate(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/**
 * The period actually in force for one class, in words.
 *
 * Three distinct answers, because the three states are genuinely different and
 * collapsing them is how a screen ends up claiming deletion that is not running:
 *
 *   no policy row      "Not configured — no automatic deletion"
 *   not yet effective  "90 days — starts <date>"
 *   in force           "90 days"
 */
export function retentionPeriodLabel(policy: RetentionPolicyRow): string {
  if (policy.source === 'none' || policy.retention_days === null) {
    return 'Not configured — no automatic deletion';
  }
  if (!policy.enabled) {
    return `${policy.retention_days} days — disabled, no automatic deletion`;
  }
  if (!policy.enforced) {
    const starts = formatRetentionDate(policy.effective_from);
    return starts
      ? `${policy.retention_days} days — starts ${starts}`
      : `${policy.retention_days} days — not yet in effect`;
  }
  return `${policy.retention_days} days`;
}

/** "Deleted" / "Anonymized" / "—". Never "Deleted" for an anonymize policy. */
export function deletionModeLabel(policy: RetentionPolicyRow): string {
  if (policy.deletion_mode === 'anonymize') {
    return 'Anonymized';
  }
  if (policy.deletion_mode === 'hard_delete') {
    return 'Deleted';
  }
  return '—';
}

/** Pill tone. An unenforced or absent policy is never rendered as healthy. */
export function retentionPolicyTone(policy: RetentionPolicyRow): 'ok' | 'pending' | 'none' {
  if (policy.source === 'none' || policy.retention_days === null || !policy.enabled) {
    return 'none';
  }
  return policy.enforced ? 'ok' : 'pending';
}

/** Where the period came from. A seeded default is never shown as the customer's. */
export function retentionSourceLabel(policy: RetentionPolicyRow): string {
  if (policy.source === 'workspace') {
    return 'Configured by this workspace';
  }
  if (policy.source === 'pilot_default') {
    return 'Decoda Pilot default';
  }
  return 'No policy';
}

export interface PilotLifecycleNotice {
  title: string;
  body: string;
  /** Present only when the backend recorded a deletion date. */
  scheduledDeletionDate: string | null;
  graceEndDate: string | null;
  showExportAction: boolean;
  showRequestDeletionAction: boolean;
  tone: 'info' | 'warning' | 'danger';
}

/**
 * The customer-facing end-of-Pilot notice, or null while nothing is scheduled.
 *
 * Returns null for an ACTIVE Pilot — including one a founder has given a
 * deadline, because a deadline is not an ending and the data schedule has not
 * started. There is deliberately no "your Pilot ends in N days" banner: the
 * plan panel already states an evaluation deadline where one exists, and a
 * deletion countdown against a Pilot nobody has ended would be an alarm with
 * nothing behind it.
 */
export function pilotLifecycleNotice(
  lifecycle: PilotDataLifecycle | null | undefined,
): PilotLifecycleNotice | null {
  if (!lifecycle) {
    return null;
  }
  if (lifecycle.state === 'ACTIVE_PILOT' || lifecycle.state === 'NOT_APPLICABLE') {
    return null;
  }

  if (lifecycle.state === 'ENDED_END_DATE_NOT_RECORDED') {
    return {
      title: 'Pilot ended',
      body:
        'Your evaluation data remains available and exportable. No deletion date has '
        + 'been recorded for this workspace, so nothing is scheduled for deletion. '
        + 'Contact Decoda to confirm the retention schedule for your data.',
      scheduledDeletionDate: null,
      graceEndDate: null,
      showExportAction: true,
      showRequestDeletionAction: true,
      tone: 'warning',
    };
  }

  const graceEnd = formatRetentionDate(lifecycle.grace_ends_at);
  const deletionDate = formatRetentionDate(lifecycle.scheduled_deletion_at);

  if (lifecycle.deletion_blocked_by_legal_hold) {
    return {
      title: 'Pilot ended — deletion on legal hold',
      body:
        'Your evaluation data remains available and exportable. A legal hold is active '
        + 'on this workspace, so the scheduled deletion will not run until the hold is '
        + 'released.',
      scheduledDeletionDate: deletionDate,
      graceEndDate: graceEnd,
      showExportAction: true,
      showRequestDeletionAction: false,
      tone: 'warning',
    };
  }

  if (lifecycle.state === 'GRACE_PERIOD') {
    return {
      title: 'Pilot ended',
      body: graceEnd
        ? `Read and export access is available until ${graceEnd}. After that date your `
          + 'telemetry, detections, alerts, incidents and evidence exports are deleted. '
          + 'Upgrading before then cancels the deletion.'
        : 'Read and export access remains available. Upgrading cancels the scheduled deletion.',
      scheduledDeletionDate: deletionDate,
      graceEndDate: graceEnd,
      showExportAction: true,
      showRequestDeletionAction: true,
      tone: 'warning',
    };
  }

  // DELETION_DUE — the deadline has passed. The data is not gone until the
  // worker has run, and this says exactly that rather than claiming either.
  return {
    title: 'Pilot ended — deletion due',
    body:
      'The read and export window for this workspace has closed. Deletion of your '
      + 'telemetry, detections, alerts, incidents and evidence exports is queued and '
      + 'runs on the next retention cycle.',
    scheduledDeletionDate: deletionDate,
    graceEndDate: graceEnd,
    showExportAction: false,
    showRequestDeletionAction: false,
    tone: 'danger',
  };
}

/**
 * The founder/admin summary rows for one tenant.
 *
 * Every value is either a recorded date or the literal string "Not recorded" —
 * an internal console that filled a blank with a computed date would be the same
 * failure as a customer-facing one.
 */
export function founderLifecycleRows(
  lifecycle: PilotDataLifecycle | null | undefined,
): Array<{ label: string; value: string }> {
  if (!lifecycle) {
    return [{ label: 'Pilot data lifecycle', value: 'Not available' }];
  }
  const notRecorded = 'Not recorded';
  return [
    { label: 'Pilot status', value: lifecycle.label },
    { label: 'Pilot ended', value: formatRetentionDate(lifecycle.ended_at) ?? notRecorded },
    { label: 'Grace period ends', value: formatRetentionDate(lifecycle.grace_ends_at) ?? notRecorded },
    {
      label: 'Scheduled deletion',
      value: lifecycle.deletion_blocked_by_legal_hold
        ? 'Blocked by legal hold'
        : formatRetentionDate(lifecycle.scheduled_deletion_at) ?? notRecorded,
    },
    {
      label: 'Security record removed',
      value: formatRetentionDate(lifecycle.security_record_deleted_at) ?? notRecorded,
    },
    { label: 'Legal hold', value: lifecycle.legal_hold_active ? 'Active' : 'None' },
  ];
}

/**
 * What the backups sentence may say.
 *
 * Deliberately a constant rather than a computed claim: nothing in the product
 * can observe the infrastructure provider's backup cycle, so nothing in the
 * product may state that deleted data is gone from backups on a schedule. The
 * wording states what IS verified — removal from active systems — and names the
 * residual copies rather than implying there are none.
 */
export const BACKUP_DISCLOSURE =
  'Deleted data is removed from Decoda’s active systems on the schedule above. '
  + 'Residual encrypted copies may remain in infrastructure backups until the provider’s '
  + 'normal backup-retention cycle completes, after which they are overwritten.';
