// Feedback vocabulary shared by the badge form, the detailed Pilot form, the
// internal console, and their tests. The values must match
// services/api/app/organizations.FEEDBACK_* exactly — the backend rejects
// anything else, so a drift here would surface as a 400 the user cannot act on.

export type FeedbackOption = { value: string; label: string };

/**
 * Quick feedback's type list. Twelve values, one per thing a Pilot customer
 * might be telling us about.
 *
 * `missing_feature` carries the "Missing capability" label rather than gaining a
 * near-duplicate sibling value: two enum values meaning one thing would split
 * the roadmap counter this vocabulary exists to feed, and every row submitted
 * before this change already uses `missing_feature`.
 */
export const FEEDBACK_TYPE_OPTIONS: ReadonlyArray<FeedbackOption> = [
  { value: 'security', label: 'Security' },
  { value: 'detection_accuracy', label: 'Detection accuracy' },
  { value: 'false_positive', label: 'False positive' },
  { value: 'missed_detection', label: 'Missed detection' },
  { value: 'investigation', label: 'Investigation' },
  { value: 'incident_response', label: 'Incident response' },
  { value: 'evidence_audit', label: 'Evidence / Audit' },
  { value: 'integration', label: 'Monitoring / Integration' },
  { value: 'policy_controls', label: 'Policy / Controls' },
  { value: 'usability', label: 'Usability' },
  { value: 'missing_feature', label: 'Missing capability' },
  { value: 'other', label: 'Other' },
];

/**
 * The detailed form's "Feedback area" — the same vocabulary, asked as a
 * workflow area rather than an event type. It is a strict SUBSET of the list
 * above so both depths land in one column the console can group by.
 */
export const FEEDBACK_AREA_OPTIONS: ReadonlyArray<FeedbackOption> = [
  { value: 'false_positive', label: 'Detection / False positive' },
  { value: 'missed_detection', label: 'Missed detection' },
  { value: 'investigation', label: 'Investigation' },
  { value: 'incident_response', label: 'Incident response' },
  { value: 'evidence_audit', label: 'Evidence / Audit' },
  { value: 'integration', label: 'Monitoring / Integration' },
  { value: 'policy_controls', label: 'Policy / Controls' },
  { value: 'usability', label: 'Usability' },
  { value: 'missing_feature', label: 'Missing capability' },
  { value: 'other', label: 'Other' },
];

/**
 * Severity is OPTIONAL on the quick form — `''` is a real choice meaning "not
 * stated", not a hidden default. The backend stores NULL for it, and the console
 * renders "—" rather than inventing a priority nobody assigned.
 */
export const FEEDBACK_SEVERITY_OPTIONS: ReadonlyArray<FeedbackOption> = [
  { value: '', label: 'Not stated' },
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
];

/** Importance on the detailed form, where the question is asked directly. */
export const FEEDBACK_IMPORTANCE_OPTIONS: ReadonlyArray<FeedbackOption> =
  FEEDBACK_SEVERITY_OPTIONS.filter((option) => option.value !== '');

export const PRODUCTION_BLOCKER_OPTIONS: ReadonlyArray<FeedbackOption> = [
  { value: '', label: 'Not stated' },
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
  { value: 'not_sure', label: 'Not sure' },
];

export const CONTINUE_INTENT_OPTIONS: ReadonlyArray<FeedbackOption> = [
  { value: '', label: 'Not stated' },
  { value: 'yes', label: 'Yes' },
  { value: 'maybe', label: 'Maybe' },
  { value: 'no', label: 'No' },
];

export const FEEDBACK_SECRET_WARNING =
  'Do not include private keys, credentials, seed phrases, or other secrets.';

export const DETAILED_FEEDBACK_HEADING = 'Pilot Security Feedback';

export const DETAILED_FEEDBACK_INTRO =
  'Help us understand your security workflow, what is working, and what Decoda would need ' +
  'before production use.';

export const END_OF_PILOT_HEADING = 'End-of-Pilot review';

export const END_OF_PILOT_INTRO =
  'Your evaluation is nearly over. This is optional — nothing in Decoda is gated on it.';

/** Labels for the two secondary entry points in the plan panel. */
export const DETAILED_FEEDBACK_CTA = 'Share detailed Pilot feedback';
export const END_OF_PILOT_CTA = 'Share end-of-Pilot review';

