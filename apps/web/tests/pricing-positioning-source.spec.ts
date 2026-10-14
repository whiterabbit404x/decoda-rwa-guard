import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { EXECUTION_NOTE, PRICING_NOTE, PRICING_PLANS } from '../app/pricing-plans';
import { DECODA_SITE } from '../app/public-site-links';

// Source-level guardrails for the public pricing surfaces. These run without a
// web server so they stay reliable in CI. They lock in the commercial statement
// of the Decoda company website (www.decodasecurity.com/pricing) — Pilot is a
// scoped evaluation on tailored terms, Scale starts from $999 a month, and
// Enterprise is custom — and they keep every published limit equal to the
// limit the entitlement engine enforces.

const APP_DIR = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

/** Every word of pricing copy a visitor can read. */
const COPY = JSON.stringify(PRICING_PLANS) + PRICING_NOTE + EXECUTION_NOTE;

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
  'Start Scale',
];

function value(plan: (typeof PRICING_PLANS)[number], label: string): string {
  const row = plan.comparison.find((entry) => entry.label === label);
  expect(row, `plan ${plan.key} is missing the "${label}" comparison row`).toBeDefined();
  return row!.value;
}

test('pricing plans are the canonical Pilot / Scale / Enterprise ladder', () => {
  expect(PRICING_PLANS.map((plan) => plan.key)).toEqual(['pilot', 'scale', 'enterprise']);
  expect(PRICING_PLANS.map((plan) => plan.tier)).toEqual(['Pilot', 'Scale', 'Enterprise']);

  const [pilot, scale, enterprise] = PRICING_PLANS;

  // Pilot: a scoped evaluation whose scope and terms are agreed with each team.
  expect(pilot.tagline).toBe('Scoped evaluation');
  expect(pilot.price).toBe('Tailored');
  expect(pilot.priceIsLabel).toBe(true);
  expect(pilot.priceNote).toBe('Tailored scope and commercial terms');
  expect(pilot.ctaLabel).toBe('Request a pilot');
  // The Pilot is where the page recommends starting — and the only featured card.
  expect(PRICING_PLANS.filter((plan) => plan.featured).map((plan) => plan.key)).toEqual(['pilot']);

  // Scale: from $999 a month; the final price depends on scope.
  expect(scale.price).toBe('From $999');
  expect(scale.priceIsLabel).toBe(false);
  expect(scale.priceSub).toBe('/ month');
  expect(scale.priceNote).toBe('Final pricing depends on scope');
  expect(scale.ctaLabel).toBe('Contact sales');

  // Enterprise: custom.
  expect(enterprise.price).toBe('Custom');
  expect(enterprise.priceIsLabel).toBe(true);
  expect(enterprise.priceNote).toBe('Custom pricing');
  expect(enterprise.ctaLabel).toBe('Contact sales');
});

test('the Pilot is never advertised as free or complimentary', () => {
  // The company website sells the Pilot as a scoped evaluation on tailored
  // terms. "Free" would read as a self-serve tier, and would not be true.
  for (const pattern of [/\bfree\b/i, /complimentary/i, /no cost/i, /\$0\b/]) {
    expect(COPY, String(pattern)).not.toMatch(pattern);
  }
});

test('the Pilot card describes a guided, recommend-only evaluation', () => {
  const [pilot] = PRICING_PLANS;
  for (const bullet of [
    'Defined monitoring scope — 1 workspace, up to 5 monitored contracts',
    'Guided evaluation with the Decoda team',
    'Alerts, incident investigation and evidence workflows',
    'Recommend-only response workflows — no production execution',
    'Feedback sessions and technical validation',
  ]) {
    expect(pilot.highlights, bullet).toContain(bullet);
  }
  // Pilot evaluates playbooks WHILE it is active — the entitlement engine turns
  // them on for an ACTIVE pilot — so the card says so, with the qualifier that
  // keeps it true once the evaluation ends.
  expect(value(pilot, 'Incident playbooks')).toBe('During evaluation');
});

test('no plan advertises automatic or autonomous production execution', () => {
  // Every plan is recommend-only by default in entitlements.py. Advertising
  // autonomous execution would promise a capability the execution gate refuses.
  expect(COPY).not.toMatch(/automatic (production )?execution/i);
  expect(COPY).not.toMatch(/autonomous/i);
  expect(value(PRICING_PLANS[0], 'Production response execution')).toBe('Recommend only');
  expect(value(PRICING_PLANS[1], 'Production response execution')).toBe('Recommend only');
  expect(value(PRICING_PLANS[2], 'Production response execution')).toBe('Only by explicit agreement');
  expect(EXECUTION_NOTE).toContain('your people authorize');
  expect(EXECUTION_NOTE).toContain('Decoda never holds customer private keys');
});

test('public pricing no longer sells Pilot as a fixed 30-day window', () => {
  for (const pattern of [/30[-\s]?day/i, /\bdays remaining\b/i, /\bexpires in\b/i]) {
    expect(COPY, String(pattern)).not.toMatch(pattern);
  }
});

test('public pricing does not present Decoda as a single-network product', () => {
  expect(COPY).not.toMatch(/Base Mainnet/i);
});

