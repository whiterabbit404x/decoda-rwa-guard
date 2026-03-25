'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from '../../pilot-auth-context';

export default function BillingPage() {
  const { authHeaders, user } = usePilotAuth();
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetch('/api/billing/summary', { headers: authHeaders(), cache: 'no-store' })
      .then(async (response) => {
        const payload = (await response.json().catch(() => ({}))) as Record<string, unknown> & { detail?: string };
        if (!response.ok) {
          throw new Error(payload.detail ?? 'Unable to load billing summary.');
        }
        setSummary(payload);
      })
      .catch((fetchError: unknown) => setError(fetchError instanceof Error ? fetchError.message : String(fetchError)));
  }, [authHeaders]);

  return <main className="productPage"><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Billing</p><h1>Plan and subscription</h1><p className="lede">Manage trial status, plan transitions, and billing provider configuration.</p></div></div><div className="threeColumnSection"><article className="dataCard"><h2>Current account</h2><p>{user?.subscription?.plan ?? 'trial'} · {user?.subscription?.status ?? 'trialing'}</p><p className="muted">Trial ends: {user?.subscription?.trial_ends_at ? new Date(user.subscription.trial_ends_at).toLocaleString() : 'Not set'}</p></article><article className="dataCard"><h2>Billing summary</h2>{error ? <p className="statusLine">{error}</p> : <pre>{JSON.stringify(summary, null, 2)}</pre>}</article></div></section></main>;
}
