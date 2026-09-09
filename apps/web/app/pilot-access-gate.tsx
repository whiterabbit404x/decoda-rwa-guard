'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

/**
 * What the product shows an authenticated account that has no Pilot access.
 *
 * The distinction this component exists to keep: being signed in is not being
 * approved. Before this change, an account with no tenant fell through to the
 * workspace-creation prompt, which minted an active Pilot organization — the
 * loophole. Now it lands here, and here shows no monitoring, no evidence
 * generation, no response actions, and nothing that consumes paid
 * infrastructure.
 *
 * The four states are kept apart rather than collapsed into one empty page,
 * because "we are reviewing your request" and "you have not applied" are
 * different facts, and showing the wrong one is a small lie the product does
 * not need to tell. The backend is the source of every one of them
 * (`GET /account/pilot-access`); nothing is inferred in the browser.
 */
export type PilotAccessState = 'active' | 'pending_review' | 'invited' | 'rejected' | 'none';

type AccessResponse = {
  state?: PilotAccessState;
  has_access?: boolean;
  request?: { status?: string; company_name?: string | null; requested_at?: string | null } | null;
};

const COPY: Record<Exclude<PilotAccessState, 'active'>, { title: string; body: string }> = {
  pending_review: {
    title: 'Pilot request under review',
    body: 'Your Decoda Pilot evaluation request has been received and is under review. We’ll email you once it has been reviewed — there is nothing else to do here yet.',
  },
  invited: {
    title: 'Your Pilot invitation is waiting',
    body: 'Decoda approved your Pilot evaluation. Open the invitation link we emailed you to activate it. Your evaluation starts when you accept.',
  },
  rejected: {
    title: 'Pilot access required',
    body: 'This account does not have an active Decoda evaluation. If your situation has changed, you can submit a new Pilot request.',
  },
  none: {
    title: 'Pilot access required',
    body: 'Your Decoda evaluation has not been activated. Decoda approves each Pilot evaluation individually — request one and we’ll review it.',
  },
};

export function PilotAccessRequired({ state }: { state: Exclude<PilotAccessState, 'active'> }) {
  const copy = COPY[state];
  return (
    <section className="emptyStatePanel" aria-live="polite">
      <h1>{copy.title}</h1>
      <p>{copy.body}</p>
      {state === 'invited' ? null : (
        <p>
          <Link href="/request-pilot" prefetch={false}>Request Pilot</Link>
        </p>
      )}
    </section>
  );
}

/**
 * Reads the caller's own access state. Returns `null` while unknown.
 *
 * An unreadable answer is NOT treated as access: the hook reports the failure
 * and the caller keeps the account out of the product, which is the fail-closed
 * direction.
 */
export function usePilotAccessState(): { state: PilotAccessState | null; loading: boolean } {
  const { authHeaders, isAuthenticated } = usePilotAuth();
  const [state, setState] = useState<PilotAccessState | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!isAuthenticated) {
      setState(null);
      return;
    }
    setLoading(true);
    void (async () => {
      try {
        const response = await fetch('/api/account/pilot-access', {
          headers: authHeaders(),
          cache: 'no-store',
        });
        if (cancelled) {
          return;
        }
        if (!response.ok) {
          setState('none');
          return;
        }
        const payload = (await response.json()) as AccessResponse;
        setState(payload.has_access === true ? 'active' : (payload.state ?? 'none'));
      } catch {
        if (!cancelled) {
          setState('none');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authHeaders, isAuthenticated]);

  return { state, loading };
}
