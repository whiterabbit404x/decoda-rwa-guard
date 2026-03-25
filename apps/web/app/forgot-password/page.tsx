'use client';

import { useState } from 'react';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  async function submit() {
    const response = await fetch('/api/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (response.ok) {
      setMessage('If an account exists, a password reset link has been issued.');
    }
  }

  return <main className="container authPage"><h1>Forgot password</h1><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} /><button onClick={() => void submit()}>Send reset link</button>{message ? <p className="statusLine">{message}</p> : null}</main>;
}
