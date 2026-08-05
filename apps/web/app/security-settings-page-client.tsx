'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { safeInternalReturnTo } from 'app/safe-internal-return-to';

export default function SecuritySettingsPageClient() {
  const {
    authHeaders,
    user,
    enrollMfa,
    confirmMfaEnrollment,
    disableMfa,
    csrfReady,
    refreshCsrfToken,
  } = usePilotAuth();
  const searchParams = useSearchParams();
  // When Screen 8 sends the operator here to complete an MFA step-up, it passes a
  // return_to back to the SAME response action (with the Review All filter + incident
  // scope preserved). Surface a clear way back once MFA is satisfied.
  const returnTo = safeInternalReturnTo(searchParams.get('return_to'));
  const resolvedWorkspace = user?.current_workspace ?? user?.memberships?.[0]?.workspace ?? null;

  const [submitting, setSubmitting] = useState(false);
  // Scoped status/error lines so a failure under one control is never echoed beneath an
  // unrelated one (e.g. a sign-out error must never appear under MFA enrollment).
  const [mfaStatus, setMfaStatus] = useState('');
  const [mfaError, setMfaError] = useState('');
  const [sessionStatus, setSessionStatus] = useState('');
  const [sessionError, setSessionError] = useState('');
  const [apiKeyStatus, setApiKeyStatus] = useState('');
  // Secure-session (anti-CSRF) initialization failure, shown next to the commands it blocks.
  const [secureSessionError, setSecureSessionError] = useState('');
  const [retryingSecureSession, setRetryingSecureSession] = useState(false);

  const [mfaSetup, setMfaSetup] = useState<{ enrollment_id: string | null; otpauth_uri: string; secret: string | null; expires_at: string | null } | null>(null);
  // The manual setup key is only rendered when the operator deliberately reveals it,
  // so a plaintext TOTP secret is never left sitting on the page longer than needed.
  const [revealSetupKey, setRevealSetupKey] = useState(false);
  // Guards against a double-fire of Enroll (rapid double-click / duplicate handler)
  // creating a second pending enrollment and rotating the secret out from under the
  // one the operator just scanned.
  const enrollInFlight = useRef(false);
  const [mfaCode, setMfaCode] = useState('');
  const [disableCode, setDisableCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [recoveryCodesAcknowledged, setRecoveryCodesAcknowledged] = useState(false);
  const [apiKeys, setApiKeys] = useState<Array<{ id: string; label: string; secret_prefix: string; revoked_at: string | null }>>([]);
  const [apiKeyLabel, setApiKeyLabel] = useState('');
  const [revealedSecret, setRevealedSecret] = useState('');
  const canManageApiKeys = ['owner', 'admin', 'workspace_owner', 'workspace_admin'].includes(String((user as any)?.role ?? user?.memberships?.[0]?.role ?? ''));

  // A state-changing request is only safe to send once the anti-CSRF token has been
  // bootstrapped; until then every command stays disabled (fail-closed) rather than
  // firing a request the backend will (correctly) reject with a CSRF error.
  const secureSessionReady = csrfReady;
  const commandsDisabled = submitting || !secureSessionReady;

  function isSecureSessionFailure(value: unknown): boolean {
    const message = value instanceof Error ? value.message : String(value ?? '');
    return /csrf|secure session/i.test(message);
  }

  // Confirm the anti-CSRF token is bootstrapped for this authenticated session before any
  // mutation is possible. The provider fetches it on session restore; this one-shot backup
  // surfaces a focused, retryable error if that initial bootstrap did not land, instead of
  // leaving the operator on an indefinite "Preparing secure session" state with no recourse.
  const bootstrapAttempted = useRef(false);
  useEffect(() => {
    if (!user || csrfReady || bootstrapAttempted.current) {
      return;
    }
    bootstrapAttempted.current = true;
    void (async () => {
      const token = await refreshCsrfToken();
      if (!token) {
        setSecureSessionError('Could not initialize a secure session. Check your connection and retry.');
      }
    })();
  }, [user, csrfReady, refreshCsrfToken]);

  async function retrySecureSession() {
    setRetryingSecureSession(true);
    setSecureSessionError('');
    try {
      const token = await refreshCsrfToken();
      if (!token) {
        setSecureSessionError('Could not initialize a secure session. Check your connection and retry.');
      }
    } catch {
      setSecureSessionError('Could not initialize a secure session. Check your connection and retry.');
    } finally {
      setRetryingSecureSession(false);
    }
  }

  async function loadApiKeys() {
    const response = await fetch('/api/workspace/api-keys', { headers: authHeaders() });
    if (!response.ok) return;
    const payload = await response.json();
    setApiKeys(Array.isArray(payload.items) ? payload.items : []);
  }

  async function signOutAllSessions() {
    setSubmitting(true);
    setSessionStatus('');
    setSessionError('');
    try {
      const response = await fetch('/api/auth/signout-all', { method: 'POST', headers: authHeaders() });
      if (response.ok) {
        setSessionStatus('All active sessions were signed out.');
      } else {
        const payload = await response.json().catch(() => ({}));
        if (isSecureSessionFailure(payload?.detail) || isSecureSessionFailure(payload?.code)) {
          setSecureSessionError('Your secure session expired. Retry the secure session, then sign out all sessions again.');
        } else {
          setSessionError(typeof payload?.detail === 'string' ? payload.detail : 'Unable to sign out all sessions.');
        }
      }
    } catch {
      setSessionError('Unable to sign out all sessions.');
    } finally {
      setSubmitting(false);
    }
  }

  async function startMfaEnrollment(options?: { regenerate?: boolean }) {
    // Single-flight: never send a second Enroll while one is in flight. A duplicate
    // request would mint a new pending secret and invalidate the one being scanned.
    if (enrollInFlight.current) {
      return;
    }
    enrollInFlight.current = true;
    setSubmitting(true);
    setMfaStatus('');
    setMfaError('');
    try {
      const enrollment = await enrollMfa();
      setMfaSetup(enrollment);
      setRevealSetupKey(false);
      setMfaCode('');
      setRecoveryCodes([]);
      setMfaStatus(options?.regenerate
        ? 'A new setup key was generated. Your previous authenticator entry no longer works — remove it and scan this new key, then enter a verification code to finish.'
        : 'Scan the setup key in your authenticator app, then enter a verification code to finish.');
    } catch (error) {
      // Keep the failure honest and specific: a blocked enrollment must never read as if
      // a challenge/email was delivered, and a secure-session failure is distinct from an
      // enrollment failure so the operator retries the right thing.
      if (isSecureSessionFailure(error)) {
        setSecureSessionError('Your secure session expired. Retry the secure session, then enroll MFA again.');
      } else {
        setMfaError(error instanceof Error ? `MFA enrollment failed: ${error.message}` : 'MFA enrollment failed.');
      }
    } finally {
      enrollInFlight.current = false;
      setSubmitting(false);
    }
  }

  async function confirmMfa() {
    setSubmitting(true);
    setMfaStatus('');
    setMfaError('');
    try {
      const result = await confirmMfaEnrollment(mfaCode, mfaSetup?.enrollment_id ?? null);
      setRecoveryCodes(result.recovery_codes);
      setRecoveryCodesAcknowledged(false);
      // Clear every trace of the pending secret from client state on success.
      setMfaSetup(null);
      setRevealSetupKey(false);
      setMfaCode('');
      setMfaStatus('MFA enabled. Save your recovery codes now.');
    } catch (error) {
      if (isSecureSessionFailure(error)) {
        setSecureSessionError('Your secure session expired. Retry the secure session, then verify again.');
      } else {
        // The backend returns a generic message; never surface secret/verification internals.
        setMfaError(error instanceof Error ? `MFA verification failed: ${error.message}` : 'MFA verification failed.');
      }
    } finally {
      setSubmitting(false);
    }
  }

  function copyRecoveryCodes() {
    if (recoveryCodes.length === 0) {
      return;
    }
    void navigator.clipboard.writeText(recoveryCodes.join('\n'));
    setMfaStatus('Recovery codes copied. Store them in a secure password vault.');
  }

  async function disableMfaFlow() {
    setSubmitting(true);
    setMfaStatus('');
    setMfaError('');
    try {
      await disableMfa(disableCode);
      setDisableCode('');
      setRecoveryCodes([]);
      setMfaStatus('MFA disabled for this account.');
    } catch (error) {
      if (isSecureSessionFailure(error)) {
        setSecureSessionError('Your secure session expired. Retry the secure session, then disable MFA again.');
      } else {
        setMfaError(error instanceof Error ? `Unable to disable MFA: ${error.message}` : 'Unable to disable MFA.');
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function createApiKey() {
    setSubmitting(true);
    setApiKeyStatus('');
    const response = await fetch('/api/workspace/api-keys', { method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ label: apiKeyLabel }) });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) setApiKeyStatus(payload.detail ?? 'Unable to create API key.');
    else {
      setRevealedSecret(payload.secret ?? '');
      setApiKeyLabel('');
      setApiKeyStatus('API key created. Secret is shown once—copy it now.');
      await loadApiKeys();
    }
    setSubmitting(false);
  }

  // Rendered NEXT TO each set of commands so the secure-session state is always adjacent
  // to the control it governs: a pending notice while the anti-CSRF token bootstraps, or a
  // focused retry affordance if initialization failed.
  const secureSessionNotice = secureSessionError ? (
    <div role="alert">
      <p className="statusLine">{secureSessionError}</p>
      <div className="buttonRow">
        <button type="button" onClick={() => void retrySecureSession()} disabled={retryingSecureSession}>
          {retryingSecureSession ? 'Retrying…' : 'Retry secure session'}
        </button>
      </div>
    </div>
  ) : !secureSessionReady ? (
    <p className="statusLine" role="status">Preparing secure session…</p>
  ) : null;

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Workspace</p><h2>API keys</h2></div></div>
        <article className="dataCard">
          <p className="muted">Create workspace API keys for automation. Secrets are revealed once and never stored in raw form.</p>
          {canManageApiKeys ? (
            <>
              <div className="buttonRow">
                <input placeholder="Key label" value={apiKeyLabel} onChange={(event) => setApiKeyLabel(event.target.value)} />
                <button type="button" onClick={() => void createApiKey()} disabled={commandsDisabled || apiKeyLabel.trim().length < 2}>Create key</button>
                <button type="button" onClick={() => void loadApiKeys()} disabled={submitting}>Refresh</button>
              </div>
              {secureSessionNotice}
              {revealedSecret ? <pre>{revealedSecret}</pre> : null}
              {apiKeyStatus ? <p className="statusLine">{apiKeyStatus}</p> : null}
              <ul>{apiKeys.map((key) => (
                <li key={key.id}>
                  <code>{key.secret_prefix}…</code> {key.label} {key.revoked_at ? '(revoked)' : ''}
                  <button type="button" disabled={commandsDisabled} onClick={async () => { await fetch(`/api/workspace/api-keys/${key.id}/rotate`, { method: 'POST', headers: authHeaders() }); await loadApiKeys(); }}>Rotate</button>
                  <button type="button" disabled={commandsDisabled} onClick={async () => { await fetch(`/api/workspace/api-keys/${key.id}`, { method: 'DELETE', headers: authHeaders() }); await loadApiKeys(); }}>Revoke</button>
                </li>
              ))}</ul>
            </>
          ) : <p className="muted">Owner or admin role is required to manage workspace API keys.</p>}
        </article>
      </section>
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Settings</p>
            <h1>Security settings</h1>
            <p className="lede">Manage workspace security controls and account session hygiene.</p>
          </div>
        </div>

        <div className="buttonRow">
          <Link href="/settings" prefetch={false}>← Back to workspace settings</Link>
        </div>
        {returnTo ? (
          <article className="dataCard" style={{ marginTop: '0.75rem' }}>
            <p className="muted" style={{ margin: '0 0 0.5rem' }}>
              You were sent here to complete MFA before approving a response action.
              {user?.mfa_enabled ? ' Once this session is MFA-verified you can return and approve it.' : ' Enroll MFA below, then return to approve it.'}
            </p>
            <div className="buttonRow">
              <Link href={returnTo} prefetch={false} className="btn btn-primary">Return to the response action</Link>
            </div>
          </article>
        ) : null}
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Account</p><h2>Multi-factor authentication</h2></div></div>
        <article className="dataCard">
          <p className="muted">Status: {user?.mfa_enabled ? 'Enabled' : 'Disabled'}.</p>
          {!user?.mfa_enabled ? (
            <div className="buttonRow">
              <button type="button" onClick={() => void startMfaEnrollment()} disabled={commandsDisabled}>Enroll MFA</button>
            </div>
          ) : null}
          {secureSessionNotice}
          {mfaSetup ? (
            <div>
              <p className="muted">
                Add this account to your authenticator app, then enter the 6-digit code it shows to finish.
                The full setup URI is not kept on the page — use the button to open it, or reveal the manual key only if you need to type it.
              </p>
              <div className="buttonRow">
                <a className="btn" href={mfaSetup.otpauth_uri}>Open in authenticator app</a>
                <button type="button" onClick={() => setRevealSetupKey((value) => !value)}>
                  {revealSetupKey ? 'Hide manual setup key' : 'Reveal manual setup key'}
                </button>
              </div>
              {revealSetupKey && mfaSetup.secret ? (
                <p className="muted">Manual setup key: <code data-testid="mfa-manual-key">{mfaSetup.secret}</code></p>
              ) : null}
              <label className="label">Verification code</label>
              <input value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} inputMode="numeric" />
              <div className="buttonRow">
                <button type="button" onClick={() => void confirmMfa()} disabled={commandsDisabled || mfaCode.trim().length < 6}>Confirm MFA</button>
                <button type="button" onClick={() => void startMfaEnrollment({ regenerate: true })} disabled={commandsDisabled}>Regenerate setup key</button>
              </div>
              <p className="muted">Regenerating creates a new secret and invalidates the previous authenticator entry, so you must remove the old one and scan the new key.</p>
            </div>
          ) : null}
          {user?.mfa_enabled ? (
            <div>
              <label className="label">Current TOTP code</label>
              <input value={disableCode} onChange={(event) => setDisableCode(event.target.value)} inputMode="numeric" />
              <div className="buttonRow">
                <button type="button" onClick={() => void disableMfaFlow()} disabled={commandsDisabled || disableCode.trim().length < 6}>Disable MFA</button>
              </div>
            </div>
          ) : null}
          {recoveryCodes.length > 0 ? (
            <div>
              <p className="muted">Recovery codes (shown once):</p>
              <pre>{recoveryCodes.join('\n')}</pre>
              <div className="buttonRow">
                <button type="button" onClick={copyRecoveryCodes}>Copy recovery codes</button>
                <button type="button" onClick={() => { setRecoveryCodes([]); setRecoveryCodesAcknowledged(true); }}>
                  I saved these recovery codes
                </button>
              </div>
              <p className="muted">These recovery codes are displayed only once. If lost, disable and re-enroll MFA to generate new codes.</p>
            </div>
          ) : null}
          {recoveryCodesAcknowledged ? <p className="statusLine">Recovery codes acknowledged and cleared from this screen.</p> : null}
          {mfaError ? <p className="statusLine" role="alert">{mfaError}</p> : null}
          {mfaStatus ? <p className="statusLine">{mfaStatus}</p> : null}
        </article>
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Workspace</p><h2>Access model</h2></div></div>
        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Current workspace</p>
            <h3>{resolvedWorkspace?.name ?? 'No workspace available yet'}</h3>
            <p className="muted">{resolvedWorkspace ? 'Decoda enforces workspace-scoped roles, audit logging, and least-privilege access controls.' : 'Create or join workspace to enable security controls and monitoring.'}</p>{!resolvedWorkspace ? <div className="buttonRow"><Link href="/workspaces" prefetch={false}>Create or join workspace</Link></div> : null}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Role protections</p>
            <p className="muted">Owners keep administrative continuity. Admin and analyst roles support operational execution with scoped permissions.</p>
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Live mode safeguards</p>
            <p className="muted">Signed API requests and authenticated workspace context keep live operations isolated from demo and fallback data.</p>
          </article>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Account</p><h2>Session controls</h2></div></div>
        <article className="dataCard">
          <p className="muted">If credentials were rotated or a device was lost, sign out all active sessions for this account.</p>
          <div className="buttonRow">
            <button type="button" onClick={() => void signOutAllSessions()} disabled={commandsDisabled}>
              Sign out all sessions
            </button>
          </div>
          {secureSessionNotice}
          {sessionError ? <p className="statusLine" role="alert">{sessionError}</p> : null}
          {sessionStatus ? <p className="statusLine">{sessionStatus}</p> : null}
        </article>
      </section>
    </main>
  );
}
