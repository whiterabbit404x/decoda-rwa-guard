/**
 * Pilot feedback — the quick path stays quick, the detailed path goes deep.
 *
 * The Pilot badge's one-field form could not answer the questions a design
 * partner conversation has to answer: what security problem are they solving,
 * how do they solve it today, what would block production. A second, structured
 * form now answers those — WITHOUT turning every submission into a survey.
 *
 * These are logic-and-contract tests over the presentation modules and the
 * component sources. The vocabulary assertions are the load-bearing ones: every
 * value here must exist in services/api/app/organizations.FEEDBACK_*, because
 * the backend refuses anything else and a drift would surface to a customer as a
 * 400 they cannot act on.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  CONTINUE_INTENT_OPTIONS,
  DETAILED_FEEDBACK_CTA,
  DETAILED_FEEDBACK_FIELDS,
  DETAILED_FEEDBACK_HEADING,
  END_OF_PILOT_CTA,
  END_OF_PILOT_CTA_DAYS,
  END_OF_PILOT_FIELDS,
  FEEDBACK_AREA_OPTIONS,
  FEEDBACK_IMPORTANCE_OPTIONS,
  FEEDBACK_SECRET_WARNING,
  FEEDBACK_SEVERITY_OPTIONS,
  FEEDBACK_TYPE_OPTIONS,
  PRODUCTION_BLOCKER_OPTIONS,
  feedbackContextFromPath,
  feedbackTypeLabel,
  productionBlockerLabel,
  shouldShowEndOfPilotCta,
} from '../app/plan-feedback';

const APP = path.join(__dirname, '..', 'app');
const badgeSrc = fs.readFileSync(path.join(APP, 'plan-badge.tsx'), 'utf-8');
const dialogSrc = fs.readFileSync(path.join(APP, 'pilot-feedback-dialog.tsx'), 'utf-8');
const adminSrc = fs.readFileSync(
  path.join(APP, 'admin', 'customers', 'admin-customers-client.tsx'), 'utf-8',
);
const stylesSrc = fs.readFileSync(path.join(APP, 'styles.css'), 'utf-8');
const routeSrc = fs.readFileSync(
  path.join(APP, 'api', 'admin', 'feedback', 'route.ts'), 'utf-8',
);

/* ── QUICK FEEDBACK stays quick ────────────────────────────────────── */

test.describe('quick feedback remains compact', () => {
  test('the quick form still asks two questions, plus an optional severity', () => {
    // The whole point of keeping two paths: adding depth must not lengthen this
    // one. Type, severity, message — nothing else was moved into the dropdown.
    expect(badgeSrc).toContain('What happened?');
    expect(badgeSrc).toContain('Severity (optional)');
    expect(badgeSrc).toContain("id=\"plan-feedback-type\"");
    for (const deepQuestion of DETAILED_FEEDBACK_FIELDS.map((field) => field.label)) {
      expect(badgeSrc).not.toContain(deepQuestion);
    }
  });

  test('severity defaults to "not stated" rather than to a priority', () => {
    // '' is a real choice the backend stores as NULL. A defaulted 'medium' would
    // put a priority in the founder's list that no customer ever assigned.
    expect(badgeSrc).toContain("const [severity, setSeverity] = useState<string>('')");
    expect(FEEDBACK_SEVERITY_OPTIONS[0]).toEqual({ value: '', label: 'Not stated' });
  });

  test('the quick form declares its mode so the backend does not have to guess', () => {
    expect(badgeSrc).toContain("feedback_mode: 'quick'");
  });

  test('the secret warning is still visible on both forms', () => {
    expect(badgeSrc).toContain('FEEDBACK_SECRET_WARNING');
    expect(dialogSrc).toContain('FEEDBACK_SECRET_WARNING');
    expect(FEEDBACK_SECRET_WARNING).toContain('private keys');
    expect(FEEDBACK_SECRET_WARNING).toContain('seed phrases');
  });

  test('every quick feedback type asked for is offered', () => {
    const labels = FEEDBACK_TYPE_OPTIONS.map((option) => option.label);
    expect(labels).toEqual([
      'Security',
      'Detection accuracy',
      'False positive',
      'Missed detection',
      'Investigation',
      'Incident response',
      'Evidence / Audit',
      'Monitoring / Integration',
      'Policy / Controls',
      'Usability',
      'Missing capability',
      'Other',
    ]);
    // No duplicate enum value behind two labels — the roadmap counters group on
    // this column, and a split value would understate a repeated pain point.
    const values = FEEDBACK_TYPE_OPTIONS.map((option) => option.value);
    expect(new Set(values).size).toBe(values.length);
  });
});

