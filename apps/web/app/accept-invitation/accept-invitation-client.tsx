'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type Invitation = {
  email: string;
  company_name: string | null;
  expires_at: string | null;
  evaluation_days: number | null;
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

export default function AcceptInvitationClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams?.get('token')?.trim() ?? '';
  const { authHeaders, csrfReady, isAuthenticated, loading: authLoading, user, refreshUser } = usePilotAuth();

  const [lookup, setLookup] = useState<LookupState>({ kind: 'loading' });
  const [accepting, setAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);

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
          <Link href="/request-pilot" className="suSubmitBtn" prefetch={false}>Request a Pilot evaluation</Link>
        </section>
      </main>
    );
  }

  const { invitation } = lookup;
  const signedInEmail = (user?.email ?? '').trim().toLowerCase();
  const invitedEmail = (invitation.email ?? '').trim().toLowerCase();
  const emailMatches = Boolean(signedInEmail) && signedInEmail === invitedEmail;
  const nextPath = `/accept-invitation?token=${encodeURIComponent(token)}`;

  return (
    <main className="pilotRequestPage">
      <section className="pilotRequestCard">
        <p className="mktSectionLabel">PILOT EVALUATION</p>
        <h1 className="pilotRequestTitle">Your Pilot evaluation is approved</h1>
        <p className="pilotRequestLede">
          {invitation.company_name
            ? `Decoda approved a Pilot evaluation for ${invitation.company_name}.`
            : 'Decoda approved your Pilot evaluation.'}
          {typeof invitation.evaluation_days === 'number' && invitation.evaluation_days > 0
            ? ` Accepting starts a ${invitation.evaluation_days}-day evaluation.`
            : ''}
        </p>
        <dl className="pilotRequestDetails">
          <div>
            <dt>Invitation issued to</dt>
            <dd>{invitation.email}</dd>
          </div>
          {invitation.expires_at ? (
            <div>
              <dt>Expires</dt>
              <dd>{new Date(invitation.expires_at).toLocaleString()}</dd>
            </div>
          ) : null}
        </dl>

        {acceptError ? <div className="pilotRequestAlert" role="alert">{acceptError}</div> : null}

        {!isAuthenticated ? (
          <>
            <p className="pilotRequestFootnote">
              Sign in as <strong>{invitation.email}</strong> to accept this invitation. If you do not
              have an account yet, create one with that address first.
            </p>
            <Link
              href={`/sign-in?next=${encodeURIComponent(nextPath)}`}
              className="suSubmitBtn"
              prefetch={false}
            >
              Sign in to accept
            </Link>
            <Link
              href={`/sign-up?next=${encodeURIComponent(nextPath)}`}
              className="pilotRequestSecondary"
              prefetch={false}
            >
              Create an account
            </Link>
          </>
        ) : !emailMatches ? (
          <>
            {/* Rendering guidance only. The refusal that matters is server-side:
                the backend compares the authenticated address against the
                approved one and returns 403 regardless of what this page shows. */}
            <div className="pilotRequestAlert" role="alert">
              You are signed in as <strong>{user?.email}</strong>. This invitation was issued to{' '}
              <strong>{invitation.email}</strong> and can only be accepted by that account.
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
