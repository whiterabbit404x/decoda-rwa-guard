/**
 * The account-password policy, mirrored from the API that enforces it.
 *
 * The backend is the only authority: `_require_password` / `_require_strong_password`
 * in `services/api/app/pilot.py` decide whether a password is accepted. This module
 * exists so the reset screen can *show* those rules and pre-validate against them —
 * never to add rules of its own.
 *
 * Promising the user a stricter policy than the API enforces is a bug (they weaken
 * their password needlessly); promising a looser one is worse (the form looks
 * satisfied, then the API rejects it). `tests/password-policy-parity.spec.ts` reads
 * the Python source and fails if these rules and that enforcement diverge.
 */

export type PasswordRequirementId = 'min_length' | 'lower_and_upper' | 'number';

export type PasswordRequirement = {
  id: PasswordRequirementId;
  label: string;
  isMet: (password: string) => boolean;
};

/** Mirrors PASSWORD_MIN_LENGTH in services/api/app/pilot.py. */
export const PASSWORD_MIN_LENGTH = 10;

export const PASSWORD_REQUIREMENTS: readonly PasswordRequirement[] = [
  {
    id: 'min_length',
    label: `At least ${PASSWORD_MIN_LENGTH} characters`,
    isMet: (password) => password.length >= PASSWORD_MIN_LENGTH,
  },
  {
    id: 'lower_and_upper',
    label: 'Uppercase & lowercase letters',
    isMet: (password) => /[a-z]/.test(password) && /[A-Z]/.test(password),
  },
  {
    id: 'number',
    label: 'One number',
    isMet: (password) => /\d/.test(password),
  },
];

export type PasswordRequirementState = {
  id: PasswordRequirementId;
  label: string;
  met: boolean;
};

/** Per-rule pass/fail, for the checklist under the password field. */
export function evaluatePasswordRequirements(password: string): PasswordRequirementState[] {
  return PASSWORD_REQUIREMENTS.map((requirement) => ({
    id: requirement.id,
    label: requirement.label,
    met: requirement.isMet(password),
  }));
}

export function isPasswordAcceptable(password: string): boolean {
  return PASSWORD_REQUIREMENTS.every((requirement) => requirement.isMet(password));
}

export type PasswordStrength = {
  /** Requirements satisfied, 0..PASSWORD_REQUIREMENTS.length. */
  score: number;
  total: number;
  label: 'Too weak' | 'Weak' | 'Good' | 'Strong';
};

/**
 * Strength is reported as progress against the real policy, not as a guess at
 * entropy: a bar that says "Strong" for a password the API will reject would be
 * a lie, and one that says "Too weak" for an accepted password is just noise.
 * An empty box reads "Too weak" rather than claiming anything about nothing.
 */
export function describePasswordStrength(password: string): PasswordStrength {
  const total = PASSWORD_REQUIREMENTS.length;
  const score = PASSWORD_REQUIREMENTS.filter((requirement) => requirement.isMet(password)).length;

  if (!password || score === 0) {
    return { score: 0, total, label: 'Too weak' };
  }
  if (score < total) {
    return { score, total, label: score === total - 1 ? 'Good' : 'Weak' };
  }
  return { score, total, label: 'Strong' };
}

/**
 * The single blocking error for the new-password form, or '' when it may be
 * submitted. Order matters: a user who has typed nothing is told to type, not
 * lectured about character classes.
 */
export function validateNewPassword(password: string, confirmation: string): string {
  if (!password) {
    return 'Enter a new password.';
  }
  if (!isPasswordAcceptable(password)) {
    return 'Your password does not meet all of the requirements below.';
  }
  if (!confirmation) {
    return 'Re-enter your new password to confirm it.';
  }
  if (password !== confirmation) {
    return 'Both passwords must match.';
  }
  return '';
}
