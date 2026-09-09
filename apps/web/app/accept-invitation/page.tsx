import { Suspense } from 'react';

import AcceptInvitationClient from './accept-invitation-client';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export const metadata = {
  title: 'Accept your Pilot invitation · Decoda RWA Guard',
  // An invitation link is addressed to one person. It is not a page that should
  // turn up in a search index.
  robots: { index: false, follow: false },
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
 */
export default function AcceptInvitationPage() {
  return (
    <Suspense fallback={<AcceptInvitationLoading />}>
      <AcceptInvitationClient />
    </Suspense>
  );
}
