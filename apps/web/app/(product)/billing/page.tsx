'use client';

import { useEffect, useState } from 'react';
import { usePilotAuth } from 'app/pilot-auth-context';

export default function BillingPage() {
  const { authHeaders } = usePilotAuth();
  const [state, setState] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    void fetch('/api/billing/state', { headers: authHeaders(), cache: 'no-store' })
      .then((response) => response.json())
      .then((payload) => setState(payload));
  }, [authHeaders]);

  return <main className="productPage"><section className="featureSection"><h1>Billing</h1><p className="lede">Plan and subscription state for your organization.</p><article className="dataCard"><pre>{JSON.stringify(state, null, 2)}</pre></article></section></main>;
}
