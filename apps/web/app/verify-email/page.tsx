'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

export default function VerifyEmailPage() {
  const params = useSearchParams();
  const [token, setToken] = useState(params?.get('token') ?? '');
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  async function verify() {
    const response = await fetch('/api/auth/verify-email', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
    const payload = (await response.json().catch(() => ({}))) as { detail?: string; verified?: boolean };
    setMessage(payload.verified ? 'Email verified. You can sign in.' : (payload.detail ?? 'Unable to verify email.'));
  }

  async function resend() {
    const response = await fetch('/api/auth/resend-verification', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) });
    const payload = (await response.json().catch(() => ({}))) as { detail?: string; verification_token?: string };
    setMessage(payload.verification_token ? `Verification token (debug mode): ${payload.verification_token}` : (payload.detail ?? 'If eligible, we sent a new verification email.'));
  }

  return <main className="container authPage"><h1>Verify email</h1><div className="twoColumnSection"><form className="dataCard authForm" onSubmit={(e)=>{e.preventDefault(); void verify();}}><label className="label">Verification token</label><input value={token} onChange={(e)=>setToken(e.target.value)} required/><button type="submit">Verify email</button></form><form className="dataCard authForm" onSubmit={(e)=>{e.preventDefault(); void resend();}}><label className="label">Email</label><input type="email" value={email} onChange={(e)=>setEmail(e.target.value)} required/><button type="submit">Resend verification</button></form></div>{message ? <p className="statusLine">{message}</p> : null}</main>;
}
