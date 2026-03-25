'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function VerifyEmailPage() {
  const searchParams = useSearchParams();
  const { verifyEmail } = usePilotAuth();
  const [message, setMessage] = useState<string | null>(null);

  async function onVerify() {
    const token = searchParams?.get('token') ?? '';
    try {
      await verifyEmail(token);
      setMessage('Your email is verified. You can now sign in.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  return <main className="container authPage"><section className="dataCard"><h1>Verify email</h1><button onClick={() => void onVerify()}>Verify email</button>{message ? <p className="statusLine">{message}</p> : null}</section></main>;
}
