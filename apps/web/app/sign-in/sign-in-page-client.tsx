'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo, useRef, useState } from 'react';

import AuthBuildBadge from '../auth-build-badge';
import AuthDiagnosticCard from '../auth-diagnostic-card';
import { resolveAuthFormState } from '../auth-form-state';
import { usePilotAuth } from 'app/pilot-auth-context';

export default function SignInPageClient({
  nextPath,
  previewNotice,
}: {
  nextPath?: string;
  previewNotice?: React.ReactNode;
}) {
  const router = useRouter();
  const {
    apiTimeoutMs,
    configLoading,
    configured,
    liveModeEnabled,
    runtimeConfigDiagnostic,
    runtimeConfigSource,
    signIn,
    completeMfaSignIn,
    refreshUser,
    mfaChallengeToken,
    apiUrl,
  } = usePilotAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState('');
  const [mfaRequired, setMfaRequired] = useState(false);
  const [loading, setLoading] = useState(false);
  const lastRedirectPath = useRef<string | null>(null);

  const runtimeConfig = useMemo(() => ({
    apiUrl: apiUrl || null,
    liveModeEnabled,
    apiTimeoutMs,
    configured,
    diagnostic: runtimeConfigDiagnostic,
    source: runtimeConfigSource,
  }), [apiTimeoutMs, apiUrl, configured, liveModeEnabled, runtimeConfigDiagnostic, runtimeConfigSource]);
  const formState = resolveAuthFormState(runtimeConfig, configLoading, loading);

  async function confirmSessionAndRedirect(source: 'password-signin' | 'mfa-complete') {
    const refreshedUser = await refreshUser();
    if (!refreshedUser) {
      console.debug('[dashboard-page-data trace] source=post-signin-session-confirmation', {
        phase: 'refresh-failure',
        trigger: source,
      });
      setError('Sign-in succeeded but the session cookie was not established. Please retry.');
      return;
    }

    const targetPath = nextPath ?? '/dashboard';
    if (lastRedirectPath.current === targetPath) {
      return;
    }
    lastRedirectPath.current = targetPath;
    console.debug('[dashboard-page-data trace] source=post-signin-client-redirect', {
      phase: 'redirect-after-session-confirmation',
      trigger: source,
      targetPath,
      userId: refreshedUser.id,
    });
    router.replace(targetPath);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await signIn({ email, password });
      console.debug('[dashboard-page-data trace] source=post-signin-session-confirmation', {
        phase: 'signin-response-success',
        trigger: 'password-signin',
      });
      await confirmSessionAndRedirect('password-signin');
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : String(submitError);
      if (message === 'MFA_REQUIRED') {
        setMfaRequired(true);
        setError(null);
      } else {
        setError(message);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleMfaSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await completeMfaSignIn(mfaCode);
      console.debug('[dashboard-page-data trace] source=post-signin-session-confirmation', {
        phase: 'signin-response-success',
        trigger: 'mfa-complete',
      });
      await confirmSessionAndRedirect('mfa-complete');
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
          <p className="eyebrow">Secure access</p>
          <h1>Sign in to your workspace</h1>
          <AuthBuildBadge />
          <p className="lede">Access your Decoda RWA Guard workspace to run analyses, review history, and coordinate your operations team.</p>
        </div>
      </div>
      {formState.statusMessage ? <p className="statusLine">{formState.statusMessage}</p> : null}
      {formState.deploymentWarning ? <p className="statusLine">{formState.deploymentWarning}</p> : null}
      {nextPath ? <p className="muted">Sign in to continue to {nextPath}.</p> : null}
      {previewNotice}
      <div className="twoColumnSection authPageGrid">
        {mfaRequired ? (
          <form className="dataCard authForm" onSubmit={handleMfaSubmit}>
            <label className="label">Authenticator code</label>
            <input value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} inputMode="numeric" pattern="[0-9 ]*" required />
            <button type="submit" disabled={loading || !mfaChallengeToken}>{loading ? 'Verifying…' : 'Complete sign in'}</button>
            {error ? <p className="statusLine">{error}</p> : null}
            <p className="muted">Enter a 6-digit TOTP code or one recovery code.</p>
            <button type="button" onClick={() => { setMfaRequired(false); setMfaCode(''); }} disabled={loading}>Use a different account</button>
          </form>
        ) : (
          <form className="dataCard authForm" onSubmit={handleSubmit}>
            <label className="label">Email</label>
            <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
            <label className="label">Password</label>
            <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required />
            <button type="submit" disabled={formState.submitDisabled}>{loading ? 'Signing in…' : 'Sign in'}</button>
            {error ? <p className="statusLine">{error}</p> : null}
            {!configLoading && !configured ? <p className="statusLine">Auth is disabled until this deployment exposes a valid API_URL.</p> : null}
            <p className="muted"><Link href="/reset-password">Forgot password?</Link></p>
            <p className="muted">Need an account? <Link href="/sign-up" prefetch={false}>Create one</Link>.</p>
          </form>
        )}
        <AuthDiagnosticCard loading={configLoading} runtimeConfig={runtimeConfig} />
      </div>
    </main>
  );
}
