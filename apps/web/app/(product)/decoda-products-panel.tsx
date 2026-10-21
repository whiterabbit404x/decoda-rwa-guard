'use client';

import { useCallback, useEffect, useState } from 'react';

import { MetricTile, StatusPill } from '../components/ui-primitives';
import { usePilotAuth } from '../pilot-auth-context';
import { parseDecodaProductsView, signalSummary, stateFor, type DecodaProductsView } from './decoda-products-presentation';
import { fmtRelative } from './integration-gateway-types';

// Decoda Assets and Decoda Vault send signed testnet events to Guard; Guard reports its
// alerts, incidents, risk and coverage back. Routing a product is an organization decision.
export function DecodaProductsPanel() {
  const { authHeaders, refreshCsrfToken } = usePilotAuth();
  const [view, setView] = useState<DecodaProductsView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch('/api/integrations/decoda', { headers: authHeaders(), cache: 'no-store' });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = (payload as { detail?: unknown }).detail;
        setError(`Unable to load Decoda products: ${typeof detail === 'string' ? detail : `HTTP ${response.status}`}`);
        return;
      }
      const parsed = parseDecodaProductsView(payload);
      if (!parsed) {
        // Fail closed: an unreadable answer is "unavailable", never an empty (or healthy) state.
        setView(null);
        setError('Decoda product status is unavailable.');
        return;
      }
      setView(parsed);
      setError('');
    } catch (err) {
      setError(`Network error loading Decoda products: ${err instanceof Error ? err.message : 'unknown error'}`);
    }
  }, [authHeaders]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(key: string, url: string, method: 'POST' | 'DELETE') {
    setBusy(key);
    setError('');
    try {
      await refreshCsrfToken().catch(() => undefined);
      const response = await fetch(url, {
        method,
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        cache: 'no-store',
        body: '{}',
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = (payload as { detail?: unknown }).detail;
        setError(typeof detail === 'string' ? detail : `Request failed (HTTP ${response.status}).`);
      }
      await load();
    } finally {
      setBusy(null);
    }
  }

  if (!view) {
    return error ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{error}</p> : null;
  }
  return (
    <section aria-label="Decoda products" data-testid="decoda-products" style={{ marginBottom: '1rem' }}>
      <h2 style={{ fontSize: '1rem', margin: '0 0 0.5rem' }}>Decoda products (testnet)</h2>
      {error ? <p className="statusLine" style={{ color: 'var(--danger-fg)', fontSize: '0.85rem' }}>{error}</p> : null}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '0.75rem' }}>
        {view.products.map((product) => {
          const state = stateFor(product.state);
          const signals = signalSummary(product.signals);
          return (
            <article key={product.producer} className="dataCard" data-testid={`decoda-product-${product.producer}`}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', alignItems: 'center' }}>
                <strong>{product.label}</strong>
                <StatusPill label={state.label} variant={state.variant} />
              </div>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                {state.detail}
                {product.linked_workspace ? ` (${product.linked_workspace})` : ''}
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem' }}>
                <MetricTile label="Events" value={product.events_received} meta={product.last_event_at ? fmtRelative(product.last_event_at) : 'None yet'} />
                <MetricTile label="Alerts" value={product.alerts_created} meta={product.evidence_label} />
                <MetricTile label="Unrouted" value={product.unrouted_events} meta="Organization" />
              </div>
              <p style={{ fontSize: '0.8rem', marginTop: '0.5rem' }}>
                <StatusPill label={signals.text} variant={signals.variant} />
              </p>
              {view.can_manage ? (
                <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                  {product.linked_here ? (
                    <button
                      type="button"
                      className="btn btn-secondary"
                      style={{ fontSize: '0.76rem' }}
                      disabled={busy !== null}
                      onClick={() => void act(`unlink-${product.producer}`, `/api/integrations/decoda/links/${product.producer}`, 'DELETE')}
                    >
                      Stop routing here
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn btn-primary"
                      style={{ fontSize: '0.76rem' }}
                      disabled={busy !== null || product.state === 'not_configured'}
                      onClick={() => void act(`link-${product.producer}`, `/api/integrations/decoda/links/${product.producer}`, 'POST')}
                    >
                      Route events to this workspace
                    </button>
                  )}
                  {product.signals.dead + product.signals.failed > 0 ? (
                    <button
                      type="button"
                      className="btn btn-secondary"
                      style={{ fontSize: '0.76rem' }}
                      disabled={busy !== null}
                      onClick={() => void act('retry', '/api/integrations/decoda/signals/retry', 'POST')}
                    >
                      Send signals again
                    </button>
                  ) : null}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
