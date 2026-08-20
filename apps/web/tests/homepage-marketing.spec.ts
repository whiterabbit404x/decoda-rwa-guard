import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Source-level guardrails for the public Decoda Security company homepage.
// These run without a web server so they stay reliable in CI. They assert the
// company-vs-flagship positioning, the truthful framing rules (roadmap items
// labelled as roadmap, product previews labelled as representative previews,
// the enterprise panel framed as positioning not live telemetry), that no
// placeholder "Product A" terminology remains, and that every link resolves to
// a real route or on-page anchor.

const APP_DIR = path.join(__dirname, '..', 'app');
const HOME_DIR = path.join(APP_DIR, 'components', 'home');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

/** Concatenated homepage source, for order-independent presence checks. */
function homepageSource(): string {
  const files = [
    path.join(APP_DIR, 'page.tsx'),
    path.join(HOME_DIR, 'marketing-header.tsx'),
    path.join(HOME_DIR, 'hero-section.tsx'),
    path.join(HOME_DIR, 'enterprise-security-panel.tsx'),
    path.join(HOME_DIR, 'what-decoda-does-section.tsx'),
    path.join(HOME_DIR, 'why-this-matters-section.tsx'),
    path.join(HOME_DIR, 'rwa-platform-section.tsx'),
    path.join(HOME_DIR, 'product-preview-card.tsx'),
    path.join(HOME_DIR, 'security-lifecycle-section.tsx'),
    path.join(HOME_DIR, 'platform-vision-section.tsx'),
    path.join(HOME_DIR, 'company-cta.tsx'),
    path.join(HOME_DIR, 'marketing-footer.tsx'),
    path.join(HOME_DIR, 'home-data.ts'),
  ];
  return files.map((file) => fs.readFileSync(file, 'utf-8')).join('\n');
}

test('hero carries the canonical company positioning', () => {
  const hero = read(HOME_DIR, 'hero-section.tsx');
  expect(hero).toContain('Security infrastructure for digital finance');
  expect(hero).toContain('Security infrastructure for blockchain financial systems.');
  expect(hero).toContain('Explore RWA Security');
  expect(hero).toContain('Request a demo');
});

test('enterprise hero panel is positioning, not fabricated live telemetry', () => {
  const panel = read(HOME_DIR, 'enterprise-security-panel.tsx');
  expect(panel).toContain('Enterprise security by design');
  // Positioning panel: it must not masquerade as live customer telemetry/data.
  expect(panel).not.toContain('Live customer data');
});

test('RWA Security is presented as the flagship platform', () => {
  const flagship = read(HOME_DIR, 'rwa-platform-section.tsx');
  expect(flagship).toContain('Flagship solution');
  expect(flagship).toContain('RWA Security Platform');
  // Canonical product description is available on the flagship section.
  expect(flagship).toContain('real-time RWA security and incident-response platform');
  expect(flagship).toContain('evidence-grounded');
});

test('flagship product previews are labelled as representative, not live data', () => {
  const preview = read(HOME_DIR, 'product-preview-card.tsx');
  expect(preview).toContain('Representative preview');
  // The three flagship preview captions describe the real screens.
  expect(preview).toContain('aria-hidden="true"');
  const flagship = read(HOME_DIR, 'rwa-platform-section.tsx');
  expect(flagship).toContain('Dashboard overview');
  expect(flagship).toContain('Investigation / incident workflow');
  expect(flagship).toContain('Evidence / audit trail');
  const source = homepageSource();
  expect(source).not.toContain('Live customer data');
  // No leftover screenshot-placeholder language.
  expect(source).not.toContain('Insert dashboard');
  expect(source).not.toContain('Space reserved for product visuals');
});

test('security lifecycle runs Observe → Detect → Investigate → Respond → Prove', () => {
  const data = read(HOME_DIR, 'home-data.ts');
  for (const step of ['Observe', 'Detect', 'Investigate', 'Respond', 'Prove']) {
    expect(data).toContain(`title: '${step}'`);
  }
  expect(read(HOME_DIR, 'security-lifecycle-section.tsx')).toContain('How RWA Security works');
});

test('roadmap items are clearly labelled as roadmap, not released products', () => {
  const roadmap = read(HOME_DIR, 'platform-vision-section.tsx');
  expect(roadmap).toContain('Roadmap preview');
  expect(roadmap).toContain('Roadmap'); // per-card badge
  expect(roadmap).toContain('Planned capability');
  expect(roadmap).toContain('not released products');
  const data = read(HOME_DIR, 'home-data.ts');
  for (const title of [
    'Policy and control orchestration',
    'Counterparty risk visibility',
    'Executive resilience command center',
  ]) {
    expect(data).toContain(title);
  }
});

test('no placeholder Product A terminology remains, and the flagship is not the pricing tier', () => {
  const source = homepageSource();
  expect(source).not.toContain('Product A');
  expect(source).not.toContain('Explore Product A');
  // "Professional" is a pricing tier inside the product, never a product name;
  // it must not appear on the public company homepage.
  expect(source).not.toContain('Professional');
});

test('homepage links resolve to real routes and on-page anchors only', () => {
  const data = read(HOME_DIR, 'home-data.ts');
  // Real, existing destinations.
  expect(data).toContain("demoMailto: 'mailto:sales@decoda.app'");
  expect(data).toContain("pricing: '/pricing'");
  expect(data).toContain("terms: '/terms'");
  expect(data).toContain("privacy: '/privacy'");
  // On-page section anchors (this page is the company site).
  for (const anchor of ['#company', '#rwa-security', '#platform-vision', '#contact']) {
    expect(data).toContain(`'${anchor}'`);
  }
  // These company sub-pages do not exist as routes — they must not be linked.
  for (const invented of ["'/contact'", "'/company'", "'/platform-vision'", "'/rwa-security'", "'/refund'"]) {
    expect(data).not.toContain(invented);
  }
});

test('navbar and footer expose the company information architecture', () => {
  const header = read(HOME_DIR, 'marketing-header.tsx');
  for (const label of ['Company', 'RWA Security', 'Platform Vision', 'Pricing', 'Contact']) {
    expect(header).toContain(label);
  }
  expect(header).toContain('Request a demo');
  // Brand is the company, Decoda Security.
  expect(header).toContain('DECODA');
  expect(header).toContain('SECURITY');

  const footer = read(HOME_DIR, 'home-data.ts');
  for (const col of ['Explore', 'Pricing', 'Legal']) {
    expect(footer).toContain(`head: '${col}'`);
  }
  for (const legal of ['Terms of Service', 'Privacy Policy', 'Refund Policy']) {
    expect(footer).toContain(legal);
  }
});

test('the section order matches the redesign structure', () => {
  const page = read(APP_DIR, 'page.tsx');
  const order = [
    'MarketingHeader',
    'HeroSection',
    'WhatDecodaDoesSection',
    'WhyThisMattersSection',
    'RWASecurityPlatformSection',
    'SecurityLifecycleSection',
    'PlatformVisionSection',
    'CompanyCTA',
    'MarketingFooter',
  ];
  let cursor = -1;
  for (const name of order) {
    const idx = page.indexOf(`<${name}`);
    expect(idx, `expected <${name}> to appear in page.tsx`).toBeGreaterThan(-1);
    expect(idx, `expected <${name}> to appear after the previous section`).toBeGreaterThan(cursor);
    cursor = idx;
  }
});
