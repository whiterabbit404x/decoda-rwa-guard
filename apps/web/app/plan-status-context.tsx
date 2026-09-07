'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import type { AccountPlanResponse } from './plan-status';

type PlanStatusContextValue = {
  plan: AccountPlanResponse | null;
  loading: boolean;
  /** Set when the plan could not be read at all. Never rendered as a plan. */
  error: string | null;
  refresh: () => Promise<void>;
};

const PlanStatusContext = createContext<PlanStatusContextValue | null>(null);

// The plan changes rarely (a lifecycle action, or crossing an evaluation day
// boundary), so one read per mount plus a slow revalidation is enough. Usage
// counters are re-read on refresh() after an action that changes them.
const PLAN_REVALIDATE_MS = 300_000;

export function PlanStatusProvider({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, authHeaders, user } = usePilotAuth();
  const [plan, setPlan] = useState<AccountPlanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const workspaceId = user?.current_workspace?.id ?? user?.current_workspace_id ?? null;

  const refresh = useCallback(async () => {
    if (!isAuthenticated) {
      setPlan(null);
      return;
    }
    setLoading(true);
    try {
      const response = await fetch('/api/account/plan', {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (!response.ok) {
        // A failed read is reported as an absent plan, never as a default one:
        // the header shows no chip rather than inventing "Pilot".
        setPlan(null);
        setError(`Plan status unavailable (HTTP ${response.status}).`);
        return;
      }
      const payload = (await response.json()) as AccountPlanResponse;
      setPlan(payload);
      setError(null);
    } catch {
      setPlan(null);
      setError('Plan status unavailable.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, isAuthenticated]);

  useEffect(() => {
    void refresh();
    if (!isAuthenticated) {
      return;
    }
    const timer = setInterval(() => void refresh(), PLAN_REVALIDATE_MS);
    return () => clearInterval(timer);
    // workspaceId participates so switching workspace re-reads the owning tenant.
  }, [isAuthenticated, refresh, workspaceId]);

  const value = useMemo<PlanStatusContextValue>(
    () => ({ plan, loading, error, refresh }),
    [error, loading, plan, refresh],
  );

  return <PlanStatusContext.Provider value={value}>{children}</PlanStatusContext.Provider>;
}

export function usePlanStatus(): PlanStatusContextValue {
  const context = useContext(PlanStatusContext);
  if (!context) {
    return { plan: null, loading: false, error: null, refresh: async () => {} };
  }
  return context;
}
