"use client";

import Link from 'next/link';

import { buildNavGroups } from './product-nav';
import { NAV_ICONS } from './nav-icons';

export default function AppNavigation({ currentPath, onNavAttempt }: { currentPath: string; onNavAttempt?: (targetHref: string) => void }) {
  const isDev = process.env.NODE_ENV !== 'production';
  const groups = buildNavGroups();

  function logNavClick(targetHref: string) {
    // Recorded in every environment, not just development: it is the epoch a
    // client-side navigation back to /dashboard is measured from, so gating it
    // on dev left production SPA navigations timed against the original
    // document navigation. One `performance.now()` read on a click.
    if (targetHref === '/dashboard' && typeof window !== 'undefined') {
      (window as Window & { __dashboardNavClickAtMs?: number }).__dashboardNavClickAtMs = performance.now();
    }

    if (!isDev) {
      return;
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
      {groups.map((group) => (
        // Each group is its own labelled list, so a screen reader announces
        // "Monitor, list, 3 items" instead of one flat run of twelve links.
        <div key={group.label} className="appNavGroup" role="group" aria-label={group.label}>
          <p className="appNavSection">{group.label}</p>
          {group.items.map((item) => {
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
        </div>
      ))}
    </nav>
  );
}
