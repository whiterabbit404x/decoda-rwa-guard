'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function AcceptInvitePage() {
  const searchParams = useSearchParams();
  const { authHeaders } = usePilotAuth();
  const [message, setMessage] = useState<string | null>(null);

  async function accept() {
    const response = await fetch('/api/auth/workspaces/invites/accept', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ token: searchParams?.get('token') ?? '' }),
    });
    const data = (await response.json()) as { detail?: string; accepted?: boolean };
    if (!response.ok) throw new Error(data.detail ?? 'Unable to accept invite.');
    setMessage('Invite accepted.');
  }

  return <main className="container authPage"><section className="dataCard"><h1>Accept workspace invite</h1><button onClick={() => void accept()}>Accept invite</button>{message ? <p className="statusLine">{message}</p> : null}</section></main>;
}
