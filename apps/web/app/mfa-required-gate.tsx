'use client';

import Link from 'next/link';

import type { PilotMfaState } from 'app/pilot-auth-context';

/**
 * What the product shows a Pilot operator who has not satisfied MFA.
 *
 * This screen is NOT the control. The backend refuses every Pilot business and
 * data API for such a session — assets, alerts, incidents, evidence exports,
 * audit events, integrations and workspace settings all answer 403 whether or
 * not this component ever renders. What it does is turn that refusal into a
 * route the operator can actually walk: a named requirement and one link to the
 * place they complete it.
 *
 * The two states are kept apart because their remedies differ. "You have no
 * authenticator" is solved by enrolling; "you have one but this session never
 * used it" is solved by verifying. Collapsing them would send half the people
 * here to the wrong control.
 *
 * Nothing about it is presented as optional, because it is not: for a Pilot
 * workspace the requirement is a plan floor, not a workspace setting an owner
 * can turn off.
 */
export const MFA_SETUP_PATH = '/settings/security';

const COPY: Record<'enroll' | 'verify', { title: string; body: string; action: string }> = {
  enroll: {
    title: 'Multi-factor authentication is required for Pilot access.',
    body: 'Set up an authenticator before accessing this workspace.',
    action: 'Set up an authenticator',
  },
  verify: {
    title: 'Multi-factor authentication is required for Pilot access.',
    body: 'Verify your authenticator to continue in this session.',
    action: 'Verify this session',
  },
};

/** Which remedy this operator needs, from the backend's own refusal code. */
export function mfaGateVariant(state: PilotMfaState | undefined): 'enroll' | 'verify' | null {
  if (!state || !state.required || state.satisfied) {
    return null;
  }
  return state.code === 'MFA_CHALLENGE_REQUIRED' || state.enrolled ? 'verify' : 'enroll';
}

/**
 * Whether this path must stay reachable so the operator can finish MFA.
 *
 * Mirrors the backend's bootstrap allowlist for the one page that matters in the
 * browser. Gating the setup page itself would make the gate a lockout.
 */
export function isMfaSetupPath(pathname: string | null | undefined): boolean {
  const path = String(pathname ?? '');
  return path === MFA_SETUP_PATH || path.startsWith(`${MFA_SETUP_PATH}/`);
}

export function MfaRequired({
  variant,
  returnTo,
}: {
  variant: 'enroll' | 'verify';
  returnTo?: string | null;
}) {
  const copy = COPY[variant];
  const href = returnTo
    ? `${MFA_SETUP_PATH}?return_to=${encodeURIComponent(returnTo)}`
    : MFA_SETUP_PATH;
  return (
    <section className="emptyStatePanel" aria-live="polite" data-testid="mfa-required-gate">
      <h1>{copy.title}</h1>
      <p>{copy.body}</p>
      <p>
        <Link href={href} prefetch={false} className="btn btn-primary">
          {copy.action}
        </Link>
      </p>
      <p className="muted">
        Decoda enforces this on the server: Pilot monitoring, evidence, and audit data stay
        unavailable to this session until multi-factor authentication is complete.
      </p>
    </section>
  );
}
