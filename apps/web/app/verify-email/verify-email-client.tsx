'use client';

import { useSearchParams } from 'next/navigation';

export default function VerifyEmailClient() {
  const searchParams = useSearchParams();
  const token = searchParams.get('token');
  const email = searchParams.get('email');

  return (
    <main className="container authPage">
      <div className="hero">
        <div>
          <p className="eyebrow">Email verification</p>
          <h1>Verify your email</h1>
          <p className="lede">Confirm your account email to unlock sign-in for this workspace.</p>
        </div>
      </div>

      {!token ? <p className="statusLine">Verification link is missing a token. Request a new verification email and try again.</p> : null}
      {email ? <p className="muted">Verifying {email}.</p> : null}

      <div className="dataCard">
        <p className="statusLine">Email verification in this deployment is handled by the backend auth service.</p>
      </div>
    </main>
  );
}
