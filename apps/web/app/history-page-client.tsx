'use client';

import { useEffect, useState } from 'react';

import HistoryRecordsView from './history-records-view';
import { HistoryPayload } from './pilot-history';
import { usePilotAuth } from './pilot-auth-context';

export default function HistoryPageClient() {
  const { apiUrl, authHeaders, isAuthenticated, loading, selectWorkspace, user } = usePilotAuth();
  const [history, setHistory] = useState<HistoryPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  const [workspaceActionLoading, setWorkspaceActionLoading] = useState(false);

  useEffect(() => {
    let active = true;

    async function loadHistory() {
      if (!isAuthenticated || !user?.current_workspace?.id) {
        setHistory(null);
        setFetching(false);
        return;
      }
      setFetching(true);
      setError(null);
      try {
        const response = await fetch(`${apiUrl}/pilot/history?limit=50`, {
          headers: authHeaders(),
          cache: 'no-store',
        });
        const payload = (await response.json().catch(() => ({ detail: 'Unable to load history right now.' }))) as HistoryPayload | { detail?: string };
        if (!response.ok) {
          const detail = typeof (payload as { detail?: unknown }).detail === 'string' ? (payload as { detail?: string }).detail : undefined;
          throw new Error(detail ?? 'Unable to load history.');
        }
        if (active) {
          setHistory(payload as HistoryPayload);
        }
      } catch (historyError) {
        if (active) {
          setError(historyError instanceof Error ? historyError.message : String(historyError));
        }
      } finally {
        if (active) {
          setFetching(false);
        }
      }
    }

    void loadHistory();
    window.addEventListener('pilot-history-refresh', loadHistory as EventListener);
    return () => {
      active = false;
      window.removeEventListener('pilot-history-refresh', loadHistory as EventListener);
    };
  }, [apiUrl, authHeaders, isAuthenticated, user?.current_workspace?.id]);

  return (
    <main className="productPage">
      <div className="workspaceControlBar">
        <label className="label compactLabel">
          Active workspace
          <select
            value={user?.current_workspace?.id ?? ''}
            onChange={(event) => {
              setWorkspaceActionLoading(true);
              void selectWorkspace(event.target.value).finally(() => setWorkspaceActionLoading(false));
            }}
            disabled={loading || workspaceActionLoading || !user?.memberships?.length}
          >
            {(user?.memberships ?? []).map((membership) => (
              <option key={membership.workspace_id} value={membership.workspace_id}>{membership.workspace.name}</option>
            ))}
          </select>
        </label>
      </div>
      {!user?.current_workspace ? (
        <section className="emptyStatePanel">
          <h2>Select a workspace to view history</h2>
          <p>No workspace is currently active for this account. Choose one above, then reload this page.</p>
        </section>
      ) : null}
      <HistoryRecordsView history={history} loading={fetching || loading} error={error} workspaceName={user?.current_workspace?.name} />
    </main>
  );
}
