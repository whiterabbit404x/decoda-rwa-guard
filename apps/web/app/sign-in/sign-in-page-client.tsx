'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { BuildInfo } from '../build-info';
import { resolveAuthFormState } from '../auth-form-state';
import { usePilotAuth } from 'app/pilot-auth-context';

type SignInRuntimeConfig = {
  apiUrl: string | null;
  liveModeEnabled: boolean;
  apiTimeoutMs: number | null;
  configured: boolean;
  diagnostic: string | null;
  source: unknown;
};

const FEATURES = [
  {
    icon: 'shield',
    title: 'End-to-End Runtime Protection',
    desc: 'From asset to incident to response and evidence.',
  },
  {
    icon: 'monitor',
    title: 'Real-Time Monitoring',
    desc: 'Telemetry, detections, anomalies, and alerts.',
  },
  {
    icon: 'clipboard',
    title: 'Actionable Response',
    desc: 'Recommended actions with audit-ready evidence.',
  },
  {
    icon: 'lock',
    title: 'Built for Trust & Compliance',
    desc: 'Exportable proof. Audit trails. Full transparency.',
  },
];

function FeatureIcon({ type }: { type: string }) {
  if (type === 'shield') {
    return (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M10 2L3 5.5v5c0 4.5 3 7.5 7 8.5 4-1 7-4 7-8.5v-5L10 2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
        <path d="M7 10l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  if (type === 'monitor') {
    return (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <circle cx="10" cy="10" r="3" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 3V1M10 19v-2M17 10h2M1 10h2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  }

  if (type === 'clipboard') {
    return (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <rect x="5" y="3" width="10" height="15" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <path d="M8 3V2a2 2 0 014 0v1" stroke="currentColor" strokeWidth="1.5" />
        <path d="M8 9h4M8 12h4M8 15h2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  }

  if (type === 'lock') {
    return (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <rect x="4" y="9" width="12" height="9" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <path d="M7 9V6a3 3 0 016 0v3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="10" cy="14" r="1.5" fill="currentColor" />
      </svg>
    );
  }

  return null;
}

function maskApiUrl(url: string | null): string {
  if (!url) return 'unset';

  try {
    const parsed = new URL(url);
    return `${parsed.protocol}//${parsed.host}/[...]`;
  } catch {
    return '[masked]';
  }
}

function formatConfigSource(source: unknown): string {
  if (!source) return 'unavailable';

  if (typeof source === 'string') {
    return source;
  }

  try {
    return JSON.stringify(source);
  } catch {
    return 'available';
  }
}

function DiagnosticsExpanded({
  runtimeConfig,
  loading,
}: {
  runtimeConfig: SignInRuntimeConfig;
  loading: boolean;
}) {
  const [buildInfo, setBuildInfo] = useState<BuildInfo | null>(null);
  const [buildLoading, setBuildLoading] = useState(true);

  useEffect(() => {
    let active = true;

    fetch('/api/build-info', { cache: 'no-store' })
      .then((res) => (res.ok ? (res.json() as Promise<BuildInfo>) : null))
      .then((data) => {
        if (active) {
          setBuildInfo(data);
          setBuildLoading(false);
        }
      })
      .catch(() => {
        if (active) setBuildLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const fmt = (value: string | null | undefined) => (value && String(value).trim() ? value : 'unavailable');

  const diagRows = [
    ['Environment', buildLoading ? 'loading...' : fmt(buildInfo?.vercelEnv)],
    ['Host', buildLoading ? 'loading...' : fmt(buildInfo?.host)],
    ['Branch', buildLoading ? 'loading...' : fmt(buildInfo?.branch)],
    ['Short commit SHA', buildLoading ? 'loading...' : fmt(buildInfo?.shortCommitSha ?? buildInfo?.commitSha)],
    ['Auth mode', buildLoading ? 'loading...' : fmt(buildInfo?.authMode)],
    ['API URL source', loading ? 'loading...' : maskApiUrl(runtimeConfig.apiUrl)],
    ['Live mode', loading ? 'loading...' : (runtimeConfig.liveModeEnabled ? 'enabled' : 'disabled')],
    ['API timeout', loading ? 'loading...' : (runtimeConfig.apiTimeoutMs ? `${runtimeConfig.apiTimeoutMs}ms` : 'default')],
    ['Config source', loading ? 'loading...' : formatConfigSource(runtimeConfig.source)],
  ] as const;

  return (
    <div className="siDiagContent">
      <div className="siDiagGrid">
        {diagRows.map(([label, value]) => (
          <div key={label} className="siDiagRow">
            <span className="siDiagLabel">{label}</span>
            <span className="siDiagValue">{value}</span>
          </div>
        ))}
      </div>
      <a href="/api/health" target="_blank" rel="noreferrer" className="siDiagHealthLink">
        Open /health
      </a>
    </div>
  );
}

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
  const [showPassword, setShowPassword] = useState(false);
  const [showDiag, setShowDiag] = useState(false);
  const [systemStatus, setSystemStatus] = useState<'checking' | 'healthy' | 'unavailable'>('checking');
  const lastRedirectPath = useRef<string | null>(null);

  const runtimeConfig = useMemo(
    () => ({
      apiUrl: apiUrl || null,
      liveModeEnabled,
      apiTimeoutMs,
      configured,
      diagnostic: runtimeConfigDiagnostic,
      source: runtimeConfigSource,
    }),
    [apiTimeoutMs, apiUrl, configured, liveModeEnabled, runtimeConfigDiagnostic, runtimeConfigSource],
  );

  const formState = resolveAuthFormState(runtimeConfig, configLoading, loading);

  useEffect(() => {
    let active = true;

    fetch('/api/health', { cache: 'no-store' })
      .then((res) => {
        if (active) setSystemStatus(res.ok ? 'healthy' : 'unavailable');
      })
      .catch(() => {
        if (active) setSystemStatus('unavailable');
      });

    return () => {
      active = false;
    };
  }, []);

  async function confirmSessionAndRedirect(source: 'password-signin' | 'mfa-complete') {
    void source;

    const refreshedUser = await refreshUser();
    if (!refreshedUser) {
      setError('Sign-in succeeded but the session cookie was not established. Please retry.');
      return;
    }

    const targetPath = nextPath ?? '/dashboard';
    if (lastRedirectPath.current === targetPath) return;

    lastRedirectPath.current = targetPath;
    router.replace(targetPath);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (loading) return;

    setLoading(true);
    setError(null);

    try {
      await signIn({ email, password });
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

    if (loading) return;

    setLoading(true);
    setError(null);

    try {
      await completeMfaSignIn(mfaCode);
      await confirmSessionAndRedirect('mfa-complete');
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : String(submitError));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="siPage">
      <div className="siWrapper">
        <div className="siContainer">
          <main className="siOuter" aria-labelledby="sign-in-heading">
            <section className="siBrand" aria-label="Decoda Security RWA Guard">
              <div className="siBrandInner">
                <header className="siLogoHeader">
                  <div className="siLogoIcon" aria-hidden="true">
                    <svg width="34" height="34" viewBox="0 0 34 34" fill="none">
                      <path d="M17 3L4 8.5v9c0 7.5 5.5 13 13 15 7.5-2 13-7.5 13-15v-9L17 3z" fill="rgba(59,130,246,0.18)" stroke="#3b82f6" strokeWidth="1.8" strokeLinejoin="round" />
                      <path d="M12 17l3.5 3.5L23 13" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </div>
                  <div className="siLogoText">
                    <span className="siLogoName">DECODA</span>
                    <span className="siLogoSub">SECURITY</span>
                  </div>
                </header>

                <span className="siBadge" aria-label="Product: RWA Guard">RWA GUARD</span>

                <h1 className="siHeadline">
                  Runtime Security.<br />
                  Real-World Assurance.
                </h1>

                <p className="siLede">
                  Decoda RWA Guard continuously monitors your real-world assets, detects threats, and helps you respond with confidence.
                </p>

                <ul className="siFeatures" role="list">
                  {FEATURES.map((feature) => (
                    <li key={feature.title} className="siFeatureItem">
                      <div className="siFeatureIcon" aria-hidden="true">
                        <FeatureIcon type={feature.icon} />
                      </div>
                      <div>
                        <p className="siFeatureTitle">{feature.title}</p>
                        <p className="siFeatureDesc">{feature.desc}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="siBrandFooter">
                <div className="siBrandDecor" aria-hidden="true" />
                <p className="siTrustLine">
                  <svg width="15" height="15" viewBox="0 0 15 15" fill="none" aria-hidden="true" style={{ flexShrink: 0 }}>
                    <path d="M7.5 1.5L1.5 4v4.5c0 3.5 2.5 6 6 7 3.5-1 6-3.5 6-7V4L7.5 1.5z" fill="none" stroke="#4ade80" strokeWidth="1.3" strokeLinejoin="round" />
                    <path d="M5 7.5l2 2 3-3" stroke="#4ade80" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  Secure. Reliable. Purpose-built for Real-World Assets.
                </p>
              </div>
            </section>

            <section className="siFormPanel" aria-labelledby="sign-in-heading">
              {previewNotice}

              {nextPath ? (
                <p className="siNextPath">
                  Sign in to continue to <strong>{nextPath}</strong>.
                </p>
              ) : null}

              {formState.statusMessage ? (
                <div className="siAlert siAlertWarn" role="status">{formState.statusMessage}</div>
              ) : null}

              {formState.deploymentWarning ? (
                <div className="siAlert siAlertWarn" role="status">{formState.deploymentWarning}</div>
              ) : null}

              <h2 id="sign-in-heading" className="siFormTitle">Welcome back</h2>
              <p className="siFormSubtitle">Sign in to your workspace</p>

              {mfaRequired ? (
                <form onSubmit={handleMfaSubmit} noValidate>
                  <div className="siFormGroup">
                    <label className="siLabel" htmlFor="si-mfa-code">Authenticator code</label>
                    <input
                      id="si-mfa-code"
                      className="siInput"
                      value={mfaCode}
                      onChange={(event) => setMfaCode(event.target.value)}
                      inputMode="numeric"
                      pattern="[0-9 ]*"
                      autoComplete="one-time-code"
                      required
                      placeholder="000 000"
                    />
                  </div>

                  {error ? <div className="siAlert siAlertError" role="alert">{error}</div> : null}

                  <p className="siMuted">Enter a 6-digit authenticator code or one unused recovery code.</p>

                  <button type="submit" className="siSubmitBtn" disabled={loading || !mfaChallengeToken} aria-busy={loading}>
                    {loading ? 'Verifying...' : 'Complete sign in'}
                  </button>

                  <button
                    type="button"
                    className="siSecondaryBtn"
                    onClick={() => {
                      setMfaRequired(false);
                      setMfaCode('');
                    }}
                    disabled={loading}
                  >
                    Use a different account
                  </button>
                </form>
              ) : (
                <form onSubmit={handleSubmit} noValidate>
                  <div className="siFormGroup">
                    <label className="siLabel" htmlFor="si-email">Email address</label>
                    <div className="siInputWrap">
                      <span className="siInputIcon" aria-hidden="true">
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                          <rect x="2" y="4" width="12" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
                          <path d="M2 6.5l6 3.5 6-3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                        </svg>
                      </span>
                      <input
                        id="si-email"
                        className="siInput siInputWithIcon"
                        type="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        autoComplete="email"
                        placeholder="you@company.com"
                        required
                      />
                    </div>
                  </div>

                  <div className="siFormGroup">
                    <label className="siLabel" htmlFor="si-password">Password</label>
                    <div className="siInputWrap">
                      <span className="siInputIcon" aria-hidden="true">
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                          <rect x="4" y="7" width="8" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
                          <path d="M5.5 7V5.5a2.5 2.5 0 015 0V7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                        </svg>
                      </span>
                      <input
                        id="si-password"
                        className="siInput siInputWithIcon siInputWithToggle"
                        type={showPassword ? 'text' : 'password'}
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        autoComplete="current-password"
 placeholder="************"
                      />
                      <button
                        type="button"
                        className="siPasswordToggle"
                        onClick={() => setShowPassword((value) => !value)}
                        aria-label={showPassword ? 'Hide password' : 'Show password'}
                      >
                        {showPassword ? (
                          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                            <path d="M2 8s2.5-4 6-4 6 4 6 4-2.5 4-6 4-6-4-6-4z" stroke="currentColor" strokeWidth="1.4" />
                            <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
                            <path d="M3 3l10 10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                          </svg>
                        ) : (
                          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                            <path d="M2 8s2.5-4 6-4 6 4 6 4-2.5 4-6 4-6-4-6-4z" stroke="currentColor" strokeWidth="1.4" />
                            <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
                          </svg>
                        )}
                      </button>
                    </div>
                  </div>

                  <div className="siCheckRow">
                    <label className="siCheckLabel">
                      <input type="checkbox" className="siCheckbox" defaultChecked />
                      <span>Remember me</span>
                    </label>
                    <Link href="/reset-password" className="siLink">Forgot password?</Link>
                  </div>

                  {error ? <div className="siAlert siAlertError" role="alert">{error}</div> : null}

                  {!configLoading && !configured ? (
                    <div className="siAlert siAlertWarn" role="status">
                      Auth is disabled until this deployment exposes a valid API_URL.
                    </div>
                  ) : null}

                  <button type="submit" className="siSubmitBtn" disabled={formState.submitDisabled} aria-busy={loading}>
                    {loading ? 'Signing in... : 'Sign in'}
                  </button>

                  <p className="siAccountRow">
                    Don&apos;t have an account?{' '}
                    <Link href="/sign-up" className="siLink" prefetch={false}>Create one</Link>
                  </p>
                </form>
              )}
            </section>
          </main>

          <section className="siDiagCard" data-testid="deployment-diagnostics" aria-label="Deployment diagnostics">
            <div className="siDiagHeader">
              <div className="siDiagMeta">
                <span className="siDiagMetaIcon" aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                    <path d="M9 1.5L2 4.5v5c0 5 3.5 7.5 7 8.5 3.5-1 7-3.5 7-8.5v-5L9 1.5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
                  </svg>
                </span>
                <div>
                  <p className="siDiagTitle">Deployment details &amp; diagnostics</p>
                  <p className="siDiagSubtitle">For operators and administrators</p>
                </div>
              </div>

              <button
                type="button"
                className="siDiagToggle"
                onClick={() => setShowDiag((value) => !value)}
                aria-expanded={showDiag}
                aria-controls="si-diag-content"
              >
                {showDiag ? 'Hide details' : 'Show details'}
                <span className="siDiagChevron" aria-hidden="true">{showDiag ? '鈭? : '鈭?}</span>
              </button>
            </div>

            {showDiag ? (
              <div id="si-diag-content">
                <DiagnosticsExpanded runtimeConfig={runtimeConfig} loading={configLoading} />
              </div>
            ) : null}
          </section>
        </div>
      </div>

      <footer className="siFooter">
        <p className="siFooterCopy">&copy; 2026 Decoda Security. All rights reserved.</p>
        <nav className="siFooterLinks" aria-label="Legal links">
          <a href="#" className="siFooterLink">Privacy Policy</a>
          <span className="siFooterSep" aria-hidden="true">鈥?/span>
          <a href="#" className="siFooterLink">Terms of Service</a>
          <span className="siFooterSep" aria-hidden="true">鈥?/span>
          <a href="#" className="siFooterLink">Security</a>
        </nav>
        <div
          className={`siStatusPill${systemStatus === 'healthy' ? ' siStatusHealthy' : ' siStatusUnknown'}`}
          role="status"
          aria-label={systemStatus === 'healthy' ? 'All Systems Operational' : 'System status unavailable'}
        >
          <span className={`siStatusDot${systemStatus === 'healthy' ? ' siStatusDotGreen' : ' siStatusDotGray'}`} aria-hidden="true" />
          {systemStatus === 'healthy' ? 'All Systems Operational' : systemStatus === 'checking' ? 'Checking status... : 'System status unavailable'}
        </div>
      </footer>
    </div>
  );
}






