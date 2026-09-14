export const APP_NAV_ITEMS = [
  { href: '/onboarding', label: 'Onboarding' },
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/assets', label: 'Assets' },
  { href: '/monitoring-sources', label: 'Monitoring Sources' },
  { href: '/threat', label: 'Threat Monitoring' },
  { href: '/alerts', label: 'Alerts' },
  { href: '/incidents', label: 'Incidents' },
  { href: '/response-actions', label: 'Response Actions' },
  { href: '/evidence', label: 'Evidence & Audit' },
  { href: '/integrations', label: 'Integrations' },
  { href: '/settings', label: 'Settings' },
  { href: '/system-health', label: 'System Health' },
] as const;

/**
 * Sidebar grouping.
 *
 * Presentation only. Every href here is one of APP_NAV_ITEMS above — no route
 * is added, renamed or moved to make the grouping work, and the group headings
 * are not routes. The order follows the product's own workflow (asset →
 * target/source → telemetry → detection → alert → incident → action →
 * evidence), so the sidebar reads as the sequence an analyst actually works.
 *
 * Onboarding stays in the list rather than becoming a hidden workflow: it is
 * where an incomplete workspace goes to finish connecting monitoring, and
 * hiding it would hide the one screen that says monitoring is not yet live.
 */
export const APP_NAV_GROUPS = [
  { label: 'Overview', hrefs: ['/onboarding', '/dashboard', '/assets'] },
  { label: 'Monitor', hrefs: ['/monitoring-sources', '/threat', '/alerts'] },
  { label: 'Investigate & respond', hrefs: ['/incidents', '/response-actions'] },
  { label: 'Govern & prove', hrefs: ['/evidence', '/integrations', '/settings'] },
  { label: 'Operations', hrefs: ['/system-health'] },
] as const;

export type AppNavItem = (typeof APP_NAV_ITEMS)[number];

/**
 * The grouped view, resolved against APP_NAV_ITEMS.
 *
 * Built from the canonical list rather than duplicating labels, so a label or
 * href change in APP_NAV_ITEMS flows through, and an item that is in the
 * canonical list but missing from every group still renders (under "More")
 * instead of silently disappearing from the product.
 */
export function buildNavGroups(): Array<{ label: string; items: AppNavItem[] }> {
  const byHref = new Map<string, AppNavItem>(APP_NAV_ITEMS.map((item) => [item.href, item]));
  const grouped: Array<{ label: string; items: AppNavItem[] }> = [];
  const placed = new Set<string>();

  for (const group of APP_NAV_GROUPS) {
    const items: AppNavItem[] = [];
    for (const href of group.hrefs) {
      const item = byHref.get(href);
      if (!item) continue;
      items.push(item);
      placed.add(href);
    }
    if (items.length > 0) grouped.push({ label: group.label, items });
  }

  const ungrouped = APP_NAV_ITEMS.filter((item) => !placed.has(item.href));
  if (ungrouped.length > 0) grouped.push({ label: 'More', items: [...ungrouped] });

  return grouped;
}
