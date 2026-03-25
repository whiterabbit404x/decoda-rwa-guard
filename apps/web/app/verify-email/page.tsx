'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

export default function VerifyEmailPage() {
  const params = useSearchParams();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const token = params.get('token') ?? '';

  async function verify() {
    setError(null);
    const response = await fetch('/api/auth/verify-email', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const data = await response.json();
    if (!response.ok) {
      setError(data.detail ?? 'Verification failed.');
      return;
    }
    setMessage('Email verified. You can now sign in.');
  }

  return <main className="container authPage"><h1>Verify email</h1><p className="muted">Complete verification to activate your account.</p><button onClick={() => void verify()} disabled={!token}>Verify email</button>{message ? <p className="statusLine">{message}</p> : null}{error ? <p className="statusLine">{error}</p> : null}</main>;
}
