'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import AuthDiagnosticCard from '../auth-diagnostic-card';
import { usePilotAuth } from '../pilot-auth-context';

export default function SignUpPage() {
  const router = useRouter();
  const { apiUrl, signUp, liveModeConfigured } = usePilotAuth();
  const [fullName, setFullName] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await signUp({ email, password, full_name: fullName, workspace_name: workspaceName });
      router.push('/dashboard');
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
          <p className="eyebrow">Pilot onboarding</p>
          <h1>Create your company workspace</h1>
          <p className="lede">Create the first workspace owner account for your team and start saving live pilot activity.</p>
        </div>
      </div>
      <div className="twoColumnSection authPageGrid">
        <form className="dataCard authForm" onSubmit={handleSubmit}>
          <label className="label">Full name</label>
          <input value={fullName} onChange={(event) => setFullName(event.target.value)} required />
          <label className="label">Workspace name</label>
          <input value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} required />
          <label className="label">Email</label>
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
          <label className="label">Password</label>
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" minLength={10} required />
          <button type="submit" disabled={loading || !liveModeConfigured}>{loading ? 'Creating account…' : 'Create account'}</button>
          {error ? <p className="statusLine">{error}</p> : null}
          <p className="muted">Already have an account? <Link href="/sign-in">Sign in</Link>.</p>
        </form>
        <AuthDiagnosticCard apiUrl={apiUrl} liveModeConfigured={liveModeConfigured} />
      </div>
    </main>
  );
}
