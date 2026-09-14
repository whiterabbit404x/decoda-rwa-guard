import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Source-level guardrails for the public marketing homepage. These run
// without a web server so they stay reliable in CI. They assert the truthful
// framing rules (illustration labelling, no fabricated live metrics) and the
// structural completeness (the four operating pillars, the console breadth).

const APP_DIR = path.join(__dirname, '..', 'app');
const HOME_DIR = path.join(APP_DIR, 'components', 'home');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

/** Every landing component's source, concatenated. */
function componentSources(): string {
  return fs
    .readdirSync(HOME_DIR)
    .filter((file) => file.endsWith('.tsx'))
    .map((file) => read(HOME_DIR, file))
    .join('\n');
}

/** Concatenated homepage source, used for order-independent presence checks. */
function homepageSource(): string {
  const files = [
    path.join(APP_DIR, 'page.tsx'),
    path.join(HOME_DIR, 'hero-section.tsx'),
    path.join(HOME_DIR, 'incident-workflow-demo.tsx'),
    path.join(HOME_DIR, 'operating-layer-section.tsx'),
    path.join(HOME_DIR, 'incident-lifecycle-section.tsx'),
    path.join(HOME_DIR, 'product-console-section.tsx'),
    path.join(HOME_DIR, 'evidence-ai-section.tsx'),
    path.join(HOME_DIR, 'rwa-security-section.tsx'),
    path.join(HOME_DIR, 'policy-automation-section.tsx'),
    path.join(HOME_DIR, 'teams-section.tsx'),
    path.join(HOME_DIR, 'final-cta.tsx'),
    // Pricing copy lives in the canonical shared config consumed by both the
    // homepage pricing section and the standalone /pricing page.
    path.join(APP_DIR, 'pricing-plans.ts'),
  ];
  return files.map((file) => fs.readFileSync(file, 'utf-8')).join('\n');
}

test('homepage carries the canonical marketing copy across its sections', () => {
  const source = homepageSource();
  const required = [
    'Detect threats.',
    'Investigate with evidence.',
    'Respond under policy.',
    'EXAMPLE INCIDENT WORKFLOW',
    'One security operating layer.',
    'From detection to defensible evidence.',
    'From a blockchain signal',
    'AI that has to',
    'Human-controlled',
    'Request Pilot',
    'Contact Sales',
  ];
  for (const phrase of required) {
    expect(source, `expected homepage source to contain "${phrase}"`).toContain(phrase);
  }
});

test('homepage renders the canonical positioning headline', () => {
  const hero = read(HOME_DIR, 'hero-section.tsx');
  expect(hero).toContain('Detect threats.');
  expect(hero).toContain('Investigate with evidence.');
  expect(hero).toContain('Respond under policy.');
  expect(hero).toContain('Autonomous security operations for RWA');
  expect(hero).toContain('evidence-grounded');
});

test('hero incident panel is explicitly labelled as an example, not live data', () => {
  const demo = read(HOME_DIR, 'incident-workflow-demo.tsx');
  expect(demo).toContain('EXAMPLE INCIDENT WORKFLOW');
});

test('product console preview is labelled as a preview and not live customer data', () => {
  const console = read(HOME_DIR, 'product-console-section.tsx');
  expect(console).toContain('Product preview');
  // Truthfulness: the preview must not claim to be live production evidence.
  expect(console).not.toContain('Live customer data');
});

test('the operating layer sells four buyer outcomes, each with its capabilities', () => {
  const data = read(HOME_DIR, 'home-data.ts');
  const pillars = [
    'Discover & Observe',
    'Detect & Investigate',
    'Respond Under Policy',
    'Govern & Prove',
  ];
  for (const pillar of pillars) {
    expect(data).toContain(pillar);
  }

  // Each pillar carries the outcome sentence a buyer is meant to take away.
  for (const outcome of [
    'Know what is monitored and where security coverage may be weak.',
    'Turn raw security signals into an evidence-backed investigation.',
    'Move from investigation to response without bypassing organizational controls.',
    'Keep every important decision connected to the evidence and policy behind it.',
  ]) {
    expect(data).toContain(outcome);
  }

  // Capabilities are outcome-shaped noun phrases, not a list of screens.
  for (const capability of [
    'Monitoring coverage',
    'Infrastructure discovery',
    'Alert correlation',
    'Evidence analysis',
    'Policy evaluation',
    'Approval workflow',
    'Controlled execution',
    'Audit history',
    'Tamper-evident exports',
  ]) {
    expect(data).toContain(capability);
  }
});

