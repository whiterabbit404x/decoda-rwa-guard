'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

export default function ResetPasswordPage() {
  const params = useSearchParams();
  const token = params.get('token') ?? '';
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    const response = await fetch('/api/auth/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, password }),
    });
    const data = await response.json();
    if (!response.ok) {
      setError(data.detail ?? 'Reset failed.');
      return;
    }
    setMessage('Password reset complete. Sign in with your new password.');
  }

  return <main className="container authPage"><h1>Reset password</h1><input type="password" minLength={10} value={password} onChange={(event) => setPassword(event.target.value)} /><button onClick={() => void submit()} disabled={!token}>Reset password</button>{message ? <p className="statusLine">{message}</p> : null}{error ? <p className="statusLine">{error}</p> : null}</main>;
}
