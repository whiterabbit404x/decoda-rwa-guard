import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { ECOSYSTEM, FINAL_CTA, HERO_VALUES, NAV_ITEMS, PRODUCT_SCREENS, ROUTES, STORY_STEPS } from '../app/components/home/home-data';
import { PRICING_PLANS } from '../app/pricing-plans';
import { DECODA_SITE } from '../app/public-site-links';

// Source-level guardrails for the public homepage. These run without a web
// server so they stay reliable in CI. They pin the approved positioning copy,
// the section structure, the truthful status of every Decoda product, and the
// rule that every link points at a route that exists.

const APP_DIR = path.join(__dirname, '..', 'app');
const HOME_DIR = path.join(APP_DIR, 'components', 'home');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

/** Source with comments removed: what can actually reach a visitor. */
function visible(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^\s*\/\/.*$/gm, ' ');
}

/** Every homepage component and its data, concatenated, comments stripped. */
function homepageSource(): string {
  const files = fs
    .readdirSync(HOME_DIR)
    .filter((file) => file.endsWith('.tsx') || file.endsWith('.ts'))
    .map((file) => read(HOME_DIR, file));
  return visible([read(APP_DIR, 'page.tsx'), ...files].join('\n'));
}

test('the homepage composes the approved sections, in order', () => {
  const page = read(APP_DIR, 'page.tsx');
  const order = [
    '<HeroSection />',
    '<ProblemSection />',
    '<PlatformSection />',
    '<HowItWorksSection />',
    '<ProductSection />',
    '<AiSection />',
    '<ControlsSection />',
    '<EcosystemSection />',
    '<PricingSection />',
    '<FinalCTA />',
  ];
  const positions = order.map((tag) => page.indexOf(tag));
  for (const [index, position] of positions.entries()) {
    expect(position, `${order[index]} is missing from the homepage`).toBeGreaterThan(-1);
    if (index > 0) expect(position, `${order[index]} is out of order`).toBeGreaterThan(positions[index - 1]);
  }
  expect(page).toContain('<PublicShell>');
});

test('the hero carries the approved headline, copy, value line and calls to action', () => {
  const hero = read(HOME_DIR, 'hero-section.tsx');
  for (const line of ['Protect the', 'Infrastructure Behind', 'Tokenized Finance.']) {
    expect(hero).toContain(line);
  }
  expect(visible(hero).replace(/\s+/g, ' ')).toContain(
    'Decoda RWA Guard brings security monitoring, evidence-grounded investigation, and policy-controlled response workflows together for tokenized financial infrastructure.',
  );
  expect(HERO_VALUES.map((value) => value.text)).toEqual([
    'Detect threats.',
    'Investigate with evidence.',
    'Respond under policy.',
  ]);
  expect(hero).toContain('Request Guard Pilot');
  expect(hero).toContain('href={ROUTES.requestPilot}');
  expect(hero).toContain('Explore How Guard Works');
  expect(hero).toContain('href={`#${SECTION_IDS.howItWorks}`}');
});

test('every approved section heading is on the page', () => {
  const source = homepageSource().replace(/\s+/g, ' ');
  for (const heading of [
    'A Valid Blockchain Transaction Can Still Be Unauthorized.',
    'One Security Operations Platform for Tokenized Assets.',
    'From First Signal to Verifiable Evidence.',
    'AI That Explains Its Findings.',
    'Built for Security Teams That Need Control.',
    'The Flagship of the Decoda Ecosystem.',
    'Start with a Scoped Pilot.',
  ]) {
    expect(source, `missing section heading "${heading}"`).toContain(heading);
  }
  expect(FINAL_CTA.title).toBe('Secure Your Tokenized Infrastructure with Decoda.');
  expect(FINAL_CTA.body).toBe(
    'Explore a scoped evaluation of Decoda RWA Guard with your institution’s security and operations team.',
  );
  expect(FINAL_CTA.primary).toEqual({ label: 'Request a Pilot', href: '/request-pilot' });
  expect(FINAL_CTA.secondary).toEqual({ label: 'Contact Decoda', href: DECODA_SITE.contact });
});

test('how Guard works tells the five-stage lifecycle', () => {
  expect(STORY_STEPS.map((step) => step.label)).toEqual([
    'Detect',
    'Investigate',
    'Review',
    'Respond',
    'Preserve evidence',
  ]);
});

