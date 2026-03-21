'use client';

import { useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

export default function SettingsPageClient() {
  const { apiUrl, authHeaders, error, liveModeConfigured, loading, selectWorkspace, user } = usePilotAuth();
  const [healthDetails, setHealthDetails] = useState<Record<string, unknown> | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  const currentMembership = useMemo(
    () => user?.memberships.find((membership) => membership.workspace_id === user.current_workspace?.id) ?? null,
    [user]
  );
  const isAdmin = currentMembership?.role === 'workspace_owner' || currentMembership?.role === 'workspace_admin';

  useEffect(() => {
    let active = true;

    async function loadHealth() {
      if (!apiUrl || !isAdmin) {
        setHealthDetails(null);
        return;
      }
      try {
        const response = await fetch(`${apiUrl}/health/details`, {
          headers: authHeaders(),
          cache: 'no-store',
        });
        const payload = (await response.json()) as Record<string, unknown> | { detail?: string };
        if (!response.ok) {
          const detail = typeof (payload as { detail?: unknown }).detail === 'string' ? (payload as { detail?: string }).detail : undefined;
          throw new Error(detail ?? 'Unable to load diagnostics.');
        }
        if (active) {
          setHealthDetails(payload as Record<string, unknown>);
          setHealthError(null);
        }
      } catch (fetchError) {
        if (active) {
          setHealthError(fetchError instanceof Error ? fetchError.message : String(fetchError));
        }
      }
    }

    void loadHealth();
    return () => {
      active = false;
    };
  }, [apiUrl, authHeaders, isAdmin]);

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Settings</p>
            <h1>Workspace and live-mode management</h1>
            <p className="lede">Manage your profile, switch the active workspace, and verify whether the deployment is running live, degraded, fallback, or sample-safe flows.</p>
          </div>
        </div>
        <div className="threeColumnSection">
          <article className="dataCard">
            <p className="sectionEyebrow">Current user</p>
            <h2>{user?.full_name ?? 'Unknown user'}</h2>
            <p className="muted">{user?.email}</p>
            <div className="kvGrid compactKvGrid">
              <p><span>Created</span>{user?.created_at ? new Date(user.created_at).toLocaleString() : '—'}</p>
              <p><span>Last sign in</span>{user?.last_sign_in_at ? new Date(user.last_sign_in_at).toLocaleString() : '—'}</p>
            </div>
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">Workspace</p>
            <h2>{user?.current_workspace?.name ?? 'No workspace selected'}</h2>
            <p className="muted">Role: {currentMembership?.role ?? 'unknown'}</p>
            <label className="label compactLabel">
              Switch workspace
              <select value={user?.current_workspace?.id ?? ''} onChange={(event) => void selectWorkspace(event.target.value)} disabled={loading}>
                {(user?.memberships ?? []).map((membership) => (
                  <option key={membership.workspace_id} value={membership.workspace_id}>{membership.workspace.name}</option>
                ))}
              </select>
            </label>
          </article>
          <article className="dataCard">
            <p className="sectionEyebrow">API diagnostics</p>
            <h2>{liveModeConfigured ? 'Live mode configured' : 'Sample mode only'}</h2>
            <p className="muted">{apiUrl || 'NEXT_PUBLIC_API_URL not configured'}</p>
            {error ? <p className="statusLine">{error}</p> : <p className="muted">Authentication errors and session expiry messages appear here.</p>}
          </article>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Diagnostics / admin</p>
            <h2>/health/details snapshot</h2>
            <p className="lede">Detailed deployment diagnostics are only shown to workspace owners and admins.</p>
          </div>
        </div>
        {!isAdmin ? <div className="emptyStatePanel"><h2>Restricted diagnostics</h2><p>You need workspace owner or admin access to view detailed dependency diagnostics.</p></div> : null}
        {isAdmin && healthError ? <div className="emptyStatePanel"><h2>Diagnostics unavailable</h2><p>{healthError}</p></div> : null}
        {isAdmin && !healthError ? <article className="dataCard"><pre>{JSON.stringify(healthDetails, null, 2)}</pre></article> : null}
      </section>
    </main>
  );
}
