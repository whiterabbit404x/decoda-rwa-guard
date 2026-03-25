'use client';
import { useSearchParams } from 'next/navigation';
import { usePilotAuth } from '../pilot-auth-context';

export default function AcceptInvitePage() {
  const token = useSearchParams()?.get('token') ?? '';
  const { authHeaders } = usePilotAuth();
  return <main className="container authPage"><div className="dataCard"><h1>Accept workspace invite</h1><button onClick={() => void fetch('/api/workspace/accept-invite', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ token }) })}>Accept invite</button></div></main>;
}
