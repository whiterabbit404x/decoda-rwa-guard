'use client';

import Link from 'next/link';
import { useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export default function SecuritySettingsPageClient() {
  const { authHeaders, user } = usePilotAuth();
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function signOutAllSessions() {
    setSubmitting(true);
    const response = await fetch('/api/auth/signout-all', { method: 'POST', headers: authHeaders() });
    setMessage(response.ok ? 'All active sessions were signed out.' : 'Unable to sign out all sessions.');
    setSubmitting(false);
  }

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Settings</p>
            <h1>Security settings</h1>
            <p className="lede">Manage workspace security controls and account session hygiene.</p>
          </div>
        </div>

        <div className="buttonRow">
          <Link href="/settings">← Back to workspace settings</Link>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Workspace</p><h2>Access model</h2></div></div>
        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Current workspace</p>
            <h3>{user?.current_workspace?.name ?? 'No workspace selected'}</h3>
            <p className="muted">Decoda enforces workspace-scoped roles, audit logging, and least-privilege access controls.</p>
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Role protections</p>
            <p className="muted">Owners keep billing and administrative continuity. Admin and analyst roles support operational execution with scoped permissions.</p>
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
