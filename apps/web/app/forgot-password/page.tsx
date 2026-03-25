'use client';

import { useState } from 'react';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    const response = await fetch('/api/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const payload = (await response.json().catch(() => ({}))) as { detail?: string; reset_token?: string };
    setMessage(payload.reset_token ? `Reset token (debug mode): ${payload.reset_token}` : (payload.detail ?? 'If your account exists, a reset email has been sent.'));
  }

  return <main className="container authPage"><h1>Forgot password</h1><form className="dataCard authForm" onSubmit={onSubmit}><label className="label">Email</label><input type="email" value={email} onChange={(e)=>setEmail(e.target.value)} required/><button type="submit">Send reset link</button>{message ? <p className="statusLine">{message}</p> : null}</form></main>;
}