/* ── DETAILED FEEDBACK ─────────────────────────────────────────────── */

test.describe('the detailed form asks the discovery questions', () => {
  test('it is reachable from the badge as a secondary action', () => {
    expect(DETAILED_FEEDBACK_CTA).toBe('Share detailed Pilot feedback');
    expect(badgeSrc).toContain('DETAILED_FEEDBACK_CTA');
    expect(badgeSrc).toContain("setDialogMode('detailed')");
    expect(badgeSrc).toContain('<PilotFeedbackDialog');
  });

  test('it opens as a modal over the page, not as more rows in the dropdown', () => {
    // The dropdown must stay short. The structured form reuses the app's own
    // .modalOverlay / .modalCard surface rather than inventing a pattern.
    expect(dialogSrc).toContain('className="modalOverlay pilotFeedbackOverlay"');
    expect(dialogSrc).toContain('className="modalCard pilotFeedbackCard"');
    expect(dialogSrc).toContain('role="dialog"');
    expect(dialogSrc).toContain('aria-modal="true"');
    expect(stylesSrc).toContain('.pilotFeedbackCard');
  });

  test('every question in the brief is asked, in order', () => {
    expect(DETAILED_FEEDBACK_HEADING).toBe('Pilot Security Feedback');
    expect(DETAILED_FEEDBACK_FIELDS.map((field) => field.label)).toEqual([
      'What were you trying to do?',
      'What security or operational problem were you trying to solve?',
      'How do you handle this today?',
      'Where did Decoda help?',
      'What was missing or difficult?',
      'What would Decoda need before you would deploy it in production?',
    ]);
  });

  test('only the first two answers are required', () => {
    // "Do NOT require every field" — a partial answer is worth more than a
    // refused submission.
    const required = DETAILED_FEEDBACK_FIELDS.filter((field) => field.required).map((f) => f.name);
    expect(required).toEqual(['goal_or_task', 'security_problem']);
  });

  test('the required-field check blocks submit and names the missing answer', () => {
    expect(dialogSrc).toContain('const missingRequired = fields');
    expect(dialogSrc).toContain('setShowRequired(true)');
    expect(dialogSrc).toContain('This answer is required.');
    expect(dialogSrc).toContain('aria-invalid={invalid || undefined}');
  });

  test('the importance and blocker choices render the asked-for options', () => {
    expect(FEEDBACK_IMPORTANCE_OPTIONS.map((o) => o.label)).toEqual([
      'Critical', 'High', 'Medium', 'Low',
    ]);
    expect(PRODUCTION_BLOCKER_OPTIONS.map((o) => o.label)).toEqual([
      'Not stated', 'Yes', 'No', 'Not sure',
    ]);
    expect(PRODUCTION_BLOCKER_OPTIONS.map((o) => o.value)).toEqual(['', 'yes', 'no', 'not_sure']);
  });

  test('the feedback-area list is a strict subset of the type vocabulary', () => {
    // Both depths write ONE column, so the console can group them together.
    const types = new Set(FEEDBACK_TYPE_OPTIONS.map((option) => option.value));
    for (const area of FEEDBACK_AREA_OPTIONS) {
      expect(types.has(area.value), `${area.value} must be a known feedback type`).toBe(true);
    }
    expect(FEEDBACK_AREA_OPTIONS[0].label).toBe('Detection / False positive');
  });

  test('contact permission is unchecked until the customer ticks it', () => {
    expect(dialogSrc).toContain('const [contactPermission, setContactPermission] = useState(false)');
    expect(dialogSrc).toContain('Can we contact you about this feedback?');
  });

  test('an entirely empty submission cannot be sent', () => {
    expect(dialogSrc).toContain('const anythingAnswered =');
    expect(dialogSrc).toContain('disabled={!csrfReady || status === \'sending\' || !anythingAnswered}');
  });
});

