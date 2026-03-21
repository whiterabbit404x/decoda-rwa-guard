'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function SignInPageClient({ nextPath }: { nextPath?: string }) {
  const router = useRouter();
  const { signIn, liveModeConfigured } = usePilotAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await signIn({ email, password });
      router.push(nextPath ?? '/dashboard');
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : String(submitError));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="container authPage">
      <div className="hero">
        <div>
          <p className="eyebrow">Pilot access</p>
          <h1>Sign in to your workspace</h1>
          <p className="lede">Open your live pilot workspace, save operating history, and keep your team on the same company view.</p>
        </div>
      </div>
      {!liveModeConfigured ? <p className="statusLine">Live workspace access will appear here after the Railway and Vercel environment variables are configured.</p> : null}
      {nextPath ? <p className="muted">Sign in to continue to {nextPath}.</p> : null}
      <form className="dataCard authForm" onSubmit={handleSubmit}>
        <label className="label">Email</label>
        <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
        <label className="label">Password</label>
        <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required />
        <button type="submit" disabled={loading || !liveModeConfigured}>{loading ? 'Signing in…' : 'Sign in'}</button>
        {error ? <p className="statusLine">{error}</p> : null}
        <p className="muted">Need an account? <Link href="/sign-up">Create one</Link>.</p>
      </form>
    </main>
  );
}
