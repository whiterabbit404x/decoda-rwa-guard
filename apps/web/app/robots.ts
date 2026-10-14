import type { MetadataRoute } from 'next';

import { GUARD_SITE_URL } from './public-site-links';

/**
 * Crawling rules. The public pages are indexable; the signed-in product, the
 * account flows and the API are not. Product pages already redirect anonymous
 * visitors to sign-in, so this is defence in depth, not the access control.
 * Non-production deployments (Vercel previews) ask not to be indexed at all.
 */
const PRIVATE_PATHS = [
  '/api/',
  '/auth/',
  '/access',
  '/accept-invitation',
  '/admin',
  '/reset-password',
  '/sign-out',
  '/sign-up',
  '/verify-email',
  // The signed-in product (app/(product)).
  '/alerts',
  '/assets',
  '/compliance',
  '/dashboard',
  '/evidence',
  '/exports',
  '/help',
  '/history',
  '/incidents',
  '/integrations',
  '/monitored-systems',
  '/monitoring-sources',
  '/onboarding',
  '/resilience',
  '/response-actions',
  '/settings',
  '/system-health',
  '/targets',
  '/templates',
  '/threat',
  '/workspaces',
];

export default function robots(): MetadataRoute.Robots {
  const vercelEnv = process.env.VERCEL_ENV;
  if (vercelEnv && vercelEnv !== 'production') {
    return { rules: { userAgent: '*', disallow: '/' } };
  }
  return {
    rules: { userAgent: '*', allow: '/', disallow: PRIVATE_PATHS },
    sitemap: `${GUARD_SITE_URL}/sitemap.xml`,
  };
}
