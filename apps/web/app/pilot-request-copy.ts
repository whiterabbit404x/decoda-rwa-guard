// ─────────────────────────────────────────────────────────────
// Canonical copy and options for the public "Request Pilot" form.
//
// Kept in its own module so the page, the tests, and any future surface that
// mentions the Pilot application (a marketing block, a confirmation email)
// cannot drift into promising something the backend does not do.
//
// The one promise this flow makes: a request is REVIEWED. It never says
// "instant access", "free trial", or "start now", because approval-only Pilot
// access means none of those are true.
// ─────────────────────────────────────────────────────────────

/** Suggested use cases. The field is free text — this list only steers it. */
export const USE_CASE_SUGGESTIONS: string[] = [
  'Tokenized treasury monitoring',
  'Stablecoin / RWA operations',
  'Tokenization platform',
  'Custodian / issuer monitoring',
  'Security evaluation',
  'Other',
];

/**
 * Rendered under the free-text fields, and mirrored by the backend, which
 * REFUSES a submission that looks like it carries a credential rather than
 * storing and redacting one.
 */
export const SECRET_WARNING =
  'Do not include private keys, credentials, seed phrases, or other secrets.';

/** Shown after a successful submission. Deliberately promises review, not access. */
export const SUBMITTED_HEADLINE = 'Pilot request received';
export const SUBMITTED_BODY =
  'We’ll review your request and contact you by email. Decoda approves each Pilot evaluation individually — access is not granted automatically.';

/** Shown when the same address already has a request under review. */
export const DUPLICATE_BODY =
  'An evaluation request for this email is already under review. We’ll contact you by email once it has been reviewed.';

/** The page’s own explanation of what the Pilot is and is not. */
export const REVIEW_NOTICE =
  'Every Decoda Pilot evaluation is reviewed and approved by our team before it is activated. Submitting this form does not create an account or start monitoring.';
