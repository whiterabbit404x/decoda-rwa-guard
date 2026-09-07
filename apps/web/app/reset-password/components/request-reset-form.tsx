'use client';

import Link from 'next/link';

import { MailIcon } from './reset-icons';

/**
 * STATE A — request a reset link.
 *
 * The email box is seeded only from the address the user typed on Sign In, and is
 * explicitly opted out of credential autofill: an unnamed email input on a same-origin
 * page is a password-manager fill target, which is how another account's address used
 * to appear here.
 */
export default function RequestResetForm({
  email,
  onEmailChange,
  onSubmit,
  submitting,
  error,
  canSubmit,
  disabledReason,
}: {
  email: string;
  onEmailChange: (value: string) => void;
  onSubmit: () => void;
  submitting: boolean;
  error: string | null;
  canSubmit: boolean;
  disabledReason: string | null;
}) {
  return (
    <>
      <h2 id="reset-step-heading" className="siFormTitle">Forgot your password?</h2>
      <p className="siFormSubtitle">
        Enter the email associated with your Decoda Security account. We&apos;ll send you a secure
        password reset link.
      </p>

      {disabledReason ? <div className="siAlert siAlertWarn" role="status">{disabledReason}</div> : null}

      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <div className="siFormGroup">
          <label className="siLabel" htmlFor="reset-request-email">Email address</label>
          <div className="siInputWrap">
            <span className="siInputIcon" aria-hidden="true"><MailIcon /></span>
            <input
              id="reset-request-email"
              name="reset_request_email"
              className="siInput siInputWithIcon"
              value={email}
              onChange={(event) => onEmailChange(event.target.value)}
              type="email"
              inputMode="email"
              autoComplete="off"
              data-1p-ignore
              data-lpignore="true"
              spellCheck={false}
              placeholder="you@company.com"
              aria-describedby={error ? 'reset-request-error' : undefined}
              aria-invalid={error ? true : undefined}
              required
            />
          </div>
          {error ? <p id="reset-request-error" className="siFieldError" role="alert">{error}</p> : null}
        </div>

        <button
          type="submit"
          className="siSubmitBtn rpPrimaryBtn"
          disabled={!canSubmit || submitting}
          aria-busy={submitting}
        >
          {submitting ? 'Sending...' : 'Send reset link'}
        </button>
      </form>

      <p className="siAccountRow">
        <Link href="/sign-in" className="siLink" prefetch={false}>Back to sign in</Link>
      </p>
    </>
  );
}
