'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export default function WorkspacesPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { loading, isAuthenticated, user, createWorkspace, selectWorkspace } = usePilotAuth();
  const [workspaceName, setWorkspaceName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
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
      <section className="threeColumnSection workspacesLayout">
        <div className="dataCard">
          <h2>Your workspaces</h2>
          {user?.memberships.map((membership) => {
            const isCurrent = user.current_workspace?.id === membership.workspace_id;
            return (
            <article
              key={membership.workspace_id}
              className={`dataCard nestedCard workspaceOption${isCurrent ? ' workspaceOption--current' : ''}`}
            >
              <div className="workspaceOptionText">
                <h3>{membership.workspace.name}</h3>
                <span className="ruleChip sharedStatusPill workspaceOptionRole">{membership.role}</span>
              </div>
              <button
                type="button"
                className={`btn ${isCurrent ? 'btn-secondary' : 'btn-primary'}`}
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
            );
          })}
        </div>
        <form className="dataCard authForm" onSubmit={handleCreateWorkspace}>
          <h2>Create a workspace</h2>
          <label className="label" htmlFor="workspace-create-name">Workspace name</label>
          <input id="workspace-create-name" value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} required />
          <button type="submit" className="btn btn-primary" disabled={submitting}>{submitting ? 'Creating…' : 'Create workspace'}</button>
          {error ? <p className="statusLine">{error}</p> : null}
          {selectionError ? <p className="statusLine">{selectionError}</p> : null}
          {selectionLoading ? <p className="statusLine">Applying workspace selection…</p> : null}
        </form>
        <div className="dataCard">
          <h2>Next step</h2>
          <p className="muted">After selecting a workspace, return to the dashboard to review live status, recent alerts, recent incidents, and saved history in one place.</p>
          <Link href="/dashboard" prefetch={false} className="btn btn-secondary">Back to dashboard</Link>
        </div>
      </section>
    </main>
  );
}