/**
 * How close to the end of an evaluation the end-of-Pilot CTA appears. Five days,
 * per the Pilot review brief.
 */
export const END_OF_PILOT_CTA_DAYS = 5;

type EvaluationLike = {
  days_remaining?: number | null;
  expired?: boolean | null;
} | null | undefined;

/**
 * Whether to offer the end-of-Pilot review.
 *
 * Driven by the backend's own `evaluation.days_remaining`, which is null for any
 * plan that has no evaluation window. A missing or unreadable countdown shows
 * NOTHING rather than assuming the Pilot is nearly over — inventing an expiry a
 * customer was never told about is the same failure in either direction.
 */
export function shouldShowEndOfPilotCta(evaluation: EvaluationLike): boolean {
  if (!evaluation) {
    return false;
  }
  const days = evaluation.days_remaining;
  if (typeof days !== 'number' || Number.isNaN(days)) {
    return false;
  }
  return days >= 0 && days <= END_OF_PILOT_CTA_DAYS;
}

/** One free-text question on a structured form. */
export type FeedbackFormField = {
  name: string;
  label: string;
  required: boolean;
  hint?: string;
};

/** The detailed form's fields, in the order the brief asks them. */
export const DETAILED_FEEDBACK_FIELDS: ReadonlyArray<FeedbackFormField> = [
  { name: 'goal_or_task', label: 'What were you trying to do?', required: true },
  {
    name: 'security_problem',
    label: 'What security or operational problem were you trying to solve?',
    required: true,
  },
  {
    name: 'current_workaround',
    label: 'How do you handle this today?',
    required: false,
    hint: 'Optional, but the most useful answer on this form.',
  },
  { name: 'where_decoda_helped', label: 'Where did Decoda help?', required: false },
  { name: 'missing_or_difficult', label: 'What was missing or difficult?', required: false },
  {
    name: 'deployment_requirement',
    label: 'What would Decoda need before you would deploy it in production?',
    required: false,
  },
];

/** The end-of-Pilot review's free-text fields, mapped onto the same columns. */
export const END_OF_PILOT_FIELDS: ReadonlyArray<FeedbackFormField> = [
  { name: 'where_decoda_helped', label: 'What was most useful?', required: false },
  { name: 'missing_or_difficult', label: 'What was least useful?', required: false },
  { name: 'security_problem', label: 'What security problem remains unsolved?', required: false },
  { name: 'deployment_requirement', label: 'What would prevent production deployment?', required: false },
  { name: 'paid_capability', label: 'Which capability would you pay for?', required: false },
];

/** Human labels for the founder console and the detail panel. */
export function feedbackTypeLabel(value: string | null | undefined): string {
  if (!value) {
    return '—';
  }
  return FEEDBACK_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

export function productionBlockerLabel(value: string | null | undefined): string {
  if (!value) {
    return '—';
  }
  return PRODUCTION_BLOCKER_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

export const FEEDBACK_MODE_LABELS: Record<string, string> = {
  quick: 'Quick',
  detailed: 'Detailed',
  end_of_pilot: 'End of Pilot',
};

/**
 * The safe context to attach to a submission, derived from the route the
 * customer is on.
 *
 * Deliberately narrow. Only the page path and, on the incident detail route, the
 * incident id — the one contextual id this product carries in a URL. Nothing
 * here reads the DOM, a request body, or any store: the brief's rule is that a
 * customer must not have to type these, NOT that we should scrape the page.
 *
 * The id is a hint, not a claim. The backend re-checks it against the
 * submitter's own tenant and drops it if it does not belong to them, so a
 * copied link from someone else's organization attaches nothing.
 */
const INCIDENT_ROUTE_RE = /^\/incidents\/([0-9a-zA-Z][0-9a-zA-Z-]{0,63})(?:\/|$)/;

export function feedbackContextFromPath(pathname: string | null | undefined): Record<string, string> {
  const path = typeof pathname === 'string' ? pathname : '';
  if (!path) {
    return {};
  }
  const context: Record<string, string> = { page: path.slice(0, 200) };
  const incident = INCIDENT_ROUTE_RE.exec(path);
  if (incident) {
    context.incident_id = incident[1];
  }
  return context;
}

/** The browser's current route as feedback context, or just `{}` on the server. */
export function currentFeedbackContext(): Record<string, string> {
  if (typeof window === 'undefined') {
    return {};
  }
  return feedbackContextFromPath(window.location.pathname);
}
