'use client';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

export default function ResetPasswordPage() {
  const token = useSearchParams()?.get('token') ?? '';
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  return <main className="container authPage"><form className="dataCard authForm" onSubmit={(e) => {
    e.preventDefault();
    void fetch('/api/auth/reset-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, password }) })
      .then(async (res) => { const body = await res.json().catch(()=>({})); if (!res.ok) throw new Error(body.detail ?? 'Unable to reset password.'); setMessage('Password reset complete.'); })
      .catch((err) => setMessage(err instanceof Error ? err.message : 'Unable to reset password.'));
  }}><h1>Set a new password</h1><label className="label">New password</label><input type="password" minLength={10} value={password} onChange={(e)=>setPassword(e.target.value)} required />
  <button type="submit">Update password</button>{message ? <p className="statusLine">{message}</p> : null}<p className="muted"><Link href="/sign-in">Back to sign in</Link></p></form></main>;
}
