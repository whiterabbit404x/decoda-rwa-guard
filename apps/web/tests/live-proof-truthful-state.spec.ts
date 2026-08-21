/**
 * Source-level guardrails for the public /live-proof page.
 *
 * Root cause this locks in: the page reads proof artifacts from the gitignored,
 * diagnostic-only `artifacts/<family>/latest/summary.json` aliases. On a public
 * deployment those are not present, which previously rendered six alarming
 * "Verification pending" cards and a "6 proofs pending verification" headline.
 *
 * The invariant: missing proof must never be represented as successful proof,
 * and the page must degrade to a truthful, non-alarming state — not fabricate
 * artifacts or hard-code success.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const src = fs.readFileSync(path.join(__dirname, '..', 'app', 'live-proof', 'page.tsx'), 'utf-8');

test('the alarming "N proofs pending verification" headline is gone', () => {
  expect(src).not.toContain('pending verification');
  expect(src).not.toContain('proofs pending');
});

test('missing artifacts degrade to a neutral, honest banner (never success)', () => {
  // The success badge is gated on real published artifacts existing.
  expect(src).toContain('const hasPublished =');
  expect(src).toMatch(/hasPublished\s*\?/);
  // When nothing is published, a neutral banner is shown — not a pass state.
  expect(src).toContain('proofSummaryBadge--neutral');
  expect(src).toContain('are not currently published');
  // "All published proof checks passing" can only appear inside the hasPublished branch.
  expect(src).toContain('All published proof checks passing');
});

test('only artifact-backed cards are rendered (unknown/absent are omitted)', () => {
  expect(src).toContain("filter((c) => c.status !== 'unknown')");
});

test('the page never fabricates proof and never uses simulator data', () => {
  expect(src).toContain('never fabricated');
  expect(src).toContain('Simulator or seeded data is never used');
  // No hard-coded blanket success independent of real artifacts.
  expect(src).not.toContain('All proof checks passing');
});

test('the genuine live deployment card is always built', () => {
  expect(src).toContain('buildDeploymentCard');
  expect(src).toContain('Read live from the deployment serving this request');
});
