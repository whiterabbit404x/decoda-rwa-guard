'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

import {
  buildResetRequestPayload,
  buildResetSubmissionPayload,
  normalizeResetEmail,
  resolveResetRequestEmail,
} from '../password-reset-request';

export default function ResetPasswordClient() {
  const searchParams = useSearchParams();
  const token = searchParams.get('token') ?? '';
  // Seeded ONLY from the address the user supplied on Sign In. A direct visit
  // (no `email` param) leaves this blank — never a pilot/demo/default account.
  const [requestEmail, setRequestEmail] = useState(() => resolveResetRequestEmail(searchParams.get('email')));
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState<string | null>(null);

  const requestPayload = buildResetRequestPayload(requestEmail);
  const submissionPayload = buildResetSubmissionPayload(token, password);

  async function requestReset() {
    // Sends exactly the submitted address, or nothing at all.
    if (!requestPayload) {
      setStatus('Enter the email address for the account you want to reset.');
      return;
    }

    const response = await fetch('/api/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload),
    });
    const data = await response.json().catch(() => ({}));
    setStatus(response.ok ? 'If that account exists, a reset link was sent.' : (data.detail ?? 'Unable to request reset.'));
  }

  async function submitReset() {
    // Only the single-use token from the reset email authorizes the change. The
    // email field above is never part of this request and never a substitute for
    // a missing token.
    if (!submissionPayload) {
      setStatus('Open this page from a reset email link to complete reset.');
      return;
    }

    const response = await fetch('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(submissionPayload),
    });
    const data = await response.json().catch(() => ({}));
    setStatus(response.ok ? 'Password reset complete. Sign in with your new password.' : (data.detail ?? 'Unable to reset password.'));
  }

  return (
    <main className="container authPage">
      <div className="hero"><div><p className="eyebrow">Account recovery</p><h1>Reset your password</h1></div></div>
      <div className="twoColumnSection authPageGrid">
        <div className="dataCard authForm">
          <label className="label" htmlFor="reset-request-email">Email</label>
          {/* A saved-credential autofill must never silently retarget this reset at
              another account, so the field is explicitly identified and opted out
              of browser/password-manager fill. */}
          <input
            id="reset-request-email"
            name="reset_request_email"
            value={requestEmail}
            onChange={(event) => setRequestEmail(event.target.value)}
            type="email"
            autoComplete="off"
            data-1p-ignore
            data-lpignore="true"
            spellCheck={false}
            placeholder="you@company.com"
          />
          <button type="button" disabled={!requestPayload} onClick={() => void requestReset()}>Send reset email</button>
        </div>
        <div className="dataCard authForm">
          <label className="label" htmlFor="reset-new-password">New password</label>
          <input
            id="reset-new-password"
            name="reset_new_password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            autoComplete="new-password"
          />
          <button type="button" disabled={!submissionPayload} onClick={() => void submitReset()}>Reset password</button>
          {!token ? <p className="statusLine">Open this page from a reset email link to complete reset.</p> : null}
        </div>
      </div>
      {requestEmail && !normalizeResetEmail(requestEmail) ? (
        <p className="statusLine">Enter a valid email address to request a reset link.</p>
      ) : null}
      {status ? <p className="statusLine">{status}</p> : null}
    </main>
  );
}
