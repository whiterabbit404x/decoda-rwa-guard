'use client';

import { useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function ForgotPasswordPage() {
  const { requestPasswordReset } = usePilotAuth();
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  return <main className="container authPage"><form className="dataCard authForm" onSubmit={(event) => {event.preventDefault(); void requestPasswordReset(email).then(() => setMessage('If that account exists, reset instructions were sent.')).catch((error) => setMessage(error instanceof Error ? error.message : String(error)));}}><h1>Forgot password</h1><input value={email} type="email" onChange={(event) => setEmail(event.target.value)} required /><button type="submit">Send reset instructions</button>{message ? <p className="statusLine">{message}</p> : null}</form></main>;
}
