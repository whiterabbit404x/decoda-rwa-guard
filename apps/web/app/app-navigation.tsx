"use client";

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { buildNavGroups } from './product-nav';
import { NAV_ICONS } from './nav-icons';

// Screens whose page segment renders backend data on the server. They are never
// prefetched: a prefetched payload would be data the router could show later.
// Every other screen's segment is markup only -- its data is read by the screen
// after it mounts, through the session-scoped BFF -- so prefetching it fully
// lets a sidebar click render at once without a server round trip, and shows
// nothing older than what the screen itself fetches on arrival.
const SERVER_DATA_ROUTES = new Set(['/system-health']);

// Prefetch waits until the screen's own first reads have had the network: ten
// page payloads fetched at hydration competed with the screen's API calls for
// the browser's connections and delayed its first answer. It starts after this
// delay, or as soon as the pointer or keyboard focus enters the sidebar --
// whichever comes first -- so a person heading for a link still gets it.
const PREFETCH_DELAY_MS = 1200;

export default function AppNavigation({
  currentPath,
  pendingPath = null,
  onNavAttempt,
}: {
  currentPath: string;
  /** The item a click is navigating to: highlighted at once, before the route
   *  commits. aria-current keeps naming the page that is actually shown. */
  pendingPath?: string | null;
  onNavAttempt?: (targetHref: string, plainClick: boolean) => void;
}) {
  const isDev = process.env.NODE_ENV !== 'production';
  const groups = buildNavGroups();
  const [prefetchArmed, setPrefetchArmed] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setPrefetchArmed(true), PREFETCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, []);

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
    <nav
      className="appNav"
      aria-label="Product navigation"
      onPointerEnter={() => setPrefetchArmed(true)}
      onFocus={() => setPrefetchArmed(true)}
    >
      {groups.map((group) => (
        // Each group is its own labelled list, so a screen reader announces
        // "Monitor, list, 3 items" instead of one flat run of twelve links.
        <div key={group.label} className="appNavGroup" role="group" aria-label={group.label}>
          <p className="appNavSection">{group.label}</p>
          {group.items.map((item) => {
            const NavIcon = NAV_ICONS[item.href];
            const isCurrent = currentPath === item.href || (item.href !== '/dashboard' && currentPath.startsWith(item.href + '/'));
            const highlightPath = pendingPath ?? currentPath;
            const isActive = highlightPath === item.href || (item.href !== '/dashboard' && highlightPath.startsWith(item.href + '/'));

            return (
              <Link
                key={item.href}
                href={item.href}
                prefetch={prefetchArmed && !SERVER_DATA_ROUTES.has(item.href)}
                onClick={(event) => {
                  logNavClick(item.href);
                  // A modified click opens a new tab or window: this page does not navigate.
                  const plainClick = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
                  onNavAttempt?.(item.href, plainClick);
                }}
                className={isActive ? 'active' : ''}
                aria-current={isCurrent ? 'page' : undefined}
                data-pending={pendingPath === item.href ? 'true' : undefined}
                // Read by the collapsed icon rail's hover/focus tooltip (CSS).
                // The label text stays in the link, so the accessible name
                // does not depend on it.
                data-label={item.label}
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
