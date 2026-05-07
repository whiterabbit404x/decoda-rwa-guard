"use client";

import Link from 'next/link';

import { APP_NAV_ITEMS } from './product-nav';
import { NAV_ICONS } from './nav-icons';

export default function AppNavigation({ currentPath, onNavAttempt }: { currentPath: string; onNavAttempt?: (targetHref: string) => void }) {
  const isDev = process.env.NODE_ENV !== 'production';

  function logNavClick(targetHref: string) {
    if (!isDev) {
      return;
    }

    if (targetHref === '/dashboard' && typeof window !== 'undefined') {
      (window as Window & { __dashboardNavClickAtMs?: number }).__dashboardNavClickAtMs = performance.now();
    }

    console.info('[nav-debug] sidebar click', {
      targetHref,
      currentPath,
      at: new Date().toISOString(),
      perfNowMs: typeof window !== 'undefined' ? performance.now() : null,
    });
  }

  return (
    <nav className="appNav" aria-label="Product navigation">
      {APP_NAV_ITEMS.map((item) => {
        const NavIcon = NAV_ICONS[item.href];
        const isActive = currentPath === item.href || (item.href !== '/dashboard' && currentPath.startsWith(item.href + '/'));

        return (
          <Link
            key={item.href}
            href={item.href}
            prefetch={item.href === '/dashboard' ? true : false}
            onClick={() => {
              logNavClick(item.href);
              onNavAttempt?.(item.href);
            }}
            className={isActive ? 'active' : ''}
            aria-current={isActive ? 'page' : undefined}
          >
            <span className="appNavIcon">
              {NavIcon ? <NavIcon size={15} /> : <span aria-hidden="true">{item.label[0]}</span>}
            </span>
            <span className="appNavLabel">{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
