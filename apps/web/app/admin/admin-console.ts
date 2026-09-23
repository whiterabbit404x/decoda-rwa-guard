/**
 * The founder (internal admin) console navigation.
 *
 * Rendering only. Every entry points at a route whose backend authorizes the
 * caller itself: /admin/* answers a customer with 403 whatever the browser
 * shows, and /dashboard and /system-health are the ordinary product screens,
 * scoped to the founder's own workspace like anyone else's.
 *
 * The External Watchlist entry appears only when the BACKEND reports the
 * feature enabled (GET /admin/console-config → features.external_watchlist
 * .enabled === true). Anything else — a 403, a network error, an API that
 * predates the field, a truthy lookalike — hides it. It is never added to the
 * customer product navigation (product-nav.ts).
 */

export const PILOT_MANAGEMENT_HREF = '/admin/customers';
export const EXTERNAL_WATCHLIST_HREF = '/admin/external-watchlist';

export type AdminConsoleConfig =
  | {
      internal_admin?: boolean;
      features?: { external_watchlist?: { enabled?: unknown } | null } | null;
    }
  | null
  | undefined;

export type AdminNavItem = { href: string; label: string };

/** True only when the backend positively stated the feature is enabled. */
export function externalWatchlistEnabled(config: AdminConsoleConfig): boolean {
  return config?.features?.external_watchlist?.enabled === true;
}

export function buildAdminConsoleNav(options: { externalWatchlistEnabled: boolean }): AdminNavItem[] {
  const items: AdminNavItem[] = [
    { href: '/dashboard', label: 'Dashboard' },
    { href: PILOT_MANAGEMENT_HREF, label: 'Pilot Management' },
  ];
  if (options.externalWatchlistEnabled === true) {
    items.push({ href: EXTERNAL_WATCHLIST_HREF, label: 'External Watchlist' });
  }
  items.push({ href: '/system-health', label: 'System Health' });
  return items;
}

export function isActiveAdminNav(pathname: string | null | undefined, href: string): boolean {
  if (!pathname) return false;
  return pathname === href || pathname.startsWith(`${href}/`);
}
