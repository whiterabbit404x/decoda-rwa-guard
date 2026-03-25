'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

import { usePilotAuth } from '../../pilot-auth-context';

export default function WorkspacesPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { loading, isAuthenticated, user, createWorkspace, selectWorkspace, authHeaders } = usePilotAuth();
  const [workspaceName, setWorkspaceName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('workspace_member');
  const [members, setMembers] = useState<Array<{ email: string; role: string }>>([]);
  const nextPath = searchParams?.get('next');

  useEffect(() => {
    if (!loading && !isAuthenticated) {
      router.push('/sign-in');
    }
  }, [isAuthenticated, loading, router]);

  useEffect(() => {
    if (loading || !isAuthenticated || user?.current_workspace || !user?.memberships?.length || user.memberships.length !== 1) {
      return;
    }
    setSelectionLoading(true);
    setSelectionError(null);
    void selectWorkspace(user.memberships[0].workspace_id)
      .then(() => {
        router.replace(nextPath || '/dashboard');
      })
      .catch((selectError) => {
        setSelectionError(selectError instanceof Error ? selectError.message : String(selectError));
      })
      .finally(() => {
        setSelectionLoading(false);
      });
  }, [isAuthenticated, loading, nextPath, router, selectWorkspace, user?.current_workspace, user?.memberships]);

  useEffect(() => {
    if (!user?.current_workspace?.id) return;
    void fetch('/api/auth/workspaces/members', { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.json())
      .then((data: { members?: Array<{ email: string; role: string }> }) => setMembers(data.members ?? []))
      .catch(() => setMembers([]));
  }, [authHeaders, user?.current_workspace?.id]);

  async function handleCreateWorkspace(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createWorkspace(workspaceName);
      setWorkspaceName('');
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : String(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="container authPage">
      <div className="hero">
        <div>
          <p className="eyebrow">Company workspaces</p>
          <h1>Select your active workspace</h1>
          <p className="lede">Choose the workspace you want to operate in. This sets your active context for dashboard data and saved analyses.</p>
        </div>
      </div>
      {!user?.current_workspace ? (
        <section className="emptyStatePanel">
          <h2>Finish onboarding by selecting a workspace</h2>
          <p>If this is your first sign-in, choose a workspace below or create one to continue.</p>
        </section>
      ) : null}
      <section className="threeColumnSection">
        <div className="dataCard">
          <h2>Your workspaces</h2>
          {user?.memberships.map((membership) => (
            <article key={membership.workspace_id} className="dataCard nestedCard">
              <h3>{membership.workspace.name}</h3>
              <p className="muted">{membership.role}</p>
              <button
                type="button"
                onClick={() => {
                  setSelectionLoading(true);
                  setSelectionError(null);
                  void selectWorkspace(membership.workspace_id)
                    .then(() => router.replace(nextPath || '/dashboard'))
                    .catch((selectError) => setSelectionError(selectError instanceof Error ? selectError.message : String(selectError)))
                    .finally(() => setSelectionLoading(false));
                }}
                disabled={selectionLoading}
              >
                {user.current_workspace?.id === membership.workspace_id ? 'Current workspace' : 'Use this workspace'}
              </button>
            </article>
          ))}
        </div>
        <form className="dataCard authForm" onSubmit={handleCreateWorkspace}>
          <h2>Create a workspace</h2>
          <label className="label">Workspace name</label>
          <input value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} required />
          <button type="submit" disabled={submitting}>{submitting ? 'Creating…' : 'Create workspace'}</button>
          {error ? <p className="statusLine">{error}</p> : null}
          {selectionError ? <p className="statusLine">{selectionError}</p> : null}
          {selectionLoading ? <p className="statusLine">Applying workspace selection…</p> : null}
        </form>
        <div className="dataCard">
          <h2>Next step</h2>
          <p className="muted">After selecting a workspace, return to the dashboard to review live status, recent alerts, recent incidents, and saved history in one place.</p>
          <Link href="/dashboard">Back to dashboard</Link>
        </div>
      </section>
      <section className="threeColumnSection">
        <form className="dataCard authForm" onSubmit={(event) => {event.preventDefault(); void fetch('/api/auth/workspaces/invites', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ email: inviteEmail, role: inviteRole }) }).then(() => setInviteEmail(''));}}>
          <h2>Invite teammate</h2>
          <input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} required />
          <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value)}>
            <option value="workspace_admin">Admin</option>
            <option value="workspace_member">Member</option>
            <option value="workspace_viewer">Viewer</option>
          </select>
          <button type="submit">Send invite</button>
        </form>
        <article className="dataCard">
          <h2>Organization members</h2>
          {members.map((member) => <p key={`${member.email}-${member.role}`}><span>{member.email}</span> {member.role}</p>)}
        </article>
      </section>
    </main>
  );
}
