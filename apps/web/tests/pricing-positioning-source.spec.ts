import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { PRICING_PLANS, PRICING_NOTE } from '../app/pricing-plans';

// Source-level guardrails for the public pricing surfaces. These run without a
// web server so they stay reliable in CI. They lock in the institutional RWA
// buying path — Pilot = evaluation, Scale = production, Enterprise = custom —
// and keep consumer-SaaS and unsupported-claim wording out of both the
// standalone /pricing page and the homepage pricing section.

const APP_DIR = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

const CONSUMER_WORDING = [
  'Base Mainnet telemetry',
  'Most popular',
  'most popular',
  '14-day trial',
  '14 day trial',
  'no credit card',
  'No credit card',
  '$299',
  'Start Pro trial',
];

test('pricing plans are the canonical Pilot / Scale / Enterprise ladder', () => {
  expect(PRICING_PLANS.map((plan) => plan.key)).toEqual(['pilot', 'scale', 'enterprise']);
  expect(PRICING_PLANS.map((plan) => plan.tier)).toEqual(['Pilot', 'Scale', 'Enterprise']);

  const [pilot, scale, enterprise] = PRICING_PLANS;

  // The 30-day window is the product promise, so it belongs in the headline
  // slot rather than only in a bullet a reader may not reach.
  expect(pilot.price).toBe('30-Day Free Evaluation');
  expect(pilot.priceSub).toBe('');
  expect(pilot.ctaLabel).toBe('Request Pilot →');
  expect(pilot.badge).toBeUndefined();
  expect(pilot.featured).toBe(false);

  expect(scale.price).toBe('From $999');
  expect(scale.priceSub).toBe('/ month');
  expect(scale.badge).toBe('Production');
  expect(scale.ctaLabel).toBe('Start Scale →');
  // Scale is the visually emphasised production tier, and the only one.
  expect(PRICING_PLANS.filter((plan) => plan.featured).map((plan) => plan.key)).toEqual(['scale']);

  expect(enterprise.price).toBe('Custom');
  expect(enterprise.priceSub).toBe('Contact us for pricing');
  expect(enterprise.ctaLabel).toBe('Contact Sales →');
});

test('plan limits match the published entitlements', () => {
  const [pilot, scale, enterprise] = PRICING_PLANS;

  expect(pilot.highlights).toContain('1 workspace');
  expect(pilot.highlights).toContain('5 monitored contracts');

  // Scale is capped at 25 monitored contracts — never the old 50.
  expect(scale.highlights).toContain('3 workspaces');
  expect(scale.highlights).toContain('25 monitored contracts');
  expect(scale.highlights).not.toContain('50 monitored contracts');
  expect(value(scale, 'Monitored contracts')).toBe('25');

  expect(enterprise.highlights).toContain('Custom workspaces & asset coverage');
  expect(enterprise.highlights).toContain('Custom SLA');
});

test('the Pilot card describes an evaluation of the production workflows', () => {
  const [pilot] = PRICING_PLANS;

  expect(pilot.description).toContain('production security workflows');
  for (const bullet of [
    '30-day evaluation',
    '1 workspace',
    '5 monitored contracts',
    'Threat & compliance detection',
    'Incident investigation & playbooks',
    'Response recommendations — Recommend only',
    'Evidence & audit workflows — up to 10 packages',
    'Standard support',
  ]) {
    expect(pilot.highlights, bullet).toContain(bullet);
  }

  // Pilot evaluates playbooks and AI investigation WHILE the window is open —
  // the entitlement engine turns both on for an ACTIVE_PILOT — so the card must
  // not present them as withheld. The qualifier keeps it truthful once the
  // window closes.
  expect(value(pilot, 'Incident playbooks')).toBe('✓ During evaluation');
  expect(value(pilot, 'AI investigation')).toBe('✓ During evaluation');
});

