'use client';

/**
 * The gap between "there is a token in the URL" and "that token can be used".
 *
 * No password control is rendered until the answer arrives, so a user can never type a
 * new password into a form that was never going to work.
 */
export default function ResetTokenValidating() {
  return (
    <div className="rpCentered" role="status" aria-live="polite" aria-busy="true">
      <span className="rpSpinner" aria-hidden="true" />
      <h2 id="reset-step-heading" className="siFormTitle">Verifying reset link...</h2>
      <p className="siFormSubtitle">Checking that this password reset link is still valid.</p>
    </div>
  );
}
