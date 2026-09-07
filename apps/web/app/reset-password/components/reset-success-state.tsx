'use client';

import Link from 'next/link';

import { CheckIcon } from './reset-icons';

/**
 * The password was changed.
 *
 * The user is NOT signed in here: a reset revokes the account's sessions on the server,
 * and turning a reset link into a session would make an intercepted link a login.
 * Signing in again with the new password is the intended last step.
 */
export default function ResetSuccessState({ redirecting }: { redirecting: boolean }) {
  return (
    <div className="rpCentered">
      <div className="rpStatusIcon rpStatusIcon--success" aria-hidden="true"><CheckIcon size={24} /></div>

      <h2 id="reset-step-heading" className="siFormTitle">Password updated</h2>
      <p className="siFormSubtitle" role="status">
        Your password has been reset successfully. You can now sign in with your new password.
      </p>

      <Link href="/sign-in" className="siSubmitBtn rpPrimaryBtn rpLinkBtn" prefetch={false}>
        Continue to sign in
      </Link>

      {redirecting ? <p className="siMuted" role="status">Redirecting to sign in...</p> : null}
    </div>
  );
}