test('UI/page count is never the marketing message', () => {
  const source = componentSources() + read(HOME_DIR, 'home-data.ts');
  for (const framing of [
    '12 security control planes',
    '12 control planes',
    '12 screens',
    '12 modules',
    'twelve screens',
    'twelve modules',
    'twelve control planes',
  ]) {
    expect(source, `landing page must not market "${framing}"`).not.toContain(framing);
  }
});

test('product breadth stays discoverable in the security console, not in the pitch', () => {
  // The pillars replaced the twelve-card grid as the marketing hierarchy. The
  // application areas themselves are still shown — one level down, in the
  // console preview, which is where a visitor explores depth.
  const data = read(HOME_DIR, 'home-data.ts');
  const navStart = data.indexOf('export const consoleNav');
  expect(navStart).toBeGreaterThan(-1);
  const nav = data.slice(navStart, data.indexOf('export const', navStart + 1));
  for (const area of [
    'Dashboard',
    'Asset Risk',
    'Threat Monitoring',
    'Alerts',
    'Incidents',
    'Response Actions',
    'Evidence & Audit',
    'Integrations',
    'Governance',
    'System Health',
  ]) {
    expect(nav, `console preview should still surface "${area}"`).toContain(area);
  }
});

test('the observe -> detect -> investigate -> respond -> prove model is on the page', () => {
  const data = read(HOME_DIR, 'home-data.ts');
  expect(data).toContain('lifecycleRibbon');
  for (const phase of ['Observe', 'Detect', 'Investigate', 'Respond', 'Prove']) {
    expect(data).toContain(`'${phase}'`);
  }
});

test('homepage links point at real existing routes only', () => {
  const dataFile = read(HOME_DIR, 'home-data.ts');
  // Auth + conversion routes that exist in the app router.
  expect(dataFile).toContain("signIn: '/sign-in'");
  expect(dataFile).toContain("startMonitoring: '/sign-up'");
  expect(dataFile).toContain("demoMailto: 'mailto:sales@decodasecurity.com'");

  // Pricing CTAs come from the canonical shared config; both the homepage and
  // /pricing render the same labels and the same real, existing routes.
  const plans = read(APP_DIR, 'pricing-plans.ts');
  expect(plans).toContain('Request Pilot');
  expect(plans).toContain('Contact Sales');
  // The Pilot CTA is an application for review, not a self-service sign-up.
  expect(plans).toContain("ctaHref: '/request-pilot'");
  expect(plans).toContain("ctaHref: '/sign-up?plan=pro'");
  expect(plans).toContain("ctaHref: 'mailto:sales@decodasecurity.com'");
  expect(plans).not.toContain('Start free trial');

  const page = read(APP_DIR, 'page.tsx');
  expect(page).toContain('PRICING_PLANS');
});

test('public support contact uses the official decodasecurity.com address', () => {
  // The footer renders the support email as both visible text and a mailto:
  // link, defaulting to this value when NEXT_PUBLIC_SUPPORT_EMAIL is unset.
  const page = read(APP_DIR, 'page.tsx');
  expect(page).toContain("support@decodasecurity.com");
  expect(page).not.toContain('support@decoda.app');
});

test('public sales/contact CTAs use the official decodasecurity.com domain', () => {
  // Public contact identities (sales, security) must use the canonical
  // decodasecurity.com domain. Transactional/infra identities (no-reply@,
  // demo@) are intentionally out of scope and handled by mail configuration.
  const page = read(APP_DIR, 'page.tsx');
  const dataFile = read(HOME_DIR, 'home-data.ts');
  // The homepage sales CTA is served by the canonical pricing config.
  const plans = read(APP_DIR, 'pricing-plans.ts');
  expect(plans).toContain('mailto:sales@decodasecurity.com');
  expect(plans).not.toContain('sales@decoda.app');
  expect(page).not.toContain('sales@decoda.app');
  expect(dataFile).not.toContain('@decoda.app');
});
