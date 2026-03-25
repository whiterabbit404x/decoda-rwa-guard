'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import AuthBuildBadge from '../auth-build-badge';
import AuthDiagnosticCard from '../auth-diagnostic-card';
import { resolveAuthFormState } from '../auth-form-state';
import { usePilotAuth } from '../pilot-auth-context';

export default function SignUpPageClient({ previewNotice }: { previewNotice?: React.ReactNode }) {
  const router = useRouter();
  const {
    apiTimeoutMs,
    configLoading,
    configured,
    liveModeEnabled,
    runtimeConfigDiagnostic,
    runtimeConfigSource,
    signUp,
    apiUrl,
    isAuthenticated,
    loading: authLoading,
  } = usePilotAuth();
  const [fullName, setFullName] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const runtimeConfig = useMemo(() => ({
    apiUrl: apiUrl || null,
    liveModeEnabled,
    apiTimeoutMs,
    configured,
    diagnostic: runtimeConfigDiagnostic,
    source: runtimeConfigSource,
  }), [apiTimeoutMs, apiUrl, configured, liveModeEnabled, runtimeConfigDiagnostic, runtimeConfigSource]);
  const formState = resolveAuthFormState(runtimeConfig, configLoading, loading);

  useEffect(() => {
    if (!authLoading && isAuthenticated) {
      router.replace('/dashboard');
    }
  }, [authLoading, isAuthenticated, router]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) {
      return;
    }
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
          <p className="eyebrow">Workspace onboarding</p>
          <h1>Create your company workspace</h1>
          <AuthBuildBadge />
          <p className="lede">Create your owner account and start your first workspace for production-ready threat, compliance, and resilience operations.</p>
        </div>
      </div>
      {formState.statusMessage ? <p className="statusLine">{formState.statusMessage}</p> : null}
      {formState.deploymentWarning ? <p className="statusLine">{formState.deploymentWarning}</p> : null}
      {previewNotice}
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
          <button type="submit" disabled={formState.submitDisabled}>{loading ? 'Creating account…' : 'Create account'}</button>
          {error ? <p className="statusLine">{error}</p> : null}
          {!configLoading && !configured ? <p className="statusLine">Auth is disabled until this deployment exposes a valid API_URL.</p> : null}
          <p className="muted">Already have an account? <Link href="/sign-in">Sign in</Link>.</p>
          <p className="muted"><Link href="/privacy">Privacy</Link> · <Link href="/terms">Terms</Link> · <Link href="/security">Security</Link></p>
        </form>
        <AuthDiagnosticCard loading={configLoading} runtimeConfig={runtimeConfig} />
      </div>
    </main>
  );
}
