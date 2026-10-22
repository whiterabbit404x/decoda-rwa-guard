import { APP_NAV_GROUPS, APP_NAV_ITEMS } from './product-nav';

export type ShellCrumb = { label: string; href?: string };

/** What a page below a sidebar screen is called in the trail (its own heading names the record). */
const DETAIL_CRUMB_LABELS: Record<string, string> = {
  '/incidents': 'Incident',
  '/monitoring-sources': 'Target telemetry',
};

/** Named sub-pages, where the path itself is the name. */
const SUBPAGE_CRUMB_LABELS: Record<string, string> = {
  '/settings/identity': 'Identity',
  '/settings/notifications': 'Notifications',
  '/settings/security': 'Security',
  '/monitoring-sources/targets': 'Targets',
  '/monitoring-sources/monitored-systems': 'Monitored systems',
  '/system-health/readiness': 'Readiness',
};

/**
 * The header's breadcrumb trail: sidebar group › screen › sub-page.
 *
 * Resolved from the canonical nav list (APP_NAV_ITEMS / APP_NAV_GROUPS), so the
 * trail can never disagree with the sidebar. The group is a heading, not a
 * route, so it carries no link; the current page is the last crumb and carries
 * none either. The screen's one <h1> is the page's own heading — the header
 * no longer repeats it as a second <h1>. Unlisted routes (/workspaces, /help)
 * get no trail rather than a guess.
 */
export function breadcrumbsFor(pathname: string): ShellCrumb[] {
  const item = APP_NAV_ITEMS.find(
    (entry) => entry.href === pathname || (entry.href !== '/dashboard' && pathname.startsWith(entry.href + '/')),
  );
  if (!item) return [];
  const group = APP_NAV_GROUPS.find((entry) => (entry.hrefs as readonly string[]).includes(item.href));
  const trail: ShellCrumb[] = group ? [{ label: group.label }] : [];
  if (pathname === item.href) {
    trail.push({ label: item.label });
    return trail;
  }
  trail.push({ label: item.label, href: item.href });
  trail.push({ label: SUBPAGE_CRUMB_LABELS[pathname] ?? DETAIL_CRUMB_LABELS[item.href] ?? 'Details' });
  return trail;
}