test('the footnote states the commercial model and claims no automatic billing', () => {
  expect(PRICING_NOTE).toContain('scoped evaluation, by request');
  expect(PRICING_NOTE).toContain('from $999 per month');
  expect(PRICING_NOTE).toContain('Enterprise: custom pricing');
  expect(PRICING_NOTE).toContain('there is no automatic checkout');
  // No provider webhook moves an organization onto Scale — `organizations.plan`
  // is changed by internal admin only. Saying otherwise would describe billing
  // behaviour the application does not implement.
  expect(PRICING_NOTE).not.toMatch(/Paddle/i);
  expect(PRICING_NOTE).not.toMatch(/billed (monthly|automatically)/i);
});

test('retention is not advertised as a plan tier it is not', () => {
  // workspace_retention_policies is per-workspace and configurable for every plan.
  for (const plan of PRICING_PLANS) {
    expect(value(plan, 'Audit log retention'), plan.key).toBe('Configurable per workspace');
  }
});

test('published limits match the backend entitlement matrix', () => {
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
  expect(value(pilot, 'Monitored contracts')).toBe(`Up to ${pilotContracts}`);
  expect(value(pilot, 'Evidence packages')).toBe(`Up to ${pilotEvidence}`);
  expect(pilot.highlights[0]).toBe(
    `Defined monitoring scope — ${pilotWorkspaces} workspace, up to ${pilotContracts} monitored contracts`,
  );

  expect(value(scale, 'Workspaces')).toBe(scaleWorkspaces);
  expect(value(scale, 'Monitored contracts')).toBe(`Up to ${scaleContracts}`);
  expect(scaleEvidence).toBe('UNLIMITED');
  expect(value(scale, 'Evidence packages')).toBe('Unlimited');
  expect(scale.highlights[0]).toBe(
    `Defined limits — ${scaleWorkspaces} workspaces, up to ${scaleContracts} monitored contracts`,
  );

  // Incident playbooks: ON for an active evaluation, OFF once it ends.
  const [, playbooksActive, playbooksExpired, playbooksScale] = matrixRow('Incident playbooks');
  expect([playbooksActive, playbooksExpired, playbooksScale]).toEqual(['YES', 'NO', 'YES']);

  // No plan advertises autonomous execution because no plan has it.
  const [, ...execution] = matrixRow('Automatic production execution');
  expect(execution.slice(0, 4)).toEqual(['NO', 'NO', 'NO', 'NO']);
});

test('capabilities the engine declares but does not enforce are not sold', () => {
  // The matrix marks these "NOT ENFORCED" (priority routing, multi-network,
  // custom evidence templates) or "no software control" (custom SLA). A public
  // card must not promise them as plan features.
  for (const pattern of [/priority alert routing/i, /multi-network/i, /custom evidence templates/i, /\bSLA\b/]) {
    expect(COPY, String(pattern)).not.toMatch(pattern);
  }
});

test('no plan advertises a TVL or asset-value cap', () => {
  expect(COPY).not.toMatch(/TVL/i);
  expect(COPY).not.toMatch(/assets under|asset value cap/i);
});

test('comparison rows stay index-aligned across every plan', () => {
  const labels = PRICING_PLANS[0].comparison.map((row) => row.label);
  for (const plan of PRICING_PLANS) {
    expect(plan.comparison.map((row) => row.label), `plan ${plan.key}`).toEqual(labels);
  }
});

test('CTA routes stay on real, existing destinations — and none is a checkout or a sign-up', () => {
  const [pilot, scale, enterprise] = PRICING_PLANS;
  // Approval-only: the Pilot CTA leads to the APPLICATION, not to sign-up.
  expect(pilot.ctaHref).toBe('/request-pilot');
  expect(fs.existsSync(path.join(APP_DIR, 'request-pilot', 'page.tsx'))).toBe(true);
  // Every paid plan is arranged with the Decoda team.
  expect(scale.ctaHref).toBe(DECODA_SITE.contact);
  expect(enterprise.ctaHref).toBe(DECODA_SITE.contact);
  for (const plan of PRICING_PLANS) {
    expect(plan.ctaHref).not.toContain('/sign-up');
    expect(plan.ctaHref).not.toMatch(/checkout|billing/i);
  }
});

test('pricing surfaces drop consumer-SaaS and unsupported-claim wording', () => {
  const sources = [
    read(APP_DIR, 'pricing-plans.ts'),
    read(APP_DIR, 'pricing', 'page.tsx'),
    read(APP_DIR, 'page.tsx'),
    read(APP_DIR, 'components', 'home', 'pricing-section.tsx'),
    read(APP_DIR, 'components', 'home', 'plan-cards.tsx'),
  ].join('\n');

  for (const phrase of CONSUMER_WORDING) {
    expect(sources, `expected pricing sources to drop "${phrase}"`).not.toContain(phrase);
  }

  // No social proof, no fabricated peer comparison, no guaranteed SLA, no
  // certification claim.
  expect(sources).not.toMatch(/Fireblocks/i);
  expect(sources).not.toMatch(/SLA guarantee/i);
  expect(sources).not.toMatch(/SOC ?2|ISO ?27001/i);
  expect(sources).not.toMatch(/\btrusted by\b/i);
});

test('both pricing surfaces render from the shared config, not duplicated copy', () => {
  expect(read(APP_DIR, 'pricing', 'page.tsx')).toContain("from '../pricing-plans'");
  expect(read(APP_DIR, 'components', 'home', 'plan-cards.tsx')).toContain("from '../../pricing-plans'");
  expect(read(APP_DIR, 'components', 'home', 'pricing-section.tsx')).toContain('<PlanCards');
  expect(read(APP_DIR, 'pricing', 'page.tsx')).toContain('<PlanCards');
});