test('the Scale card describes ongoing production monitoring', () => {
  const [, scale] = PRICING_PLANS;

  expect(scale.description).toBe(
    'Production monitoring and incident response for ongoing RWA operations.',
  );
  for (const bullet of [
    'Continuous production monitoring',
    'Priority alert routing',
    'Incident playbooks',
    'Unlimited evidence packages',
    'Audit-ready exports',
    'Priority email support',
  ]) {
    expect(scale.highlights, bullet).toContain(bullet);
  }
});

test('no card advertises automatic production execution', () => {
  // Every plan is recommend-only by default in entitlements.py. Advertising
  // autonomous execution would promise a capability the execution gate refuses.
  const copy = JSON.stringify(PRICING_PLANS) + PRICING_NOTE;
  expect(copy).not.toMatch(/automatic (production )?execution/i);
  expect(copy).not.toMatch(/autonomous/i);
  expect(value(PRICING_PLANS[0], 'Response execution')).toBe('Recommend only');
  for (const plan of PRICING_PLANS.slice(1)) {
    expect(value(plan, 'Response execution')).toBe('Policy-gated, human-authorized');
  }
});

test('public pricing does not present Decoda as a single-network product', () => {
  // A primary supported network in the current deployment is an implementation
  // fact, not a commercial limit, and it does not belong on a public card.
  const copy = JSON.stringify(PRICING_PLANS) + PRICING_NOTE;
  expect(copy).not.toMatch(/Base Mainnet/i);
});

test('the footnote states the evaluation model and claims no automatic billing', () => {
  expect(PRICING_NOTE).toContain('30-day, approval-only evaluation');
  expect(PRICING_NOTE).toContain('ongoing production monitoring');
  expect(PRICING_NOTE).toContain('Enterprise pricing is custom');
  // Checkout exists, but no provider webhook moves an organization onto Scale —
  // `organizations.plan` is changed by internal admin only. Saying otherwise
  // would describe billing behaviour the application does not implement.
  expect(PRICING_NOTE).not.toMatch(/Paddle/i);
  expect(PRICING_NOTE).not.toMatch(/billed (monthly|automatically)/i);
});

test('retention is not advertised as a plan tier it is not', () => {
  // workspace_retention_policies is per-workspace and configurable 1–3650 days
  // for every plan. The old "30 days" / "1 year" rows described a tiering the
  // product does not implement.
  for (const plan of PRICING_PLANS.slice(0, 2)) {
    expect(value(plan, 'Audit log retention'), plan.key).toBe('Configurable per workspace');
  }
});

test('published prices match the backend entitlement matrix', () => {
  // docs/PLAN_ENTITLEMENT_MATRIX.md is GENERATED from
  // services/api/app/entitlements.py :: capability_matrix(). Reading it here is
  // what keeps the public numbers and the enforced numbers the same numbers —
  // a limit changed in the engine fails this test until the card follows.
  const matrix = read(APP_DIR, '..', '..', '..', 'docs', 'PLAN_ENTITLEMENT_MATRIX.md');

  function matrixRow(capability: string): string[] {
    const line = matrix
      .split('\n')
      .find((row) => row.startsWith(`| ${capability} `) || row.startsWith(`| ${capability}|`));
    expect(line, `matrix is missing the "${capability}" row`).toBeDefined();
    return line!.split('|').slice(1, -1).map((cell) => cell.trim());
  }

  // Columns: capability, ACTIVE PILOT, EXPIRED PILOT, SCALE, ENTERPRISE, decided by.
  const [, pilotWorkspaces, , scaleWorkspaces] = matrixRow('Workspaces');
  const [, pilotContracts, , scaleContracts] = matrixRow('Contracts');
  const [, pilotEvidence, , scaleEvidence] = matrixRow('Evidence packages');

  const [pilot, scale] = PRICING_PLANS;
  expect(value(pilot, 'Workspaces')).toBe(pilotWorkspaces);
  expect(value(pilot, 'Monitored contracts')).toBe(pilotContracts);
  expect(value(pilot, 'Evidence packages')).toBe(`Up to ${pilotEvidence}`);
  expect(pilot.highlights).toContain(`${pilotWorkspaces} workspace`);
  expect(pilot.highlights).toContain(`${pilotContracts} monitored contracts`);

  expect(value(scale, 'Workspaces')).toBe(scaleWorkspaces);
  expect(value(scale, 'Monitored contracts')).toBe(scaleContracts);
  expect(scaleEvidence).toBe('UNLIMITED');
  expect(value(scale, 'Evidence packages')).toBe('Unlimited');

  // Incident playbooks: ON for an active evaluation, OFF once it ends. That is
  // exactly what the Pilot card's qualifier says, and what Scale states flatly.
  const [, playbooksActive, playbooksExpired, playbooksScale] = matrixRow('Incident playbooks');
  expect([playbooksActive, playbooksExpired, playbooksScale]).toEqual(['YES', 'NO', 'YES']);

  // No plan advertises autonomous execution because no plan has it.
  const [, ...execution] = matrixRow('Automatic production execution');
  expect(execution.slice(0, 4)).toEqual(['NO', 'NO', 'NO', 'NO']);
});