/* ── END-OF-PILOT REVIEW ───────────────────────────────────────────── */

test.describe('the end-of-Pilot review appears near expiry and only then', () => {
  test('the CTA threshold is five days', () => {
    expect(END_OF_PILOT_CTA_DAYS).toBe(5);
    expect(END_OF_PILOT_CTA).toBe('Share end-of-Pilot review');
  });

  test('an evaluation at or inside the threshold shows it', () => {
    for (const days of [0, 1, 3, 5]) {
      expect(shouldShowEndOfPilotCta({ days_remaining: days, expired: false })).toBe(true);
    }
  });

  test('an early Pilot does not show it', () => {
    for (const days of [6, 12, 23, 89]) {
      expect(shouldShowEndOfPilotCta({ days_remaining: days, expired: false })).toBe(false);
    }
  });

  test('a plan with no evaluation window shows nothing rather than assuming one', () => {
    // Scale and Enterprise have no countdown, and an unreadable plan has none
    // either. Neither may be rendered as "your Pilot is nearly over".
    expect(shouldShowEndOfPilotCta(null)).toBe(false);
    expect(shouldShowEndOfPilotCta(undefined)).toBe(false);
    expect(shouldShowEndOfPilotCta({ days_remaining: null, expired: false })).toBe(false);
    expect(shouldShowEndOfPilotCta({ expired: false })).toBe(false);
    expect(shouldShowEndOfPilotCta({ days_remaining: Number.NaN, expired: false })).toBe(false);
  });

  test('the badge gates the CTA on that helper, not on its own arithmetic', () => {
    expect(badgeSrc).toContain('shouldShowEndOfPilotCta(evaluation)');
    expect(badgeSrc).toContain("setDialogMode('end_of_pilot')");
  });

  test('the review asks its seven questions and stays optional', () => {
    expect(END_OF_PILOT_FIELDS.map((field) => field.label)).toEqual([
      'What was most useful?',
      'What was least useful?',
      'What security problem remains unsolved?',
      'What would prevent production deployment?',
      'Which capability would you pay for?',
    ]);
    // Nothing on the review is required — it must never block product use.
    expect(END_OF_PILOT_FIELDS.every((field) => !field.required)).toBe(true);
    expect(dialogSrc).toContain('Would you continue using Decoda?');
    expect(dialogSrc).toContain('Anything else we should improve?');
    expect(CONTINUE_INTENT_OPTIONS.map((o) => o.value)).toEqual(['', 'yes', 'maybe', 'no']);
  });
});

/* ── CONTEXT CAPTURE ───────────────────────────────────────────────── */

test.describe('context is derived, never typed and never scraped', () => {
  test('the page path is captured on both paths', () => {
    expect(feedbackContextFromPath('/alerts')).toEqual({ page: '/alerts' });
    expect(badgeSrc).toContain('currentFeedbackContext()');
    expect(dialogSrc).toContain('currentFeedbackContext()');
  });

  test('an incident route contributes its id automatically', () => {
    expect(feedbackContextFromPath('/incidents/inc-123')).toEqual({
      page: '/incidents/inc-123',
      incident_id: 'inc-123',
    });
    expect(feedbackContextFromPath('/incidents/inc-123/timeline')).toEqual({
      page: '/incidents/inc-123/timeline',
      incident_id: 'inc-123',
    });
  });

  test('a route with no entity id contributes only the page', () => {
    expect(feedbackContextFromPath('/incidents')).toEqual({ page: '/incidents' });
    expect(feedbackContextFromPath('/dashboard')).toEqual({ page: '/dashboard' });
    expect(feedbackContextFromPath('')).toEqual({});
    expect(feedbackContextFromPath(null)).toEqual({});
  });

  test('neither form reads the DOM or any credential-bearing store', () => {
    // "Do NOT capture arbitrary page DOM or full request bodies", and nothing
    // that could carry a secret.
    for (const src of [badgeSrc, dialogSrc]) {
      expect(src).not.toContain('document.body.innerHTML');
      expect(src).not.toContain('document.querySelectorAll');
      expect(src).not.toContain('localStorage');
      expect(src).not.toContain('document.cookie');
    }
  });
});

