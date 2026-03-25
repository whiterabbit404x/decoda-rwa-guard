'use client';

import { useEffect, useState } from 'react';

import { usePilotAuth } from '../../../../pilot-auth-context';

type BillingState = {
  plan_name: string;
  status: 'trial' | 'active' | 'past_due' | 'canceled';
  trial_ends_at: string | null;
  billing_configured: boolean;
};

export default function BillingSettingsPage() {
  const { authHeaders } = usePilotAuth();
  const [state, setState] = useState<BillingState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetch('/api/auth/billing/status', { headers: authHeaders(), cache: 'no-store' })
      .then(async (response) => {
        const data = (await response.json()) as BillingState & { detail?: string };
        if (!response.ok) throw new Error(data.detail ?? 'Unable to load billing state.');
        setState(data);
      })
      .catch((nextError) => setError(nextError instanceof Error ? nextError.message : String(nextError)));
  }, [authHeaders]);

  async function startCheckout() {
    const response = await fetch('/api/auth/billing/checkout-session', { method: 'POST', headers: authHeaders() });
    const data = (await response.json()) as { checkout_url?: string; detail?: string };
    if (!response.ok || !data.checkout_url) {
      throw new Error(data.detail ?? 'Unable to create checkout session.');
    }
    window.location.href = data.checkout_url;
  }

  async function openPortal() {
    const response = await fetch('/api/auth/billing/portal-session', { method: 'POST', headers: authHeaders() });
    const data = (await response.json()) as { portal_url?: string; detail?: string };
    if (!response.ok || !data.portal_url) {
      throw new Error(data.detail ?? 'Unable to open billing portal.');
    }
    window.location.href = data.portal_url;
  }

  return (
    <main className="productPage">
      <section className="featureSection">
        <div className="sectionHeader"><div><p className="eyebrow">Billing</p><h1>Plan and subscription</h1></div></div>
        {error ? <p className="statusLine">{error}</p> : null}
        {state ? <article className="dataCard"><p>Plan: {state.plan_name}</p><p>Status: {state.status}</p><p>Trial ends: {state.trial_ends_at ? new Date(state.trial_ends_at).toLocaleString() : '—'}</p>{!state.billing_configured ? <p className="statusLine">Billing provider not configured in this environment.</p> : <div className="heroActionRow"><button onClick={() => void startCheckout()}>Start checkout</button><button onClick={() => void openPortal()}>Manage subscription</button></div>}</article> : null}
      </section>
    </main>
  );
}
