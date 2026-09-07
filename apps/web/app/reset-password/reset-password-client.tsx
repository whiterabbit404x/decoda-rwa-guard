'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  RESET_PASSWORD_PATH,
  buildResetRequestPayload,
  buildResetSubmissionPayload,
  normalizeResetEmail,
  resolveResetRequestEmail,
} from '../password-reset-request';
import { validateNewPassword } from '../password-policy';
import RequestResetForm from './components/request-reset-form';
import ResetEmailSentState from './components/reset-email-sent-state';
import ResetLinkErrorState from './components/reset-link-error-state';
import ResetPasswordShell from './components/reset-password-shell';
import ResetSuccessState from './components/reset-success-state';
import ResetTokenValidating from './components/reset-token-validating';
import SetNewPasswordForm from './components/set-new-password-form';
import {
  RESEND_COOLDOWN_SECONDS,
  VALIDATING_STATE,
  buildResetValidationPayload,
  canSubmitNewPassword,
  describeResetLinkProblem,
  interpretTokenValidation,
  resolveResetFlowStep,
  type ResetTokenState,
} from './reset-token-state';

const REDIRECT_TO_SIGN_IN_MS = 2500;

/**
 * One coherent account-recovery flow, not two forms sharing a page.
 *
 * The URL decides which of the three steps is shown, and only one is ever rendered:
 *
 *   /reset-password                -> request a link
 *   /reset-password?email=…        -> request a link, seeded with that address
 *   /reset-password?token=…        -> validate the link, then set a new password
 *
 * Two invariants hold throughout:
 *
 *  1. The `email` query param is a convenience for the *request* step only. It is
 *     never sent with a password change and never identifies the account being reset.
 *  2. The single-use token from the reset email is the sole authority for the change,
 *     and the account it belongs to is resolved server-side, from the token.
 */
export default function ResetPasswordClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = (searchParams.get('token') ?? '').trim();
  const step = resolveResetFlowStep(token);

  return step === 'reset' ? (
    <TokenResetFlow token={token} />
  ) : (
    <RequestLinkFlow initialEmail={resolveResetRequestEmail(searchParams.get('email'))} router={router} />
  );
}

