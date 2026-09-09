'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  invitationDestination,
  invitationSignInHref,
  invitationSignUpHref,
} from '../invitation-routing';
import { usePilotAuth } from 'app/pilot-auth-context';

type Invitation = {
  email: string;
  company_name: string | null;
  expires_at: string | null;
  evaluation_days: number | null;
  status?: string | null;
  /**
   * Answered by the backend, from the database, about the address THIS invitation
   * names. The page never guesses it, and never asks about any other address.
   */
  account_exists?: boolean | null;
};

type LookupState =
  | { kind: 'loading' }
  | { kind: 'valid'; invitation: Invitation }
  | { kind: 'invalid'; message: string };

function detailMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object') {
    const detail = (payload as Record<string, unknown>).detail;
    if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string') {
      return String((detail as Record<string, unknown>).message);
    }
    if (typeof detail === 'string') {
      return detail;
    }
  }
  return fallback;
}

export default function AcceptInvitationClient({ hasSessionCookie = false }: { hasSessionCookie?: boolean }) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams?.get('token')?.trim() ?? '';
  const { authHeaders, csrfReady, isAuthenticated, loading: authLoading, user, refreshUser } = usePilotAuth();

  const [lookup, setLookup] = useState<LookupState>({ kind: 'loading' });
  const [accepting, setAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);
  // Activation is attempted at most once per page load. A double-click, a React
  // re-mount, or a refresh mid-flight must not post a second acceptance; the
  // backend is race-safe too (the activating UPDATE is conditional and rolls the
  // organization back when it loses), but the cheapest duplicate is the one that
  // is never sent.
  const activationStarted = useRef(false);
  const routed = useRef(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!token) {
        setLookup({ kind: 'invalid', message: 'This invitation link is not valid.' });
        return;
      }
      try {
        const response = await fetch(`/api/pilot-invitations?token=${encodeURIComponent(token)}`, {
          cache: 'no-store',
        });
        const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
        if (cancelled) {
          return;
        }
        if (!response.ok || payload.valid !== true) {
          setLookup({
            kind: 'invalid',
            message:
              typeof payload.message === 'string' && payload.message
                ? payload.message
                : detailMessage(payload, 'This invitation link is not valid.'),
          });
          return;
        }
        setLookup({ kind: 'valid', invitation: payload.invitation as Invitation });
      } catch {
        if (!cancelled) {
          setLookup({ kind: 'invalid', message: 'We could not check this invitation. Please try again.' });
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const accept = useCallback(async () => {
    if (accepting) {
      return;
    }
    setAccepting(true);
    setAcceptError(null);
    try {
      const response = await fetch('/api/pilot-invitations/accept', {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
        body: JSON.stringify({ token }),
      });
      const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      if (!response.ok) {
        setAcceptError(detailMessage(payload, `Your evaluation could not be activated (HTTP ${response.status}).`));
        return;
      }
      // The activated tenant changes this session's memberships, so re-read the
      // account before entering the product rather than routing on stale state.
      await refreshUser();
      router.replace('/dashboard');
    } catch {
      setAcceptError('Your evaluation could not be activated. Please try again.');
    } finally {
      setAccepting(false);
    }
  }, [accepting, authHeaders, refreshUser, router, token]);

  const invitation = lookup.kind === 'valid' ? lookup.invitation : null;
  const signedInEmail = (user?.email ?? '').trim().toLowerCase();
  const invitedEmail = (invitation?.email ?? '').trim().toLowerCase();
  const emailMatches = Boolean(signedInEmail) && signedInEmail === invitedEmail;

  // ── the fork ──────────────────────────────────────────────────────────────
  // Where an unauthenticated holder of a valid invitation belongs, decided from
  // the backend's `account_exists`. Armed only with no session cookie present:
  // /sign-up bounces a cookie-holding visitor straight back here, so routing on
  // a stale cookie is how the two pages would ping-pong.
  useEffect(() => {
    if (authLoading || isAuthenticated || hasSessionCookie || routed.current) {
      return;
    }
    const destination = invitationDestination(
      invitation ? { valid: true, account_exists: invitation.account_exists } : null,
      token,
    );
    if (!destination) {
      return;
    }
    routed.current = true;
    router.replace(destination);
  }, [authLoading, hasSessionCookie, invitation, isAuthenticated, router, token]);

  // Coming back from sign-in or invitation-aware signup, the click that started
  // this was "Accept invitation" in the approved email. Finish it rather than
  // asking for the same consent a second time. Every check that matters still
  // runs server-side on the POST.
  useEffect(() => {
    if (authLoading || !isAuthenticated || !emailMatches || !csrfReady || activationStarted.current) {
      return;
    }
    activationStarted.current = true;
    void accept();
  }, [accept, authLoading, csrfReady, emailMatches, isAuthenticated]);

  if (lookup.kind === 'loading' || authLoading) {
    return (
      <main className="pilotRequestPage">
        <section className="pilotRequestCard">
          <p className="mktSectionLabel">PILOT EVALUATION</p>
          <h1 className="pilotRequestTitle">Checking your invitation…</h1>
        </section>
      </main>
    );
  }

  if (lookup.kind === 'invalid') {
    return (
      <main className="pilotRequestPage">
        <section className="pilotRequestCard" aria-live="polite">
          <p className="mktSectionLabel">PILOT EVALUATION</p>
          <h1 className="pilotRequestTitle">Invitation unavailable</h1>
          {/* The backend's own reason, verbatim: expired, already used, or not
              valid. The page never softens it into something that sounds like
              access is still coming. */}
          <p className="pilotRequestLede">{lookup.message}</p>
          {/* An invitation that was already accepted is the common case here, and
              the account it activated still works — so offer the way back in
              rather than only the application form. */}
          <Link href={isAuthenticated ? '/dashboard' : '/sign-in'} className="suSubmitBtn" prefetch={false}>
            {isAuthenticated ? 'Go to your dashboard' : 'Sign in'}
          </Link>
          <Link href="/request-pilot" className="pilotRequestSecondary" prefetch={false}>
            Request a Pilot evaluation
          </Link>
        </section>
      </main>
    );
  }

  const validInvitation = lookup.invitation;
  const accountExists = validInvitation.account_exists !== false;

  return (
    <main className="pilotRequestPage">
      <section className="pilotRequestCard">
        <p className="mktSectionLabel">PILOT EVALUATION</p>
        <h1 className="pilotRequestTitle">Your Pilot evaluation is approved</h1>
        <p className="pilotRequestLede">
          {validInvitation.company_name
            ? `Decoda approved a Pilot evaluation for ${validInvitation.company_name}.`
            : 'Decoda approved your Pilot evaluation.'}
          {typeof validInvitation.evaluation_days === 'number' && validInvitation.evaluation_days > 0
            ? ` Accepting starts a ${validInvitation.evaluation_days}-day evaluation.`
            : ''}
        </p>
        <dl className="pilotRequestDetails">
          <div>
            <dt>Invitation issued to</dt>
            <dd>{validInvitation.email}</dd>
          </div>
          {validInvitation.expires_at ? (
            <div>
              <dt>Expires</dt>
              <dd>{new Date(validInvitation.expires_at).toLocaleString()}</dd>
            </div>
          ) : null}
        </dl>

        {acceptError ? <div className="pilotRequestAlert" role="alert">{acceptError}</div> : null}

        {!isAuthenticated ? (
          accountExists ? (
            <>
              {/* An account already exists for the approved address, so the way
                  in is sign-in — and the invitation travels with it. */}
              <p className="pilotRequestFootnote">
                Sign in as <strong>{validInvitation.email}</strong> to accept this invitation.
              </p>
              <Link href={invitationSignInHref(token)} className="suSubmitBtn" prefetch={false}>
                Sign in to accept
              </Link>
              <Link href={invitationSignUpHref(token)} className="pilotRequestSecondary" prefetch={false}>
                Create an account instead
              </Link>
            </>
          ) : (
            <>
              {/* No Decoda account for the approved address yet. Sign-in has
                  nothing to offer this person — they have no password — so the
                  primary action creates the account. */}
              <p className="pilotRequestFootnote">
                Create your Decoda account for <strong>{validInvitation.email}</strong> to accept this
                invitation. You will choose your own password.
              </p>
              <Link href={invitationSignUpHref(token)} className="suSubmitBtn" prefetch={false}>
                Create your Decoda account
              </Link>
              <Link href={invitationSignInHref(token)} className="pilotRequestSecondary" prefetch={false}>
                Already have an account? Sign in
              </Link>
            </>
          )
        ) : !emailMatches ? (
          <>
            {/* Rendering guidance only. The refusal that matters is server-side:
                the backend compares the authenticated address against the
                approved one and returns 403 regardless of what this page shows. */}
            <div className="pilotRequestAlert" role="alert">
              You are signed in as <strong>{user?.email}</strong>. This invitation was issued to{' '}
              <strong>{validInvitation.email}</strong> and can only be accepted by that account.
            </div>
            <Link href="/sign-out" className="suSubmitBtn" prefetch={false}>Sign out and switch account</Link>
          </>
        ) : (
          <button type="button" className="suSubmitBtn" disabled={accepting || !csrfReady} onClick={() => void accept()}>
            {accepting ? 'Activating…' : 'Accept invitation and start evaluation'}
          </button>
        )}
      </section>
    </main>
  );
}
