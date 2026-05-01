'use client';

import Link from 'next/link';
import { useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export default function SecuritySettingsPageClient() {
  const { authHeaders, user, enrollMfa, confirmMfaEnrollment, disableMfa } = usePilotAuth();
  const resolvedWorkspace = user?.current_workspace ?? user?.memberships?.[0]?.workspace ?? null;
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [mfaSetup, setMfaSetup] = useState<{ otpauth_uri: string; secret: string | null } | null>(null);
  const [mfaCode, setMfaCode] = useState('');
  const [disableCode, setDisableCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [recoveryCodesAcknowledged, setRecoveryCodesAcknowledged] = useState(false);
  const [apiKeys, setApiKeys] = useState<Array<{ id: string; label: string; secret_prefix: string; revoked_at: string | null }>>([]);
  const [apiKeyLabel, setApiKeyLabel] = useState('');
  const [revealedSecret, setRevealedSecret] = useState('');
  const canManageApiKeys = ['owner', 'admin', 'workspace_owner', 'workspace_admin'].includes(String((user as any)?.role ?? user?.memberships?.[0]?.role ?? ''));

  async function loadApiKeys() {
    const response = await fetch('/api/workspace/api-keys', { headers: authHeaders() });
    if (!response.ok) return;
    const payload = await response.json();
    setApiKeys(Array.isArray(payload.items) ? payload.items : []);
  }

  async function signOutAllSessions() {
    setSubmitting(true);
    const response = await fetch('/api/auth/signout-all', { method: 'POST', headers: authHeaders() });
    setMessage(response.ok ? 'All active sessions were signed out.' : 'Unable to sign out all sessions.');
    setSubmitting(false);
  }

  async function startMfaEnrollment() {
    setSubmitting(true);
    setMessage('');
    try {
      const enrollment = await enrollMfa();
      setMfaSetup(enrollment);
      setRecoveryCodes([]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to start MFA enrollment.');
    } finally {
      setSubmitting(false);
    }
  }

  async function confirmMfa() {
    setSubmitting(true);
    setMessage('');
    try {
      const result = await confirmMfaEnrollment(mfaCode);
      setRecoveryCodes(result.recovery_codes);
      setRecoveryCodesAcknowledged(false);
      setMfaSetup(null);
      setMfaCode('');
      setMessage('MFA enabled. Save your recovery codes now.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to confirm MFA enrollment.');
    } finally {
      setSubmitting(false);
    }
  }

  function copyRecoveryCodes() {
    if (recoveryCodes.length === 0) {
      return;
    }
    void navigator.clipboard.writeText(recoveryCodes.join('\n'));
    setMessage('Recovery codes copied. Store them in a secure password vault.');
  }

  async function disableMfaFlow() {
    setSubmitting(true);
    setMessage('');
    try {
      await disableMfa(disableCode);
      setDisableCode('');
      setRecoveryCodes([]);
      setMessage('MFA disabled for this account.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to disable MFA.');
    } finally {
      setSubmitting(false);
    }
  }
  async function createApiKey() {
    setSubmitting(true);
    setMessage('');
    const response = await fetch('/api/workspace/api-keys', { method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ label: apiKeyLabel }) });
    const payload = await response.json();
    if (!response.ok) setMessage(payload.detail ?? 'Unable to create API key.');
    else {
      setRevealedSecret(payload.secret ?? '');
      setApiKeyLabel('');
      setMessage('API key created. Secret is shown once—copy it now.');
      await loadApiKeys();
    }
    setSubmitting(false);
  }

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
                <button type="button" onClick={() => void createApiKey()} disabled={submitting || apiKeyLabel.trim().length < 2}>Create key</button>
                <button type="button" onClick={() => void loadApiKeys()} disabled={submitting}>Refresh</button>
              </div>
              {revealedSecret ? <pre>{revealedSecret}</pre> : null}
              <ul>{apiKeys.map((key) => (
                <li key={key.id}>
                  <code>{key.secret_prefix}…</code> {key.label} {key.revoked_at ? '(revoked)' : ''}
                  <button type="button" onClick={async () => { await fetch(`/api/workspace/api-keys/${key.id}/rotate`, { method: 'POST', headers: authHeaders() }); await loadApiKeys(); }}>Rotate</button>
                  <button type="button" onClick={async () => { await fetch(`/api/workspace/api-keys/${key.id}`, { method: 'DELETE', headers: authHeaders() }); await loadApiKeys(); }}>Revoke</button>
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
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Account</p><h2>Multi-factor authentication</h2></div></div>
        <article className="dataCard">
          <p className="muted">Status: {user?.mfa_enabled ? 'Enabled' : 'Disabled'}.</p>
          {!user?.mfa_enabled ? (
            <div className="buttonRow">
              <button type="button" onClick={() => void startMfaEnrollment()} disabled={submitting}>Enroll MFA</button>
            </div>
          ) : null}
          {mfaSetup ? (
            <div>
              <p className="muted">Scan this URI in your authenticator app:</p>
              <pre>{mfaSetup.otpauth_uri}</pre>
              {mfaSetup.secret ? <p className="muted">Secret: <code>{mfaSetup.secret}</code></p> : null}
              <label className="label">Verification code</label>
              <input value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} inputMode="numeric" />
              <div className="buttonRow">
                <button type="button" onClick={() => void confirmMfa()} disabled={submitting || mfaCode.trim().length < 6}>Confirm MFA</button>
              </div>
            </div>
          ) : null}
          {user?.mfa_enabled ? (
            <div>
              <label className="label">Current TOTP code</label>
              <input value={disableCode} onChange={(event) => setDisableCode(event.target.value)} inputMode="numeric" />
              <div className="buttonRow">
                <button type="button" onClick={() => void disableMfaFlow()} disabled={submitting || disableCode.trim().length < 6}>Disable MFA</button>
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
          {message ? <p className="statusLine">{message}</p> : null}
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
            <button type="button" onClick={() => void signOutAllSessions()} disabled={submitting}>
              Sign out all sessions
            </button>
          </div>
          {message ? <p className="statusLine">{message}</p> : null}
        </article>
      </section>
    </main>
  );
}
