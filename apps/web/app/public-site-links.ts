// ─────────────────────────────────────────────────────────────
// Destinations on the Decoda company website that the public Guard
// pages link to. Browser-safe constants (no env, no server APIs).
//
// The company website owns contact, the ecosystem overview and the
// other products' pages, so the Guard site links there instead of
// keeping a second copy. Pilot requests are NOT here: they go through
// this app's own /request-pilot route, which sends shared-identity
// deployments to the company website's Request pilot page itself
// (app/request-pilot/page.tsx, decodaLinks().requestAccess).
// ─────────────────────────────────────────────────────────────

export const DECODA_WEBSITE_URL = 'https://www.decodasecurity.com';

export const DECODA_SITE = {
  website: DECODA_WEBSITE_URL,
  contact: `${DECODA_WEBSITE_URL}/contact`,
  ecosystem: `${DECODA_WEBSITE_URL}/platform#ecosystem`,
  pricing: `${DECODA_WEBSITE_URL}/pricing`,
  guard: `${DECODA_WEBSITE_URL}/products/guard`,
  vault: `${DECODA_WEBSITE_URL}/products/vault`,
  assets: `${DECODA_WEBSITE_URL}/products/assets`,
} as const;

/** The canonical public origin of Decoda RWA Guard. */
export const GUARD_SITE_URL = 'https://rwa.decodasecurity.com';
