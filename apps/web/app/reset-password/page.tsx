'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

export default function ResetPasswordPage() {
  const params = useSearchParams();
  const [token, setToken] = useState(params?.get('token') ?? '');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const response = await fetch('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, password }),
    });
    const payload = (await response.json().catch(() => ({}))) as { detail?: string; password_reset?: boolean };
    setMessage(payload.password_reset ? 'Password reset complete. You can sign in now.' : (payload.detail ?? 'Unable to reset password.'));
  }

  return <main className="container authPage"><h1>Reset password</h1><form className="dataCard authForm" onSubmit={onSubmit}><label className="label">Reset token</label><input value={token} onChange={(e)=>setToken(e.target.value)} required/><label className="label">New password</label><input type="password" minLength={10} value={password} onChange={(e)=>setPassword(e.target.value)} required/><button type="submit">Reset password</button>{message ? <p className="statusLine">{message}</p> : null}</form></main>;
}
