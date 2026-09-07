'use client';

import Link from 'next/link';

import { AlertIcon } from './reset-icons';
import type { ResetLinkProblem } from '../reset-token-state';

/**
 * A reset link that cannot be completed — expired, already used, unrecognised, or
 * unverifiable because the service could not be reached.
 *
 * Each state says plainly that the link will not work and what to do instead. None of
 * them shows a password form, and none reveals anything about the token itself.
 */
export default function ResetLinkErrorState({
  problem,
  onRequestNewLink,
  onRetry,
}: {
  problem: ResetLinkProblem;
  onRequestNewLink: () => void;
  onRetry: () => void;
}) {
  return (
    <>
      <div className="rpStatusIcon rpStatusIcon--warn" aria-hidden="true"><AlertIcon size={22} /></div>

      <h2 id="reset-step-heading" className="siFormTitle">{problem.heading}</h2>
      <p className="siFormSubtitle" role="alert">{problem.body}</p>

      {problem.retryable ? (
        <button type="button" className="siSubmitBtn rpPrimaryBtn" onClick={onRetry}>
          Try again
        </button>
      ) : null}

      {problem.offerNewLink ? (
        <button
          type="button"
          className={problem.retryable ? 'siSecondaryBtn' : 'siSubmitBtn rpPrimaryBtn'}
          onClick={onRequestNewLink}
        >
          Request a new link
        </button>
      ) : null}

      <p className="siAccountRow rpTopSpaced">
        <Link href="/sign-in" className="siLink" prefetch={false}>Back to sign in</Link>
      </p>
    </>
  );
}
