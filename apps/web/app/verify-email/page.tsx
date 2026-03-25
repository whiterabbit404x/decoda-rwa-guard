import { Suspense } from 'react';

import VerifyEmailClient from './verify-email-client';

function PageLoadingState() {
  return (
    <main className="container authPage">
      <div className="hero">
        <div>
          <p className="eyebrow">Email verification</p>
          <h1>Verify your email</h1>
          <p className="lede">Loading verification request…</p>
        </div>
      </div>
    </main>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<PageLoadingState />}>
      <VerifyEmailClient />
    </Suspense>
  );
}
