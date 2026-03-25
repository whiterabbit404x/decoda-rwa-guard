'use client';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

export default function VerifyEmailPage() {
  const params = useSearchParams();
  const token = params?.get('token') ?? '';
  const email = params?.get('email') ?? '';
  const [state, setState] = useState<'idle'|'loading'|'success'|'error'>('idle');
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!token) return;
    setState('loading');
    void fetch('/api/auth/verify-email', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) })
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail ?? 'Verification failed.');
        setState('success');
      })
      .catch((error) => { setState('error'); setMessage(error instanceof Error ? error.message : 'Verification failed.'); });
  }, [token]);

  return <main className="container authPage"><div className="dataCard"><h1>Verify your email</h1>
    {!token ? <><p className="muted">Check your inbox for the verification link{email ? ` sent to ${email}` : ''}.</p>
    <button onClick={() => void fetch('/api/auth/resend-verification', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) })}>Resend verification email</button></> : null}
    {state === 'loading' ? <p className="statusLine">Verifying…</p> : null}
    {state === 'success' ? <p className="statusLine">Email verified. You can now sign in.</p> : null}
    {state === 'error' ? <p className="statusLine">{message}</p> : null}
    <p className="muted"><Link href="/sign-in">Back to sign in</Link></p></div></main>;
}
