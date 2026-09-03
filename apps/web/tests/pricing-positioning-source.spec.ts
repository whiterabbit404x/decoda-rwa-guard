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

  expect(pilot.price).toBe('Free Evaluation');
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
  expect(pilot.ctaHref).toBe('/sign-up');
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
