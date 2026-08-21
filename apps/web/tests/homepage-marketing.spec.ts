import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Source-level guardrails for the public marketing homepage. These run
// without a web server so they stay reliable in CI. They assert the truthful
// framing rules (illustration labelling, no fabricated live metrics) and the
// structural completeness (all 12 control planes, the four lifecycle phases).

const APP_DIR = path.join(__dirname, '..', 'app');
const HOME_DIR = path.join(APP_DIR, 'components', 'home');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
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
    '12 security control planes',
    'From a blockchain signal',
    'AI that has to',
    'Human-controlled',
    'Start pilot',
    'Contact sales',
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

test('operating layer lists all twelve control planes with their agents', () => {
  const data = read(HOME_DIR, 'home-data.ts');
  const screens = [
    'Onboarding',
    'Dashboard',
    'Asset Risk',
    'Monitoring Sources',
    'Threat Monitoring',
    'Alerts',
    'Incidents',
    'Response Actions',
    'Evidence & Audit',
    'Integrations',
    'Governance',
    'System Health',
  ];
  for (const screen of screens) {
    expect(data).toContain(screen);
  }

  const agents = [
    'Infrastructure Discovery Agent',
    'Executive Co-Pilot',
    'Asset Risk Assessor',
    'Source Optimization Agent',
    'Threat Detection Agent',
    'Alert Triage Agent',
    'Digital Forensics Investigator',
    'Playbook Execution Agent',
    'Crypto-Auditing Agent',
    'Integration Gateway Agent',
    'Governance Guard',
    'Self-Healing Reliability Agent',
  ];
  for (const agent of agents) {
    expect(data).toContain(agent);
  }
});

test('operating layer is grouped into the four Decoda phases', () => {
  const data = read(HOME_DIR, 'home-data.ts');
  expect(data).toContain('01 — Discover & Observe');
  expect(data).toContain('02 — Detect & Investigate');
  expect(data).toContain('03 — Respond & Prove');
  expect(data).toContain('04 — Govern & Heal');
});

test('homepage links point at real existing routes only', () => {
  const dataFile = read(HOME_DIR, 'home-data.ts');
  // Auth + conversion routes that exist in the app router.
  expect(dataFile).toContain("signIn: '/sign-in'");
  expect(dataFile).toContain("startMonitoring: '/sign-up'");
  expect(dataFile).toContain("demoMailto: 'mailto:sales@decodasecurity.com'");

  const page = read(APP_DIR, 'page.tsx');
  // Real pricing CTAs are reused verbatim; no invented plan wording.
  expect(page).toContain('Start pilot');
  expect(page).toContain('Contact sales');
  expect(page).not.toContain('Start free trial');
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
  expect(page).toContain('mailto:sales@decodasecurity.com');
  expect(page).not.toContain('sales@decoda.app');
  expect(dataFile).not.toContain('@decoda.app');
});
