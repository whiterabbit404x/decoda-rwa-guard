'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export default function SettingsPageClient() {
  const { apiUrl, authHeaders, error, liveModeConfigured, loading, selectWorkspace, user } = usePilotAuth();
  const [billingSummary, setBillingSummary] = useState('Loading billing status…');
  const [teamSummary, setTeamSummary] = useState('Loading team members…');
  const [members, setMembers] = useState<Array<{ id: string; email: string; full_name: string; role: string }>>([]);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('viewer');
  const [seatSummary, setSeatSummary] = useState('');
  const currentMembership = useMemo(() => user?.memberships.find((m) => m.workspace_id === user.current_workspace?.id) ?? null, [user]);

  useEffect(() => {
    let active = true;
    async function loadPanels() {
      if (!apiUrl || !user?.current_workspace?.id) return;
      try {
        const [subscription, membersResponse, seatsResponse] = await Promise.all([
          fetch(`${apiUrl}/billing/subscription`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/workspace/members`, { headers: authHeaders(), cache: 'no-store' }),
          fetch(`${apiUrl}/team/seats`, { headers: authHeaders(), cache: 'no-store' }),
        ]);
        if (!active) return;
        if (subscription.ok) {
          const payload = (await subscription.json()) as { subscription?: { status?: string; plan_key?: string } | null };
          setBillingSummary(`Plan: ${payload.subscription?.plan_key ?? 'none'} · Status: ${payload.subscription?.status ?? 'no subscription'}`);
        }
        if (membersResponse.ok) {
          const payload = (await membersResponse.json()) as { members?: Array<{ id: string; email: string; full_name: string; role: string }> };
          setTeamSummary(`${payload.members?.length ?? 0} members in current workspace.`);
          setMembers(payload.members ?? []);
        }
        if (seatsResponse.ok) {
          const seats = (await seatsResponse.json()) as { used: number; limit: number };
          setSeatSummary(`Seats: ${seats.used}/${seats.limit}`);
        }
      } catch {
        if (active) {
          setBillingSummary('Billing status unavailable.');
          setTeamSummary('Team members unavailable.');
        }
      }
    }
    void loadPanels();
    return () => {
      active = false;
    };
  }, [apiUrl, authHeaders, user?.current_workspace?.id]);

  async function inviteMember() {
    if (!apiUrl) return;
    const response = await fetch(`${apiUrl}/workspace/invitations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ email: inviteEmail, role: inviteRole })
    });
    setTeamSummary(response.ok ? `Invitation sent to ${inviteEmail}.` : 'Invitation failed.');
  }

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Settings</p><h1>Workspace management</h1><p className="lede">Manage workspace membership, subscription status, and session controls.</p></div></div>
        <div className="threeColumnSection">
          <article className="dataCard"><p className="sectionEyebrow">Current user</p><h2>{user?.full_name ?? 'Unknown user'}</h2><p className="muted">{user?.email}</p><p className="muted">Role: {currentMembership?.role ?? 'unknown'}</p></article>
          <article className="dataCard"><p className="sectionEyebrow">Workspace</p><h2>{user?.current_workspace?.name ?? 'No workspace selected'}</h2><label className="label compactLabel">Switch workspace<select value={user?.current_workspace?.id ?? ''} onChange={(event) => void selectWorkspace(event.target.value)} disabled={loading}>{(user?.memberships ?? []).map((membership) => (<option key={membership.workspace_id} value={membership.workspace_id}>{membership.workspace.name}</option>))}</select></label></article>
          <article className="dataCard"><p className="sectionEyebrow">API diagnostics</p><h2>{liveModeConfigured ? 'Live mode configured' : 'Sample mode only'}</h2><p className="muted">{apiUrl || 'NEXT_PUBLIC_API_URL not configured'}</p>{error ? <p className="statusLine">{error}</p> : null}</article>
        </div>
      </section>
      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Self-serve SaaS</p><h2>Billing and team controls</h2></div></div>
        <div className="threeColumnSection">
          <article className="dataCard"><p className="sectionEyebrow">Subscription</p><p className="muted">{billingSummary}</p><p className="muted">{seatSummary}</p></article>
          <article className="dataCard"><p className="sectionEyebrow">Team</p><p className="muted">{teamSummary}</p>
            <div className="buttonRow"><input value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="invite@company.com" /><select value={inviteRole} onChange={(event) => setInviteRole(event.target.value)}><option value="admin">admin</option><option value="analyst">analyst</option><option value="viewer">viewer</option></select><button type="button" onClick={inviteMember}>Invite</button></div>
            {members.map((member) => <p key={member.id}>{member.full_name || member.email} · {member.role}</p>)}
          </article>
          <article className="dataCard"><p className="sectionEyebrow">Sessions</p><button type="button" onClick={() => void fetch('/api/auth/signout-all', { method: 'POST', headers: authHeaders() })}>Sign out all sessions</button></article>
        </div>
      </section>
    </main>
  );
}