test('no plan advertises a TVL or asset-value cap', () => {
  const copy = JSON.stringify(PRICING_PLANS) + PRICING_NOTE;
  expect(copy).not.toMatch(/TVL/i);
  expect(copy).not.toMatch(/assets under|asset value cap/i);
});

test('comparison rows stay index-aligned across every plan', () => {
  const labels = PRICING_PLANS[0].comparison.map((row) => row.label);
  for (const plan of PRICING_PLANS) {
    expect(plan.comparison.map((row) => row.label), `plan ${plan.key}`).toEqual(labels);
  }
});

test('CTA routes stay on real, existing destinations', () => {
  const [pilot, scale, enterprise] = PRICING_PLANS;
  // Approval-only: the Pilot CTA leads to the APPLICATION, not to sign-up.
  // Sending it to sign-up was the self-serve path, where anyone who found the
  // URL received an active evaluation without Decoda approving them.
  expect(pilot.ctaHref).toBe('/request-pilot');
  expect(scale.ctaHref.startsWith('/sign-up')).toBe(true);
  expect(enterprise.ctaHref).toBe('mailto:sales@decodasecurity.com');
});

test('pricing surfaces drop consumer-SaaS and unsupported-claim wording', () => {
  const sources = [
    read(APP_DIR, 'pricing-plans.ts'),
    read(APP_DIR, 'pricing', 'page.tsx'),
    read(APP_DIR, 'page.tsx'),
    read(APP_DIR, 'components', 'home', 'pricing-section.tsx'),
  ].join('\n');

  for (const phrase of CONSUMER_WORDING) {
    expect(sources, `expected pricing sources to drop "${phrase}"`).not.toContain(phrase);
  }

  // No social proof, no fabricated peer comparison, no guaranteed SLA claim.
  expect(sources).not.toMatch(/Fireblocks/i);
  expect(sources).not.toMatch(/SLA guarantee/i);
  expect(sources).not.toMatch(/SOC ?2|ISO ?27001/i);
  expect(sources).not.toMatch(/\btrusted by\b/i);
});

test('both pricing surfaces render from the shared config, not duplicated copy', () => {
  expect(read(APP_DIR, 'pricing', 'page.tsx')).toContain("from '../pricing-plans'");
  expect(read(APP_DIR, 'page.tsx')).toContain("from 'app/pricing-plans'");
});

function value(plan: (typeof PRICING_PLANS)[number], label: string): string {
  const row = plan.comparison.find((entry) => entry.label === label);
  expect(row, `plan ${plan.key} is missing the "${label}" comparison row`).toBeDefined();
  return row!.value;
}
