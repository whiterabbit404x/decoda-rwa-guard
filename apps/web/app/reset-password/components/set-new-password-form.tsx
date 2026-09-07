'use client';

import Link from 'next/link';
import { useId, useState } from 'react';

import PasswordRequirements from './password-requirements';
import { EyeIcon, LockIcon, MailIcon } from './reset-icons';

/**
 * STATE C — set the new password.
 *
 * This screen never offers to send a reset email: requesting a link and completing a
 * reset are different steps, and mixing them is what made the old page ambiguous.
 *
 * The address shown is the one the backend resolved from the token, and is rendered
 * read-only. It is confirmation of *which account is about to change*, not an input:
 * the token alone decides that, and this field is never sent back.
 */
export default function SetNewPasswordForm({
  accountEmail,
  password,
  confirmation,
  onPasswordChange,
  onConfirmationChange,
  onSubmit,
  submitting,
  error,
}: {
  accountEmail: string;
  password: string;
  confirmation: string;
  onPasswordChange: (value: string) => void;
  onConfirmationChange: (value: string) => void;
  onSubmit: () => void;
  submitting: boolean;
  error: string | null;
}) {
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const policyId = useId();

  return (
    <>
      <h2 id="reset-step-heading" className="siFormTitle">Set your new password</h2>
      <p className="siFormSubtitle">
        Create a strong password for <strong className="rpEmphasis">{accountEmail}</strong>.
      </p>

      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <div className="siFormGroup">
          <label className="siLabel" htmlFor="reset-account-email">Email address</label>
          <div className="siInputWrap">
            <span className="siInputIcon" aria-hidden="true"><MailIcon /></span>
            <input
              id="reset-account-email"
              className="siInput siInputWithIcon rpReadOnlyInput"
              value={accountEmail}
              type="email"
              readOnly
              tabIndex={-1}
              aria-describedby="reset-account-email-note"
            />
            <span className="rpReadOnlyTag" aria-hidden="true">Read-only</span>
          </div>
          <p id="reset-account-email-note" className="rpFieldNote">
            Resolved from your reset link. This is the account that will be changed.
          </p>
        </div>

        <div className="siFormGroup">
          <label className="siLabel" htmlFor="reset-new-password">New password</label>
          <div className="siInputWrap">
            <span className="siInputIcon" aria-hidden="true"><LockIcon /></span>
            <input
              id="reset-new-password"
              name="reset_new_password"
              className="siInput siInputWithIcon siInputWithToggle"
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={(event) => onPasswordChange(event.target.value)}
              autoComplete="new-password"
              placeholder="Enter your new password"
              aria-describedby={policyId}
              required
            />
            <button
              type="button"
              className="siPasswordToggle"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? 'Hide new password' : 'Show new password'}
              aria-pressed={showPassword}
            >
              <EyeIcon open={showPassword} />
            </button>
          </div>
        </div>

        <div className="siFormGroup">
          <label className="siLabel" htmlFor="reset-confirm-password">Confirm new password</label>
          <div className="siInputWrap">
            <span className="siInputIcon" aria-hidden="true"><LockIcon /></span>
            <input
              id="reset-confirm-password"
              name="reset_confirm_password"
              className="siInput siInputWithIcon siInputWithToggle"
              type={showConfirmation ? 'text' : 'password'}
              value={confirmation}
              onChange={(event) => onConfirmationChange(event.target.value)}
              autoComplete="new-password"
              placeholder="Confirm your new password"
              aria-describedby={error ? 'reset-password-error' : undefined}
              aria-invalid={error ? true : undefined}
              required
            />
            <button
              type="button"
              className="siPasswordToggle"
              onClick={() => setShowConfirmation((value) => !value)}
              aria-label={showConfirmation ? 'Hide password confirmation' : 'Show password confirmation'}
              aria-pressed={showConfirmation}
            >
              <EyeIcon open={showConfirmation} />
            </button>
          </div>
        </div>

        <PasswordRequirements password={password} describedById={policyId} />

        {error ? <p id="reset-password-error" className="siFieldError rpBlockError" role="alert">{error}</p> : null}

        <button
          type="submit"
          className="siSubmitBtn rpPrimaryBtn"
          disabled={submitting}
          aria-busy={submitting}
        >
          {submitting ? 'Resetting...' : 'Reset password'}
        </button>
      </form>

      <p className="siAccountRow">
        <Link href="/sign-in" className="siLink" prefetch={false}>Back to sign in</Link>
      </p>
    </>
  );
}
