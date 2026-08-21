/**
 * Source-level guardrails for the public /trust page.
 *
 * Trust claims must not contradict observable evidence:
 *   - no unverifiable "every push" CI claim (the only workflow is manual),
 *   - no perfect-readiness score,
 *   - no "Live EVM telemetry proven" style claim while Base-first,
 *   - no prominent link into the de-emphasised Live Proof page,
 *   - SOC 2 is disclosed as not-yet-certified.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const src = fs.readFileSync(path.join(__dirname, '..', 'app', 'trust', 'page.tsx'), 'utf-8');

test('no unverifiable "every push" CI proof claim', () => {
  expect(src).not.toContain('Every push triggers');
});

test('no perfect production-readiness score claim', () => {
  expect(src).not.toContain('100/100');
  expect(src).not.toContain('100 percent');
});

test('no "Live EVM telemetry proven" style claim', () => {
  expect(src).not.toContain('Live EVM telemetry proven');
  expect(src).not.toContain('telemetry proven');
});

test('Trust does not prominently link the de-emphasised Live Proof page', () => {
  expect(src).not.toContain('/live-proof');
  expect(src).not.toContain('Live Proof');
});

test('SOC 2 is disclosed as not yet certified', () => {
  expect(src).toContain('not yet certified');
  expect(src).toContain('Not yet');
});

test('fail-closed release proof gate is described without an absolute proven claim', () => {
  expect(src).toContain('Fail-closed release proof gates');
  expect(src).toContain('unverified claim');
});
