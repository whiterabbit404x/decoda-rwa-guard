import { Suspense } from 'react';

import ResetPasswordClient from './reset-password-client';

function PageLoadingState() {
  return (
    <main className="container authPage">
      <div className="hero">
        <div>
          <p className="eyebrow">Account recovery</p>
          <h1>Reset your password</h1>
          <p className="lede">Loading reset request…</p>
        </div>
      </div>
    </main>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<PageLoadingState />}>
      <ResetPasswordClient />
    </Suspense>
  );
}
