'use client';

import Link from 'next/link';
import { useEffect, useState, type ReactNode } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

import { CONSOLE_CONFIG_API, getJson } from './external-watchlist-api';
import type { NetworkRef } from './external-watchlist-view';

export type ConsoleNetwork = NetworkRef & { explorer_url: string; rpc_configured: boolean };

export type ExternalWatchlistFeature = {
  enabled: boolean;
  schema_ready?: boolean;
  networks?: ConsoleNetwork[];
  target_types?: string[];
  detection_profiles?: Array<{ key: string; label: string }>;
  backfill_day_options?: number[];
  default_backfill_days?: number;
  finding_statuses?: Array<{ key: string; label: string }>;
  notices?: Record<string, string>;
  pilot_evaluation_days?: number;
};

export type ConsoleState =
  | { state: 'loading' }
  | { state: 'signed_out' }
  | { state: 'denied' }
  | { state: 'disabled' }
  | { state: 'error'; message: string }
  | { state: 'ready'; feature: ExternalWatchlistFeature };

/**
 * Whether this session may use the External Watchlist, as the BACKEND says.
 * 403 → denied (not staff); enabled !== true → disabled. Nothing here is an
 * authorization decision — every data request is authorized again server-side.
 */
export function useExternalWatchlistConsole(): ConsoleState {
  const { authHeaders, isAuthenticated, loading } = usePilotAuth();
  const [state, setState] = useState<ConsoleState>({ state: 'loading' });

  useEffect(() => {
    if (loading) {
      setState({ state: 'loading' });
      return;
    }
    if (!isAuthenticated) {
      setState({ state: 'signed_out' });
      return;
    }
    let cancelled = false;
    getJson(CONSOLE_CONFIG_API, authHeaders)
      .then(({ ok, status, payload }) => {
        if (cancelled) return;
        if (status === 403) {
          setState({ state: 'denied' });
          return;
        }
        if (status === 401) {
          setState({ state: 'signed_out' });
          return;
        }
        if (!ok) {
          setState({ state: 'error', message: `The founder console configuration could not be read (HTTP ${status}).` });
          return;
        }
        const features = (payload.features ?? {}) as Record<string, unknown>;
        const feature = (features.external_watchlist ?? null) as ExternalWatchlistFeature | null;
        if (!feature || feature.enabled !== true) {
          setState({ state: 'disabled' });
          return;
        }
        setState({ state: 'ready', feature });
      })
      .catch(() => {
        if (!cancelled) setState({ state: 'error', message: 'The founder console configuration could not be read.' });
      });
    return () => {
      cancelled = true;
    };
  }, [authHeaders, isAuthenticated, loading]);

  return state;
}

export function ConsoleGate({ state, children }: { state: ConsoleState; children: (feature: ExternalWatchlistFeature) => ReactNode }) {
  if (state.state === 'loading') {
    return (
      <main className="adminConsole">
        <p className="muted">Loading…</p>
      </main>
    );
  }
  if (state.state === 'signed_out') {
    return (
      <main className="adminConsole">
        <div className="adminConsoleDenied ewlGateNeutral">
          <h1 style={{ fontSize: '1.05rem', margin: '0 0 0.4rem' }}>Sign in required</h1>
          <p style={{ margin: 0, fontSize: '0.85rem' }}>
            <Link href="/sign-in" prefetch={false}>Sign in</Link> with a Decoda staff account to continue.
          </p>
        </div>
      </main>
    );
  }
  if (state.state === 'denied') {
    return (
      <main className="adminConsole">
        <div className="adminConsoleDenied" data-testid="external-watchlist-denied">
          <h1 style={{ fontSize: '1.05rem', margin: '0 0 0.4rem' }}>Not available</h1>
          <p style={{ margin: 0, fontSize: '0.85rem' }}>This area is restricted to Decoda internal staff.</p>
        </div>
      </main>
    );
  }
  if (state.state === 'disabled') {
    return (
      <main className="adminConsole">
        <div className="adminConsoleDenied ewlGateNeutral" data-testid="external-watchlist-disabled">
          <h1 style={{ fontSize: '1.05rem', margin: '0 0 0.4rem' }}>External Watchlist is not enabled</h1>
          <p style={{ margin: 0, fontSize: '0.85rem' }}>
            This deployment has not enabled the External Watchlist (EXTERNAL_WATCHLIST_ENABLED).
          </p>
        </div>
      </main>
    );
  }
  if (state.state === 'error') {
    return (
      <main className="adminConsole">
        <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{state.message}</p>
      </main>
    );
  }
  return <>{children(state.feature)}</>;
}
