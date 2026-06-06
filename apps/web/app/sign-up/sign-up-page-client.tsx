'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { resolveAuthFormState } from '../auth-form-state';
import { usePilotAuth } from 'app/pilot-auth-context';

const FEATURES = [
  {
    icon: 'monitor',
    title: 'Live Monitoring',
    desc: '24/7 real-time detection of threats and anomalous activity across your RWA infrastructure.',
  },
  {
    icon: 'clipboard',
    title: 'Audit-Ready Reporting',
    desc: 'Automatically generate compliance-ready reports with immutable evidence and traceability.',
  },
  {
    icon: 'shield',
    title: 'Compliance Visibility',
    desc: 'Centralized dashboards to track controls, risks, and posture across your RWA ecosystem.',
  },
];

const PARTNERS = ['ATLAS CAPITAL', 'NOVA ASSETS', 'CREDORA', 'VERITAS GROUP'];

function FeatureCardIcon({ type }: { type: string }) {
  if (type === 'monitor') {
    return (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <rect x="2" y="3" width="16" height="11" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <path d="M7 17h6M10 14v3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M6 8l2 2 3-3 3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
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
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M10 2L3 5.5v5c0 4.5 3 7.5 7 8.5 4-1 7-4 7-8.5v-5L10 2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M7 10l2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path d="M16.5 9.2c0-.6-.05-1.2-.15-1.8H9v3.4h4.2c-.18 1-.73 1.85-1.55 2.4v2h2.5c1.48-1.36 2.35-3.36 2.35-5.7h-.7z" fill="#4285F4" />
      <path d="M9 17c2.1 0 3.86-.7 5.15-1.9l-2.5-2c-.7.48-1.6.76-2.65.76-2.03 0-3.75-1.37-4.36-3.22H2.05v2.06C3.33 15.33 5.99 17 9 17z" fill="#34A853" />
      <path d="M4.64 10.64c-.16-.48-.25-1-.25-1.64s.09-1.16.25-1.64V5.3H2.05A8 8 0 001 9c0 1.3.31 2.52.86 3.6l2.2-1.96-.42.01z" fill="#FBBC05" />
      <path d="M9 3.58c1.14 0 2.17.39 2.98 1.16l2.23-2.23C12.86.8 11.1 0 9 0 5.99 0 3.33 1.67 2.05 4.1l2.59 2.03C5.25 4.95 6.97 3.58 9 3.58z" fill="#EA4335" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="currentColor" aria-hidden="true">
      <path fillRule="evenodd" clipRule="evenodd" d="M9 0C4.03 0 0 4.03 0 9c0 3.98 2.58 7.35 6.16 8.54.45.08.61-.2.61-.43V15.6c-2.5.54-3.03-1.2-3.03-1.2-.41-1.04-1-1.32-1-1.32-.82-.56.06-.55.06-.55.9.06 1.38.93 1.38.93.8 1.37 2.1.97 2.61.74.08-.58.31-.97.57-1.2-1.99-.23-4.09-1-4.09-4.43 0-.98.35-1.78.93-2.4-.09-.23-.4-1.14.09-2.37 0 0 .76-.24 2.49.93a8.64 8.64 0 014.53 0c1.73-1.17 2.49-.93 2.49-.93.49 1.23.18 2.14.09 2.37.58.62.93 1.42.93 2.4 0 3.44-2.1 4.2-4.1 4.42.32.28.61.83.61 1.67v2.47c0 .24.16.52.62.43A9.01 9.01 0 0018 9c0-4.97-4.03-9-9-9z" />
    </svg>
  );
}

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
  const [showPassword, setShowPassword] = useState(false);
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
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const result = await signUp({ email, password, full_name: fullName, workspace_name: workspaceName });
      if (result.verificationRequired) return;
      router.push('/dashboard');
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : String(submitError));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="suPage">
      <div className="suBgGlow" aria-hidden="true" />
      <div className="suBgGrid" aria-hidden="true" />

      <div className="suWrapper">
        {previewNotice}

        <main className="suOuter" aria-labelledby="su-heading">
          {/* Left branding panel */}
          <section className="suBrand" aria-label="Decoda Security">
            <div className="suBrandInner">
              <header className="suLogoHeader">
                <div className="suLogoIcon" aria-hidden="true">
                  <svg width="34" height="34" viewBox="0 0 34 34" fill="none">
                    <path d="M17 3L4 8.5v9c0 7.5 5.5 13 13 15 7.5-2 13-7.5 13-15v-9L17 3z" fill="rgba(59,130,246,0.18)" stroke="#3b82f6" strokeWidth="1.8" strokeLinejoin="round" />
                    <path d="M12 17l3.5 3.5L23 13" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <div className="suLogoText">
                  <span className="suLogoName">DECODA</span>
                  <span className="suLogoSub">SECURITY</span>
                </div>
              </header>

              <span className="suBadge" aria-label="Product: RWA Security Monitoring">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                  <path d="M6 1L1.5 3v4c0 2.5 2 4.3 4.5 5 2.5-.7 4.5-2.5 4.5-5V3L6 1z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
                </svg>
                RWA SECURITY MONITORING
              </span>

              <h1 className="suHeadline">
                Secure your RWA<br />
                operations with<br />
                <span className="suHeadlineAccent">confidence</span>
              </h1>

              <p className="suLede">
                Decoda Security gives you real-time visibility, risk insights, and audit-ready reporting so you can move fast without compromising on security or compliance.
              </p>

              <div className="suFeatureCards" role="list">
                {FEATURES.map((feature) => (
                  <div key={feature.title} className="suFeatureCard" role="listitem">
                    <div className="suFeatureCardIcon" aria-hidden="true">
                      <FeatureCardIcon type={feature.icon} />
                    </div>
                    <div>
                      <p className="suFeatureCardTitle">{feature.title}</p>
                      <p className="suFeatureCardDesc">{feature.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="suTrustSection">
              <p className="suTrustLine">Trusted by forward-thinking teams building the future of real-world assets.</p>
              <div className="suPartners" role="list">
                {PARTNERS.map((partner) => (
                  <span key={partner} className="suPartner" role="listitem">{partner}</span>
                ))}
              </div>
            </div>
          </section>

          {/* Right signup card */}
          <section className="suFormPanel" aria-labelledby="su-heading">
            <div className="suCard">
              <h2 id="su-heading" className="suCardTitle">Get started with Decoda Security</h2>
              <p className="suCardSubtitle">Create your workspace to start securing your RWA operations.</p>

              {formState.statusMessage ? (
                <div className="suAlert suAlertWarn" role="status">{formState.statusMessage}</div>
              ) : null}
              {formState.deploymentWarning ? (
                <div className="suAlert suAlertWarn" role="status">{formState.deploymentWarning}</div>
              ) : null}

              <form onSubmit={handleSubmit} noValidate>
                <div className="suFormGroup">
                  <label className="suLabel" htmlFor="su-full-name">FULL NAME</label>
                  <div className="suInputWrap">
                    <span className="suInputIcon" aria-hidden="true">
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                        <circle cx="8" cy="5" r="3" stroke="currentColor" strokeWidth="1.4" />
                        <path d="M2 14c0-3 2.7-5 6-5s6 2 6 5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                      </svg>
                    </span>
                    <input
                      id="su-full-name"
                      className="suInput suInputWithIcon"
                      type="text"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      autoComplete="name"
                      placeholder="Enter your full name"
                      required
                    />
                  </div>
                </div>

                <div className="suFormGroup">
                  <label className="suLabel" htmlFor="su-workspace-name">WORKSPACE NAME</label>
                  <div className="suInputWrap">
                    <span className="suInputIcon" aria-hidden="true">
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                        <rect x="2" y="4" width="12" height="10" rx="1" stroke="currentColor" strokeWidth="1.4" />
                        <path d="M5 4V3a1 1 0 011-1h4a1 1 0 011 1v1" stroke="currentColor" strokeWidth="1.4" />
                        <path d="M6 9h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                      </svg>
                    </span>
                    <input
                      id="su-workspace-name"
                      className="suInput suInputWithIcon"
                      type="text"
                      value={workspaceName}
                      onChange={(e) => setWorkspaceName(e.target.value)}
                      autoComplete="organization"
                      placeholder="Enter your workspace name"
                      required
                    />
                  </div>
                </div>

                <div className="suFormGroup">
                  <label className="suLabel" htmlFor="su-email">WORK EMAIL</label>
                  <div className="suInputWrap">
                    <span className="suInputIcon" aria-hidden="true">
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                        <rect x="2" y="4" width="12" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
                        <path d="M2 6.5l6 3.5 6-3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                      </svg>
                    </span>
                    <input
                      id="su-email"
                      className="suInput suInputWithIcon"
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      autoComplete="email"
                      placeholder="you@company.com"
                      required
                    />
                  </div>
                </div>

                <div className="suFormGroup">
                  <label className="suLabel" htmlFor="su-password">PASSWORD</label>
                  <div className="suInputWrap">
                    <span className="suInputIcon" aria-hidden="true">
                      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                        <rect x="4" y="7" width="8" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
                        <path d="M5.5 7V5.5a2.5 2.5 0 015 0V7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                      </svg>
                    </span>
                    <input
                      id="su-password"
                      className="suInput suInputWithIcon suInputWithToggle"
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      autoComplete="new-password"
                      placeholder="Create a strong password"
                      minLength={10}
                      required
                    />
                    <button
                      type="button"
                      className="suPasswordToggle"
                      onClick={() => setShowPassword((v) => !v)}
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
                  <p className="suInputHint">Minimum 10 characters with a mix of letters, numbers &amp; symbols.</p>
                </div>

                {error ? <div className="suAlert suAlertError" role="alert">{error}</div> : null}

                {!configLoading && !configured ? (
                  <div className="suAlert suAlertWarn" role="status">
                    Auth is disabled until this deployment exposes a valid API_URL.
                  </div>
                ) : null}

                <button type="submit" className="suSubmitBtn" disabled={formState.submitDisabled} aria-busy={loading}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" style={{ marginRight: '0.4rem' }}>
                    <path d="M8 2l1.5 3.5L13 7l-3.5 1.5L8 12l-1.5-3.5L3 7l3.5-1.5L8 2z" fill="currentColor" />
                  </svg>
                  {loading ? 'Creating workspace…' : 'Create workspace'}
                </button>
              </form>

              <div className="suDivider" aria-hidden="true">
                <span className="suDividerLine" />
                <span className="suDividerText">OR CONTINUE WITH</span>
                <span className="suDividerLine" />
              </div>

              <div className="suSocialRow">
                <button type="button" className="suSocialBtn" disabled aria-disabled="true" title="Google sign-up coming soon">
                  <GoogleIcon />
                  Continue with Google
                </button>
                <button type="button" className="suSocialBtn" disabled aria-disabled="true" title="GitHub sign-up coming soon">
                  <GitHubIcon />
                  Continue with GitHub
                </button>
              </div>

              <p className="suAccountRow">
                Already have an account?{' '}
                <Link href="/sign-in" className="suLink">Sign in</Link>
              </p>

              <p className="suPrivacyNote">
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true">
                  <path d="M6.5 1L1 3.5V7c0 3 2 4.8 5.5 5.8C10 11.8 12 10 12 7V3.5L6.5 1z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
                </svg>
                We respect your privacy. Your data is encrypted and never shared.
              </p>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
