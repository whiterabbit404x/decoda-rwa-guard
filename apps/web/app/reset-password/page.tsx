'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function ResetPasswordPage() {
  const searchParams = useSearchParams();
  const { resetPassword } = usePilotAuth();
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  return <main className="container authPage"><form className="dataCard authForm" onSubmit={(event) => {event.preventDefault(); void resetPassword(searchParams?.get('token') ?? '', password).then(() => setMessage('Password updated.')).catch((error) => setMessage(error instanceof Error ? error.message : String(error)));}}><h1>Reset password</h1><input value={password} type="password" minLength={10} onChange={(event) => setPassword(event.target.value)} required /><button type="submit">Set new password</button>{message ? <p className="statusLine">{message}</p> : null}</form></main>;
}