/* ── FOUNDER ADMIN ─────────────────────────────────────────────────── */

test.describe('the founder console can find repeated pain points', () => {
  test('the customer table keeps its feedback count', () => {
    expect(adminSrc).toContain('{customer.feedback_count}');
  });

  test('the feedback table shows what the brief asked for', () => {
    for (const header of [
      '<th>Organization</th>',
      '<th>Primary contact</th>',
      '<th>Severity</th>',
      '<th>Production blocker</th>',
      '<th>Context</th>',
    ]) {
      expect(adminSrc).toContain(header);
    }
  });

  test('filters exist for type, severity, blocker, organization and date', () => {
    for (const testId of [
      'feedback-filter-type',
      'feedback-filter-severity',
      'feedback-filter-blocker',
      'feedback-filter-organization',
      'feedback-filter-since',
    ]) {
      expect(adminSrc).toContain(testId);
    }
  });

  test('filtering happens server-side so the counters match the rows', () => {
    // Narrowing the array in the component would filter one page of a
    // server-capped list while the summary described another.
    expect(adminSrc).toContain('const feedbackQuery = new URLSearchParams()');
    expect(adminSrc).toContain('/api/admin/feedback?${feedbackQuery.toString()}');
    expect(adminSrc).toContain('setFeedbackSummary(feedbackPayload.summary ?? null)');
    for (const key of ['feedback_type', 'severity', 'production_blocker', 'since']) {
      expect(routeSrc).toContain(`'${key}'`);
    }
  });

  test('the four roadmap counters are rendered', () => {
    for (const label of [
      'Production blockers:',
      'High/Critical feedback:',
      'Missing capability:',
      'Detection issues:',
    ]) {
      expect(adminSrc).toContain(label);
    }
  });

  test('a detail view shows every stored answer', () => {
    for (const question of [
      'What were you trying to do?',
      'What problem were you solving?',
      'How do you handle it today?',
      'Where did Decoda help?',
      'What was missing?',
      'What would Decoda need before production?',
    ]) {
      expect(adminSrc).toContain(question);
    }
    expect(adminSrc).toContain('data-testid="feedback-detail"');
    expect(adminSrc).toContain('Pilot day:');
  });

  test('an unrecorded answer is not rendered as an unanswered one', () => {
    // Before migration 0153 the columns do not exist. "We cannot record that"
    // and "they chose not to answer" are different facts and get different words.
    expect(adminSrc).toContain("return 'Not recorded on this deployment'");
    expect(adminSrc).toContain("'Not answered'");
    expect(adminSrc).toContain('has not run migration 0153');
  });

  test('a long answer wraps instead of breaking the table', () => {
    expect(adminSrc).toContain('function summarize(');
    const answer = stylesSrc.slice(stylesSrc.indexOf('.adminFeedbackAnswer {'));
    expect(answer).toContain('white-space: pre-wrap');
    expect(answer).toContain('overflow-wrap: anywhere');
  });

  test('the console reads feedback only through the internal admin route', () => {
    // Tenant isolation is the backend's job, but the frontend must not offer a
    // second door: no customer-facing endpoint returns another tenant's rows.
    expect(adminSrc).toContain("'/api/admin/feedback'");
    expect(adminSrc).not.toContain('/api/account/feedback');
  });
});

/* ── LABEL HELPERS ─────────────────────────────────────────────────── */

test.describe('labels never invent a value', () => {
  test('a known value renders its label; an absent one renders a dash', () => {
    expect(feedbackTypeLabel('missing_feature')).toBe('Missing capability');
    expect(feedbackTypeLabel(null)).toBe('—');
    expect(productionBlockerLabel('not_sure')).toBe('Not sure');
    expect(productionBlockerLabel(null)).toBe('—');
  });

  test('an unrecognised stored value is shown as itself, not as a guess', () => {
    // A row written by a newer deployment must not be relabelled into whatever
    // this build happens to have first in its list.
    expect(feedbackTypeLabel('some_future_type')).toBe('some_future_type');
    expect(productionBlockerLabel('unknown')).toBe('unknown');
  });
});
