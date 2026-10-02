import Link from 'next/link';

import { decodaLinks } from '../decoda-identity';
import { ACCESS_REASONS, isAccessReason } from '../decoda-identity-shared';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Access · Decoda RWA Guard',
};

/**
 * Why a person signed in with Decoda cannot open RWA Guard right now. The
 * reason comes from the Guard API's refusal (the Decoda platform's decision);
 * this page only explains it and offers the next honest step. It grants
 * nothing and reveals no identifiers.
 */
export default async function AccessPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = (await searchParams) ?? {};
  const raw = Array.isArray(params.reason) ? params.reason[0] : params.reason;
  const reason = isAccessReason(raw) ? raw : 'unavailable';
  const copy = ACCESS_REASONS[reason];
  const links = decodaLinks();

  return (
    <main className="container" aria-labelledby="access-heading" data-testid="decoda-access" data-reason={reason}>
      <section className="card">
        <p className="eyebrow">Decoda RWA Guard</p>
        <h1 id="access-heading">{copy.title}</h1>
        <p>{copy.body}</p>
        <div className="buttonRow">
          <a className="btn btn-primary" href={links.launcher}>Open the Decoda launcher</a>
          {copy.requestAccess ? <a className="btn btn-secondary" href={links.requestAccess}>Request access</a> : null}
          <Link className="btn btn-secondary" href="/sign-out" prefetch={false}>Sign in with a different account</Link>
        </div>
      </section>
    </main>
  );
}
