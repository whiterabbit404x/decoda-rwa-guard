'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';
import { BillingRuntime, billingDisabledMessage, billingEnabled } from './billing-capability';

type Member = { id: string; user_id: string; email: string; full_name: string; role: 'owner' | 'admin' | 'analyst' | 'viewer'; created_at: string };
type Invitation = { id: string; email: string; role: 'owner' | 'admin' | 'analyst' | 'viewer'; status: string; expires_at: string; created_at: string; updated_at: string };
type SeatSummary = { used: number; limit: number; plan_key?: string };

export default function SettingsPageClient() {
  const { apiUrl, authHeaders, error, liveModeConfigured, loading, selectWorkspace, user } = usePilotAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [plans, setPlans] = useState<Array<{ plan_key: string; plan_name: string; max_members: number; features?: Record<string, unknown> }>>([]);
  const [subscription, setSubscription] = useState<any>(null);
  const [billingRuntime, setBillingRuntime] = useState<BillingRuntime>({ provider: 'none', available: false });
  const [seatSummary, setSeatSummary] = useState<SeatSummary | null>(null);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const currentMembership = useMemo(() => user?.memberships.find((m) => m.workspace_id === user.current_workspace?.id) ?? null, [user]);

  async function call(path: string, init?: RequestInit) {
    return fetch(`${apiUrl}${path}`, { cache: 'no-store', ...init, headers: { ...(init?.headers ?? {}), ...authHeaders() } });
  }

  async function loadAll() {
    if (!apiUrl || !user?.current_workspace?.id) return;
    const [membersResponse, inviteResponse, seatsResponse, subscriptionResponse, plansResponse] = await Promise.all([
      call('/workspace/members'),
      call('/workspace/invitations'),
      call('/team/seats'),
      call('/billing/subscription'),
      call('/billing/plans'),
    ]);
    if (membersResponse.ok) setMembers((await membersResponse.json()).members ?? []);
    if (inviteResponse.ok) setInvitations((await inviteResponse.json()).invitations ?? []);
    if (seatsResponse.ok) setSeatSummary(await seatsResponse.json());
    if (subscriptionResponse.ok) {
      const payload = await subscriptionResponse.json();
      setSubscription(payload.subscription ?? null);
      setBillingRuntime(payload.billing ?? { provider: 'none', available: false });
    }
    if (plansResponse.ok) setPlans((await plansResponse.json()).plans ?? []);
  }

  useEffect(() => { void loadAll(); }, [apiUrl, user?.current_workspace?.id]);

  async function inviteMember() {
    if (!apiUrl || !inviteEmail) return;
    setSubmitting(true);
    const response = await call('/workspace/invitations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: inviteEmail, role: inviteRole })
    });
    setMessage(response.ok ? `Invitation sent to ${inviteEmail}.` : 'Invitation failed.');
    setSubmitting(false);
    if (response.ok) {
      setInviteEmail('');
      void loadAll();
    }
  }

  async function updateRole(memberId: string, role: string) {
    const response = await call(`/workspace/members/${memberId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role }),
    });
    setMessage(response.ok ? 'Role updated.' : 'Role update failed.');
    if (response.ok) void loadAll();
  }

  async function removeMember(memberId: string) {
    if (!window.confirm('Remove this member from the workspace?')) return;
    const response = await call(`/workspace/members/${memberId}`, { method: 'DELETE' });
    setMessage(response.ok ? 'Member removed.' : 'Member removal failed.');
    if (response.ok) void loadAll();
  }

  async function resendInvitation(invitationId: string) {
    if (!window.confirm('Resend this pending invitation?')) return;
    const response = await call(`/workspace/invitations/${invitationId}/resend`, { method: 'POST' });
    setMessage(response.ok ? 'Invitation resent.' : 'Unable to resend invitation.');
    if (response.ok) void loadAll();
  }

  async function revokeInvitation(invitationId: string) {
    if (!window.confirm('Revoke this pending invitation?')) return;
    const response = await call(`/workspace/invitations/${invitationId}`, { method: 'DELETE' });
    setMessage(response.ok ? 'Invitation revoked.' : 'Unable to revoke invitation.');
    if (response.ok) void loadAll();
  }

  async function startCheckout(planKey: string) {
    const response = await call('/billing/checkout-session', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan_key: planKey }),
    });
    if (!response.ok) {
      setMessage('Unable to start checkout session.');
      return;
    }
    const payload = await response.json();
    window.location.href = payload.checkout_url;
  }

  const billingStatus = subscription?.status ?? 'none';
  const nearSeatLimit = seatSummary ? seatSummary.used >= seatSummary.limit : false;
  const billingAvailable = billingEnabled(billingRuntime);
  const roleDescriptions: Record<string, string> = {
    owner: 'Full workspace and administrative control. At least one owner must remain.',
    admin: 'Can manage members, integrations, and workflow operations.',
    analyst: 'Can run analysis and operate findings/actions.',
    viewer: 'Read-only access for reporting and dashboards.',
  };

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Settings</p><h1>Workspace administration</h1><p className="lede">Manage users, seats, entitlements, and workspace governance in one place.</p></div></div>
        <div className="threeColumnSection">
          <article className="dataCard"><p className="sectionEyebrow">Current user</p><h2>{user?.full_name ?? 'Unknown user'}</h2><p className="muted">{user?.email}</p><p className="muted">Role: {currentMembership?.role ?? 'unknown'}</p></article>
          <article className="dataCard"><p className="sectionEyebrow">Workspace</p><h2>{user?.current_workspace?.name ?? 'No workspace selected'}</h2><label className="label compactLabel">Switch workspace<select value={user?.current_workspace?.id ?? ''} onChange={(event) => void selectWorkspace(event.target.value)} disabled={loading}>{(user?.memberships ?? []).map((membership) => (<option key={membership.workspace_id} value={membership.workspace_id}>{membership.workspace.name}</option>))}</select></label></article>
          <article className="dataCard"><p className="sectionEyebrow">API diagnostics</p><h2>{liveModeConfigured ? 'Live mode configured' : 'Sample mode only'}</h2><p className="muted">{apiUrl || 'NEXT_PUBLIC_API_URL not configured'}</p>{error ? <p className="statusLine">{error}</p> : null}</article>
          <article className="dataCard">
            <p className="sectionEyebrow">Security</p>
            <h2>Workspace security controls</h2>
            <p className="muted">Review workspace protections, access principles, and session controls.</p>
            <div className="buttonRow">
              <Link href="/settings/security" prefetch={false}>Open security settings</Link>
            </div>
          </article>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Billing</p><h2>Subscription and entitlements</h2></div></div>
        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Current subscription</p>
            <p className="muted">Plan: {subscription?.plan_key ?? 'none'}</p>
            <p className="muted">Status: {billingStatus}</p>
            <p className="muted">Seats: {seatSummary ? `${seatSummary.used}/${seatSummary.limit}` : 'loading'}</p>
            <p className="muted">Seat policy: {nearSeatLimit ? 'At limit' : 'Within limit'}</p>
            {nearSeatLimit ? <p className="statusLine">Seat limit reached. Contact support to expand access for your pilot workspace.</p> : null}
            {billingStatus === 'trialing' ? <p className="statusLine">Trialing plan active. Review limits before launch.</p> : null}
            {billingStatus === 'past_due' ? <p className="statusLine">Billing is past_due. Update billing details to avoid disruption.</p> : null}
            {billingStatus === 'canceled' ? <p className="statusLine">Subscription canceled. Re-activate to retain premium features.</p> : null}
            <p className="muted">Provider: {billingRuntime.provider ?? 'unknown'}</p>
            {billingAvailable ? null : <p className="statusLine">{billingDisabledMessage(billingRuntime)}</p>}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Plan catalog</p>
            {billingAvailable ? (plans.length === 0 ? <p className="muted">No plans available.</p> : plans.map((plan) => (
              <div key={plan.plan_key} style={{ marginBottom: 12 }}>
                <strong>{plan.plan_name}</strong>
                <p className="muted">{plan.plan_key} · max seats {plan.max_members}</p>
                <button type="button" onClick={() => void startCheckout(plan.plan_key)} disabled={!billingAvailable}>Choose {plan.plan_name}</button>
              </div>
            ))) : (
              <p className="muted">Commercial plans are managed through assisted pilot onboarding. Reach out via <a href="mailto:support@decoda.app">support@decoda.app</a>.</p>
            )}
          </article>
          <article className="dataCard"><p className="sectionEyebrow">Sessions</p><button type="button" onClick={() => void fetch('/api/auth/signout-all', { method: 'POST', headers: authHeaders() })}>Sign out all sessions</button></article>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Team admin</p><h2>Members and invitations</h2></div></div>
        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Invite teammate</p>
            <div className="buttonRow"><input value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="invite@company.com" /><select value={inviteRole} onChange={(event) => setInviteRole(event.target.value)}><option value="owner">owner</option><option value="admin">admin</option><option value="analyst">analyst</option><option value="viewer">viewer</option></select><button type="button" disabled={submitting} onClick={() => void inviteMember()}>Invite</button></div>
            <p className="muted">{roleDescriptions[inviteRole]}</p>
            {message ? <p className="statusLine">{message}</p> : null}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Members</p>
            {members.length === 0 ? <p className="muted">No members yet.</p> : members.map((member) => (
              <div key={member.id} style={{ marginBottom: 10 }}>
                <p>{member.full_name || member.email} · {member.email}</p>
                <p className="muted">{roleDescriptions[member.role]}{member.role === 'owner' ? ' Owner protection: keep at least one owner in this workspace.' : ''}</p>
                <div className="buttonRow"><select value={member.role} onChange={(event) => void updateRole(member.id, event.target.value)}><option value="owner">owner</option><option value="admin">admin</option><option value="analyst">analyst</option><option value="viewer">viewer</option></select><button type="button" onClick={() => void removeMember(member.id)}>Remove</button></div>
              </div>
            ))}
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Pending invitations</p>
            {invitations.length === 0 ? <p className="muted">No invitations.</p> : invitations.map((invitation) => (
              <div key={invitation.id} style={{ marginBottom: 10 }}>
                <p>{invitation.email} · {invitation.role} · {invitation.status}</p>
                <p className="muted">Expires {new Date(invitation.expires_at).toLocaleString()}</p>
                <div className="buttonRow"><button type="button" onClick={() => void resendInvitation(invitation.id)}>Resend</button><button type="button" onClick={() => void revokeInvitation(invitation.id)}>Revoke</button></div>
              </div>
            ))}
          </article>
        </div>
      </section>
    </main>
  );
}
