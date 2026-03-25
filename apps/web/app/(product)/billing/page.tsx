'use client';
import { useEffect, useState } from 'react';
import { usePilotAuth } from '../../pilot-auth-context';

export default function BillingPage() {
  const { authHeaders } = usePilotAuth();
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void fetch('/api/billing/status', { headers: authHeaders() }).then(async (r) => {
    const b = await r.json().catch(()=>({})); if (!r.ok) throw new Error(b.detail ?? 'Unable to load billing.'); setStatus(b);
  }).catch((e)=>setError(e instanceof Error?e.message:String(e))); }, [authHeaders]);
  return <main className="productPage"><section className="featureSection"><div className="sectionHeader"><div><p className="eyebrow">Billing</p><h1>Plan and subscription</h1></div></div>
    <article className="dataCard"><p className="muted">Current plan: {String(status?.plan ?? 'loading')}</p><p className="muted">Trial ends: {String(status?.trial_ends_at ?? '—')}</p>{error ? <p className="statusLine">{error}</p> : null}
    <button onClick={() => void fetch('/api/billing/checkout', { method: 'POST', headers: authHeaders() }).then(async (r)=>{const b=await r.json(); if(b.checkout_url) window.location.href=b.checkout_url;})}>Start checkout</button>
    <button onClick={() => void fetch('/api/billing/portal', { method: 'POST', headers: authHeaders() }).then(async (r)=>{const b=await r.json(); if(b.portal_url) window.location.href=b.portal_url;})}>Manage subscription</button></article></section></main>;
}
