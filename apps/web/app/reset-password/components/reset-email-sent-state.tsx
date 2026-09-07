'use client';

import Link from 'next/link';

import { MailIcon } from './reset-icons';
import { formatResendCooldown } from '../reset-token-state';

/**
 * STATE B — the request was accepted.
 *
 * The wording is deliberately conditional ("If an account exists for …"): the API
 * answers the same way for a registered and an unregistered address, and this screen
 * must not undo that by implying an account was found.
 */
export default function ResetEmailSentState({
  email,
  cooldownSeconds,
  onResend,
  onUseDifferentEmail,
  resending,
  error,
}: {
  email: string;
  cooldownSeconds: number;
  onResend: () => void;
  onUseDifferentEmail: () => void;
  resending: boolean;
  error: string | null;
}) {
  const cooling = cooldownSeconds > 0;

  return (
    <>
      <div className="rpStatusIcon rpStatusIcon--info" aria-hidden="true"><MailIcon size={22} /></div>

      <h2 id="reset-step-heading" className="siFormTitle">Check your email</h2>
      <p className="siFormSubtitle">
        If an account exists for <strong className="rpEmphasis">{email}</strong>, we&apos;ve sent a
        password reset link.
      </p>

      <p className="siMuted">Check your spam or junk folder if you don&apos;t see the email.</p>

      {error ? <div className="siAlert siAlertError" role="alert">{error}</div> : null}

      <p className="rpFieldNote">Didn&apos;t receive it?</p>

      <button
        type="button"
        className="siSubmitBtn rpPrimaryBtn"
        onClick={onResend}
        disabled={cooling || resending}
        aria-busy={resending}
        // The countdown is the button's own label, so a screen reader announces the
        // same reason for the disabled state that a sighted user reads.
        aria-describedby={cooling ? 'reset-resend-cooldown' : undefined}
      >
        {resending ? 'Sending...' : formatResendCooldown(cooldownSeconds)}
      </button>

      {cooling ? (
        <p id="reset-resend-cooldown" className="siMuted" role="status">
          You can request another email in a moment.
        </p>
      ) : null}

      <button type="button" className="siSecondaryBtn" onClick={onUseDifferentEmail}>
        Use a different email
      </button>

      <p className="siAccountRow rpTopSpaced">
        <Link href="/sign-in" className="siLink" prefetch={false}>Back to sign in</Link>
      </p>
    </>
  );
}