test('the product tour covers the nine Guard screens', () => {
  expect(PRODUCT_SCREENS.map((screen) => screen.label)).toEqual([
    'Dashboard',
    'Asset Risk',
    'Monitoring Sources',
    'Threat Monitoring',
    'Alerts',
    'Incidents',
    'Response Actions',
    'Evidence & Audit',
    'System Health',
  ]);
});

test('the navigation reaches every section and the sign-in and pilot entry points', () => {
  expect(NAV_ITEMS.map((item) => item.label)).toEqual(['Platform', 'How it works', 'Product', 'Pricing', 'Ecosystem']);
  const authNav = read(HOME_DIR, 'auth-nav.tsx');
  expect(authNav).toContain('Sign in');
  expect(authNav).toContain('Request Pilot');
});

test('every Decoda product carries the status the company states for it', () => {
  const byKey = Object.fromEntries(ECOSYSTEM.map((product) => [product.key, product]));
  expect(byKey.guard.status).toBe('Available for pilot evaluation');
  expect(byKey.vault.status).toBe('In development · Testnet');
  expect(byKey.assets.status).toBe('Sandbox prototype · Testnet');
  // Vault and Assets are never presented as launched, sold or integrated.
  const ecosystem = visible(read(HOME_DIR, 'ecosystem-section.tsx'));
  expect(ecosystem).toContain('not commercially available');
  expect(ecosystem).toContain('not integrated with Guard today');
  expect(byKey.vault.href).toBe(DECODA_SITE.vault);
  expect(byKey.assets.href).toBe(DECODA_SITE.assets);
  expect(ecosystem).toContain('DECODA_SITE.ecosystem');
});

test('pricing matches the company website: a scoped pilot, Scale from $999, Enterprise custom', () => {
  const [pilot, scale, enterprise] = PRICING_PLANS;
  expect([pilot.tier, scale.tier, enterprise.tier]).toEqual(['Pilot', 'Scale', 'Enterprise']);
  expect(pilot.price).toBe('Tailored');
  expect(pilot.ctaLabel).toBe('Request a pilot');
  expect(pilot.ctaHref).toBe('/request-pilot');
  expect(scale.price).toBe('From $999');
  expect(scale.priceNote).toBe('Final pricing depends on scope');
  expect(scale.ctaLabel).toBe('Contact sales');
  expect(enterprise.price).toBe('Custom');
  expect(enterprise.ctaLabel).toBe('Contact sales');
  // Every plan is arranged with the Decoda team — no checkout, no free tier.
  const plans = visible(read(APP_DIR, 'pricing-plans.ts'));
  for (const banned of ['/sign-up', 'complimentary', 'Complimentary', 'free trial', 'Free trial', 'Start free', 'checkout?']) {
    expect(plans, `pricing must not offer "${banned}"`).not.toContain(banned);
  }
});

test('homepage links point at real existing routes only', () => {
  for (const [name, route] of Object.entries(ROUTES)) {
    if (route.startsWith('/#')) continue;
    const segment = route.replace(/^\//, '');
    const candidates = segment
      ? [path.join(APP_DIR, segment, 'page.tsx'), path.join(APP_DIR, '(product)', segment, 'page.tsx')]
      : [path.join(APP_DIR, 'page.tsx')];
    expect(candidates.some((file) => fs.existsSync(file)), `ROUTES.${name} (${route}) has no page`).toBe(true);
  }
  for (const href of Object.values(DECODA_SITE)) {
    expect(href.startsWith('https://www.decodasecurity.com')).toBe(true);
  }
});

test('public contact addresses use the official decodasecurity.com domain', () => {
  const shell = read(HOME_DIR, 'public-shell.tsx');
  expect(shell).toContain("'support@decodasecurity.com'");
  const footer = read(HOME_DIR, 'marketing-footer.tsx');
  expect(footer).toContain('mailto:${supportEmail}');
  for (const source of [homepageSource(), read(APP_DIR, 'pricing-plans.ts')]) {
    expect(source).not.toContain('@decoda.app');
  }
});

test('UI/page count is never the marketing message', () => {
  const source = homepageSource();
  for (const framing of ['12 security control planes', '12 control planes', '12 screens', '12 modules', 'twelve screens']) {
    expect(source, `the homepage must not market "${framing}"`).not.toContain(framing);
  }
});
