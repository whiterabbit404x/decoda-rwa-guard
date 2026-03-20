'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function WorkspacesPage() {
  const router = useRouter();
  const { loading, isAuthenticated, user, createWorkspace, selectWorkspace } = usePilotAuth();
  const [workspaceName, setWorkspaceName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && !isAuthenticated) {
      router.push('/sign-in');
    }
  }, [isAuthenticated, loading, router]);

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
          <p className="lede">Choose the company workspace you want to operate in, or create a new one for a separate pilot environment.</p>
        </div>
      </div>
      <section className="threeColumnSection">
        <div className="dataCard">
          <h2>Your workspaces</h2>
          {user?.memberships.map((membership) => (
            <article key={membership.workspace_id} className="dataCard nestedCard">
              <h3>{membership.workspace.name}</h3>
              <p className="muted">{membership.role}</p>
              <button type="button" onClick={() => void selectWorkspace(membership.workspace_id)}>
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
        </form>
        <div className="dataCard">
          <h2>Next step</h2>
          <p className="muted">After selecting a workspace, return to the dashboard to review live status, recent alerts, recent incidents, and saved history in one place.</p>
          <Link href="/">Back to dashboard</Link>
        </div>
      </section>
    </main>
  );
}