// ---------------------------------------------------------------------------
// Steps 1 and 2 — request a reset link, then confirm it was requested
// ---------------------------------------------------------------------------
function RequestLinkFlow({
  initialEmail,
  router,
}: {
  initialEmail: string;
  router: ReturnType<typeof useRouter>;
}) {
  // Seeded ONLY from the address the user supplied on Sign In. A direct visit
  // (no `email` param) leaves this blank — never a pilot/demo/default account.
  const [email, setEmail] = useState(initialEmail);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const timer = setTimeout(() => setCooldown((seconds) => Math.max(0, seconds - 1)), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  const requestPayload = buildResetRequestPayload(email);

  async function sendResetLink() {
    // Sends exactly the submitted address, or nothing at all.
    if (!requestPayload) {
      setError('Enter a valid email address to request a reset link.');
      return;
    }
    if (submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestPayload),
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        setError(typeof data?.detail === 'string' ? data.detail : 'Unable to request a reset link. Please try again.');
        return;
      }

      // The API answers identically for a registered and an unregistered address, and
      // so does this screen: moving on is not a signal that an account was found.
      setSentTo(requestPayload.email);
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } catch {
      setError('We could not reach the account service. Check your connection and try again.');
    } finally {
      setSubmitting(false);
    }
  }

  if (sentTo) {
    return (
      <ResetPasswordShell activeStep={2}>
        <ResetEmailSentState
          email={sentTo}
          cooldownSeconds={cooldown}
          resending={submitting}
          error={error}
          onResend={() => void sendResetLink()}
          onUseDifferentEmail={() => {
            setSentTo(null);
            setError(null);
            setCooldown(0);
            // Drop any carried address from the URL so a shared or bookmarked link
            // does not reopen this page pointed at someone else's account.
            router.replace(RESET_PASSWORD_PATH);
          }}
        />
      </ResetPasswordShell>
    );
  }

  return (
    <ResetPasswordShell activeStep={1}>
      <RequestResetForm
        email={email}
        onEmailChange={(value) => {
          setEmail(value);
          if (error) setError(null);
        }}
        onSubmit={() => void sendResetLink()}
        submitting={submitting}
        error={error}
        canSubmit={Boolean(requestPayload)}
        disabledReason={
          email && !normalizeResetEmail(email)
            ? 'Enter a valid email address to request a reset link.'
            : null
        }
      />
    </ResetPasswordShell>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — validate the link, then set the new password
// ---------------------------------------------------------------------------
function TokenResetFlow({ token }: { token: string }) {
  const router = useRouter();
  const [tokenState, setTokenState] = useState<ResetTokenState>(VALIDATING_STATE);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [succeeded, setSucceeded] = useState(false);
  const validationAttempt = useRef(0);

  const validateToken = useCallback(async () => {
    const payload = buildResetValidationPayload(token);
    if (!payload) {
      setTokenState({ status: 'invalid', email: null });
      return;
    }

    const attempt = (validationAttempt.current += 1);
    setTokenState(VALIDATING_STATE);
    try {
      const response = await fetch('/api/auth/reset-password/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      // A superseded attempt must not overwrite the current one's answer.
      if (attempt !== validationAttempt.current) return;
      setTokenState(interpretTokenValidation(response, data));
    } catch {
      if (attempt !== validationAttempt.current) return;
      setTokenState({ status: 'error', email: null });
    }
  }, [token]);

  useEffect(() => {
    void validateToken();
  }, [validateToken]);

  useEffect(() => {
    if (!succeeded) return undefined;
    const timer = setTimeout(() => router.push('/sign-in'), REDIRECT_TO_SIGN_IN_MS);
    return () => clearTimeout(timer);
  }, [router, succeeded]);

  async function submitReset() {
    // Only the single-use token from the reset email authorizes the change. The
    // account address shown above is never part of this request and never a
    // substitute for a missing or rejected token.
    if (submitting || !canSubmitNewPassword(tokenState)) return;

    const validationError = validateNewPassword(password, confirmation);
    if (validationError) {
      setError(validationError);
      return;
    }

    const submissionPayload = buildResetSubmissionPayload(token, password);
    if (!submissionPayload) {
      setError('Open this page from a reset email link to complete your reset.');
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(submissionPayload),
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        // A link rejected at submit time is spent or expired: send the user back to
        // the blocked-link screen rather than leaving a form that cannot succeed.
        setTokenState({ status: 'invalid', email: null });
        setError(typeof data?.detail === 'string' ? data.detail : 'Unable to reset your password.');
        return;
      }

      setSucceeded(true);
    } catch {
      setError('We could not reach the account service. Check your connection and try again.');
    } finally {
      setSubmitting(false);
    }
  }

  if (succeeded) {
    return (
      <ResetPasswordShell activeStep={3}>
        <ResetSuccessState redirecting />
      </ResetPasswordShell>
    );
  }

  if (tokenState.status === 'validating') {
    return (
      <ResetPasswordShell activeStep={3}>
        <ResetTokenValidating />
      </ResetPasswordShell>
    );
  }

  const problem = describeResetLinkProblem(tokenState);
  if (problem) {
    return (
      <ResetPasswordShell activeStep={3}>
        <ResetLinkErrorState
          problem={problem}
          onRequestNewLink={() => router.push(RESET_PASSWORD_PATH)}
          onRetry={() => void validateToken()}
        />
        {error ? <p className="rpVisuallyHidden" role="status">{error}</p> : null}
      </ResetPasswordShell>
    );
  }

  return (
    <ResetPasswordShell activeStep={3}>
      <SetNewPasswordForm
        accountEmail={tokenState.email ?? ''}
        password={password}
        confirmation={confirmation}
        onPasswordChange={(value) => {
          setPassword(value);
          if (error) setError(null);
        }}
        onConfirmationChange={(value) => {
          setConfirmation(value);
          if (error) setError(null);
        }}
        onSubmit={() => void submitReset()}
        submitting={submitting}
        error={error}
      />
    </ResetPasswordShell>
  );
}
