import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { publicPageMetadata, SHARE_IMAGE } from '../app/public-metadata';
import { GUARD_SITE_URL } from '../app/public-site-links';
import robots from '../app/robots';
import sitemap from '../app/sitemap';

// Search and sharing metadata for the PUBLIC site: every public page has its
// own title, description, canonical URL and share card; crawlers are kept out
// of the signed-in product; and the sitemap lists public pages only.

const APP_DIR = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

async function withEnv<T>(overrides: Record<string, string | undefined>, run: () => T): Promise<T> {
  const saved = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(overrides)) {
    saved.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  try {
    return run();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

/** Public pages and the canonical path each one declares. */
const PUBLIC_PAGES: [string[], string][] = [
  [['page.tsx'], "path: '/'"],
  [['pricing', 'page.tsx'], "path: '/pricing'"],
  [['trust', 'page.tsx'], "path: '/trust'"],
  [['security', 'page.tsx'], "path: '/security'"],
  [['privacy', 'page.tsx'], "path: '/privacy'"],
  [['terms', 'page.tsx'], "path: '/terms'"],
  [['support', 'page.tsx'], "path: '/support'"],
  [['request-pilot', 'page.tsx'], "path: '/request-pilot'"],
  [['live-proof', 'page.tsx'], "path: '/live-proof'"],
];

test('the document title and canonical origin are the Guard site', () => {
  // (The root layout loads a font through the Next compiler, so it is read as
  // source rather than imported.)
  const layout = read(APP_DIR, 'layout.tsx');
  expect(layout).toContain("title: 'Decoda RWA Guard | Decoda Security'");
  expect(layout).toContain('metadataBase: new URL(GUARD_SITE_URL)');
  expect(GUARD_SITE_URL).toBe('https://rwa.decodasecurity.com');
});

test('every public page declares its own metadata through the shared helper', () => {
  for (const [segments, canonical] of PUBLIC_PAGES) {
    const source = read(APP_DIR, ...segments);
    expect(source, `${segments.join('/')} must use publicPageMetadata`).toContain('publicPageMetadata({');
    expect(source, `${segments.join('/')} canonical`).toContain(canonical);
  }
  // The homepage keeps the exact document title.
  const home = read(APP_DIR, 'page.tsx');
  expect(home).toContain("const HOME_TITLE = 'Decoda RWA Guard | Decoda Security';");
  expect(home).toContain('title: { absolute: HOME_TITLE }');
});

test('page metadata carries a canonical URL and a complete share card', () => {
  const meta = publicPageMetadata({ title: 'Pricing', description: 'Plans.', path: '/pricing' });
  expect(meta.title).toEqual({ absolute: 'Pricing | Decoda RWA Guard' });
  expect(meta.alternates?.canonical).toBe('/pricing');
  const og = meta.openGraph as Record<string, unknown>;
  expect(og.url).toBe('/pricing');
  expect(og.siteName).toBe('Decoda Security');
  expect(og.title).toBe('Pricing | Decoda RWA Guard');
  // A page-level openGraph object replaces the inherited one, so the share
  // image must be named on every page or it silently disappears.
  expect(og.images).toEqual([SHARE_IMAGE]);
  const twitter = meta.twitter as Record<string, unknown>;
  expect(twitter.card).toBe('summary_large_image');
  expect(twitter.images).toEqual([{ url: SHARE_IMAGE.url, alt: SHARE_IMAGE.alt }]);
});

test('the share image is a 1200×630 PNG with matching alt text', () => {
  const png = fs.readFileSync(path.join(APP_DIR, 'opengraph-image.png'));
  expect(png.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
  expect(png.readUInt32BE(16)).toBe(1200);
  expect(png.readUInt32BE(20)).toBe(630);
  expect(SHARE_IMAGE).toMatchObject({ url: '/opengraph-image.png', width: 1200, height: 630, type: 'image/png' });
  expect(read(APP_DIR, 'opengraph-image.alt.txt').trim()).toBe(SHARE_IMAGE.alt);
  // Small enough to unfurl quickly everywhere.
  expect(png.length).toBeLessThan(300 * 1024);
});

test('crawlers may index the public pages but not the product, account flows or API', () => {
  const rules = withEnv({ VERCEL_ENV: undefined }, () => robots());
  return rules.then((result) => {
    const rule = Array.isArray(result.rules) ? result.rules[0] : result.rules;
    expect(rule.userAgent).toBe('*');
    expect(rule.allow).toBe('/');
    const disallow = rule.disallow as string[];
    for (const privatePath of ['/api/', '/auth/', '/dashboard', '/assets', '/alerts', '/incidents', '/evidence', '/settings', '/onboarding', '/admin', '/sign-up', '/accept-invitation']) {
      expect(disallow, `${privatePath} must not be crawled`).toContain(privatePath);
    }
    // No public page is caught by a disallow prefix.
    for (const publicPath of ['/', '/pricing', '/trust', '/security', '/privacy', '/terms', '/support', '/request-pilot', '/sign-in']) {
      const blocked = disallow.some((prefix) => publicPath === prefix || (prefix !== '/' && publicPath.startsWith(prefix)));
      expect(blocked, `${publicPath} is blocked by robots.txt`).toBe(false);
    }
    expect(result.sitemap).toBe(`${GUARD_SITE_URL}/sitemap.xml`);
  });
});

test('every route of the signed-in product is disallowed', () => {
  const productDir = path.join(APP_DIR, '(product)');
  const routes = fs
    .readdirSync(productDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => `/${entry.name}`);
  expect(routes.length).toBeGreaterThan(10);
  return withEnv({ VERCEL_ENV: 'production' }, () => robots()).then((result) => {
    const rule = Array.isArray(result.rules) ? result.rules[0] : result.rules;
    for (const route of routes) {
      expect(rule.disallow as string[], `${route} is crawlable`).toContain(route);
    }
  });
});

test('a preview deployment asks not to be indexed at all', async () => {
  const result = await withEnv({ VERCEL_ENV: 'preview' }, () => robots());
  expect(result.rules).toEqual({ userAgent: '*', disallow: '/' });
  expect(result.sitemap).toBeUndefined();
});

test('the sitemap lists the public pages only', async () => {
  const urls = (await withEnv({ GUARD_IDENTITY_MODE: undefined }, () => sitemap())).map((entry) => entry.url);
  expect(urls).toEqual([
    GUARD_SITE_URL,
    `${GUARD_SITE_URL}/pricing`,
    `${GUARD_SITE_URL}/trust`,
    `${GUARD_SITE_URL}/security`,
    `${GUARD_SITE_URL}/privacy`,
    `${GUARD_SITE_URL}/terms`,
    `${GUARD_SITE_URL}/support`,
    `${GUARD_SITE_URL}/request-pilot`,
  ]);
});

test('under the shared Decoda identity the sitemap leaves /request-pilot to the company website', async () => {
  for (const mode of ['dual', 'workos']) {
    const urls = (await withEnv({ GUARD_IDENTITY_MODE: mode }, () => sitemap())).map((entry) => entry.url);
    expect(urls).not.toContain(`${GUARD_SITE_URL}/request-pilot`);
    expect(urls).toContain(`${GUARD_SITE_URL}/pricing`);
  }
});

test('public pages keep semantic structure: one h1 per page, from the page hero', () => {
  for (const segments of [['pricing', 'page.tsx'], ['trust', 'page.tsx'], ['live-proof', 'page.tsx'], ['request-pilot', 'request-pilot-view.tsx']]) {
    const source = read(APP_DIR, ...segments);
    expect(source, segments.join('/')).toContain('<PageHero');
    expect(source, `${segments.join('/')} renders its own h1`).not.toContain('<h1');
  }
  expect(read(APP_DIR, 'components', 'home', 'site-ui.tsx')).toMatch(/<h1 id=\{titleId\} className=\{styles\.pageHeroTitle\}>/);
});
