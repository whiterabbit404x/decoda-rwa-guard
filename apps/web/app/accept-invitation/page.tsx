import { cookies } from 'next/headers';
import { Suspense } from 'react';

import AcceptInvitationClient from './accept-invitation-client';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export const metadata = {
  title: 'Accept your Pilot invitation · Decoda RWA Guard',
  // An invitation link is addressed to one person. It is not a page that should
  // turn up in a search index.
  robots: { index: false, follow: false },
  // Belt and braces against token leakage: this URL carries the invitation in its
  // query string, so no outbound request from this page may carry it in Referer.
  referrer: 'no-referrer',
};

function AcceptInvitationLoading() {
  return (
    <main className="pilotRequestPage">
      <section className="pilotRequestCard">
        <p className="mktSectionLabel">PILOT EVALUATION</p>
        <h1 className="pilotRequestTitle">Checking your invitation…</h1>
      </section>
    </main>
  );
}

/**
 * Invitation acceptance.
 *
 * The page renders what the BACKEND says about the token — valid, expired,
 * already used, or not valid at all — and never decides for itself. Accepting
 * requires a signed-in account whose address matches the approved one; that
 * comparison happens server-side, so a page state cannot be edited into access.
 *
 * It is also the fork in the road. The backend reports whether the approved
 * address already has a Decoda account, and this page routes on that answer:
 * an existing account goes to sign-in, a brand-new one goes to invitation-aware
 * signup. Sending everyone to sign-in — what this page used to do — left every
 * new applicant at "Invalid email or password" with no account to sign in to.
 *
 * `hasSessionCookie` is read here rather than in the browser because it guards
 * the automatic hop: /sign-up redirects a visitor who HAS a session cookie back
 * to this page, so auto-routing is armed only when there is no cookie at all and
 * the two pages cannot bounce a stale one between them.
 */
export default async function AcceptInvitationPage() {
  const cookieStore = await cookies();
  const hasSessionCookie = Boolean(cookieStore.get('decoda_session')?.value);

  return (
    <Suspense fallback={<AcceptInvitationLoading />}>
      <AcceptInvitationClient hasSessionCookie={hasSessionCookie} />
    </Suspense>
  );
}
