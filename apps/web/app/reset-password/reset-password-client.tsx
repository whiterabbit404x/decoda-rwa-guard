'use client';

import { useSearchParams } from 'next/navigation';

export default function ResetPasswordClient() {
  const searchParams = useSearchParams();
  const token = searchParams.get('token');
  const email = searchParams.get('email');

  return (
    <main className="container authPage">
      <div className="hero">
        <div>
          <p className="eyebrow">Account recovery</p>
          <h1>Reset your password</h1>
          <p className="lede">Use the password reset link from your email to continue with a secure password update.</p>
        </div>
      </div>

      {!token ? <p className="statusLine">Reset link is missing a token. Request a new reset email and try again.</p> : null}
      {email ? <p className="muted">Resetting password for {email}.</p> : null}

      <div className="dataCard">
        <p className="statusLine">Password reset in this deployment is handled by the backend auth service.</p>
      </div>
    </main>
  );
}
