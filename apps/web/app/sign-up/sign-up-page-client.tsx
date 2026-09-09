'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { resolveAuthFormState } from '../auth-form-state';
import { acceptInvitationPath, invitationSignInHref } from '../invitation-routing';
import {
  ACCOUNT_CREATED_BODY,
  ACCOUNT_CREATED_HEADLINE,
  APPROVAL_ONLY_BODY,
  APPROVAL_ONLY_HEADLINE,
  CHECKING_HEADLINE,
  INVITATION_UNAVAILABLE,
  INVITED_ACCOUNT_EXISTS,
  INVITED_CONFIRM_PASSWORD_LABEL,
  INVITED_EMAIL_HINT,
  INVITED_EMAIL_LABEL,
  INVITED_HEADLINE,
  INVITED_PASSWORD_MISMATCH,
  INVITED_SUBMIT_CTA,
  INVITED_SUBTITLE,
  REQUEST_PILOT_CTA,
  SIGN_IN_CTA,
  SIGN_IN_PROMPT,
  resolveInvitationToken,
} from '../signup-access';
import { usePilotAuth } from 'app/pilot-auth-context';

/**
 * What the backend says about the invitation in the URL.
 *
 * `checking` renders no form: an unverified token must never produce a usable
 * account-creation flow, not even for the moment before the answer arrives. The
 * only state that renders inputs is `invited`, and the address it shows is the
 * one the backend returned.
 */
type Invitation = {
  email: string;
  company_name: string | null;
  expires_at: string | null;
  evaluation_days: number | null;
};

type GateState =
  | { kind: 'checking' }
  | { kind: 'approval_required'; message: string | null }
  | { kind: 'invited'; invitation: Invitation };

function invitationRefusalMessage(payload: Record<string, unknown>): string {
  if (typeof payload.message === 'string' && payload.message.trim()) {
    return payload.message;
  }
  const detail = payload.detail;
  if (detail && typeof detail === 'object' && typeof (detail as Record<string, unknown>).message === 'string') {
    return String((detail as Record<string, unknown>).message);
  }
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }
  return INVITATION_UNAVAILABLE;
}

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

