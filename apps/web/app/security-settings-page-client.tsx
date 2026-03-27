'use client';

import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

type SessionRecord = {
  id: string;
  auth_mode: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  ip_address: string | null;
  user_agent: string | null;
};

export default function SecuritySettingsPageClient() {
  const { apiUrl, authHeaders, loading, user, refreshUser, signOut } = usePilotAuth();
  const [status, setStatus] = useState<string | null>(null);
  const [enrollUri, setEnrollUri] = useState<string | null>(null);
  const [enrollCode, setEnrollCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [disableCode, setDisableCode] = useState('');
  const [disablePassword, setDisablePassword] = useState('');
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [busy, setBusy] = useState(false);

  const apiCall = useCallback(async (path: string, init?: RequestInit) => {
    return fetch(`${apiUrl}${path}`, {
      cache: 'no-store',
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        ...authHeaders(),
      },
    });
  }, [apiUrl, authHeaders]);

  const loadSessions = useCallback(async () => {
    if (!apiUrl) return;
    const response = await apiCall('/auth/sessions');
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    setSessions(payload.sessions ?? []);
  }, [apiCall, apiUrl]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  async function beginMfaEnrollment() {
    setBusy(true);
    setStatus(null);
    setRecoveryCodes([]);
    const response = await apiCall('/auth/mfa/enroll', { method: 'POST' });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      setStatus(payload.detail ?? 'Unable to start MFA enrollment.');
      setBusy(false);
      return;
    }
    const payload = await response.json();
    setEnrollUri(payload.otpauth_uri ?? null);
    setStatus('Enrollment started. Add this account to your authenticator app and confirm with a code.');
    setBusy(false);
  }

  async function confirmEnrollment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus(null);
    const response = await apiCall('/auth/mfa/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: enrollCode }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setStatus(payload.detail ?? 'Unable to confirm MFA enrollment.');
      setBusy(false);
      return;
    }
    setRecoveryCodes(payload.recovery_codes ?? []);
    setEnrollCode('');
    setEnrollUri(null);
    await signOut();
    setStatus('MFA enabled. Your other sessions were signed out for security. Sign in again and store your recovery codes.');
    setBusy(false);
  }

  async function disableMfa(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus(null);
    const response = await apiCall('/auth/mfa/disable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: disableCode, password: disablePassword }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setStatus(payload.detail ?? 'Unable to disable MFA.');
      setBusy(false);
      return;
    }
    setDisableCode('');
    setDisablePassword('');
    await signOut();
    setStatus('MFA disabled. All active sessions were revoked. Sign in again to continue.');
    setBusy(false);
  }

  async function revokeSession(sessionId: string) {
    setBusy(true);
    const response = await apiCall('/auth/sessions/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      setStatus(payload.detail ?? 'Unable to revoke session.');
      setBusy(false);
      return;
    }
    setStatus('Session revoked.');
    await loadSessions();
    setBusy(false);
  }

  async function signOutAll() {
    setBusy(true);
    await apiCall('/auth/signout-all', { method: 'POST' });
    await signOut();
    setBusy(false);
  }

  const activeSessions = useMemo(() => sessions.filter((session) => !session.revoked_at), [sessions]);

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Security settings</p>
            <h1>Account security and session controls</h1>
            <p className="lede">Manage multi-factor authentication, recovery codes, and active sessions for your account.</p>
          </div>
        </div>
        {status ? <p className="statusLine">{status}</p> : null}
        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Multi-factor authentication</p>
            <h2>{user?.mfa_enabled ? 'Enabled' : 'Not enabled'}</h2>
            {!user?.mfa_enabled ? (
              <>
                <p className="muted">Protect your account with TOTP-based MFA and one-time recovery codes.</p>
                <button type="button" onClick={() => void beginMfaEnrollment()} disabled={busy || loading}>Start enrollment</button>
                {enrollUri ? (
                  <>
                    <p className="muted">Authenticator setup URI (copy into your authenticator app if QR render is unavailable):</p>
                    <code style={{ display: 'block', overflowWrap: 'anywhere' }}>{enrollUri}</code>
                    <form className="authForm" onSubmit={(event) => void confirmEnrollment(event)}>
                      <label className="label">Verification code</label>
                      <input value={enrollCode} onChange={(event) => setEnrollCode(event.target.value)} inputMode="numeric" placeholder="123456" required />
                      <button type="submit" disabled={busy || !enrollCode.trim()}>Confirm MFA</button>
                    </form>
                  </>
                ) : null}
              </>
            ) : (
              <form className="authForm" onSubmit={(event) => void disableMfa(event)}>
                <p className="muted">Disabling MFA requires your current password and an authenticator code.</p>
                <label className="label">Current password</label>
                <input value={disablePassword} onChange={(event) => setDisablePassword(event.target.value)} type="password" required />
                <label className="label">Authenticator code</label>
                <input value={disableCode} onChange={(event) => setDisableCode(event.target.value)} inputMode="numeric" required />
                <button type="submit" disabled={busy || !disableCode.trim() || !disablePassword}>Disable MFA</button>
              </form>
            )}
          </article>

          <article className="dataCard">
            <p className="sectionEyebrow">Recovery codes</p>
            <p className="muted">Store codes in a password manager. Each code can be used once during MFA sign-in recovery.</p>
            {recoveryCodes.length === 0 ? <p className="muted">New codes are shown only immediately after MFA enrollment.</p> : (
              <ul>
                {recoveryCodes.map((code) => <li key={code}><code>{code}</code></li>)}
              </ul>
            )}
          </article>

          <article className="dataCard">
            <p className="sectionEyebrow">Account summary</p>
            <p className="muted">User: {user?.email}</p>
            <p className="muted">Last sign-in: {user?.last_sign_in_at ? new Date(user.last_sign_in_at).toLocaleString() : 'N/A'}</p>
            <div className="buttonRow">
              <button type="button" onClick={() => void refreshUser()} disabled={busy || loading}>Refresh account</button>
              <button type="button" onClick={() => void signOutAll()} disabled={busy}>Sign out all sessions</button>
            </div>
          </article>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Session activity</p>
            <h2>Active and historical sessions</h2>
          </div>
        </div>
        <div className="stack compactStack">
          {sessions.length === 0 ? <article className="dataCard"><p className="muted">No sessions recorded.</p></article> : activeSessions.map((session) => (
            <article className="dataCard" key={session.id}>
              <div className="listHeader">
                <div>
                  <h3>{session.auth_mode}</h3>
                  <p className="muted">Last seen: {session.last_seen_at ? new Date(session.last_seen_at).toLocaleString() : 'Never'}</p>
                  <p className="muted">IP: {session.ip_address ?? 'unknown'}</p>
                  <p className="muted">User agent: {session.user_agent ?? 'unknown'}</p>
                </div>
                <button type="button" onClick={() => void revokeSession(session.id)} disabled={busy}>Revoke</button>
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
