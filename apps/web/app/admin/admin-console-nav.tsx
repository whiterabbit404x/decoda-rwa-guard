'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

import { showsInternalAdminLink } from 'app/internal-admin';
import { usePilotAuth } from 'app/pilot-auth-context';

import {
  PILOT_MANAGEMENT_HREF,
  buildAdminConsoleNav,
  externalWatchlistEnabled,
  isActiveAdminNav,
  type AdminConsoleConfig,
} from './admin-console';

/**
 * Founder console navigation bar.
 *
 * Rendered only after the backend confirmed internal-admin access by answering
 * GET /admin/console-config with 200. A customer who opens an /admin URL sees
 * no console chrome at all — the page itself reports "Not available" from its
 * own 403. The External Watchlist entry additionally requires the backend's
 * feature flag.
 */
export default function AdminConsoleNav() {
  const pathname = usePathname();
  const { authHeaders, isAuthenticated, user } = usePilotAuth();
  const [config, setConfig] = useState<AdminConsoleConfig>(null);
  const [confirmed, setConfirmed] = useState(false);
  const mayBeStaff = isAuthenticated && showsInternalAdminLink(user);

  useEffect(() => {
    if (!mayBeStaff) {
      setConfirmed(false);
      setConfig(null);
      return;
    }
    let cancelled = false;
    fetch('/api/admin/console-config', { headers: authHeaders(), cache: 'no-store' })
      .then(async (response) => {
        if (cancelled) return;
        if (!response.ok) {
          setConfirmed(false);
          setConfig(null);
          return;
        }
        setConfig((await response.json().catch(() => null)) as AdminConsoleConfig);
        setConfirmed(true);
      })
      .catch(() => {
        if (!cancelled) {
          setConfirmed(false);
          setConfig(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [authHeaders, mayBeStaff]);

  if (!confirmed) {
    return null;
  }

  const items = buildAdminConsoleNav({ externalWatchlistEnabled: externalWatchlistEnabled(config) });
  return (
    <header className="adminConsoleBar">
      <Link href={PILOT_MANAGEMENT_HREF} className="adminConsoleBrand" prefetch={false}>
        <span className="adminConsoleBrandMark" aria-hidden="true">D</span>
        <span>
          <span className="adminConsoleBrandName">Decoda</span>
          <span className="adminConsoleBrandSub">Founder console</span>
        </span>
      </Link>
      <nav className="adminConsoleNav" aria-label="Founder console">
        {items.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            prefetch={false}
            className={isActiveAdminNav(pathname, item.href) ? 'active' : ''}
            aria-current={isActiveAdminNav(pathname, item.href) ? 'page' : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
