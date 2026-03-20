'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function SignInPage() {
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
      router.push('/workspaces');
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
          <p className="eyebrow">Pilot SaaS access</p>
          <h1>Sign in</h1>
          <p className="lede">Use your workspace account to enable live mode, persist runs in Neon, and review prior history later.</p>
        </div>
      </div>
      {!liveModeConfigured ? <p className="statusLine">Live mode is disabled until the Vercel and Railway env vars are configured.</p> : null}
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