export default function SignUpPageClient({ previewNotice }: { previewNotice?: React.ReactNode }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const {
    apiTimeoutMs,
    configLoading,
    configured,
    liveModeEnabled,
    runtimeConfigDiagnostic,
    runtimeConfigSource,
    signUpWithInvitation,
    apiUrl,
    isAuthenticated,
    loading: authLoading,
  } = usePilotAuth();

  // The token is a lookup key, never a grant. It authorizes nothing on its own:
  // the backend resolves it by hash and decides whether it is still approved,
  // unexpired, and unused, and which address it belongs to.
  const invitationToken = useMemo(() => resolveInvitationToken(searchParams), [searchParams]);

  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [accountCreated, setAccountCreated] = useState(false);
  // Fail closed: with no token there is nothing to check and nothing to offer.
  const [gate, setGate] = useState<GateState>(
    invitationToken ? { kind: 'checking' } : { kind: 'approval_required', message: null },
  );

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
      // An account that already exists has no signup to do. With an invitation
      // in hand it goes to the one activation path; otherwise to the product,
      // where the Pilot access gate states its real access.
      router.replace(invitationToken ? acceptInvitationPath(invitationToken) : '/dashboard');
    }
  }, [authLoading, invitationToken, isAuthenticated, router]);

  useEffect(() => {
    if (!invitationToken) {
      setGate({ kind: 'approval_required', message: null });
      return;
    }
    let cancelled = false;
    setGate({ kind: 'checking' });
    void (async () => {
      try {
        const response = await fetch(`/api/pilot-invitations?token=${encodeURIComponent(invitationToken)}`, {
          cache: 'no-store',
        });
        const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
        if (cancelled) return;
        if (!response.ok || payload.valid !== true || !payload.invitation) {
          // The backend's own reason, verbatim — expired, already used, or not
          // valid at all — and the approval-only state, not a signup form.
          setGate({ kind: 'approval_required', message: invitationRefusalMessage(payload) });
          return;
        }
        setGate({ kind: 'invited', invitation: payload.invitation as Invitation });
      } catch {
        // An unreadable answer is not an approved one.
        if (!cancelled) {
          setGate({ kind: 'approval_required', message: INVITATION_UNAVAILABLE });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [invitationToken]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading) return;
    // Belt and braces: the submit control only exists in the invited state, and
    // the handler refuses to run outside it too.
    if (gate.kind !== 'invited') {
      setError('Decoda reviews and approves each Pilot evaluation before an account can be created.');
      return;
    }
    if (password !== confirmPassword) {
      setError(INVITED_PASSWORD_MISMATCH);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      // Two fields, and only two. The approved address is NOT sent: the backend
      // reads it from the invitation the token resolves to, so nothing this page
      // submits — and nothing a browser could edit into it — chooses whose
      // account is created, on which plan, in which organization.
      const result = await signUpWithInvitation({
        token: invitationToken,
        password,
        full_name: fullName,
      });
      if (result.accountExists) {
        // The address already has an account. Signup will not touch it — an
        // invitation is not a password reset — so continue on the path that can
        // actually work, with the invitation still in hand. The reason is stated
        // as well as acted on, so a slow redirect is not a silent one.
        setError(INVITED_ACCOUNT_EXISTS);
        router.replace(invitationSignInHref(invitationToken));
        return;
      }
      // The account and its session now exist; the evaluation does not yet.
      // Activation is the accept page's job, and it re-checks the invitation
      // against this session before provisioning anything.
      setAccountCreated(true);
      router.replace(acceptInvitationPath(invitationToken));
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

          {/* Right panel: approval-only unless the backend resolves an invitation */}
          <section className="suFormPanel" aria-labelledby="su-heading">
            <div className="suCard">
              {gate.kind === 'checking' ? (
                <>
                  <h2 id="su-heading" className="suCardTitle">{CHECKING_HEADLINE}</h2>
                  <p className="suCardSubtitle" role="status">
                    We are checking the invitation on this link with Decoda.
                  </p>
                </>
              ) : null}

              {gate.kind === 'approval_required' ? (
                <>
                  {/* No form, no workspace-creation control, and no social
                      buttons. An unapproved visitor is offered the two things
                      that are actually true: apply, or sign in. */}
                  <h2 id="su-heading" className="suCardTitle">{APPROVAL_ONLY_HEADLINE}</h2>
                  <p className="suCardSubtitle">{APPROVAL_ONLY_BODY}</p>

                  {gate.message ? (
                    <div className="suAlert suAlertWarn" role="status">{gate.message}</div>
                  ) : null}

                  <Link href="/request-pilot" className="suSubmitBtn" prefetch={false}>
                    {REQUEST_PILOT_CTA}
                  </Link>

                  <p className="suAccountRow">
                    {SIGN_IN_PROMPT}{' '}
                    <Link href="/sign-in" className="suLink" prefetch={false}>{SIGN_IN_CTA}</Link>
                  </p>
                </>
              ) : null}

              {gate.kind === 'invited' && accountCreated ? (
                <>
                  <h2 id="su-heading" className="suCardTitle">{ACCOUNT_CREATED_HEADLINE}</h2>
                  <p className="suCardSubtitle">{ACCOUNT_CREATED_BODY}</p>
                  <Link
                    href={acceptInvitationPath(invitationToken)}
                    className="suSubmitBtn"
                    prefetch={false}
                  >
                    Open your invitation
                  </Link>
                </>
              ) : null}

              {gate.kind === 'invited' && !accountCreated ? (
                <>
                  <h2 id="su-heading" className="suCardTitle">{INVITED_HEADLINE}</h2>
                  <p className="suCardSubtitle">{INVITED_SUBTITLE}</p>

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
                      <label className="suLabel" htmlFor="su-email">{INVITED_EMAIL_LABEL}</label>
                      <div className="suInputWrap">
                        <span className="suInputIcon" aria-hidden="true">
                          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                            <rect x="2" y="4" width="12" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
                            <path d="M2 6.5l6 3.5 6-3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                          </svg>
                        </span>
                        {/* Read-only, and read-only is not the control that
                            matters: the address posted is gate.invitation.email
                            and the backend re-compares it against the approved
                            one when the invitation is accepted. Editing this
                            field in a browser claims nobody else's invitation. */}
                        <input
                          id="su-email"
                          className="suInput suInputWithIcon suInputLocked"
                          type="email"
                          value={gate.invitation.email}
                          autoComplete="email"
                          readOnly
                          aria-readonly="true"
                        />
                      </div>
                      <p className="suInputHint">{INVITED_EMAIL_HINT}</p>
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

                    <div className="suFormGroup">
                      <label className="suLabel" htmlFor="su-confirm-password">{INVITED_CONFIRM_PASSWORD_LABEL}</label>
                      <div className="suInputWrap">
                        <span className="suInputIcon" aria-hidden="true">
                          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                            <rect x="4" y="7" width="8" height="7" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
                            <path d="M5.5 7V5.5a2.5 2.5 0 015 0V7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                          </svg>
                        </span>
                        {/* A typo in a password nobody can see is the one signup
                            failure that locks an approved applicant out of their
                            own evaluation. The comparison is local — it guards a
                            mistake, not an attacker — and the backend enforces
                            the policy regardless. */}
                        <input
                          id="su-confirm-password"
                          className="suInput suInputWithIcon"
                          type={showPassword ? 'text' : 'password'}
                          value={confirmPassword}
                          onChange={(e) => setConfirmPassword(e.target.value)}
                          autoComplete="new-password"
                          placeholder="Re-enter your password"
                          minLength={10}
                          required
                          aria-invalid={Boolean(confirmPassword) && confirmPassword !== password}
                        />
                      </div>
                      {confirmPassword && confirmPassword !== password ? (
                        <p className="suInputHint" role="alert">{INVITED_PASSWORD_MISMATCH}</p>
                      ) : null}
                    </div>

                    {error ? <div className="suAlert suAlertError" role="alert">{error}</div> : null}

                    {!configLoading && !configured ? (
                      <div className="suAlert suAlertWarn" role="status">
                        Auth is disabled until this deployment exposes a valid API_URL.
                      </div>
                    ) : null}

                    <button type="submit" className="suSubmitBtn" disabled={formState.submitDisabled} aria-busy={loading}>
                      {loading ? 'Creating account…' : INVITED_SUBMIT_CTA}
                    </button>
                  </form>

                  {/* Carries the SAME invitation to /sign-in — both as `invite`,
                      so that screen knows what the sign-in is for and keeps its
                      own "Create one" link inside this flow, and as `next`, so
                      authenticating lands on activation. A bare /sign-in here is
                      how the invitation gets dropped mid-flow. */}
                  <p className="suAccountRow">
                    Already have an account?{' '}
                    <Link href={invitationSignInHref(invitationToken)} className="suLink" prefetch={false}>
                      Sign in to accept
                    </Link>
                  </p>
                </>
              ) : null}

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
