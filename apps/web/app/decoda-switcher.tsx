'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';

type ContextOrganization = { id: string; name: string; slug: string; role: string; product_access: string; current: boolean };
type ContextProduct = { product: string; name: string; availability: string; access: string; entitlement_status: string | null };
type IdentityContext = {
  organizations: ContextOrganization[];
  products: ContextProduct[];
  links: { website: string; launcher: string; account: string; requestAccess: string; vault: string };
};

const PRODUCT_ORDER = ['rwa_guard', 'vault', 'assets'];
const PRODUCT_NAMES: Record<string, string> = { rwa_guard: 'RWA Guard', vault: 'Vault', assets: 'Assets' };

function productLabel(product: ContextProduct): string {
  if (product.product === 'rwa_guard') return 'Current';
  if (product.availability !== 'available') return 'Coming soon';
  if (product.access === 'granted') return product.entitlement_status === 'pilot' ? 'Pilot' : 'Open';
  if (product.access === 'entitlement_suspended') return 'Suspended';
  if (product.access === 'entitlement_expired') return 'Expired';
  return 'Not enabled';
}

/**
 * "Decoda ▾" — the Decoda products this organization can open and the
 * organizations this person can switch RWA Guard to. Shown only for a Decoda
 * session. Everything comes from the Guard API, which reads the Decoda
 * platform; a product that is not enabled can only be requested, never opened.
 */
export default function DecodaSwitcher() {
  const { user, authHeaders } = usePilotAuth();
  const [open, setOpen] = useState(false);
  const [context, setContext] = useState<IdentityContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await fetch('/api/auth/identity-context', { cache: 'no-store' });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        setError(typeof payload?.detail?.message === 'string' ? payload.detail.message : 'Decoda organizations are unavailable right now.');
        return;
      }
      setContext(payload as IdentityContext);
    } catch {
      setError('Decoda organizations are unavailable right now.');
    }
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event: MouseEvent) => {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (user?.identity?.auth_method !== 'workos') return null;

  async function switchTo(organizationId: string) {
    setSwitching(organizationId);
    setError(null);
    try {
      const response = await fetch('/api/auth/switch-organization', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ organizationId }),
      });
      const payload = (await response.json().catch(() => null)) as { redirect?: unknown; detail?: { message?: unknown } } | null;
      if (typeof payload?.redirect === 'string' && payload.redirect) {
        // Full navigation: nothing from the previous organization stays in memory.
        window.location.assign(payload.redirect);
        return;
      }
      setError(typeof payload?.detail?.message === 'string' ? payload.detail.message : 'The organization could not be switched.');
    } catch {
      setError('The organization could not be switched.');
    }
    setSwitching(null);
  }

  const products = context ? [...context.products].sort((a, b) => PRODUCT_ORDER.indexOf(a.product) - PRODUCT_ORDER.indexOf(b.product)) : [];

  return (
    <div className="shellUserMenuWrap" ref={wrap} data-testid="decoda-switcher">
      <button
        className="shellUserChip"
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Decoda products and organizations"
        onClick={() => {
          if (!open && !context) void load();
          setOpen((value) => !value);
        }}
      >
        <span className="shellUserChipEmail">Decoda</span>
      </button>
      {open ? (
        <div className="shellUserMenu" role="menu" aria-label="Decoda">
          <p className="shellUserMenuLabel">Products</p>
          {products.map((product) => {
            const label = productLabel(product);
            const name = PRODUCT_NAMES[product.product] ?? product.name;
            const href = product.product === 'vault' && product.access === 'granted' ? context?.links.vault : null;
            return href ? (
              <a key={product.product} className="shellUserMenuItem" role="menuitem" href={href} rel="noreferrer noopener">
                {name} · {label}
              </a>
            ) : (
              <span key={product.product} className="shellUserMenuItem" role="menuitem" aria-disabled="true">
                {name} · {label}
                {label === 'Not enabled' && context ? (
                  <>
                    {' '}
                    <a className="siLink" href={`${context.links.website}/request-pilot?product=${encodeURIComponent(product.product)}`} rel="noreferrer noopener">
                      Request access
                    </a>
                  </>
                ) : null}
              </span>
            );
          })}
          <hr className="shellUserMenuSep" />
          <p className="shellUserMenuLabel">Organizations</p>
          {(context?.organizations ?? []).map((organization) => (
            <button
              key={organization.id}
              type="button"
              className="shellUserMenuItem"
              role="menuitem"
              disabled={organization.current || organization.product_access !== 'granted' || switching !== null}
              aria-current={organization.current ? 'true' : undefined}
              onClick={() => void switchTo(organization.id)}
            >
              {organization.name}
              {organization.current ? ' · Current' : organization.product_access !== 'granted' ? ' · RWA Guard not enabled' : switching === organization.id ? ' · Switching…' : ''}
            </button>
          ))}
          {!context && !error ? <p className="shellUserMenuLabel">Loading…</p> : null}
          {error ? <p className="shellUserMenuLabel" role="alert">{error}</p> : null}
          <hr className="shellUserMenuSep" />
          {context ? (
            <>
              <a className="shellUserMenuItem" role="menuitem" href={context.links.launcher} rel="noreferrer noopener">Decoda launcher</a>
              <a className="shellUserMenuItem" role="menuitem" href={context.links.account} rel="noreferrer noopener">Decoda account</a>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
