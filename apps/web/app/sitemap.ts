import type { MetadataRoute } from 'next';

import { guardIdentityMode } from './decoda-identity';
import { GUARD_SITE_URL } from './public-site-links';

// Read at request time: whether /request-pilot is a page of this site or a
// hand-off to the company website depends on the deployment's identity mode.
export const dynamic = 'force-dynamic';

/** The public pages of this site. Product and account pages are never listed. */
const PUBLIC_PATHS = ['/', '/pricing', '/trust', '/security', '/privacy', '/terms', '/support'];

export default function sitemap(): MetadataRoute.Sitemap {
  // Under the shared Decoda identity /request-pilot redirects to the company
  // website, which lists its own page; only the legacy mode serves it here.
  const paths = guardIdentityMode() === 'legacy' ? [...PUBLIC_PATHS, '/request-pilot'] : PUBLIC_PATHS;
  return paths.map((path) => ({ url: path === '/' ? GUARD_SITE_URL : `${GUARD_SITE_URL}${path}` }));
}
